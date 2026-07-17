"""Meta-eval: proves the Tier 3 judge actually has teeth (docs/EVAL_PLAN.md,
docs/adr/0012-*). A faithfulness judge that scores everything faithful is
worthless -- this asserts the REAL judge model correctly flags deliberately
unfaithful (grounded_facts, explanation) pairs as unfaithful. This is the
eval-harness equivalent of the concurrency test in the reliability suite: it
proves the mechanism works, not just that it runs.

Gated behind ANTHROPIC_API_KEY and skips cleanly when absent, same pattern
as tests/explanation/test_guardrails_live.py. Not required for merge --
Tier 3 reports and gates only on regression, never blocks unconditionally
(AGENTS.md, docs/EVAL_PLAN.md).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from claims_pipeline.evals.judge import judge_faithfulness

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "faithfulness_meta_eval.json"


def _api_key_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


@pytest.fixture
def require_api_key() -> None:
    if not _api_key_available():
        pytest.skip("ANTHROPIC_API_KEY not set; skipping live judge meta-eval")


def _load_cases() -> list[dict[str, object]]:
    payload = json.loads(FIXTURE_PATH.read_text())
    return list(payload["cases"])


@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["case"])
def test_live_judge_catches_known_bad_explanation(
    require_api_key: None, case: dict[str, object]
) -> None:
    facts = case["grounded_facts"]
    explanation = case["explanation"]
    assert isinstance(facts, dict)
    assert isinstance(explanation, str)

    verdict = judge_faithfulness(facts, explanation)

    assert verdict.faithful is False, (
        f"judge failed to catch a known-bad explanation for case {case['case']!r} "
        f"(violation type: {case['violation']!r}): verdict={verdict!r}"
    )
    assert verdict.violations, "an unfaithful verdict must list at least one violation"
    assert verdict.score < 1.0
