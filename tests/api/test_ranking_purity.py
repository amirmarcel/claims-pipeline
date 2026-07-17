"""Tier 2 guardrail (docs/EVAL_PLAN.md): ranking purity. GET /providers/ranking
must produce byte-identical ordering whether or not the explanation endpoint
has ever been called for this provider set -- the model cannot have moved
anything (ADR-0003). The model call is stubbed, so this runs keyless and
blocks merge. Requires a reachable local Postgres; skips cleanly otherwise,
same pattern as tests/test_integration_smoke.py.
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from claims_pipeline.api.app import app
from claims_pipeline.api.dependencies import get_anthropic_client, get_connection
from claims_pipeline.db import repository
from tests.explanation.stub_client import StubAnthropicClient


def _postgres_reachable() -> bool:
    try:
        repository.connect().close()
    except psycopg.OperationalError:
        return False
    return True


@pytest.fixture
def conn() -> Iterator[psycopg.Connection[object]]:
    if not _postgres_reachable():
        pytest.skip("Postgres not reachable; see infra/local/README.md")
    connection = repository.connect()
    repository.apply_schema(connection)
    connection.execute("TRUNCATE claim_scores, provider_scores")
    connection.commit()
    for provider_id, score, cost, quality, count in [
        ("P-002", 1.0, 1.0, 1.0, 1),
        ("P-001", 0.70, 0.65, 0.75, 2),
    ]:
        connection.execute(
            "INSERT INTO provider_scores "
            "(provider_id, provider_score, cost_efficiency, quality, claim_count) "
            "VALUES (%s, %s, %s, %s, %s)",
            (provider_id, score, cost, quality, count),
        )
    connection.commit()
    yield connection
    connection.execute("TRUNCATE claim_scores, provider_scores")
    connection.commit()
    connection.close()


@pytest.fixture
def client(conn: psycopg.Connection[object]) -> Iterator[TestClient]:
    def _conn_override() -> Iterator[psycopg.Connection[object]]:
        yield conn

    app.dependency_overrides[get_connection] = _conn_override
    app.dependency_overrides[get_anthropic_client] = lambda: StubAnthropicClient(
        "P-001 ranks 2nd with a score of 0.7, behind P-002 at 1.0."
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_ranking_ordering_unaffected_by_explanation_calls(client: TestClient) -> None:
    before = client.get("/providers/ranking").json()

    explanation_response = client.get("/providers/P-001/explanation")
    assert explanation_response.status_code == 200

    after = client.get("/providers/ranking").json()

    assert before == after
    assert [entry["provider_id"] for entry in after] == ["P-002", "P-001"]
