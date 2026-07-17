"""Orchestrates the confined explanation endpoint (SPEC.md §4, ADR-0003):
deterministic facts in, model prose out. This is the only path in the
codebase that reaches from a database read to a model call -- the ranking
endpoints in `claims_pipeline.api.routers.ranking` never import this module.
"""

from __future__ import annotations

from typing import Any

import psycopg

from claims_pipeline.db.repository import fetch_ranking
from claims_pipeline.explanation.client import AnthropicClientLike, generate_explanation
from claims_pipeline.explanation.facts import GroundedFacts, build_grounded_facts


def fetch_explanation_facts(
    conn: psycopg.Connection[Any], provider_id: str
) -> GroundedFacts | None:
    """Read the ranking and assemble the fixed grounded_facts envelope for
    `provider_id`. No model call -- this is the cheap existence check the
    endpoint must run before ever constructing a model client, so an unknown
    provider 404s without touching the model (SPEC.md §4). Returns None if
    the provider has no score (SPEC.md §3: a provider with zero valid claims
    does not appear in the ranking).
    """
    ranking = fetch_ranking(conn)
    return build_grounded_facts(ranking, provider_id)


def build_explanation(
    facts: GroundedFacts,
    *,
    client: AnthropicClientLike | None = None,
) -> dict[str, Any]:
    """Call the model for prose describing an already-resolved grounded_facts
    envelope. Callers must resolve `facts` via `fetch_explanation_facts`
    first and handle the not-found case before ever reaching this function.
    """
    facts_dict = facts.to_dict()
    explanation = generate_explanation(facts_dict, client=client)
    return {
        "provider_id": facts.provider_id,
        "rank": facts.rank,
        "grounded_facts": facts_dict,
        "explanation": explanation,
    }
