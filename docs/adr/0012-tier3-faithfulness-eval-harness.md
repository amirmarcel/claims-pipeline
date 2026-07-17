# 0012 — Tier 3 faithfulness eval harness: judge model, baseline, and threshold

**Status:** Accepted

## Context

`docs/EVAL_PLAN.md` specifies Tier 3 as a labeled `grounded_facts` eval set graded by
an LLM judge for faithfulness, reporting an aggregate score and gating CI only on
regression below a committed baseline — unlike Tier 1/2, which block unconditionally.
Building this requires three decisions the eval plan deliberately leaves to
implementation: which model judges, what the initial baseline is, and how much
regression is tolerated before it blocks merge.

## Decision

**Judge model: `claude-opus-4-8`**, the same model used by the explanation layer
itself (`src/claims_pipeline/explanation/client.py`). Faithfulness grading is a
judgment call over nuanced text (does this paraphrase introduce an unsupported
causal claim? does this number round correctly?), not a task to economize on with a
weaker model — a judge that misses subtle unfaithfulness is worse than no judge. The
same-tier choice also keeps the number of distinct models this project depends on at
one.

**Harness architecture**, mirroring the two-layer pattern ADR-0011 already
established for Tier 2:

- `src/claims_pipeline/evals/judge.py` — a second confined model call, structurally
  identical to `explanation/client.py`: fixed inputs (`grounded_facts` and a
  generated explanation) in, a structured `FaithfulnessVerdict` (`faithful: bool`,
  `score: float`, `violations: list[str]`, `reasoning: str`) out. It grades an
  explanation already generated elsewhere; it has no path to influence scoring or
  ranking (AGENTS.md #2).
- `src/claims_pipeline/evals/runner.py` — `run_eval_set` takes both model clients as
  injectable parameters (same shape as `AnthropicClientLike`), so the deterministic
  harness logic (loading `benchmark/faithfulness.eval.jsonl`, orchestrating one
  explanation + one judgment per case, aggregating the score, clustering failures by
  a keyword match over the judge's `violations` text, writing the JSON + Markdown
  report) is unit-tested with stubs and no network access
  (`tests/evals/test_runner_stubbed.py`). `python -m claims_pipeline.evals` is the
  only place that decides whether `ANTHROPIC_API_KEY` is available and wires the real
  clients; it skips cleanly (prints a message, exits 0) when the key is absent.
- `benchmark/faithfulness.eval.jsonl` — ten `grounded_facts` cases covering the shape
  space Tier 2 doesn't already stress: top/bottom/sole-provider rank (null
  neighbors), a zero-quality provider, a perfect-cost/poor-quality provider, large
  claim counts, four-decimal-precision scores, and a tied neighbor score. Tier 2's
  existing injection-resistance cases are not duplicated here — Tier 3 measures
  faithfulness quality on benign input, Tier 2 already covers adversarial input.
- `tests/fixtures/faithfulness_meta_eval.json` +
  `tests/evals/test_meta_eval_live.py` — the meta-eval. Three hand-written,
  deliberately unfaithful (`grounded_facts`, explanation) pairs, one per violation
  type (an invented number, an invented causal reason, a rank contradiction), asserted
  to score `faithful: false` against the **real** judge model. This is what makes the
  harness credible: a judge that scores everything faithful would pass every other
  test in this suite too. Live and key-gated, same skip pattern as
  `tests/explanation/test_guardrails_live.py` — not required for merge, but run in
  this session (infra up, key present) and confirmed to catch all three cases.

**Baseline: `1.0000`, threshold: `0.05`.** Recorded in
`benchmark/reports/faithfulness_baseline.json` by running
`python -m claims_pipeline.evals --write-baseline` against the ten-case eval set
above with the live judge (Session 5, 2026-07-17): every case scored a perfect 1.0 —
the explanation model introduced no ungrounded numbers or unsupported claims on any
case in this set. The threshold is a tolerance band, not an expectation of future
perfection: CI (`python -m claims_pipeline.evals --check-baseline`) fails only if a
future run's aggregate score drops more than `0.05` below `1.0`, i.e. below `0.95`.
This is a starting judgment call, not a proof that `0.05` is the correct sensitivity
— it should be revisited once real prompt or model changes produce a non-perfect run
to calibrate against.

**CI wiring**: a new `faithfulness-eval` job in `.github/workflows/ci.yml`, separate
from `lint-and-test`, runs `python -m claims_pipeline.evals --check-baseline` with
`ANTHROPIC_API_KEY` from `secrets.ANTHROPIC_API_KEY`. Until that secret is actually
configured in the repository, the job is a no-op that exits 0 (the runner's own
keyless skip) — it does not block merge and is not a hidden dependency on
infrastructure this session didn't set up.

## Consequences

Tier 3 is additive to, not a replacement for, Tier 2: the deterministic groundedness/
injection/non-empty checks in `tests/explanation/test_guardrails_stubbed.py` still
block merge unconditionally and are unchanged. Tier 3 adds a quality signal Tier 2
structurally cannot express (Tier 2 asserts *no* invented number; Tier 3 additionally
grades whether the framing of correct numbers is honest — e.g. attributing a real
score to a fabricated cause, which contains no invented number at all). The
trade-off is the same one ADR-0011 accepted for Tier 2's live layer: the judge is a
live model call, so the meta-eval and any live harness run cost API calls and are
non-deterministic in exact wording (though the `faithful` boolean was stable across
this session's run). A `1.0` baseline leaves no headroom to *improve* — only to
regress — which is expected for a first eval set this small; the eval set should grow
by the improvement loop (`docs/EVAL_PLAN.md`) as real unfaithful cases are found in
production explanations, the same way `benchmark/golden.seed.jsonl` grew for Tier 1.
