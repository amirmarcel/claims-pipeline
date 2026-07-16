"""End-to-end smoke test against a real LocalStack instance.

Skipped automatically if LocalStack (or the claims.raw/validation.q
provisioning from infra/local/provision.sh) isn't reachable, so this isn't a
hard CI dependency yet -- see infra/local/README.md to run it locally:

    docker compose -f infra/local/docker-compose.yml up -d
    ./infra/local/provision.sh
    pytest tests/test_integration_smoke.py
"""

from __future__ import annotations

import json
import os

import boto3
import pytest
from botocore.exceptions import EndpointConnectionError

from claims_pipeline.generator.claims import generate_claims
from claims_pipeline.generator.config import GeneratorConfig
from claims_pipeline.generator.publisher import publish_claims

ENDPOINT_URL = os.environ.get("LOCALSTACK_ENDPOINT_URL", "http://localhost:4566")
REGION = "us-east-1"


def _validation_queue_url() -> str | None:
    sqs = boto3.client("sqs", endpoint_url=ENDPOINT_URL, region_name=REGION)
    try:
        return sqs.get_queue_url(QueueName="validation.q")["QueueUrl"]
    except (EndpointConnectionError, sqs.exceptions.QueueDoesNotExist):
        return None


@pytest.fixture
def queue_url() -> str:
    url = _validation_queue_url()
    if url is None:
        pytest.skip(
            "LocalStack not reachable / validation.q not provisioned; "
            "see infra/local/README.md"
        )
    return url


def test_published_claim_is_receivable_on_validation_queue(queue_url: str) -> None:
    config = GeneratorConfig(rate=1000.0, seed=99, count=1)
    [claim] = generate_claims(config)

    sent = publish_claims(
        [claim], rate=1000.0, endpoint_url=ENDPOINT_URL, region_name=REGION
    )
    assert sent == 1

    sqs = boto3.client("sqs", endpoint_url=ENDPOINT_URL, region_name=REGION)
    response = sqs.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=10,
        WaitTimeSeconds=5,
    )
    messages = response.get("Messages", [])
    received_claim_ids = [json.loads(m["Body"])["claim_id"] for m in messages]

    assert claim.claim_id in received_claim_ids
