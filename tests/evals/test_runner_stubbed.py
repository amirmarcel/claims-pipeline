"""Deterministic tests for the Tier 3 harness runner (docs/EVAL_PLAN.md):
aggregation, clustering, and baseline-regression logic, run against stubbed
explanation/judge clients -- no ANTHROPIC_API_KEY, no network. This is the
harness logic that must be testable without a live key per AGENTS.md; the
live judge itself is proven separately in test_meta_eval_live.py.
"""

from __future__ import annotations

import json
from typing import Any

from claims_pipeline.evals.runner import check_regression, run_eval_set
from tests.evals.stub_client import SequencedStubAnthropicClient

_CASES = [
    {
        "case": "faithful-case",
        "grounded_facts": {
            "provider_id": "P-001",
            "provider_score": 0.70,
            "cost_efficiency": 0.65,
            "quality": 0.75,
            "claim_count": 2,
            "rank": 2,
            "neighbor_above": {"provider_id": "P-002", "provider_score": 1.0},
            "neighbor_below": None,
        },
    },
    {
        "case": "unfaithful-number-case",
        "grounded_facts": {
            "provider_id": "P-050",
            "provider_score": 0.30,
            "cost_efficiency": 0.60,
            "quality": 0.0,
            "claim_count": 6,
            "rank": 9,
            "neighbor_above": {"provider_id": "P-049", "provider_score": 0.32},
            "neighbor_below": {"provider_id": "P-051", "provider_score": 0.28},
        },
    },
    {
        "case": "unfaithful-rank-case",
        "grounded_facts": {
            "provider_id": "P-021",
            "provider_score": 0.60,
            "cost_efficiency": 1.0,
            "quality": 0.20,
            "claim_count": 3,
            "rank": 12,
            "neighbor_above": {"provider_id": "P-020", "provider_score": 0.62},
            "neighbor_below": {"provider_id": "P-022", "provider_score": 0.58},
        },
    },
]

_EXPLANATION_STUB = SequencedStubAnthropicClient([("A plain-prose explanation.", "end_turn")])

_JUDGE_VERDICTS = [
    (json.dumps({"faithful": True, "score": 1.0, "violations": [], "reasoning": "ok"}), "end_turn"),
    (
        json.dumps(
            {
                "faithful": False,
                "score": 0.1,
                "violations": ["invents a number absent from grounded_facts"],
                "reasoning": "ungrounded number",
            }
        ),
        "end_turn",
    ),
    (
        json.dumps(
            {
                "faithful": False,
                "score": 0.0,
                "violations": ["contradicts the stated rank"],
                "reasoning": "rank contradiction",
            }
        ),
        "end_turn",
    ),
]


def test_run_eval_set_aggregates_and_clusters_failures() -> None:
    judge_stub = SequencedStubAnthropicClient(_JUDGE_VERDICTS)

    report = run_eval_set(_CASES, explanation_client=_EXPLANATION_STUB, judge_client=judge_stub)

    assert report.eval_set_size == 3
    assert report.faithful_count == 1
    assert report.unfaithful_count == 2
    # (1.0 + 0.1 + 0.0) / 3
    assert report.faithfulness_score == round((1.0 + 0.1 + 0.0) / 3, 4)
    assert report.failure_clusters == {"ungrounded_number": 1, "rank_contradiction": 1}


def test_run_eval_set_report_serializes_to_dict_and_markdown() -> None:
    judge_stub = SequencedStubAnthropicClient(_JUDGE_VERDICTS)
    report = run_eval_set(_CASES, explanation_client=_EXPLANATION_STUB, judge_client=judge_stub)

    payload = report.to_dict()
    assert payload["eval_set_size"] == 3
    assert len(payload["cases"]) == 3

    markdown = report.to_markdown()
    assert "faithful-case" in markdown
    assert "PASS" in markdown and "FAIL" in markdown


class _ExplosiveExplanationClient:
    """Stub that fails loudly if `.messages.create` is ever called -- used to
    prove a case with a pinned `explanation` skips live generation entirely
    (docs/adr/0012-*: near-miss judge-calibration cases control wording a
    live model can't reliably reproduce, so they must never hit this call).
    """

    class _Messages:
        def create(self, **kwargs: Any) -> Any:
            raise AssertionError(
                "generate_explanation must not be called for a case with a pinned explanation"
            )

    messages = _Messages()


def test_run_eval_set_uses_pinned_explanation_without_generating() -> None:
    case = {
        "case": "near-miss-fixture",
        "grounded_facts": _CASES[0]["grounded_facts"],
        "explanation": "A hand-written, pre-approved explanation string.",
    }
    verdict_json = json.dumps({"faithful": True, "score": 0.9, "violations": [], "reasoning": "ok"})
    judge_stub = SequencedStubAnthropicClient([(verdict_json, "end_turn")])

    report = run_eval_set(
        [case], explanation_client=_ExplosiveExplanationClient(), judge_client=judge_stub
    )

    assert report.cases[0].explanation == "A hand-written, pre-approved explanation string."
    assert report.cases[0].explanation_source == "fixed"
    assert "fixed" in report.to_markdown()


def test_check_regression_passes_within_threshold() -> None:
    baseline = {"faithfulness_score": 0.90}

    passed, message = check_regression(0.87, baseline, threshold=0.05)

    assert passed is True
    assert "0.87" in message


def test_check_regression_fails_below_floor() -> None:
    baseline = {"faithfulness_score": 0.90}

    passed, message = check_regression(0.80, baseline, threshold=0.05)

    assert passed is False
    assert "regressed" in message
