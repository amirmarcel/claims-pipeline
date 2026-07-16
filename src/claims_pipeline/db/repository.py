"""Postgres persistence for claim/provider scores (SPEC.md §2, ADR-0007).

This is the thin I/O layer around the pure scoring core in
`claims_pipeline.scoring`: it stores the per-claim signals and recomputes the
provider aggregate from the stored set of that provider's claims on every
upsert, never by incrementing a running total. Raw SQL DDL, no ORM/migration
framework -- see
docs/adr/0009-raw-sql-schema-no-migration-framework.md.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import psycopg

from claims_pipeline.events import ClaimEvent
from claims_pipeline.scoring import cost_efficiency, provider_score, quality

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

DEFAULT_DSN = "postgresql://claims:claims@localhost:5432/claims_pipeline"


def default_dsn() -> str:
    return os.environ.get("CLAIMS_PIPELINE_DATABASE_URL", DEFAULT_DSN)


def connect(dsn: str | None = None) -> psycopg.Connection[Any]:
    return psycopg.connect(dsn or default_dsn())


def apply_schema(conn: psycopg.Connection[Any]) -> None:
    conn.execute(SCHEMA_PATH.read_text())
    conn.commit()


def upsert_claim_and_recompute(conn: psycopg.Connection[Any], claim: ClaimEvent) -> None:
    """Idempotent upsert on claim_id, then recompute the provider aggregate
    from the full stored set of that provider's claims (ADR-0007).

    Assumes `claim` has already passed `events.validate` -- only valid claims
    reach the scoring stage (SPEC.md §2 step 4).
    """
    with conn.transaction():
        conn.execute(
            """
            INSERT INTO claim_scores (claim_id, provider_id, cost_efficiency, quality)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (claim_id) DO UPDATE SET
                provider_id = EXCLUDED.provider_id,
                cost_efficiency = EXCLUDED.cost_efficiency,
                quality = EXCLUDED.quality
            """,
            (claim.claim_id, claim.provider_id, cost_efficiency(claim), quality(claim)),
        )
        _recompute_provider_aggregate(conn, claim.provider_id)


def _recompute_provider_aggregate(conn: psycopg.Connection[Any], provider_id: str) -> None:
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)",
        (provider_id,),
    )
    cursor = conn.execute(
        """
        SELECT AVG(cost_efficiency), AVG(quality), COUNT(*)
        FROM claim_scores
        WHERE provider_id = %s
        """,
        (provider_id,),
    )
    row = cursor.fetchone()
    assert row is not None
    avg_cost, avg_quality, claim_count = row

    conn.execute(
        """
        INSERT INTO provider_scores
            (provider_id, provider_score, cost_efficiency, quality, claim_count)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (provider_id) DO UPDATE SET
            provider_score = EXCLUDED.provider_score,
            cost_efficiency = EXCLUDED.cost_efficiency,
            quality = EXCLUDED.quality,
            claim_count = EXCLUDED.claim_count
        """,
        (
            provider_id,
            provider_score(avg_cost, avg_quality),
            avg_cost,
            avg_quality,
            claim_count,
        ),
    )
