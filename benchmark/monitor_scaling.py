"""Session 7 load-test observer.

Samples queue depth (SQS), worker replica counts (kubectl), and processed
claim count (Postgres) at a fixed interval while a load-test generator run
is in flight and until the queues drain, so the KEDA autoscaling story --
burst -> scale-up -> drain -> scale-down -- is captured from a real run
rather than asserted. Writes one CSV row per sample to stdout-redirected
output; `benchmark/plot_scaling.py` turns the CSV into the committed graphs.

Not part of the application (`src/claims_pipeline/`) -- a benchmark-only
tool, like `provision.sh`, that talks to the same LocalStack/Postgres/
kubectl surfaces a human running the load test would use by hand.
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import time

import boto3
import psycopg

QUEUES = ("validation-q", "scoring-q")
DEPLOYMENTS = ("validation-worker", "scoring-worker")


def _queue_depth(sqs: object, queue_url: str) -> int:
    attrs = sqs.get_queue_attributes(  # type: ignore[attr-defined]
        QueueUrl=queue_url, AttributeNames=["ApproximateNumberOfMessages"]
    )
    return int(attrs["Attributes"]["ApproximateNumberOfMessages"])


def _replica_count(deployment: str) -> int:
    out = subprocess.run(
        [
            "kubectl",
            "-n",
            "claims-pipeline",
            "get",
            "deployment",
            deployment,
            "-o",
            "jsonpath={.status.readyReplicas}",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return int(out) if out else 0


def _processed_count(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute("select count(*) from claim_scores")
        row = cur.fetchone()
        assert row is not None
        return int(row[0])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint-url", default="http://localhost:4566")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument(
        "--dsn", default=None, help="Postgres DSN (default: repository.default_dsn())"
    )
    parser.add_argument("--interval", type=float, default=2.0, help="seconds between samples")
    parser.add_argument(
        "--drain-idle-samples",
        type=int,
        default=10,
        help="stop after this many consecutive samples with both queues empty",
    )
    parser.add_argument("--max-samples", type=int, default=600)
    parser.add_argument("--out", required=True, help="CSV output path")
    args = parser.parse_args(argv)

    sqs = boto3.client("sqs", endpoint_url=args.endpoint_url, region_name=args.region)
    queue_urls = {q: sqs.get_queue_url(QueueName=q)["QueueUrl"] for q in QUEUES}

    from claims_pipeline.db import repository

    conn = repository.connect(args.dsn)

    start = time.monotonic()
    idle_streak = 0
    last_processed = _processed_count(conn)

    with open(args.out, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "elapsed_s",
                "validation_q_depth",
                "scoring_q_depth",
                "validation_worker_replicas",
                "scoring_worker_replicas",
                "claims_processed_total",
                "throughput_claims_per_s",
            ]
        )
        for sample in range(args.max_samples):
            elapsed = time.monotonic() - start
            depths = {q: _queue_depth(sqs, url) for q, url in queue_urls.items()}
            replicas = {d: _replica_count(d) for d in DEPLOYMENTS}
            processed = _processed_count(conn)
            throughput = (processed - last_processed) / args.interval
            last_processed = processed

            row = [
                round(elapsed, 1),
                depths["validation-q"],
                depths["scoring-q"],
                replicas["validation-worker"],
                replicas["scoring-worker"],
                processed,
                round(throughput, 2),
            ]
            writer.writerow(row)
            f.flush()
            print(",".join(str(v) for v in row), file=sys.stderr)

            if depths["validation-q"] == 0 and depths["scoring-q"] == 0:
                idle_streak += 1
                if idle_streak >= args.drain_idle_samples and sample > args.drain_idle_samples:
                    break
            else:
                idle_streak = 0
            time.sleep(args.interval)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
