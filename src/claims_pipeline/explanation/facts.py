"""Grounded-facts assembly for the confined explanation layer (SPEC.md §4).

Pure: computed entirely from an already-ordered ranking (itself a pure read
over `provider_scores`, ADR-0003). No I/O, no model call -- this module
defines the fixed, exhaustive set of facts the model is allowed to see and
describe. Nothing outside this envelope reaches the prompt.
"""

from __future__ import annotations

from dataclasses import dataclass

from claims_pipeline.scoring import ProviderScore


@dataclass(frozen=True, slots=True)
class Neighbor:
    provider_id: str
    provider_score: float

    def to_dict(self) -> dict[str, object]:
        return {"provider_id": self.provider_id, "provider_score": self.provider_score}


@dataclass(frozen=True, slots=True)
class GroundedFacts:
    provider_id: str
    provider_score: float
    cost_efficiency: float
    quality: float
    claim_count: int
    rank: int
    neighbor_above: Neighbor | None
    neighbor_below: Neighbor | None

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "provider_score": self.provider_score,
            "cost_efficiency": self.cost_efficiency,
            "quality": self.quality,
            "claim_count": self.claim_count,
            "rank": self.rank,
            "neighbor_above": self.neighbor_above.to_dict() if self.neighbor_above else None,
            "neighbor_below": self.neighbor_below.to_dict() if self.neighbor_below else None,
        }


def build_grounded_facts(ranking: list[ProviderScore], provider_id: str) -> GroundedFacts | None:
    """Locate `provider_id` in an already-ordered ranking (score desc,
    provider_id asc -- SPEC.md §3) and assemble the fixed fact envelope the
    model is allowed to see (SPEC.md §4). Returns None if the provider has
    no score, i.e. is absent from the ranking.
    """
    index = next((i for i, p in enumerate(ranking) if p.provider_id == provider_id), None)
    if index is None:
        return None

    provider = ranking[index]
    neighbor_above = (
        Neighbor(ranking[index - 1].provider_id, ranking[index - 1].provider_score)
        if index > 0
        else None
    )
    neighbor_below = (
        Neighbor(ranking[index + 1].provider_id, ranking[index + 1].provider_score)
        if index + 1 < len(ranking)
        else None
    )
    return GroundedFacts(
        provider_id=provider.provider_id,
        provider_score=provider.provider_score,
        cost_efficiency=provider.cost_efficiency,
        quality=provider.quality,
        claim_count=provider.claim_count,
        rank=index + 1,
        neighbor_above=neighbor_above,
        neighbor_below=neighbor_below,
    )
