"""Tests for the ranking API (SPEC.md §4): pure reads over `provider_scores`
that never touch the language model. Requires a reachable local Postgres
(infra/local/docker-compose.yml); skips cleanly otherwise, same pattern as
tests/test_integration_smoke.py.
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from claims_pipeline.api.app import app
from claims_pipeline.api.dependencies import get_connection
from claims_pipeline.db import repository


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
    yield connection
    connection.execute("TRUNCATE claim_scores, provider_scores")
    connection.commit()
    connection.close()


@pytest.fixture
def client(conn: psycopg.Connection[object]) -> Iterator[TestClient]:
    def _override() -> Iterator[psycopg.Connection[object]]:
        yield conn

    app.dependency_overrides[get_connection] = _override
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed(
    conn: psycopg.Connection[object], rows: list[tuple[str, float, float, float, int]]
) -> None:
    for provider_id, score, cost, quality, count in rows:
        conn.execute(
            "INSERT INTO provider_scores "
            "(provider_id, provider_score, cost_efficiency, quality, claim_count) "
            "VALUES (%s, %s, %s, %s, %s)",
            (provider_id, score, cost, quality, count),
        )
    conn.commit()


def test_ranking_matches_golden_seed_worked_example(
    conn: psycopg.Connection[object], client: TestClient
) -> None:
    # SPEC.md §3 worked example.
    _seed(conn, [("P-001", 0.70, 0.65, 0.75, 2), ("P-002", 1.0, 1.0, 1.0, 1)])

    response = client.get("/providers/ranking")

    assert response.status_code == 200
    body = response.json()
    assert [entry["provider_id"] for entry in body] == ["P-002", "P-001"]
    assert body[0] == {
        "rank": 1,
        "provider_id": "P-002",
        "provider_score": 1.0,
        "cost_efficiency": 1.0,
        "quality": 1.0,
        "claim_count": 1,
    }
    assert body[1]["rank"] == 2


def test_ranking_ties_break_by_provider_id_ascending(
    conn: psycopg.Connection[object], client: TestClient
) -> None:
    _seed(conn, [("P-002", 0.5, 0.5, 0.5, 1), ("P-001", 0.5, 0.5, 0.5, 1)])

    response = client.get("/providers/ranking")

    assert [entry["provider_id"] for entry in response.json()] == ["P-001", "P-002"]


def test_ranking_limit_preserves_rank_from_full_set(
    conn: psycopg.Connection[object], client: TestClient
) -> None:
    _seed(
        conn,
        [
            ("P-003", 0.9, 0.9, 0.9, 1),
            ("P-002", 0.8, 0.8, 0.8, 1),
            ("P-001", 0.7, 0.7, 0.7, 1),
        ],
    )

    response = client.get("/providers/ranking?limit=2")

    body = response.json()
    assert [entry["provider_id"] for entry in body] == ["P-003", "P-002"]
    assert [entry["rank"] for entry in body] == [1, 2]


def test_get_single_provider(conn: psycopg.Connection[object], client: TestClient) -> None:
    _seed(conn, [("P-002", 1.0, 1.0, 1.0, 1), ("P-001", 0.70, 0.65, 0.75, 2)])

    response = client.get("/providers/P-001")

    assert response.status_code == 200
    assert response.json() == {
        "rank": 2,
        "provider_id": "P-001",
        "provider_score": 0.70,
        "cost_efficiency": 0.65,
        "quality": 0.75,
        "claim_count": 2,
    }


def test_get_unknown_provider_404s(conn: psycopg.Connection[object], client: TestClient) -> None:
    response = client.get("/providers/P-999")
    assert response.status_code == 404


def test_unknown_provider_explanation_404s_without_constructing_a_model_client(
    monkeypatch: pytest.MonkeyPatch, conn: psycopg.Connection[object], client: TestClient
) -> None:
    """Session-4 carry-over fix: the explanation endpoint must resolve
    provider existence (cheap, no model) before ever constructing an
    Anthropic client, so an unknown provider with no API key 404s instead of
    500ing out of client construction. Same monkeypatch-to-raise pattern as
    test_ranking_endpoints_never_construct_a_model_client / ADR-0003.
    """

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "unknown-provider explanation must never construct an Anthropic client"
        )

    monkeypatch.setattr("anthropic.Anthropic.__init__", _boom)

    response = client.get("/providers/P-999/explanation")

    assert response.status_code == 404


def test_known_provider_explanation_503s_without_api_key(
    monkeypatch: pytest.MonkeyPatch, conn: psycopg.Connection[object], client: TestClient
) -> None:
    """Sibling of test_unknown_provider_explanation_404s_without_constructing_a_model_client:
    a *known* provider still can't reach the model if ANTHROPIC_API_KEY isn't
    set. `anthropic.Anthropic()` doesn't validate the key at construction
    time, so without the dependencies.get_anthropic_client guard this would
    hit `messages.create` and blow up with an unhandled 500 instead of a
    clean 503.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _seed(conn, [("P-001", 0.70, 0.65, 0.75, 2)])

    response = client.get("/providers/P-001/explanation")

    assert response.status_code == 503


def test_ranking_endpoints_never_construct_a_model_client(
    monkeypatch: pytest.MonkeyPatch, conn: psycopg.Connection[object], client: TestClient
) -> None:
    """ADR-0003: the ranking read must never be in a path that can reach the
    model. Force any attempt to construct an Anthropic client to fail loudly,
    then exercise both ranking endpoints -- if this test passes, no code path
    triggered by these endpoints touched the model.
    """

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("ranking endpoint must never construct an Anthropic client")

    monkeypatch.setattr("anthropic.Anthropic.__init__", _boom)
    _seed(conn, [("P-001", 0.70, 0.65, 0.75, 2)])

    assert client.get("/providers/ranking").status_code == 200
    assert client.get("/providers/P-001").status_code == 200
