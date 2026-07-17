"""GET /providers/ranking and GET /providers/{provider_id} (SPEC.md §4).

Pure reads over `provider_scores`. This module must never import
`claims_pipeline.explanation` or construct a model client -- the ranking
is a deterministic read and the model is never in this path (ADR-0003).
"""

from __future__ import annotations

from typing import Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from claims_pipeline.api.dependencies import get_connection
from claims_pipeline.api.schemas import RankingEntry
from claims_pipeline.db.repository import fetch_ranking

router = APIRouter(prefix="/providers", tags=["ranking"])


def _rank_entries(conn: psycopg.Connection[Any]) -> list[RankingEntry]:
    ranking = fetch_ranking(conn)
    return [
        RankingEntry(
            rank=i + 1,
            provider_id=p.provider_id,
            provider_score=p.provider_score,
            cost_efficiency=p.cost_efficiency,
            quality=p.quality,
            claim_count=p.claim_count,
        )
        for i, p in enumerate(ranking)
    ]


@router.get("/ranking", response_model=list[RankingEntry])
def get_ranking(
    limit: int | None = Query(default=None, gt=0),
    conn: psycopg.Connection[Any] = Depends(get_connection),
) -> list[RankingEntry]:
    entries = _rank_entries(conn)
    return entries[:limit] if limit is not None else entries


@router.get("/{provider_id}", response_model=RankingEntry)
def get_provider(
    provider_id: str,
    conn: psycopg.Connection[Any] = Depends(get_connection),
) -> RankingEntry:
    for entry in _rank_entries(conn):
        if entry.provider_id == provider_id:
            return entry
    raise HTTPException(status_code=404, detail=f"provider not found: {provider_id}")
