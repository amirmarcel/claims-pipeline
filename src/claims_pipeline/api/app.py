"""FastAPI application entry point (SPEC.md §4, docs/adr/0011-*).

FastAPI: async, typed request/response models via Pydantic (already a
transitive dependency of the SDKs this repo uses), and free OpenAPI docs --
the natural fit for a small, typed read API without pulling in a heavier
framework.

Run locally with:
    uvicorn claims_pipeline.api.app:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI

from claims_pipeline.api.routers import explanation, ranking

app = FastAPI(title="claims-pipeline ranking API")
app.include_router(ranking.router)
app.include_router(explanation.router)
