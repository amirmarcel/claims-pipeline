"""Ack discipline for the validation and scoring workers (SPEC.md §5, ADR-0010).

Unit-level: exercises `handle_message` directly with a fake SQS client (and,
for the scoring worker, a fake DB connection), so the ack *decision* -- delete
on success, leave unacked on failure -- runs unconditionally in CI without
LocalStack or Postgres. The real redrive-to-DLQ mechanics (SQS's own redrive
policy acting on an unacked message) are exercised end-to-end against real
infra in tests/test_reliability_e2e.py.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import psycopg
import pytest

from claims_pipeline.events import ClaimEvent
from claims_pipeline.workers import scoring as scoring_worker
from claims_pipeline.workers import validation as validation_worker


class FakeSQS:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.deleted: list[str] = []

    def send_message(self, *, QueueUrl: str, MessageBody: str) -> dict[str, Any]:
        self.sent.append((QueueUrl, MessageBody))
        return {"MessageId": "fake"}

    def delete_message(self, *, QueueUrl: str, ReceiptHandle: str) -> None:
        self.deleted.append(ReceiptHandle)


def _message(
    body: str, *, receipt_handle: str = "rh-1", message_id: str = "msg-1"
) -> dict[str, Any]:
    return {
        "MessageId": message_id,
        "ReceiptHandle": receipt_handle,
        "Body": body,
        "Attributes": {"ApproximateReceiveCount": "1"},
    }


VALID_CLAIM = ClaimEvent(
    claim_id="clm_ack_0001",
    provider_id="P-001",
    specialty="cardiology",
    procedure_code="Q100",
    billed_amount=1000.0,
    allowed_amount=800.0,
    outcome="clean",
    patient_ref="ref_000000000001",
    service_date="2026-01-01",
    schema_version="1.0",
)


def test_validation_worker_acks_successfully_processed_message() -> None:
    sqs = FakeSQS()
    message = _message(json.dumps(VALID_CLAIM.to_dict()))

    acked = validation_worker.handle_message(
        sqs,
        message,
        validation_url="validation-q-url",
        scoring_url="scoring-q-url",
        dlq_url="validation-dlq-url",
    )

    assert acked is True
    assert message["ReceiptHandle"] in sqs.deleted
    assert sqs.sent == [("scoring-q-url", json.dumps(VALID_CLAIM.to_dict()))]


def test_validation_worker_does_not_ack_poison_message() -> None:
    sqs = FakeSQS()
    message = _message("not valid json at all {")

    acked = validation_worker.handle_message(
        sqs,
        message,
        validation_url="validation-q-url",
        scoring_url="scoring-q-url",
        dlq_url="validation-dlq-url",
    )

    assert acked is False
    assert sqs.deleted == []
    assert sqs.sent == []


def test_validation_worker_acks_business_invalid_message() -> None:
    # Business-invalid (Session 2 path): decodable, but violates events.validate.
    # It IS successfully routed, so it IS acked -- distinct from the poison case.
    sqs = FakeSQS()
    invalid = replace(VALID_CLAIM, allowed_amount=2000.0)
    message = _message(json.dumps(invalid.to_dict()))

    acked = validation_worker.handle_message(
        sqs,
        message,
        validation_url="validation-q-url",
        scoring_url="scoring-q-url",
        dlq_url="validation-dlq-url",
    )

    assert acked is True
    assert message["ReceiptHandle"] in sqs.deleted
    [(url, body)] = sqs.sent
    assert url == "validation-dlq-url"
    assert json.loads(body)["reason"] == "allowed_amount exceeds billed_amount"


class FakeConn:
    def __init__(self, *, raise_on_upsert: bool = False, rollback_fails: bool = False) -> None:
        self.raise_on_upsert = raise_on_upsert
        self.rollback_fails = rollback_fails
        self.rolled_back = False
        self.closed = False
        self.upserted: list[str] = []

    def rollback(self) -> None:
        if self.rollback_fails:
            self.closed = True
            raise psycopg.OperationalError("connection already closed")
        self.rolled_back = True


def test_scoring_worker_acks_successfully_processed_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sqs = FakeSQS()
    conn = FakeConn()
    monkeypatch.setattr(
        scoring_worker.repository,
        "upsert_claim_and_recompute",
        lambda c, claim: conn.upserted.append(claim.claim_id),
    )
    message = _message(json.dumps(VALID_CLAIM.to_dict()))

    acked = scoring_worker.handle_message(sqs, conn, message, scoring_url="scoring-q-url")  # type: ignore[arg-type]

    assert acked is True
    assert message["ReceiptHandle"] in sqs.deleted
    assert conn.upserted == [VALID_CLAIM.claim_id]
    assert not conn.rolled_back


def test_scoring_worker_does_not_ack_message_that_fails_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sqs = FakeSQS()
    conn = FakeConn()

    def _boom(c: Any, claim: Any) -> None:
        raise RuntimeError("transient downstream failure")

    monkeypatch.setattr(scoring_worker.repository, "upsert_claim_and_recompute", _boom)
    message = _message(json.dumps(VALID_CLAIM.to_dict()))

    acked = scoring_worker.handle_message(sqs, conn, message, scoring_url="scoring-q-url")  # type: ignore[arg-type]

    assert acked is False
    assert sqs.deleted == []
    assert conn.rolled_back


def test_scoring_worker_survives_rollback_on_broken_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A transient outage can break the connection itself, not just the
    # query -- rollback() on a dead connection raises. handle_message must
    # not let that raise crash out; it should still return unacked so `run`
    # can reconnect before the next message.
    sqs = FakeSQS()
    conn = FakeConn(rollback_fails=True)

    def _boom(c: Any, claim: Any) -> None:
        raise RuntimeError("transient downstream failure")

    monkeypatch.setattr(scoring_worker.repository, "upsert_claim_and_recompute", _boom)
    message = _message(json.dumps(VALID_CLAIM.to_dict()))

    acked = scoring_worker.handle_message(sqs, conn, message, scoring_url="scoring-q-url")  # type: ignore[arg-type]

    assert acked is False
    assert sqs.deleted == []
    assert conn.closed


def test_scoring_worker_does_not_ack_undecodable_body() -> None:
    sqs = FakeSQS()
    conn = FakeConn()
    message = _message("{not-json")

    acked = scoring_worker.handle_message(sqs, conn, message, scoring_url="scoring-q-url")  # type: ignore[arg-type]

    assert acked is False
    assert sqs.deleted == []
