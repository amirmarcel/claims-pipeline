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


def test_judge_does_not_raise_when_reasoning_field_is_missing() -> None:
    """Regression for the production parser bug (docs/adr/0012-*): a REAL
    judge response of faithful=false with a score and violations but no
    top-level 'reasoning' key previously raised "judge response did not
    match the expected verdict shape" -- crashing exactly when the judge
    correctly caught unfaithfulness. reasoning is explanatory, not part of
    the verdict signal, so a missing key must default, never raise.
    """
    stub = StubAnthropicClient(
        json.dumps(
            {
                "faithful": False,
                "score": 0.4,
                "violations": ["Unsupported causal claim: '...' is not present in grounded_facts"],
            }
        )
    )

    verdict = judge_faithfulness(_FACTS, "some explanation", client=stub)

    assert verdict.faithful is False
    assert verdict.score == 0.4
    assert verdict.violations != []
    assert verdict.reasoning == ""


def test_judge_parses_rich_unfaithful_verdict_with_nested_quotes_in_violations() -> None:
    """The exact shape that broke the old free-text parser: a long
    'violations' entry containing nested single quotes/apostrophes, plus a
    'reasoning' string containing an escaped double quote. Regular JSON
    string escaping handles this fine -- the old bug was never actually the
    quote content, it was the missing 'reasoning' key covered above -- but
    this pins that rich, quote-heavy verdicts parse correctly end to end.
    """
    payload = {
        "faithful": False,
        "score": 0.4,
        "violations": [
            "Unsupported causal claim: 'the provider's low score stems from a "
            "documented billing dispute' is not present in grounded_facts",
            'Introduces a comparison to "industry average" that grounded_facts does not contain',
        ],
        "reasoning": (
            'The explanation invents a specific cause ("a documented billing '
            "dispute\") for the provider's score; grounded_facts contains only "
            "the numeric fields, not any narrative reason."
        ),
    }
    stub = StubAnthropicClient(json.dumps(payload))

    verdict = judge_faithfulness(_FACTS, "some explanation", client=stub)

    assert verdict.faithful is False
    assert verdict.score == 0.4
    assert verdict.violations == payload["violations"]
    assert verdict.reasoning == payload["reasoning"]


def test_judge_extracts_json_from_markdown_fenced_response() -> None:
    """Structured outputs (output_config.format) should make this a no-op
    on the live API, but the extraction is defense-in-depth for any
    response -- stubbed or otherwise -- that wraps the JSON in prose or a
    code fence instead of returning it bare.
    """
    payload = {"faithful": True, "score": 1.0, "violations": [], "reasoning": "ok"}
    stub = StubAnthropicClient(f"```json\n{json.dumps(payload)}\n```")

    verdict = judge_faithfulness(_FACTS, "some explanation", client=stub)

    assert verdict.faithful is True
    assert verdict.score == 1.0
