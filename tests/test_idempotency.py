"""Idempotency at the persistence layer (ADR-0007, EVAL_PLAN.md Tier 1).

The pure-function invariant (duplicate claim_id collapses to one entry) is
already asserted in test_scoring.py via the "duplicate-claim-id-no-double-count"
golden case. This test exercises the *same* golden case through the real
persistence layer against a real Postgres, since that is where the actual
upsert-and-recompute (not incremented) behavior lives (ADR-0007).

Skipped when Postgres isn't reachable, reusing the socket reachability skip
pattern from tests/test_integration_smoke.py.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psycopg
import pytest

from claims_pipeline.db import repository
from claims_pipeline.events import ClaimEvent

GOLDEN_PATH = Path(__file__).parent.parent / "benchmark" / "golden.seed.jsonl"
DSN = os.environ.get("CLAIMS_PIPELINE_DATABASE_URL", repository.DEFAULT_DSN)


def _load_case(case_name: str) -> dict[str, Any]:
    with GOLDEN_PATH.open() as f:
        for line in f:
            if not line.strip():
                continue
            case: dict[str, Any] = json.loads(line)
            if case["case"] == case_name:
                return case
    raise AssertionError(f"golden case not found: {case_name}")


DUPLICATE_CASE = _load_case("duplicate-claim-id-no-double-count")


def _postgres_reachable(timeout: float = 0.5) -> bool:
    parsed = urlparse(DSN)
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture
def conn() -> Any:
    if not _postgres_reachable():
        pytest.skip(f"Postgres not reachable at {DSN}; see infra/local/README.md")
    try:
        connection = psycopg.connect(DSN)
    except psycopg.OperationalError:
        # Something is listening on the port but rejects our credentials --
        # e.g. an unrelated local Postgres, not the docker-compose instance.
        pytest.skip(f"could not authenticate to Postgres at {DSN}; see infra/local/README.md")
    repository.apply_schema(connection)
    try:
        yield connection
    finally:
        connection.execute("TRUNCATE claim_scores, provider_scores")
        connection.commit()
        connection.close()


def test_duplicate_claim_id_does_not_double_count(conn: psycopg.Connection[Any]) -> None:
    claims = [ClaimEvent.from_dict(c) for c in DUPLICATE_CASE["claims"]]
    for claim in claims:
        repository.upsert_claim_and_recompute(conn, claim)

    provider_id = DUPLICATE_CASE["expected"]["ranking"][0]
    expected = DUPLICATE_CASE["expected"]["provider_scores"][provider_id]

    row = conn.execute(
        "SELECT provider_score, cost_efficiency, quality, claim_count "
        "FROM provider_scores WHERE provider_id = %s",
        (provider_id,),
    ).fetchone()
    assert row is not None
    provider_score, cost_efficiency, quality, claim_count = row

    assert claim_count == expected["claim_count"]
    assert provider_score == pytest.approx(expected["provider_score"])
    assert cost_efficiency == pytest.approx(expected["cost_efficiency"])
    assert quality == pytest.approx(expected["quality"])
