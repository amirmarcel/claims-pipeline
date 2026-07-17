"""CLI entrypoint: `python -m claims_pipeline.generator`."""

from __future__ import annotations

import argparse
import os

from .claims import generate_claims
from .config import FAILURE_INJECTION_MODES, GeneratorConfig
from .publisher import DEFAULT_TOPIC_NAME, publish_claims

DEFAULT_ENDPOINT_URL = "http://localhost:4566"


def _parse_failure_injection(raw: list[str] | None) -> dict[str, float] | None:
    """Parse repeated `--failure-injection mode=fraction` args."""
    if not raw:
        return None
    result: dict[str, float] = {}
    for item in raw:
        mode, _, fraction_str = item.partition("=")
        if not fraction_str:
            raise argparse.ArgumentTypeError(
                f"--failure-injection must be mode=fraction, got {item!r}"
            )
        if mode not in FAILURE_INJECTION_MODES:
            raise argparse.ArgumentTypeError(
                f"unknown failure_injection mode {mode!r}; choose from {FAILURE_INJECTION_MODES}"
            )
        try:
            result[mode] = float(fraction_str)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"invalid fraction in {item!r}") from exc
    return result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m claims_pipeline.generator",
        description="Publish deterministic synthetic claim events to the claims-raw SNS topic.",
    )
    parser.add_argument("--rate", type=float, required=True, help="events per second")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--count", type=int, help="number of events to publish")
    group.add_argument("--duration", type=float, help="seconds to publish for")
    parser.add_argument("--seed", type=int, required=True, help="RNG seed, for reproducibility")
    parser.add_argument(
        "--provider-distribution",
        default="uniform",
        choices=["uniform"],
        help="how claims spread across providers (only 'uniform' in v1)",
    )
    parser.add_argument("--provider-pool-size", type=int, default=20)
    parser.add_argument(
        "--failure-injection",
        action="append",
        dest="failure_injection",
        metavar="MODE=FRACTION",
        help=(
            "inject a fraction of events as a failure mode (SPEC.md §6); "
            f"repeatable, modes: {', '.join(FAILURE_INJECTION_MODES)}. "
            "e.g. --failure-injection malformed=0.05"
        ),
    )
    parser.add_argument("--topic", default=DEFAULT_TOPIC_NAME, help="SNS topic name")
    parser.add_argument(
        "--endpoint-url",
        default=os.environ.get("LOCALSTACK_ENDPOINT_URL", DEFAULT_ENDPOINT_URL),
        help="AWS endpoint (LocalStack by default)",
    )
    parser.add_argument("--region", default="us-east-1")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    config = GeneratorConfig(
        rate=args.rate,
        seed=args.seed,
        count=args.count,
        duration=args.duration,
        provider_distribution=args.provider_distribution,
        provider_pool_size=args.provider_pool_size,
        failure_injection=_parse_failure_injection(args.failure_injection),
    )
    claims = generate_claims(config)
    sent = publish_claims(
        claims,
        rate=args.rate,
        topic_name=args.topic,
        endpoint_url=args.endpoint_url,
        region_name=args.region,
    )
    print(f"published {sent} claim events to {args.topic}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
