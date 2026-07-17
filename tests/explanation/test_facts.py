"""Pure unit tests for grounded_facts assembly (SPEC.md §4). No I/O, no
model call -- these are ordinary deterministic tests, not evals.
"""

from __future__ import annotations

from claims_pipeline.explanation.facts import build_grounded_facts
from claims_pipeline.scoring import ProviderScore

RANKING = [
    ProviderScore(
        provider_id="P-002", provider_score=1.0, cost_efficiency=1.0, quality=1.0, claim_count=1
    ),
    ProviderScore(
        provider_id="P-001", provider_score=0.70, cost_efficiency=0.65, quality=0.75, claim_count=2
    ),
    ProviderScore(
        provider_id="P-003", provider_score=0.50, cost_efficiency=0.5, quality=0.5, claim_count=1
    ),
]


def test_middle_provider_has_both_neighbors() -> None:
    facts = build_grounded_facts(RANKING, "P-001")

    assert facts is not None
    assert facts.rank == 2
    assert facts.neighbor_above is not None
    assert facts.neighbor_above.provider_id == "P-002"
    assert facts.neighbor_below is not None
    assert facts.neighbor_below.provider_id == "P-003"


def test_top_ranked_provider_has_no_neighbor_above() -> None:
    facts = build_grounded_facts(RANKING, "P-002")

    assert facts is not None
    assert facts.rank == 1
    assert facts.neighbor_above is None
    assert facts.neighbor_below is not None
    assert facts.neighbor_below.provider_id == "P-001"


def test_bottom_ranked_provider_has_no_neighbor_below() -> None:
    facts = build_grounded_facts(RANKING, "P-003")

    assert facts is not None
    assert facts.rank == 3
    assert facts.neighbor_below is None


def test_unknown_provider_returns_none() -> None:
    assert build_grounded_facts(RANKING, "P-999") is None


def test_to_dict_matches_spec_shape() -> None:
    facts = build_grounded_facts(RANKING, "P-001")
    assert facts is not None

    assert facts.to_dict() == {
        "provider_id": "P-001",
        "provider_score": 0.70,
        "cost_efficiency": 0.65,
        "quality": 0.75,
        "claim_count": 2,
        "rank": 2,
        "neighbor_above": {"provider_id": "P-002", "provider_score": 1.0},
        "neighbor_below": {"provider_id": "P-003", "provider_score": 0.50},
    }
