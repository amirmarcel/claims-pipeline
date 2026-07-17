"""Live smoke test for the Tier 3 harness runner (docs/EVAL_PLAN.md): proves
`run_eval_set` is wired correctly end-to-end against the real explanation
model and the real judge model, not just against stubs. Gated behind
ANTHROPIC_API_KEY and skips cleanly when absent, same pattern as
tests/explanation/test_guardrails_live.py. Deliberately runs on a small
subset of the full eval set to keep this a cheap sanity check -- the full
eval set is run by the CLI (`python -m claims_pipeline.evals`), not by this
test.
"""

from __future__ import annotations

import os

import pytest

from claims_pipeline.evals.runner import DEFAULT_EVAL_SET, load_eval_set, run_eval_set


def _api_key_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


@pytest.fixture
def require_api_key() -> None:
    if not _api_key_available():
        pytest.skip("ANTHROPIC_API_KEY not set; skipping live runner smoke test")


def test_live_run_eval_set_on_a_small_subset(require_api_key: None) -> None:
    cases = load_eval_set(DEFAULT_EVAL_SET)[:2]

    report = run_eval_set(cases, explanation_client=None, judge_client=None)

    assert report.eval_set_size == 2
    for case_result in report.cases:
        assert case_result.explanation.strip() != ""
        assert 0.0 <= case_result.verdict.score <= 1.0
