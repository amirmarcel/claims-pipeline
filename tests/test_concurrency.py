"""Concurrent scoring-worker replicas must not lose updates (ADR-0007).

`_recompute_provider_aggregate` reads the stored claims for a provider and
upserts the aggregate. Two transactions racing on the same provider_id under
READ COMMITTED can each compute from a snapshot that doesn't include the
other's not-yet-committed insert -- whichever commits last silently
overwrites the correct aggregate with a stale one. This is closed by a
per-provider `pg_advisory_xact_lock` acquired before the read.

This test drives two real Postgres connections through the insert-then-
recompute flow with a barrier between the insert and the recompute, so both
transactions are guaranteed to have their own claim inserted but uncommitted
when the recompute step starts -- the exact window the lock must close.

Skipped when Postgres isn't reachable, reusing the pattern from
tests/test_idempotency.py.
"""

from __future__ import annotations

import os
import socket
import threading
from typing import Any
from urllib.parse import urlparse

import psycopg
import pytest

from claims_pipeline.db import repository
from claims_pipeline.events import ClaimEvent

DSN = os.environ.get("CLAIMS_PIPELINE_DATABASE_URL", repository.DEFAULT_DSN)


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
        pytest.skip(f"could not authenticate to Postgres at {DSN}; see infra/local/README.md")
    repository.apply_schema(connection)
    try:
        yield connection
    finally:
        connection.execute("TRUNCATE claim_scores, provider_scores")
        connection.commit()
        connection.close()


def _claim(claim_id: str, provider_id: str) -> ClaimEvent:
    return ClaimEvent(
        claim_id=claim_id,
        provider_id=provider_id,
        specialty="cardiology",
        procedure_code="99213",
        billed_amount=100.0,
        allowed_amount=80.0,
        outcome="clean",
        patient_ref="patient-1",
        service_date="2026-01-01",
        schema_version="1.0",
    )


def test_concurrent_recompute_for_same_provider_does_not_lose_updates(
    conn: psycopg.Connection[Any],
) -> None:
    provider_id = "provider-concurrency-test"
    claim_a = _claim("claim-a", provider_id)
    claim_b = _claim("claim-b", provider_id)

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def worker(claim: ClaimEvent) -> None:
        worker_conn = psycopg.connect(DSN)
        try:
            with worker_conn.transaction():
                worker_conn.execute(
                    """
                    INSERT INTO claim_scores (claim_id, provider_id, cost_efficiency, quality)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (claim_id) DO NOTHING
                    """,
                    (claim.claim_id, claim.provider_id, 0.8, 0.9),
                )
                barrier.wait(timeout=5)
                repository._recompute_provider_aggregate(worker_conn, claim.provider_id)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            worker_conn.close()

    t1 = threading.Thread(target=worker, args=(claim_a,))
    t2 = threading.Thread(target=worker, args=(claim_b,))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not errors, errors

    row = conn.execute(
        "SELECT claim_count FROM provider_scores WHERE provider_id = %s",
        (provider_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == 2
