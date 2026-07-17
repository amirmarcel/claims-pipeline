"""CLI entrypoint: `python -m claims_pipeline.replay`.

Two modes against a single dead-letter queue:

    python -m claims_pipeline.replay --dlq validation-dlq --dry-run
    python -m claims_pipeline.replay --dlq validation-dlq --source-queue validation-q

`--dry-run` (the default) only inspects; `--source-queue` implies replay and
is required unless `--dry-run` is passed explicitly.
"""

from __future__ import annotations

import argparse
import os

import boto3

from .core import list_dead_letters, replay_dead_letters

DEFAULT_ENDPOINT_URL = "http://localhost:4566"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m claims_pipeline.replay",
        description="Inspect and replay dead-lettered claim events (SPEC.md §5).",
    )
    parser.add_argument("--dlq", required=True, help="DLQ name, e.g. validation-dlq")
    parser.add_argument(
        "--source-queue",
        help="source queue to replay onto, e.g. validation-q. Required unless --dry-run.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list DLQ contents with derived reasons; do not replay",
    )
    parser.add_argument("--limit", type=int, default=None, help="max messages to inspect/replay")
    parser.add_argument(
        "--claim-id",
        action="append",
        dest="claim_ids",
        default=None,
        help="replay only this claim_id (repeatable). Omit to replay everything selected.",
    )
    parser.add_argument(
        "--endpoint-url",
        default=os.environ.get("LOCALSTACK_ENDPOINT_URL", DEFAULT_ENDPOINT_URL),
        help="AWS endpoint (LocalStack by default)",
    )
    parser.add_argument("--region", default="us-east-1")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if not args.dry_run and not args.source_queue:
        build_arg_parser().error("--source-queue is required unless --dry-run is passed")

    sqs = boto3.client("sqs", endpoint_url=args.endpoint_url, region_name=args.region)
    dlq_url = sqs.get_queue_url(QueueName=args.dlq)["QueueUrl"]

    if args.dry_run:
        records = list_dead_letters(sqs, dlq_url, limit=args.limit)
        if not records:
            print(f"{args.dlq}: empty")
            return 0
        for record in records:
            claim_part = f" claim_id={record.claim_id}" if record.claim_id else ""
            print(
                f"[{record.kind}]{claim_part} "
                f"message_id={record.message_id} reason={record.reason}"
            )
        print(f"{len(records)} message(s) on {args.dlq}")
        return 0

    assert args.source_queue is not None
    source_url = sqs.get_queue_url(QueueName=args.source_queue)["QueueUrl"]
    claim_ids = set(args.claim_ids) if args.claim_ids else None
    replayed = replay_dead_letters(
        sqs,
        dlq_url=dlq_url,
        source_queue_url=source_url,
        limit=args.limit,
        claim_ids=claim_ids,
    )
    print(f"replayed {len(replayed)} message(s) from {args.dlq} to {args.source_queue}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
