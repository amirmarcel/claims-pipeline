"""Scoring worker (SPEC.md §2 step 4): scoring-q -> claim_scores / provider_scores.

A thin I/O shell around the pure scoring core (`claims_pipeline.scoring`) and
the persistence layer (`claims_pipeline.db.repository`): every message is
upserted by claim_id and the provider aggregate is fully recomputed from the
stored claims, never incremented (ADR-0007).

This worker does no business validation of its own -- valid-shaped claims
already passed the validation worker. So any failure here (an undecodable
body, or an unexpected error such as a transient Postgres outage) is a
poison/processing failure, not a business-invalid one, and it all goes
through the same ack discipline: `run` leaves a failed message unacked so
SQS's own redrive policy (maxReceiveCount=3, scoring-dlq,
infra/local/provision.sh) redrives it after enough failed receives. There is
no in-process retry loop -- the SQS visibility timeout is the only backoff,
including for transient downstream errors (ADR-0010).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import boto3
import psycopg

from claims_pipeline.db import repository
from claims_pipeline.events import ClaimEvent

logger = logging.getLogger(__name__)

SCORING_QUEUE_NAME = "scoring-q"


def process_message(conn: psycopg.Connection[Any], body: str) -> None:
    data = json.loads(body)
    claim = ClaimEvent.from_dict(data)
    repository.upsert_claim_and_recompute(conn, claim)


def handle_message(
    sqs: Any, conn: psycopg.Connection[Any], message: dict[str, Any], *, scoring_url: str
) -> bool:
    """Apply ack discipline to one received message: process it, delete it on
    success, and leave it unacked (for SQS redelivery/redrive) on failure.

    Returns whether the message was acked (deleted). Split out from `run` so
    the ack decision can be unit tested with a fake `sqs` client and a real
    (or fake) connection, without a live queue.
    """
    try:
        process_message(conn, message["Body"])
    except Exception:
        conn.rollback()
        logger.exception(
            "scoring-q message failed processing; leaving unacked for "
            "redelivery/redrive (message_id=%s, receive_count=%s)",
            message["MessageId"],
            message.get("Attributes", {}).get("ApproximateReceiveCount"),
        )
        return False
    sqs.delete_message(QueueUrl=scoring_url, ReceiptHandle=message["ReceiptHandle"])
    return True


def run(
    *,
    endpoint_url: str,
    region_name: str = "us-east-1",
    dsn: str | None = None,
    poll_seconds: int = 10,
    idle_polls_before_exit: int | None = None,
) -> None:
    """Long-poll scoring-q and score each message until interrupted.

    `idle_polls_before_exit` bounds the loop to a fixed number of consecutive
    empty polls -- used by tests and one-shot local runs; left `None` (the
    default) it polls forever, as a deployed worker would.
    """
    sqs: Any = boto3.client("sqs", endpoint_url=endpoint_url, region_name=region_name)
    scoring_url = sqs.get_queue_url(QueueName=SCORING_QUEUE_NAME)["QueueUrl"]

    with repository.connect(dsn) as conn:
        empty_polls = 0
        while idle_polls_before_exit is None or empty_polls < idle_polls_before_exit:
            response = sqs.receive_message(
                QueueUrl=scoring_url,
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
                handle_message(sqs, conn, message, scoring_url=scoring_url)
