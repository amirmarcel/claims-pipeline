"""End-to-end pipeline test against real LocalStack + Postgres.

Publishes a golden-seed case's claims onto claims-raw, runs the validation and
scoring workers for a bounded number of polls, and asserts the resulting
provider_scores in Postgres match the golden expectation. Skipped when
LocalStack or Postgres aren't reachable / provisioned, following the same
pattern as tests/test_integration_smoke.py -- see infra/local/README.md to run
this locally:

    docker compose -f infra/local/docker-compose.yml up -d
    ./infra/local/provision.sh
    python -c "from claims_pipeline.db import repository as r; r.apply_schema(r.connect())"
    pytest tests/test_integration_pipeline.py
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import boto3
import psycopg
import pytest

from claims_pipeline.db import repository
from claims_pipeline.events import ClaimEvent
from claims_pipeline.generator.publisher import publish_claims
from claims_pipeline.workers import scoring as scoring_worker
from claims_pipeline.workers import validation as validation_worker

ENDPOINT_URL = os.environ.get("LOCALSTACK_ENDPOINT_URL", "http://localhost:4566")
REGION = "us-east-1"
DSN = os.environ.get("CLAIMS_PIPELINE_DATABASE_URL", repository.DEFAULT_DSN)

GOLDEN_PATH = Path(__file__).parent.parent / "benchmark" / "golden.seed.jsonl"


def _load_case(case_name: str) -> dict[str, Any]:
    with GOLDEN_PATH.open() as f:
        for line in f:
            if not line.strip():
                continue
            case: dict[str, Any] = json.loads(line)
            if case["case"] == case_name:
                return case
    raise AssertionError(f"golden case not found: {case_name}")


CASE = _load_case("single-provider-mixed-outcomes")


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


@pytest.fixture
def pipeline_env() -> Any:
    if not _localstack_reachable():
        pytest.skip(f"LocalStack not reachable at {ENDPOINT_URL}; see infra/local/README.md")
    if not _postgres_reachable():
        pytest.skip(f"Postgres not reachable at {DSN}; see infra/local/README.md")

    sqs: Any = boto3.client("sqs", endpoint_url=ENDPOINT_URL, region_name=REGION)
    queue_urls = {
        name: _queue_url(sqs, name) for name in ("validation-q", "scoring-q", "validation-dlq")
    }
    if any(url is None for url in queue_urls.values()):
        pytest.skip("queues not provisioned; run infra/local/provision.sh")

    for url in queue_urls.values():
        assert url is not None
        sqs.purge_queue(QueueUrl=url)

    try:
        connection = psycopg.connect(DSN)
    except psycopg.OperationalError:
        # Something is listening on the port but rejects our credentials --
        # e.g. an unrelated local Postgres, not the docker-compose instance.
        pytest.skip(f"could not authenticate to Postgres at {DSN}; see infra/local/README.md")
    repository.apply_schema(connection)
    connection.execute("TRUNCATE claim_scores, provider_scores")
    connection.commit()

    try:
        yield connection
    finally:
        connection.execute("TRUNCATE claim_scores, provider_scores")
        connection.commit()
        connection.close()


def test_pipeline_produces_expected_provider_scores(pipeline_env: psycopg.Connection[Any]) -> None:
    claims = [ClaimEvent.from_dict(c) for c in CASE["claims"]]
    sent = publish_claims(claims, rate=1000.0, endpoint_url=ENDPOINT_URL, region_name=REGION)
    assert sent == len(claims)

    validation_worker.run(
        endpoint_url=ENDPOINT_URL,
        region_name=REGION,
        poll_seconds=3,
        idle_polls_before_exit=2,
    )
    scoring_worker.run(
        endpoint_url=ENDPOINT_URL,
        region_name=REGION,
        dsn=DSN,
        poll_seconds=3,
        idle_polls_before_exit=2,
    )

    expected = CASE["expected"]["provider_scores"]
    for provider_id, expected_scores in expected.items():
        row = pipeline_env.execute(
            "SELECT provider_score, cost_efficiency, quality, claim_count "
            "FROM provider_scores WHERE provider_id = %s",
            (provider_id,),
        ).fetchone()
        assert row is not None, f"no provider_scores row for {provider_id}"
        provider_score, cost_efficiency, quality, claim_count = row
        assert provider_score == pytest.approx(expected_scores["provider_score"])
        assert cost_efficiency == pytest.approx(expected_scores["cost_efficiency"])
        assert quality == pytest.approx(expected_scores["quality"])
        assert claim_count == expected_scores["claim_count"]
