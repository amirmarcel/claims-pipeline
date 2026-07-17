"""Session 7 observability artifact: a real distributed trace of one claim
flowing SNS -> SQS(validation-q) -> validation -> SQS(scoring-q) -> scoring
-> Postgres, exported via OTLP/HTTP to a local Jaeger for a legible waterfall
screenshot (docs/images/).

**Scope, flagged deliberately narrow.** Full production tracing would thread
W3C trace-context propagation through every `MessageAttributes` hop inside
`src/claims_pipeline/workers/*.py` and `generator/publisher.py`, plus
auto-instrument boto3/psycopg -- a change large enough to touch most of the
worker test suite's mocked-`sqs` call signatures for a Session whose budget
is mostly the KEDA/load-test work. Instead, this script drives the exact
same application logic those modules use (`events.validate`,
`repository.upsert_claim_and_recompute`) for a single generated claim,
manually wrapped in OTel spans with context injected into real SQS
`MessageAttributes` at each hop -- a genuine trace of real code, not a
fabrication, without permanently changing the worker/publisher production
code paths or their tests. Production instrumentation is future work if this
proves valuable, not shipped speculatively (AGENTS.md: don't build beyond
what's needed).

Run once, with Jaeger up (`docker run -d -p 16686:16686 -p 4318:4318
jaegertracing/all-in-one:latest`) and the local rig provisioned:

    python benchmark/trace_one_claim.py

Then open http://localhost:16686, find the `claims-pipeline` service, and
open the one trace -- that's the waterfall to screenshot.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import boto3
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import extract, inject
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from claims_pipeline.db import repository
from claims_pipeline.events import validate
from claims_pipeline.generator.claims import generate_claims
from claims_pipeline.generator.config import GeneratorConfig

VALIDATION_QUEUE_NAME = "validation-q"
SCORING_QUEUE_NAME = "scoring-q"


def _configure_tracer(otlp_endpoint: str) -> trace.Tracer:
    provider = TracerProvider(resource=Resource.create({"service.name": "claims-pipeline"}))
    exporter = OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces")
    # SimpleSpanProcessor: this script emits exactly one trace and exits --
    # a BatchSpanProcessor's export delay could lose spans on process exit.
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return trace.get_tracer("claims_pipeline.trace_demo")


def _inject_message_attributes() -> dict[str, Any]:
    carrier: dict[str, str] = {}
    inject(carrier)
    return {k: {"DataType": "String", "StringValue": v} for k, v in carrier.items()}


def _extract_context(message_attributes: dict[str, Any] | None) -> Any:
    carrier = {
        k: v["StringValue"] for k, v in (message_attributes or {}).items() if "StringValue" in v
    }
    return extract(carrier)


def main() -> int:
    endpoint_url = os.environ.get("LOCALSTACK_ENDPOINT_URL", "http://localhost:4566")
    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    region = "us-east-1"

    tracer = _configure_tracer(otlp_endpoint)
    sns: Any = boto3.client("sns", endpoint_url=endpoint_url, region_name=region)
    sqs: Any = boto3.client("sqs", endpoint_url=endpoint_url, region_name=region)

    topic_arn = sns.create_topic(Name="claims-raw")["TopicArn"]
    validation_url = sqs.get_queue_url(QueueName=VALIDATION_QUEUE_NAME)["QueueUrl"]
    scoring_url = sqs.get_queue_url(QueueName=SCORING_QUEUE_NAME)["QueueUrl"]

    (claim,) = generate_claims(GeneratorConfig(rate=1.0, seed=7, count=1))

    with tracer.start_as_current_span("generator.publish_claim") as span:
        span.set_attribute("claim.id", claim.claim_id)
        sns.publish(
            TopicArn=topic_arn,
            Message=json.dumps(claim.to_dict()),
            MessageAttributes=_inject_message_attributes(),
        )

    print(f"published {claim.claim_id}, waiting for validation-q delivery...")
    message = None
    for _ in range(15):
        resp = sqs.receive_message(
            QueueUrl=validation_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=5,
            MessageAttributeNames=["All"],
        )
        messages = resp.get("Messages", [])
        if messages:
            message = messages[0]
            break
    assert message is not None, "claim did not arrive on validation-q in time"

    ctx = _extract_context(message.get("MessageAttributes"))
    with tracer.start_as_current_span("validation_worker.process_message", context=ctx) as span:
        body = json.loads(message["Body"])
        ok, reason = validate(claim)
        span.set_attribute("claim.valid", ok)
        if reason:
            span.set_attribute("claim.invalid_reason", reason)
        sqs.send_message(
            QueueUrl=scoring_url,
            MessageBody=json.dumps(body),
            MessageAttributes=_inject_message_attributes(),
        )
    sqs.delete_message(QueueUrl=validation_url, ReceiptHandle=message["ReceiptHandle"])

    print("forwarded to scoring-q, waiting for delivery...")
    message = None
    for _ in range(15):
        resp = sqs.receive_message(
            QueueUrl=scoring_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=5,
            MessageAttributeNames=["All"],
        )
        messages = resp.get("Messages", [])
        if messages:
            message = messages[0]
            break
    assert message is not None, "claim did not arrive on scoring-q in time"

    ctx = _extract_context(message.get("MessageAttributes"))
    with tracer.start_as_current_span("scoring_worker.process_message", context=ctx) as span:
        span.set_attribute("claim.id", claim.claim_id)
        with tracer.start_as_current_span("db.upsert_claim_and_recompute"):
            with repository.connect() as conn:
                repository.upsert_claim_and_recompute(conn, claim)
    sqs.delete_message(QueueUrl=scoring_url, ReceiptHandle=message["ReceiptHandle"])

    # Give the exporter's HTTP POST a moment before the process exits.
    time.sleep(1)
    print(f"trace complete for {claim.claim_id} -- view at http://localhost:16686")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
