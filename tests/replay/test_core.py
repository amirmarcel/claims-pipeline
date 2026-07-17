"""Replay utility core logic (SPEC.md §5), unit-level against a fake SQS
client so classification and re-drive selection run unconditionally in CI.
End-to-end replay against a real DLQ is exercised in
tests/test_reliability_e2e.py.
"""

from __future__ import annotations

import json
from typing import Any

from claims_pipeline.events import ClaimEvent
from claims_pipeline.replay.core import describe_body, list_dead_letters, replay_dead_letters

VALID_CLAIM = ClaimEvent(
    claim_id="clm_replay_0001",
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


def test_describe_body_classifies_business_invalid() -> None:
    reason_text = "allowed_amount exceeds billed_amount"
    body = json.dumps({"claim": VALID_CLAIM.to_dict(), "reason": reason_text})
    kind, reason, claim_id = describe_body(body)
    assert kind == "business-invalid"
    assert reason == reason_text
    assert claim_id == VALID_CLAIM.claim_id


def test_describe_body_classifies_malformed_json() -> None:
    kind, reason, claim_id = describe_body("not valid json {")
    assert kind == "poison"
    assert "malformed JSON" in reason
    assert claim_id is None


def test_describe_body_classifies_undecodable_claim_shape() -> None:
    kind, reason, claim_id = describe_body(json.dumps({"claim_id": "clm_x"}))
    assert kind == "poison"
    assert "undecodable claim event" in reason
    assert claim_id == "clm_x"


def test_describe_body_classifies_decodable_claim_that_failed_processing() -> None:
    kind, reason, claim_id = describe_body(json.dumps(VALID_CLAIM.to_dict()))
    assert kind == "poison"
    assert claim_id == VALID_CLAIM.claim_id


class FakeSQS:
    """In-memory SQS-like queue collection, keyed by queue url."""

    def __init__(self) -> None:
        self.queues: dict[str, list[dict[str, Any]]] = {}
        self._next_id = 0

    def seed(self, queue_url: str, bodies: list[str]) -> None:
        messages = []
        for body in bodies:
            self._next_id += 1
            messages.append(
                {
                    "MessageId": f"msg-{self._next_id}",
                    "ReceiptHandle": f"rh-{self._next_id}",
                    "Body": body,
                }
            )
        self.queues.setdefault(queue_url, []).extend(messages)

    def receive_message(
        self, *, QueueUrl: str, MaxNumberOfMessages: int, **_: Any
    ) -> dict[str, Any]:
        available = self.queues.get(QueueUrl, [])
        batch = available[:MaxNumberOfMessages]
        return {"Messages": batch}

    def send_message(self, *, QueueUrl: str, MessageBody: str) -> dict[str, Any]:
        self._next_id += 1
        message = {
            "MessageId": f"msg-{self._next_id}",
            "ReceiptHandle": f"rh-{self._next_id}",
            "Body": MessageBody,
        }
        self.queues.setdefault(QueueUrl, []).append(message)
        return {"MessageId": message["MessageId"]}

    def delete_message(self, *, QueueUrl: str, ReceiptHandle: str) -> None:
        self.queues[QueueUrl] = [
            m for m in self.queues.get(QueueUrl, []) if m["ReceiptHandle"] != ReceiptHandle
        ]


def test_list_dead_letters_does_not_delete_messages() -> None:
    sqs = FakeSQS()
    sqs.seed("dlq-url", ["not valid json {", json.dumps(VALID_CLAIM.to_dict())])

    records = list_dead_letters(sqs, "dlq-url")

    assert len(records) == 2
    assert {r.kind for r in records} == {"poison"}
    assert len(sqs.queues["dlq-url"]) == 2  # nothing deleted by inspection


def test_list_dead_letters_respects_limit() -> None:
    sqs = FakeSQS()
    sqs.seed("dlq-url", [json.dumps(VALID_CLAIM.to_dict())] * 5)

    records = list_dead_letters(sqs, "dlq-url", limit=2)

    assert len(records) == 2


def test_replay_dead_letters_moves_messages_to_source_queue() -> None:
    sqs = FakeSQS()
    sqs.seed("dlq-url", [json.dumps(VALID_CLAIM.to_dict())])

    replayed = replay_dead_letters(sqs, dlq_url="dlq-url", source_queue_url="source-url")

    assert len(replayed) == 1
    assert sqs.queues["dlq-url"] == []
    [source_message] = sqs.queues["source-url"]
    assert json.loads(source_message["Body"]) == VALID_CLAIM.to_dict()


def test_replay_dead_letters_filters_by_claim_id() -> None:
    other = ClaimEvent(
        claim_id="clm_replay_0002",
        provider_id="P-002",
        specialty="primary",
        procedure_code="Q200",
        billed_amount=500.0,
        allowed_amount=500.0,
        outcome="clean",
        patient_ref="ref_000000000002",
        service_date="2026-01-02",
        schema_version="1.0",
    )
    sqs = FakeSQS()
    sqs.seed("dlq-url", [json.dumps(VALID_CLAIM.to_dict()), json.dumps(other.to_dict())])

    replayed = replay_dead_letters(
        sqs,
        dlq_url="dlq-url",
        source_queue_url="source-url",
        claim_ids={VALID_CLAIM.claim_id},
    )

    assert [r.claim_id for r in replayed] == [VALID_CLAIM.claim_id]
    remaining = [json.loads(m["Body"])["claim_id"] for m in sqs.queues["dlq-url"]]
    assert remaining == [other.claim_id]
