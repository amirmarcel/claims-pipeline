"""Typed response models for the ranking API (SPEC.md §4)."""

from __future__ import annotations

from pydantic import BaseModel


class RankingEntry(BaseModel):
    rank: int
    provider_id: str
    provider_score: float
    cost_efficiency: float
    quality: float
    claim_count: int


class NeighborFacts(BaseModel):
    provider_id: str
    provider_score: float


class GroundedFactsResponse(BaseModel):
    provider_id: str
    provider_score: float
    cost_efficiency: float
    quality: float
    claim_count: int
    rank: int
    neighbor_above: NeighborFacts | None
    neighbor_below: NeighborFacts | None


class ExplanationResponse(BaseModel):
    provider_id: str
    rank: int
    grounded_facts: GroundedFactsResponse
    explanation: str
