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
- `benchmark/faithfulness.eval.jsonl` — fourteen cases: ten `grounded_facts`-only
  cases (explanation generated live) covering the shape space Tier 2 doesn't already
  stress — top/bottom/sole-provider rank (null neighbors), a zero-quality provider, a
  perfect-cost/poor-quality provider, large claim counts, four-decimal-precision
  scores, and a tied neighbor score — plus four near-miss judge-calibration cases
  with a hand-written, pinned `explanation` (see the amendment below). Tier 2's
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
case in this set. **The perfect scores are real, not an artifact of the judge-parser
bug described below**: that bug only ever raised on a verdict with `faithful: false`
(a benign-case verdict without violations always includes every field, since there's
nothing extra to explain), so it could not have silently converted a real violation
into a passing case — the failure mode was a crash, not a false positive. Re-run
after the parser fix (below) with a fresh baseline write: still `1.0000` across all
ten cases. The threshold is a tolerance band, not an expectation of future perfection:
CI (`python -m claims_pipeline.evals --check-baseline`) fails only if a future run's
aggregate score drops more than `0.05` below `1.0`, i.e. below `0.95`. This is a
starting judgment call, not a proof that `0.05` is the correct sensitivity — it
should be revisited once real prompt or model changes produce a non-perfect run to
calibrate against.

### Amendment (Session 5, same day): judge parser bug

The first version of `judge.py` parsed the verdict by requiring all four keys
(`faithful`, `score`, `violations`, `reasoning`) to be present, or raising a generic
`ValueError`. In production this crashed intermittently — and specifically on the
case that matters most: a real judge response of `{"faithful": false, "score": 0.4,
"violations": ["Unsupported causal claim: '...' is not present in grounded_facts"]}`
with no top-level `reasoning` key raised "judge response did not match the expected
verdict shape" instead of returning the (correct) unfaithful verdict. The harness
crashed exactly when the judge caught something — the opposite of what it exists to
prove.

**First choice, not taken: `output_config.format` (structured outputs).** Having the
API enforce the JSON schema server-side is the correct long-term fix — it removes
free-form parsing (and this bug class) entirely. This repo pins `anthropic==0.75.0` (`pyproject.toml`), and a live call
with `output_config` against that version fails with `TypeError: Messages.create()
got an unexpected keyword argument 'output_config'` — the installed SDK predates the
parameter. The latest available release at the time of this session is `0.117.0`;
the exact minimum version that adds `output_config` support wasn't pinned down, only
that `0.75.0` doesn't have it. Bumping a pinned dependency mid-session, for a repo whose AGENTS.md
states "Dependencies pinned. Reproducible builds are a requirement, not a
preference," is a decision for a human to make, not a drive-by fix bundled into a bug
report. **Flagging for approval:** bump `anthropic` to a version supporting
`output_config` (`client.messages.parse()` / `output_config.format`) and wire
`judge.py`'s already-defined `_VERDICT_SCHEMA` through it, which would make this
entire bug class server-side-impossible rather than client-side-tolerated.

**What shipped instead:** `judge.py`'s `_parse_verdict` now treats `reasoning` as
optional (defaults to `""`) — it is explanatory only, not part of the verdict signal
(`faithful`/`score`/`violations` are still required and still raise, now as a
distinct `JudgeParseError(ValueError)` rather than a bare `ValueError`, so a genuine
parse failure is distinguishable in logs from a flaky-looking generic error). JSON
extraction is also hardened (`_extract_json_object`) to tolerate a response wrapped
in prose or a markdown code fence, in case the model doesn't return a bare object.
`MAX_TOKENS` was raised 512 → 1024 so a verdict with several long `violations`
entries has room to complete. Regression coverage added to
`tests/evals/test_judge_stubbed.py`: the exact missing-`reasoning` shape from the bug
report, a rich verdict with nested single/double quotes inside `violations` and
`reasoning`, and a markdown-fenced response. Re-verified against the live judge:
`test_meta_eval_live.py` + `test_runner_live.py` run three times in a row, all
passing — the intermittent crash did not recur.

### Amendment (Session 5, same day): near-miss calibration cases

A `1.0` baseline with a `0.05` threshold is only meaningful if the judge has been
pressure-tested on inputs that are genuinely faithful but subtle enough that a
reasonable judge *could* score them below a perfect 1.0 — otherwise the ten original
cases may simply have been unambiguous, and the first realistic production
explanation that hedges, rounds, or phrases tersely would fail CI as a false
"regression" the moment it appears.

**Four near-miss cases added** to `benchmark/faithfulness.eval.jsonl`
(`near-miss-defensible-rounding`, `near-miss-implicit-derived-gap`,
`near-miss-terse-phrasing`, `near-miss-edge-of-support-characterization`), each
genuinely faithful but exercising a different way a faithful explanation can look
risky: two-decimal rounding of a four-decimal score ("about 0.67" for 0.6667), a
score gap stated as a number that's only implicit in `grounded_facts` (requires
subtraction, not a literal lookup), deliberately terse/choppy phrasing that violates
the explanation model's own "2-4 sentences of plain prose" style guidance without
introducing any ungrounded content, and a qualitative characterization ("essentially
tied") of a very small (0.0003-0.0004) score gap.

These required a runner change: `run_eval_set` (`src/claims_pipeline/evals/runner.py`)
now accepts an optional `explanation` field per eval-set case. When present, that
exact string is judged directly and `generate_explanation` is never called
(`explanation_source: "fixed"` in the report, vs. `"generated"` for the other ten
cases). This is necessary because live generation can't be reliably steered into a
specific rounding or phrasing choice — the near-miss cases need to control the
explanation, not just the `grounded_facts`, to test the judge's sensitivity rather
than the explanation model's.

**Result: all four near-miss cases scored a perfect 1.0**, judged correctly as
faithful, across four separate live runs against the full 14-case set (`Aggregate
faithfulness score: 1.0000`, `Faithful: 14 / 14` on every run) plus two further live
runs of the meta-eval and runner smoke test. The judge did not penalize reasonable
rounding, correct-but-derived arithmetic, terse non-prose phrasing, or qualitative
framing of a near-zero score gap. **The `1.0` baseline is now empirically justified,
not merely provisional** — this is the strongest evidence available that the ten
original cases weren't simply too easy, short of finding real production cases the
judge scores below 1.0. The baseline value and `0.05` threshold are unchanged;
`benchmark/reports/faithfulness_baseline.json` was refreshed only to reflect the new
`eval_set_size: 14` (score and threshold identical). **Caveat, for the record:**
four hand-written cases from one session's brainstorm are not exhaustive — this
establishes the judge tolerates *these specific* faithful-but-subtle patterns, not
that no faithful-but-subtle pattern exists that would score lower. The right response
to a future case that legitimately scores in the 0.85-0.95 range is not to panic the
gate, but to add it to the near-miss set and revisit the threshold with real data, per
the improvement loop below.

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
