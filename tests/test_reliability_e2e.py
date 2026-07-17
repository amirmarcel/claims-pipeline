"""End-to-end reliability story against real LocalStack + Postgres (SPEC.md
§5, ADR-0010). Skipped when infra isn't reachable, following the same
socket-reachability pattern as tests/test_integration_pipeline.py -- see
infra/local/README.md to run this locally:

    docker compose -f infra/local/docker-compose.yml up -d
    ./infra/local/provision.sh
    python -c "from claims_pipeline.db import repository as r; r.apply_schema(r.connect())"
    pytest tests/test_reliability_e2e.py

Two distinct poison scenarios are exercised, matching the two failure classes
SPEC.md §5 names explicitly:

- **validation worker: unparseable** -- a genuinely malformed body (produced
  by the generator's `failure_injection={"malformed": ...}` knob) is redriven
  to validation-dlq after maxReceiveCount receives, without ever crashing the
  worker. A truly undecodable body has no "corrected" form to replay back
  onto the source queue byte-for-byte, so this scenario asserts the redrive
  and the dry-run classification, not a successful replay.
- **scoring worker: unexpected error** -- a well-formed, decodable claim that
  fails processing (simulated here the same way a transient bug or a
  momentary downstream outage would: `repository.upsert_claim_and_recompute`
  raises for a bounded number of attempts) is redriven to scoring-dlq. Once
  the cause is "addressed" (the failure condition clears), the replay utility
  re-drives the exact same message onto scoring-q and it processes cleanly --
  this is the case SPEC.md §5's "once the cause is addressed" replay
  language actually describes, and it is what proves idempotency holds across
  replay: the provider aggregate reflects exactly one claim, not the failed
  attempts.
"""

from __future__ import annotations

import json
import os
import socket
import time
from typing import Any
from urllib.parse import urlparse

import boto3
import psycopg
import pytest

from claims_pipeline.db import repository
from claims_pipeline.events import ClaimEvent
from claims_pipeline.generator.claims import MalformedEvent, generate_claims
from claims_pipeline.generator.config import GeneratorConfig
from claims_pipeline.generator.publisher import publish_claims
from claims_pipeline.replay.core import list_dead_letters, replay_dead_letters
from claims_pipeline.workers import scoring as scoring_worker
from claims_pipeline.workers import validation as validation_worker

ENDPOINT_URL = os.environ.get("LOCALSTACK_ENDPOINT_URL", "http://localhost:4566")
REGION = "us-east-1"
DSN = os.environ.get("CLAIMS_PIPELINE_DATABASE_URL", repository.DEFAULT_DSN)

QUEUE_NAMES = ("validation-q", "scoring-q", "validation-dlq", "scoring-dlq")


def _reachable(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _localstack_reachable() -> bool:
    parsed = urlparse(ENDPOINT_URL)
    return _reachable(parsed.hostname or "localhost", parsed.port or 80)


def _postgres_reachable() -> bool:
    parsed = urlparse(DSN)
    return _reachable(parsed.hostname or "localhost", parsed.port or 5433)


def _queue_url(sqs: Any, name: str) -> str | None:
    try:
        url: str = sqs.get_queue_url(QueueName=name)["QueueUrl"]
        return url
    except sqs.exceptions.QueueDoesNotExist:
        return None


def _run_worker_until(
    run_once: Any, *, is_done: Any, max_rounds: int = 8, settle_seconds: float = 2.5
) -> bool:
    """Run a worker repeatedly, pausing to let the (short, local-rig-only)
    visibility timeout expire between rounds, until `is_done()` is true or
    `max_rounds` is exhausted. Round-based rather than a fixed receive count
    because the exact number of `run()` calls needed to accumulate
    maxReceiveCount receives depends on SQS-implementation receive/redrive
    timing, not just the configured count."""
    for _ in range(max_rounds):
        run_once()
        if is_done():
            return True
        time.sleep(settle_seconds)
    return is_done()


@pytest.fixture
def reliability_env() -> Any:
    if not _localstack_reachable():
        pytest.skip(f"LocalStack not reachable at {ENDPOINT_URL}; see infra/local/README.md")
    if not _postgres_reachable():
        pytest.skip(f"Postgres not reachable at {DSN}; see infra/local/README.md")

    sqs: Any = boto3.client("sqs", endpoint_url=ENDPOINT_URL, region_name=REGION)
    queue_urls = {name: _queue_url(sqs, name) for name in QUEUE_NAMES}
    if any(url is None for url in queue_urls.values()):
        pytest.skip("queues not provisioned; run infra/local/provision.sh")

    for url in queue_urls.values():
        assert url is not None
        sqs.purge_queue(QueueUrl=url)

    try:
        connection = psycopg.connect(DSN)
    except psycopg.OperationalError:
        pytest.skip(f"could not authenticate to Postgres at {DSN}; see infra/local/README.md")
    repository.apply_schema(connection)
    connection.execute("TRUNCATE claim_scores, provider_scores")
    connection.commit()

    try:
        yield sqs, queue_urls, connection
    finally:
        connection.execute("TRUNCATE claim_scores, provider_scores")
        connection.commit()
        connection.close()


def test_malformed_message_redrives_to_validation_dlq_without_crashing_worker(
    reliability_env: Any,
) -> None:
    sqs, queue_urls, _conn = reliability_env

    config = GeneratorConfig(rate=1000.0, seed=7, count=1, failure_injection={"malformed": 1.0})
    [event] = generate_claims(config)
    assert isinstance(event, MalformedEvent)

    sent = publish_claims([event], rate=1000.0, endpoint_url=ENDPOINT_URL, region_name=REGION)
    assert sent == 1

    def _run_once() -> None:
        validation_worker.run(
            endpoint_url=ENDPOINT_URL,
            region_name=REGION,
            poll_seconds=1,
            idle_polls_before_exit=2,
        )

    def _redriven() -> bool:
        return bool(sqs.get_queue_attributes(
            QueueUrl=queue_urls["validation-dlq"],
            AttributeNames=["ApproximateNumberOfMessages"],
        )["Attributes"]["ApproximateNumberOfMessages"] != "0")

    # maxReceiveCount=3 (ADR-0010): repeated failed receives eventually
    # redrive the message. The local rig's VisibilityTimeout is 2s
    # (provision.sh), so a short sleep between rounds lets it become visible
    # again for the next attempt.
    assert _run_worker_until(_run_once, is_done=_redriven), (
        "message was not redriven to validation-dlq within the round budget"
    )

    dlq_records = list_dead_letters(sqs, queue_urls["validation-dlq"])
    assert len(dlq_records) == 1
    [record] = dlq_records
    assert record.kind == "poison"
    assert "malformed JSON" in record.reason

    # The worker never raised out of run() across the redelivery cycles --
    # nothing was dropped silently, and validation-q is now empty.
    remaining = sqs.receive_message(QueueUrl=queue_urls["validation-q"], WaitTimeSeconds=1)
    assert remaining.get("Messages", []) == []


def test_scoring_worker_poison_message_redrives_and_replay_recovers_cleanly(
    reliability_env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sqs, queue_urls, conn = reliability_env

    claim = ClaimEvent(
        claim_id="clm_reliability_0001",
        provider_id="P-900",
        specialty="cardiology",
        procedure_code="Q100",
        billed_amount=1000.0,
        allowed_amount=800.0,
        outcome="clean",
        patient_ref="ref_000000000900",
        service_date="2026-01-01",
        schema_version="1.0",
    )
    sqs.send_message(
        QueueUrl=queue_urls["scoring-q"], MessageBody=json.dumps(claim.to_dict())
    )

    # Simulate a transient downstream failure (e.g. a momentarily-unavailable
    # Postgres, or a bug that is later fixed): every call to the real upsert
    # raises until the test flips `should_fail` off. ADR-0010 says the
    # worker's only response to a failure is to leave the message unacked and
    # let SQS's own redrive policy handle it -- so as long as `should_fail` is
    # on, the message can never succeed prematurely, regardless of exactly
    # how many times SQS redelivers it before diverting to the DLQ.
    real_upsert = repository.upsert_claim_and_recompute
    attempts = {"count": 0}
    should_fail = {"value": True}

    def _flaky_upsert(connection: psycopg.Connection[Any], c: ClaimEvent) -> None:
        attempts["count"] += 1
        if should_fail["value"]:
            raise RuntimeError("simulated transient downstream failure")
        real_upsert(connection, c)

    monkeypatch.setattr(scoring_worker.repository, "upsert_claim_and_recompute", _flaky_upsert)

    def _run_once() -> None:
        scoring_worker.run(
            endpoint_url=ENDPOINT_URL,
            region_name=REGION,
            dsn=DSN,
            poll_seconds=1,
            idle_polls_before_exit=2,
        )

    def _redriven() -> bool:
        return bool(sqs.get_queue_attributes(
            QueueUrl=queue_urls["scoring-dlq"],
            AttributeNames=["ApproximateNumberOfMessages"],
        )["Attributes"]["ApproximateNumberOfMessages"] != "0")

    assert _run_worker_until(_run_once, is_done=_redriven), (
        "message was not redriven to scoring-dlq within the round budget"
    )

    assert attempts["count"] >= 3  # at least maxReceiveCount failed attempts occurred
    dlq_records = list_dead_letters(sqs, queue_urls["scoring-dlq"])
    assert len(dlq_records) == 1
    assert dlq_records[0].claim_id == claim.claim_id

    row = conn.execute(
        "SELECT COUNT(*) FROM claim_scores WHERE claim_id = %s", (claim.claim_id,)
    ).fetchone()
    assert row is not None and row[0] == 0  # nothing committed by the failed attempts

    # list_dead_letters peeks with a short visibility timeout of its own; let
    # it lapse so the message is available again for the replay's own receive.
    time.sleep(2.5)

    # "Once the cause is addressed": the failure condition clears.
    should_fail["value"] = False

    replayed = replay_dead_letters(
        sqs, dlq_url=queue_urls["scoring-dlq"], source_queue_url=queue_urls["scoring-q"]
    )
    assert len(replayed) == 1
    assert replayed[0].claim_id == claim.claim_id

    scoring_worker.run(
        endpoint_url=ENDPOINT_URL,
        region_name=REGION,
        dsn=DSN,
        poll_seconds=1,
        idle_polls_before_exit=1,
    )

    row = conn.execute(
        "SELECT provider_score, cost_efficiency, quality, claim_count "
        "FROM provider_scores WHERE provider_id = %s",
        (claim.provider_id,),
    ).fetchone()
    assert row is not None
    provider_score, cost_efficiency, quality, claim_count = row
    assert claim_count == 1  # idempotency: not 4, despite 3 failed + 1 successful attempt
    assert cost_efficiency == pytest.approx(0.8)
    assert quality == pytest.approx(1.0)
    assert provider_score == pytest.approx(0.9)
