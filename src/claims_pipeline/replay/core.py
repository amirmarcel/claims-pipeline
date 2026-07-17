"""Core DLQ inspection and replay logic, split from `cli.py` so it is testable
without going through argv (SPEC.md §5).

A dead-lettered message is one of two shapes (ADR-0010):

- **business-invalid**: `{"claim": {...}, "reason": "..."}`, written by the
  validation worker itself when a decodable claim fails an `events.validate`
  rule.
- **poison**: anything else. Either the body never decoded as JSON at all, or
  it decoded but not into a well-formed `ClaimEvent`, or it decoded into a
  perfectly good `ClaimEvent` that nonetheless failed processing (e.g. a
  transient downstream error on the scoring worker) -- SQS's redrive policy
  moved it here verbatim, with no reason field of our own authorship to read.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from claims_pipeline.events import ClaimEvent

# SQS caps a single receive_message call at 10 messages.
_MAX_BATCH = 10


@dataclass(frozen=True, slots=True)
class DeadLetterRecord:
    message_id: str
    receipt_handle: str
    body: str
    kind: str  # "business-invalid" | "poison"
    reason: str
    claim_id: str | None


def describe_body(body: str) -> tuple[str, str, str | None]:
    """Classify a DLQ message body. Returns (kind, reason, claim_id)."""
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        return "poison", f"malformed JSON: {exc}", None

    if isinstance(data, dict) and "claim" in data and "reason" in data:
        claim = data["claim"]
        claim_id = claim.get("claim_id") if isinstance(claim, dict) else None
        return "business-invalid", str(data["reason"]), claim_id

    claim_id = data.get("claim_id") if isinstance(data, dict) else None
    try:
        claim = ClaimEvent.from_dict(data)
    except (KeyError, ValueError, TypeError) as exc:
        return "poison", f"undecodable claim event: {exc}", claim_id

    return (
        "poison",
        "decodable claim event that failed processing (see worker logs for the cause)",
        claim.claim_id,
    )


def list_dead_letters(
    sqs: Any, dlq_url: str, *, limit: int | None = None
) -> list[DeadLetterRecord]:
    """Peek at up to `limit` messages on a DLQ without deleting them.

    Uses a short visibility timeout so messages become available again
    quickly; this is inspection, not consumption.
    """
    records: list[DeadLetterRecord] = []
    seen_receipts: set[str] = set()
    while limit is None or len(records) < limit:
        batch_size = _MAX_BATCH if limit is None else min(_MAX_BATCH, limit - len(records))
        response = sqs.receive_message(
            QueueUrl=dlq_url,
            MaxNumberOfMessages=batch_size,
            VisibilityTimeout=2,
            WaitTimeSeconds=1,
        )
        messages = response.get("Messages", [])
        if not messages:
            break
        new_messages = [m for m in messages if m["ReceiptHandle"] not in seen_receipts]
        if not new_messages:
            break
        for message in new_messages:
            seen_receipts.add(message["ReceiptHandle"])
            kind, reason, claim_id = describe_body(message["Body"])
            records.append(
                DeadLetterRecord(
                    message_id=message["MessageId"],
                    receipt_handle=message["ReceiptHandle"],
                    body=message["Body"],
                    kind=kind,
                    reason=reason,
                    claim_id=claim_id,
                )
            )
    return records


def replay_dead_letters(
    sqs: Any,
    *,
    dlq_url: str,
    source_queue_url: str,
    limit: int | None = None,
    claim_ids: set[str] | None = None,
) -> list[DeadLetterRecord]:
    """Re-drive selected messages from a DLQ back onto their source queue.

    Safe because consumers are idempotent on `claim_id` (ADR-0007): a
    replayed message that was already processed converges to the same
    aggregate rather than double-counting. Each message is only deleted from
    the DLQ after it has been successfully re-sent to the source queue, so a
    failure mid-replay leaves the message on the DLQ rather than losing it
    (AGENTS.md non-negotiable #4).
    """
    replayed: list[DeadLetterRecord] = []
    while limit is None or len(replayed) < limit:
        batch_size = _MAX_BATCH if limit is None else min(_MAX_BATCH, limit - len(replayed))
        response = sqs.receive_message(
            QueueUrl=dlq_url,
            MaxNumberOfMessages=batch_size,
            VisibilityTimeout=30,
            WaitTimeSeconds=1,
        )
        messages = response.get("Messages", [])
        if not messages:
            break
        progressed = False
        for message in messages:
            kind, reason, claim_id = describe_body(message["Body"])
            if claim_ids is not None and claim_id not in claim_ids:
                continue
            sqs.send_message(QueueUrl=source_queue_url, MessageBody=message["Body"])
            sqs.delete_message(QueueUrl=dlq_url, ReceiptHandle=message["ReceiptHandle"])
            replayed.append(
                DeadLetterRecord(
                    message_id=message["MessageId"],
                    receipt_handle=message["ReceiptHandle"],
                    body=message["Body"],
                    kind=kind,
                    reason=reason,
                    claim_id=claim_id,
                )
            )
            progressed = True
            if limit is not None and len(replayed) >= limit:
                break
        if not progressed:
            # every message in this batch was filtered out by claim_ids;
            # avoid spinning forever re-receiving the same unmatched messages.
            break
    return replayed
