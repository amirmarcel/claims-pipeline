"""Deterministic tests for the Tier 3 judge (docs/EVAL_PLAN.md), run against
recorded/stubbed responses -- no ANTHROPIC_API_KEY, no network. These pin the
judge's response-parsing contract (JSON verdict shape in, FaithfulnessVerdict
out); they do not prove the judge model itself catches bad explanations --
that is what tests/evals/test_meta_eval_live.py exists for.
"""

from __future__ import annotations

import json

import pytest

from claims_pipeline.evals.judge import judge_faithfulness
from tests.explanation.stub_client import StubAnthropicClient

_FACTS = {
    "provider_id": "P-001",
    "provider_score": 0.70,
    "cost_efficiency": 0.65,
    "quality": 0.75,
    "claim_count": 2,
    "rank": 2,
    "neighbor_above": {"provider_id": "P-002", "provider_score": 1.0},
    "neighbor_below": None,
}


def test_judge_parses_a_faithful_verdict() -> None:
    stub = StubAnthropicClient(
        json.dumps(
            {
                "faithful": True,
                "score": 1.0,
                "violations": [],
                "reasoning": "Every number matches grounded_facts.",
            }
        )
    )

    verdict = judge_faithfulness(_FACTS, "P-001 ranks 2nd with a score of 0.70.", client=stub)

    assert verdict.faithful is True
    assert verdict.score == 1.0
    assert verdict.violations == []


def test_judge_parses_an_unfaithful_verdict() -> None:
    stub = StubAnthropicClient(
        json.dumps(
            {
                "faithful": False,
                "score": 0.2,
                "violations": ["cites a 92% satisfaction figure absent from grounded_facts"],
                "reasoning": "Introduces an ungrounded number.",
            }
        )
    )

    verdict = judge_faithfulness(_FACTS, "P-001 has 92% satisfaction.", client=stub)

    assert verdict.faithful is False
    assert verdict.violations != []


def test_judge_rejects_non_json_response() -> None:
    stub = StubAnthropicClient("Sure, this explanation looks faithful to me!")

    with pytest.raises(ValueError, match="not valid JSON"):
        judge_faithfulness(_FACTS, "some explanation", client=stub)


def test_judge_rejects_response_missing_required_fields() -> None:
    stub = StubAnthropicClient(json.dumps({"faithful": True}))

    with pytest.raises(ValueError, match="expected verdict shape"):
        judge_faithfulness(_FACTS, "some explanation", client=stub)


def test_judge_treats_truncated_response_as_a_failure() -> None:
    stub = StubAnthropicClient('{"faithful": true, "score"', stop_reason="max_tokens")

    with pytest.raises(ValueError, match="truncated"):
        judge_faithfulness(_FACTS, "some explanation", client=stub)
