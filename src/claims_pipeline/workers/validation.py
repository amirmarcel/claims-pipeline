"""Validation worker (SPEC.md §2 step 3): validation-q -> scoring-q | validation-dlq.

A thin I/O shell around the pure `events.validate` core (AGENTS.md non-negotiable
#1 / ADR-0003): this module only decodes messages and routes them, it makes no
scoring decisions. Idempotent on claim_id: forwarding (or dead-lettering) the
same claim twice is a safe no-op downstream, because the scoring worker
upserts by claim_id (ADR-0007).

Poison (unparseable) messages are NOT handled here. Bounded-receive-count
redrive to validation-dlq and the replay utility are Session 3 (SPEC.md §5);
a message that fails to decode raises and relies on SQS's own visibility
timeout for redelivery for now.
"""

from __future__ import annotations

import json
from typing import Any

import boto3

from claims_pipeline.events import ClaimEvent, validate

VALIDATION_QUEUE_NAME = "validation-q"
SCORING_QUEUE_NAME = "scoring-q"
VALIDATION_DLQ_NAME = "validation-dlq"


def process_message(sqs: Any, body: str, *, scoring_queue_url: str, dlq_url: str) -> None:
    # TODO(session-3): unparseable bodies should be redriven to
    # validation-dlq as poison messages after a bounded receive count,
    # rather than raising. See SPEC.md §5.
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
        )
        messages = response.get("Messages", [])
        if not messages:
            empty_polls += 1
            continue
        empty_polls = 0
        for message in messages:
            process_message(sqs, message["Body"], scoring_queue_url=scoring_url, dlq_url=dlq_url)
            sqs.delete_message(QueueUrl=validation_url, ReceiptHandle=message["ReceiptHandle"])
