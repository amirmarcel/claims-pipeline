"""Shared FastAPI dependencies: a per-request Postgres connection and the
Anthropic client used by the explanation endpoint.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any, cast

import anthropic
import psycopg
from fastapi import HTTPException

from claims_pipeline.db import repository
from claims_pipeline.explanation.client import AnthropicClientLike


def get_connection() -> Iterator[psycopg.Connection[Any]]:
    conn = repository.connect()
    try:
        yield conn
    finally:
        conn.close()


def get_anthropic_client() -> AnthropicClientLike:
    """A fresh `anthropic.Anthropic()` client per request. Exposed as a
    dependency (rather than constructed inline in the router) so Tier 2
    guardrail tests can override it with a stub and never touch the network
    or require an API key (docs/EVAL_PLAN.md Tier 2).

    `anthropic.Anthropic()` itself does not validate the key at construction
    time -- it only fails once a request is actually sent, with a bare
    `TypeError` rather than an `anthropic.AnthropicError`, which would
    otherwise surface as an unhandled 500 from the explanation endpoint. So
    the key's presence is checked here, up front, and turned into a clean
    503 the router propagates as-is.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(status_code=503, detail="model unavailable: ANTHROPIC_API_KEY not set")
    # See the `cast` note in claims_pipeline.explanation.client: the real
    # client satisfies AnthropicClientLike at runtime; mypy just can't match
    # its full overload set structurally against the narrow protocol.
    return cast(AnthropicClientLike, anthropic.Anthropic())
