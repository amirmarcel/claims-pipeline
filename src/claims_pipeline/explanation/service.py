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
from claims_pipeline.explanation.facts import build_grounded_facts


def build_explanation(
    conn: psycopg.Connection[Any],
    provider_id: str,
    *,
    client: AnthropicClientLike | None = None,
) -> dict[str, Any] | None:
    """Read the ranking, assemble the fixed grounded_facts envelope for
    `provider_id`, and call the model for prose. Returns None if the
    provider has no score (SPEC.md §3: a provider with zero valid claims
    does not appear in the ranking).
    """
    ranking = fetch_ranking(conn)
    facts = build_grounded_facts(ranking, provider_id)
    if facts is None:
        return None

    facts_dict = facts.to_dict()
    explanation = generate_explanation(facts_dict, client=client)
    return {
        "provider_id": provider_id,
        "rank": facts.rank,
        "grounded_facts": facts_dict,
        "explanation": explanation,
    }
