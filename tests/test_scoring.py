"""Tier 1: golden-seed tests for the pure scoring core (EVAL_PLAN.md, SPEC.md §3).

Reads benchmark/golden.seed.jsonl directly instead of transcribing cases into
Python -- the jsonl is the single source of truth, so this test can't drift
from the oracle. Runs against pure functions only: no I/O, no infrastructure,
so it runs in CI unconditionally and blocks merge.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from claims_pipeline.events import ClaimEvent
from claims_pipeline.scoring import score_claims

GOLDEN_PATH = Path(__file__).parent.parent / "benchmark" / "golden.seed.jsonl"


def _load_cases() -> list[dict[str, Any]]:
    with GOLDEN_PATH.open() as f:
        return [json.loads(line) for line in f if line.strip()]


CASES = _load_cases()


@pytest.mark.parametrize("case", CASES, ids=[c["case"] for c in CASES])
def test_golden_case(case: dict[str, Any]) -> None:
    claims = [ClaimEvent.from_dict(c) for c in case["claims"]]
    result = score_claims(claims)

    expected = case["expected"]
    assert result.ranking == expected["ranking"]
    assert set(result.provider_scores) == set(expected["provider_scores"])

    for provider_id, expected_scores in expected["provider_scores"].items():
        actual = result.provider_scores[provider_id]
        assert actual.provider_score == pytest.approx(expected_scores["provider_score"])
        assert actual.cost_efficiency == pytest.approx(expected_scores["cost_efficiency"])
        assert actual.quality == pytest.approx(expected_scores["quality"])
        assert actual.claim_count == expected_scores["claim_count"]

    actual_dead_lettered = [
        {"claim_id": d.claim_id, "reason": d.reason} for d in result.dead_lettered
    ]
    assert actual_dead_lettered == expected["dead_lettered"]
