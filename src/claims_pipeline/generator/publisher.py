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

from claims_pipeline.events import ClaimEvent

DEFAULT_TOPIC_NAME = "claims-raw"


def publish_claims(
    claims: Iterable[ClaimEvent],
    *,
    rate: float,
    topic_name: str = DEFAULT_TOPIC_NAME,
    endpoint_url: str,
    region_name: str = "us-east-1",
) -> int:
    """Publish claims to the SNS topic at `rate` events/sec. Returns the count sent."""
    sns: Any = boto3.client("sns", endpoint_url=endpoint_url, region_name=region_name)
    # create_topic is idempotent: returns the existing topic's ARN if the
    # topic (provisioned by infra/local/provision.sh) already exists.
    topic_arn = sns.create_topic(Name=topic_name)["TopicArn"]

    interval = 1.0 / rate
    sent = 0
    for claim in claims:
        sns.publish(TopicArn=topic_arn, Message=json.dumps(claim.to_dict()))
        sent += 1
        time.sleep(interval)
    return sent
