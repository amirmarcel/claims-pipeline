"""The deterministic scoring core (SPEC.md §3, ADR-0003).

Pure functions only: no I/O, no clock, no randomness, no queue, no Postgres
(AGENTS.md non-negotiable #1). This is the spine the golden-seed tests
(benchmark/golden.seed.jsonl) exercise directly with no infrastructure, so
they run in CI unconditionally and block merge (EVAL_PLAN.md Tier 1).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from claims_pipeline.events import ClaimEvent, validate

QUALITY_BY_OUTCOME: dict[str, float] = {
    "clean": 1.0,
    "complication": 0.5,
    "readmission": 0.0,
}


@dataclass(frozen=True, slots=True)
class DeadLetteredClaim:
    claim_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class ProviderScore:
    provider_id: str
    provider_score: float
    cost_efficiency: float
    quality: float
    claim_count: int


@dataclass(frozen=True, slots=True)
class ScoringResult:
    provider_scores: dict[str, ProviderScore]
    ranking: list[str]
    dead_lettered: list[DeadLetteredClaim]


def cost_efficiency(claim: ClaimEvent) -> float:
    """SPEC.md §3: allowed_amount / billed_amount, in (0, 1] for a valid claim."""
    return claim.allowed_amount / claim.billed_amount


def quality(claim: ClaimEvent) -> float:
    """SPEC.md §3: outcome mapped to a fixed quality signal."""
    return QUALITY_BY_OUTCOME[claim.outcome]


def provider_score(cost_efficiency_avg: float, quality_avg: float) -> float:
    """SPEC.md §3: 0.5/0.5 weighting, rounded to 4dp.

    Single-sourced here so the persistence layer's recompute (ADR-0007) and
    the pure batch scorer below apply the exact same formula.
    """
    return round(0.5 * cost_efficiency_avg + 0.5 * quality_avg, 4)


def score_claims(claims: Iterable[ClaimEvent]) -> ScoringResult:
    """Validate, score, and rank a set of claims (SPEC.md §2-3).

    Claims are keyed by claim_id before aggregation, so a duplicate claim_id
    within the input collapses to a single entry (ADR-0007) rather than being
    double-counted -- matching the upsert semantics of the persistence layer.
    """
    valid_claims_by_id: dict[str, ClaimEvent] = {}
    dead_lettered: list[DeadLetteredClaim] = []

    for claim in claims:
        ok, reason = validate(claim)
        if ok:
            valid_claims_by_id[claim.claim_id] = claim
        else:
            assert reason is not None
            dead_lettered.append(DeadLetteredClaim(claim.claim_id, reason))

    claims_by_provider: dict[str, list[ClaimEvent]] = defaultdict(list)
    for claim in valid_claims_by_id.values():
        claims_by_provider[claim.provider_id].append(claim)

    provider_scores: dict[str, ProviderScore] = {}
    for provider_id, provider_claims in claims_by_provider.items():
        n = len(provider_claims)
        avg_cost = sum(cost_efficiency(c) for c in provider_claims) / n
        avg_quality = sum(quality(c) for c in provider_claims) / n
        provider_scores[provider_id] = ProviderScore(
            provider_id=provider_id,
            provider_score=provider_score(avg_cost, avg_quality),
            cost_efficiency=avg_cost,
            quality=avg_quality,
            claim_count=n,
        )

    ranking = sorted(provider_scores, key=lambda pid: (-provider_scores[pid].provider_score, pid))

    return ScoringResult(
        provider_scores=provider_scores,
        ranking=ranking,
        dead_lettered=dead_lettered,
    )
