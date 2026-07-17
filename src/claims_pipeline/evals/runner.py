"""Tier 3 faithfulness eval harness runner (docs/EVAL_PLAN.md).

Deterministic harness logic (loading the eval set, orchestrating one
explanation + one judgment per case, aggregating, clustering, and writing
reports) is separated from the model calls it drives, exactly like
`claims_pipeline.explanation.client` / `claims_pipeline.evals.judge`: both
model clients are injected parameters, so `run_eval_set` itself is testable
with stub clients and no network access (tests/evals/test_runner.py). The
CLI entrypoint (`main`, `python -m claims_pipeline.evals`) is the only place
that decides whether a live key is available and wires the real clients.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from claims_pipeline.evals.judge import (
    FaithfulnessVerdict,
    JudgeAnthropicClientLike,
    judge_faithfulness,
)
from claims_pipeline.explanation.client import AnthropicClientLike, generate_explanation

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_EVAL_SET = REPO_ROOT / "benchmark" / "faithfulness.eval.jsonl"
DEFAULT_REPORT_PATH = REPO_ROOT / "benchmark" / "reports" / "faithfulness_report.json"
DEFAULT_BASELINE_PATH = REPO_ROOT / "benchmark" / "reports" / "faithfulness_baseline.json"

# Coarse, keyword-based clustering of the judge's freeform `violations`
# strings into the three failure causes the meta-eval fixtures exercise
# (tests/fixtures/faithfulness_meta_eval.json) -- this is what turns a flat
# pass/fail list into "cluster failures by cause" (docs/EVAL_PLAN.md, the
# improvement loop).
_CLUSTER_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ungrounded_number", re.compile(r"number|invent|estimat|round", re.IGNORECASE)),
    ("unsupported_fact", re.compile(r"reason|cause|unsupported|fact", re.IGNORECASE)),
    ("rank_contradiction", re.compile(r"rank|order|neighbor", re.IGNORECASE)),
]


def _cluster(violation: str) -> str:
    for label, pattern in _CLUSTER_PATTERNS:
        if pattern.search(violation):
            return label
    return "other"


@dataclass(frozen=True, slots=True)
class CaseResult:
    case: str
    grounded_facts: dict[str, Any]
    explanation: str
    explanation_source: str
    verdict: FaithfulnessVerdict
    clusters: list[str]


@dataclass(frozen=True, slots=True)
class EvalReport:
    generated_at: str
    eval_set_size: int
    faithfulness_score: float
    faithful_count: int
    unfaithful_count: int
    failure_clusters: dict[str, int]
    cases: list[CaseResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "eval_set_size": self.eval_set_size,
            "faithfulness_score": self.faithfulness_score,
            "faithful_count": self.faithful_count,
            "unfaithful_count": self.unfaithful_count,
            "failure_clusters": self.failure_clusters,
            "cases": [
                {
                    "case": c.case,
                    "grounded_facts": c.grounded_facts,
                    "explanation": c.explanation,
                    "explanation_source": c.explanation_source,
                    "verdict": asdict(c.verdict),
                    "clusters": c.clusters,
                }
                for c in self.cases
            ],
        }

    def to_markdown(self) -> str:
        lines = [
            "# Tier 3 faithfulness eval report",
            "",
            f"Generated: {self.generated_at}",
            f"Cases: {self.eval_set_size}",
            f"Aggregate faithfulness score: {self.faithfulness_score:.4f}",
            f"Faithful: {self.faithful_count} / {self.eval_set_size}",
            "",
        ]
        if self.failure_clusters:
            lines.append("## Failure clusters")
            for label, count in sorted(self.failure_clusters.items(), key=lambda kv: -kv[1]):
                lines.append(f"- {label}: {count}")
            lines.append("")
        lines.append("## Per-case results")
        for c in self.cases:
            status = "PASS" if c.verdict.faithful else "FAIL"
            source = f", {c.explanation_source}" if c.explanation_source == "fixed" else ""
            lines.append(f"### [{status}] {c.case} (score={c.verdict.score:.2f}{source})")
            lines.append(f"- explanation: {c.explanation!r}")
            if c.verdict.violations:
                lines.append(f"- violations: {c.verdict.violations}")
            lines.append(f"- reasoning: {c.verdict.reasoning}")
            lines.append("")
        return "\n".join(lines)


def load_eval_set(path: Path) -> list[dict[str, Any]]:
    cases = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def run_eval_set(
    cases: list[dict[str, Any]],
    *,
    explanation_client: AnthropicClientLike | None,
    judge_client: JudgeAnthropicClientLike | None,
) -> EvalReport:
    """Judge one explanation per case, then aggregate. For most cases the
    explanation is generated live via the confined explanation model
    (`explanation_client`); a case may instead pin an `explanation` string
    directly in the eval set, which skips generation entirely. Pinning is
    for judge-calibration cases where the point is to control specific
    wording (defensible rounding, terse phrasing, an implicit-but-derivable
    comparison) that live generation can't reliably reproduce -- see the
    near-miss cases in benchmark/faithfulness.eval.jsonl and docs/adr/0012-*.
    Both clients are injectable so this function runs, deterministically,
    against stubs in tests (no network, no key) as well as live clients.
    """
    results: list[CaseResult] = []
    for case in cases:
        facts = case["grounded_facts"]
        pinned_explanation = case.get("explanation")
        if pinned_explanation is not None:
            explanation = pinned_explanation
            explanation_source = "fixed"
        else:
            explanation = generate_explanation(facts, client=explanation_client)
            explanation_source = "generated"
        verdict = judge_faithfulness(facts, explanation, client=judge_client)
        clusters = [_cluster(v) for v in verdict.violations] if not verdict.faithful else []
        results.append(
            CaseResult(
                case=case["case"],
                grounded_facts=facts,
                explanation=explanation,
                explanation_source=explanation_source,
                verdict=verdict,
                clusters=clusters,
            )
        )

    faithful_count = sum(1 for r in results if r.verdict.faithful)
    failure_clusters: dict[str, int] = {}
    for r in results:
        for cluster in r.clusters:
            failure_clusters[cluster] = failure_clusters.get(cluster, 0) + 1

    aggregate_score = sum(r.verdict.score for r in results) / len(results) if results else 0.0

    return EvalReport(
        generated_at=datetime.now(UTC).isoformat(),
        eval_set_size=len(results),
        faithfulness_score=round(aggregate_score, 4),
        faithful_count=faithful_count,
        unfaithful_count=len(results) - faithful_count,
        failure_clusters=failure_clusters,
        cases=results,
    )


def load_baseline(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return cast(dict[str, Any], json.loads(path.read_text()))


def check_regression(
    score: float, baseline: dict[str, Any], threshold: float
) -> tuple[bool, str]:
    """Returns (passed, message). Tier 3 gates only on regression below the
    committed baseline minus a tolerance threshold (docs/EVAL_PLAN.md) --
    unlike Tier 1/2, a single low-scoring case is not itself a failure.
    """
    baseline_score = float(baseline["faithfulness_score"])
    floor = baseline_score - threshold
    if score < floor:
        return False, (
            f"faithfulness score {score:.4f} regressed below baseline {baseline_score:.4f} "
            f"- threshold {threshold:.4f} = floor {floor:.4f}"
        )
    return True, (
        f"faithfulness score {score:.4f} >= floor {floor:.4f} (baseline {baseline_score:.4f})"
    )


def _api_key_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Tier 3 faithfulness eval harness.")
    parser.add_argument("--eval-set", type=Path, default=DEFAULT_EVAL_SET)
    parser.add_argument("--report-out", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE_PATH)
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.05,
        help="max allowed drop below the committed baseline before this is a regression",
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="record this run's score as the new committed baseline instead of checking it",
    )
    parser.add_argument(
        "--check-baseline",
        action="store_true",
        help="exit non-zero if this run's score regresses below the committed baseline",
    )
    args = parser.parse_args(argv)

    if not _api_key_available():
        print("ANTHROPIC_API_KEY not set; skipping Tier 3 faithfulness eval run.")
        return 0

    cases = load_eval_set(args.eval_set)
    report = run_eval_set(cases, explanation_client=None, judge_client=None)

    args.report_out.parent.mkdir(parents=True, exist_ok=True)
    args.report_out.write_text(json.dumps(report.to_dict(), indent=2) + "\n")
    print(report.to_markdown())

    if args.write_baseline:
        baseline = {
            "faithfulness_score": report.faithfulness_score,
            "threshold": args.threshold,
            "recorded_at": report.generated_at,
            "eval_set_size": report.eval_set_size,
            "judge_model": "claude-opus-4-8",
        }
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(json.dumps(baseline, indent=2) + "\n")
        print(f"Wrote new baseline: {baseline}")
        return 0

    if args.check_baseline:
        committed_baseline = load_baseline(args.baseline)
        if committed_baseline is None:
            print(f"No baseline found at {args.baseline}; cannot check regression.")
            return 1
        threshold = float(committed_baseline.get("threshold", args.threshold))
        passed, message = check_regression(report.faithfulness_score, committed_baseline, threshold)
        print(message)
        return 0 if passed else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
