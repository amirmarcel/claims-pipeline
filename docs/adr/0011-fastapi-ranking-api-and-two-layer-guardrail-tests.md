# 0011 — FastAPI for the ranking API; two-layer Tier 2 guardrail tests

**Status:** Accepted

## Context

Session 4 builds the read surface: the ranking API (SPEC.md §4) and the confined
explanation endpoint (ADR-0003). Two decisions here are architecturally significant
enough to record.

**Framework choice.** The ranking API is a small, typed, read-mostly HTTP surface with
one endpoint that calls a language model. It needs request/response typing, working
against `mypy --strict`, and ideally free API documentation, without pulling in a
heavier web framework than the problem calls for.

**Testing the model boundary without a live key.** EVAL_PLAN.md Tier 2 guardrail
checks (groundedness, injection resistance, no-empty-success, ranking purity) are
deterministic assertions over *generated* text, but generating that text normally
requires a live call to the language model. AGENTS.md's quality gate requires Tier 1
+ Tier 2 to block merge on every push, and that can't depend on CI secrets being
configured correctly, or on live-model non-determinism producing a flaky gate.

## Decision

**FastAPI**, for the API. It is async, typed via Pydantic (already implied by the
`anthropic` SDK's own dependency chain), generates OpenAPI for free, and needs no
additional web framework beyond `fastapi` + `uvicorn`. The ranking read path
(`src/claims_pipeline/api/routers/ranking.py`) is written to never import
`claims_pipeline.explanation` or construct a model client — this is enforced by a
test that monkeypatches `anthropic.Anthropic.__init__` to raise and then exercises
both ranking endpoints (ADR-0003).

**Two-layer Tier 2 guardrail tests**, not one:

1. **Stubbed layer** (`tests/explanation/test_guardrails_stubbed.py`,
   `tests/api/test_ranking_purity.py`) — runs the real prompt-construction and
   response-parsing code in `claims_pipeline.explanation.client`, but against a
   **recorded, committed response fixture**
   (`tests/fixtures/explanation_recorded*.json`) played back through a stub client
   satisfying the same narrow protocol (`AnthropicClientLike`) the real
   `anthropic.Anthropic` client does. No network call, no API key, fully
   deterministic. This is the layer that blocks merge unconditionally, per
   AGENTS.md's quality gate.
2. **Live layer** (`tests/explanation/test_guardrails_live.py`) — a thinner check
   that hits the real API to catch drift between the recorded fixtures and actual
   model behavior. Gated behind `ANTHROPIC_API_KEY` and skips cleanly when it's
   absent, using the same skip pattern `tests/test_integration_smoke.py` already
   uses for LocalStack. It is not required for merge; layer 1 already provides that
   guarantee keylessly. It can optionally be wired into CI later via a secret.

The model call itself is confined to one function, `generate_explanation` in
`src/claims_pipeline/explanation/client.py`, which takes an injectable `client`
parameter for exactly this reason: both test layers, and the FastAPI dependency
(`get_anthropic_client`) that wires the real client into the endpoint, go through
the same narrow interface.

## Consequences

The ranking API is typed, documented, and provably model-free by test, not just by
code review. The Tier 2 hard gate runs on every push with no live dependency and no
flakiness from model non-determinism — a fixture recorded once (and hand-verified to
actually resist the injection it was recorded against) is the oracle from then on,
same spirit as `benchmark/golden.seed.jsonl` for Tier 1. The trade-off is that the
stubbed layer can drift from real model behavior over time (a prompt change could
regress live behavior while the stale fixture still "passes"); the live layer is the
check against that drift, and its explicit skip-when-keyless design keeps that risk
from ever leaking into the merge-blocking gate.

Stubbed-layer review also caught a second, narrower injection vector: `json.dumps`
does not escape `<`/`>`, so an untrusted `provider_id` containing the literal closing
tag could forge the end of the `<grounded_facts>` block and smuggle attacker text
outside it. `_build_user_message` (`src/claims_pipeline/explanation/client.py`) now
escapes angle brackets as JSON unicode escapes after serialization, and
`test_delimiter_breaking_payload_cannot_forge_a_closing_tag` in
`tests/explanation/test_guardrails_stubbed.py` pins that behavior (SPEC.md §4).
