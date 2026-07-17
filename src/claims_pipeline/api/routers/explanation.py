"""GET /providers/{provider_id}/explanation -- the only endpoint in this
codebase that calls the language model (SPEC.md §4, ADR-0003).
"""

from __future__ import annotations

from typing import Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request

from claims_pipeline.api.dependencies import get_anthropic_client, get_connection
from claims_pipeline.api.schemas import ExplanationResponse
from claims_pipeline.explanation.service import build_explanation, fetch_explanation_facts

router = APIRouter(prefix="/providers", tags=["explanation"])


@router.get("/{provider_id}/explanation", response_model=ExplanationResponse)
def get_explanation(
    provider_id: str,
    request: Request,
    conn: psycopg.Connection[Any] = Depends(get_connection),
) -> ExplanationResponse:
    # Resolve provider existence first -- cheap, no model call -- so an
    # unknown provider 404s without ever constructing an Anthropic client.
    # `get_anthropic_client` is looked up (not Depends()'d) so it stays
    # unconstructed until after this check, while still honoring
    # `app.dependency_overrides` the same way Depends() would (tests stub
    # the client via that mechanism, e.g. tests/api/test_ranking_purity.py).
    facts = fetch_explanation_facts(conn, provider_id)
    if facts is None:
        raise HTTPException(status_code=404, detail=f"provider not found: {provider_id}")

    client_provider = request.app.dependency_overrides.get(
        get_anthropic_client, get_anthropic_client
    )
    result = build_explanation(facts, client=client_provider())
    return ExplanationResponse(**result)
