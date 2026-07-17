"""SNS publishing for the load generator.

Talks to AWS through the standard boto3 client pointed at an endpoint
(ADR-0008): the only difference between LocalStack and real AWS is the
`endpoint_url`, not the code path.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from typing import Any

import boto3

from claims_pipeline.generator.claims import GeneratedEvent, MalformedEvent
from claims_pipeline.generator.config import BurstConfig

DEFAULT_TOPIC_NAME = "claims-raw"


def _wire_body(event: GeneratedEvent) -> str:
    """A `MalformedEvent`'s `raw_body` is published verbatim -- it is
    deliberately not valid JSON (SPEC.md §6 failure_injection: malformed).
    Everything else is a well-formed `ClaimEvent`, serialized as usual."""
    if isinstance(event, MalformedEvent):
        return event.raw_body
    return json.dumps(event.to_dict())


def _interval_for_index(index: int, *, rate: float, burst: BurstConfig | None) -> float:
    """Seconds to sleep after publishing the event at `index` (0-based).

    Before the burst's step change, pace at `rate`; from the step change
    onward, at `burst.rate`. The cutover index is where `rate` events/sec
    would have reached `burst.offset` seconds -- consistent with
    `GeneratorConfig.event_count`'s accounting for the same schedule.
    """
    if burst is None:
        return 1.0 / rate
    cutover_index = round(rate * burst.offset)
    return 1.0 / rate if index < cutover_index else 1.0 / burst.rate


def publish_claims(
    claims: Iterable[GeneratedEvent],
    *,
    rate: float,
    burst: BurstConfig | None = None,
    topic_name: str = DEFAULT_TOPIC_NAME,
    endpoint_url: str,
    region_name: str = "us-east-1",
) -> int:
    """Publish claims (or failure-injected events) to the SNS topic at `rate`
    events/sec, stepping to `burst.rate` at `burst.offset` seconds in if a
    burst is configured. Returns the count sent."""
    sns: Any = boto3.client("sns", endpoint_url=endpoint_url, region_name=region_name)
    # create_topic is idempotent: returns the existing topic's ARN if the
    # topic (provisioned by infra/local/provision.sh) already exists.
    topic_arn = sns.create_topic(Name=topic_name)["TopicArn"]

    sent = 0
    for index, claim in enumerate(claims):
        sns.publish(TopicArn=topic_arn, Message=_wire_body(claim))
        sent += 1
        time.sleep(_interval_for_index(index, rate=rate, burst=burst))
    return sent
