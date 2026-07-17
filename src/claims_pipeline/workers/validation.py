"""Validation worker (SPEC.md §2 step 3): validation-q -> scoring-q | validation-dlq.

A thin I/O shell around the pure `events.validate` core (AGENTS.md non-negotiable
#1 / ADR-0003): this module only decodes messages and routes them, it makes no
scoring decisions. Idempotent on claim_id: forwarding (or dead-lettering) the
same claim twice is a safe no-op downstream, because the scoring worker
upserts by claim_id (ADR-0007).

Two distinct failure paths land in validation-dlq, and only one of them is this
module's own doing (SPEC.md §5, ADR-0010):

- **Business-invalid** claims (decodable, but fail an `events.validate` rule,
  e.g. `allowed_amount > billed_amount`) are explicitly routed here by
  `process_message`, with a structured `reason` it authors itself. This is
  Session 2 behavior, unchanged.
- **Poison** messages (bodies that don't even decode into a `ClaimEvent`) are
  *not* handled by this module directly. `process_message` lets the decode
  error propagate; `run` catches it, logs structured context, and does not
  delete the message. SQS's own redrive policy (maxReceiveCount=3,
  infra/local/provision.sh) is what moves it to validation-dlq after enough
  failed receives -- ack discipline, not a hand-rolled retry loop (ADR-0010).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import boto3

from claims_pipeline.events import ClaimEvent, validate

logger = logging.getLogger(__name__)

VALIDATION_QUEUE_NAME = "validation-q"
SCORING_QUEUE_NAME = "scoring-q"
VALIDATION_DLQ_NAME = "validation-dlq"


def process_message(sqs: Any, body: str, *, scoring_queue_url: str, dlq_url: str) -> None:
    """Decode and route one message. Raises on an undecodable body (poison);
    the caller (`run`) is responsible for ack discipline around that."""
    data = json.loads(body)
    claim = ClaimEvent.from_dict(data)
    ok, reason = validate(claim)
    if ok:
        sqs.send_message(QueueUrl=scoring_queue_url, MessageBody=json.dumps(claim.to_dict()))
    else:
        assert reason is not None
        sqs.send_message(
            QueueUrl=dlq_url,
            MessageBody=json.dumps({"claim": claim.to_dict(), "reason": reason}),
        )


def handle_message(
    sqs: Any, message: dict[str, Any], *, validation_url: str, scoring_url: str, dlq_url: str
) -> bool:
    """Apply ack discipline to one received message: process it, delete it on
    success, and leave it unacked (for SQS redelivery/redrive) on failure.

    Returns whether the message was acked (deleted). Split out from `run` so
    the ack decision -- the part of this session's non-negotiable that does
    not require a real queue -- can be unit tested with a fake `sqs` client.
    """
    try:
        process_message(sqs, message["Body"], scoring_queue_url=scoring_url, dlq_url=dlq_url)
    except Exception:
        logger.exception(
            "validation-q message failed processing; leaving unacked for "
            "redelivery/redrive (message_id=%s, receive_count=%s)",
            message["MessageId"],
            message.get("Attributes", {}).get("ApproximateReceiveCount"),
        )
        return False
    sqs.delete_message(QueueUrl=validation_url, ReceiptHandle=message["ReceiptHandle"])
    return True


def run(
    *,
    endpoint_url: str,
    region_name: str = "us-east-1",
    poll_seconds: int = 10,
    idle_polls_before_exit: int | None = None,
) -> None:
    """Long-poll validation-q and route each message until interrupted.

    `idle_polls_before_exit` bounds the loop to a fixed number of consecutive
    empty polls -- used by tests and one-shot local runs; left `None` (the
    default) it polls forever, as a deployed worker would.
    """
    sqs: Any = boto3.client("sqs", endpoint_url=endpoint_url, region_name=region_name)
    validation_url = sqs.get_queue_url(QueueName=VALIDATION_QUEUE_NAME)["QueueUrl"]
    scoring_url = sqs.get_queue_url(QueueName=SCORING_QUEUE_NAME)["QueueUrl"]
    dlq_url = sqs.get_queue_url(QueueName=VALIDATION_DLQ_NAME)["QueueUrl"]

    empty_polls = 0
    while idle_polls_before_exit is None or empty_polls < idle_polls_before_exit:
        response = sqs.receive_message(
            QueueUrl=validation_url,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=poll_seconds,
            AttributeNames=["ApproximateReceiveCount"],
        )
        messages = response.get("Messages", [])
        if not messages:
            empty_polls += 1
            continue
        empty_polls = 0
        for message in messages:
            handle_message(
                sqs,
                message,
                validation_url=validation_url,
                scoring_url=scoring_url,
                dlq_url=dlq_url,
            )
