"""GET /providers/{provider_id}/explanation -- the only endpoint in this
codebase that calls the language model (SPEC.md §4, ADR-0003).
"""

from __future__ import annotations

from typing import Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException

from claims_pipeline.api.dependencies import get_anthropic_client, get_connection
from claims_pipeline.api.schemas import ExplanationResponse
from claims_pipeline.explanation.client import AnthropicClientLike
from claims_pipeline.explanation.service import build_explanation

router = APIRouter(prefix="/providers", tags=["explanation"])


@router.get("/{provider_id}/explanation", response_model=ExplanationResponse)
def get_explanation(
    provider_id: str,
    conn: psycopg.Connection[Any] = Depends(get_connection),
    client: AnthropicClientLike = Depends(get_anthropic_client),
) -> ExplanationResponse:
    result = build_explanation(conn, provider_id, client=client)
    if result is None:
        raise HTTPException(status_code=404, detail=f"provider not found: {provider_id}")
    return ExplanationResponse(**result)
