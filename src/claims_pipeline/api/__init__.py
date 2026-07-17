"""The ranking API (SPEC.md §4): FastAPI, pure reads over `provider_scores`.

Nothing in `claims_pipeline.api.routers.ranking` imports
`claims_pipeline.explanation` -- the model is reachable only through the
explanation router (ADR-0003).
"""
