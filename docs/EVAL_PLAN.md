# Evaluation plan

The evaluation harness is the primary artifact of this project. It encodes a single
discipline: **deterministic behavior is verified as tests; the language-model
boundary is measured as evals.** Confusing the two — grading deterministic output
with a judge, or asserting exact text on generated output — is the mistake this plan
exists to prevent.

## The split

| Behavior                                   | Kind  | Why                                                    |
|--------------------------------------------|-------|--------------------------------------------------------|
| Per-claim signal computation               | test  | Pure function. Exact expected values.                  |
| Provider aggregate + ranking order         | test  | Deterministic. Golden dataset with exact assertions.   |
| Idempotency (reprocess → no change)         | test  | Deterministic invariant.                               |
| Dead-letter routing + replay               | test  | Deterministic control flow.                            |
| Explanation groundedness / no invented facts | eval | Generated output; judged, not asserted.                |
| Explanation faithfulness to the numbers    | eval  | Generated output; judged against grounded facts.       |

If a behavior can be checked with `assert actual == expected`, it is a test and it
blocks merge. Only the generated-text boundary is an eval.

## Three tiers

**Tier 1 — Deterministic tests (hard gate).**
The golden dataset in `benchmark/golden.seed.jsonl` maps input claim sets to expected
per-provider scores and expected ranking order. Every case is verified by hand (the
worked example in `SPEC.md` §3 is one of them). These run in CI and block merge on
any failure. Reliability invariants live here too: feed a duplicate `claim_id` and
assert the aggregate is unchanged; feed a poison message and assert it lands in the
dead-letter queue with the right reason; replay it and assert clean processing.

**Tier 2 — Guardrail checks (hard gate, still deterministic).**
The explanation layer has properties that are checkable without a judge: the
explanation must not contain a number absent from `grounded_facts`; it must refuse
or safely ignore injected instructions in claim-derived fields; it must never emit an
empty or truncated explanation as success. These are deterministic assertions over
the model's output and they block merge.

**Tier 3 — Faithfulness evals (report + threshold gate).**
A labeled set of `grounded_facts → acceptable-explanation` cases. A judge scores each
generated explanation for faithfulness: does every quantitative claim match a
grounded fact, and does the explanation avoid introducing unsupported facts? The
harness reports the aggregate faithfulness score; CI gates on a threshold (e.g. it
must not regress below the last committed baseline). Unlike Tiers 1–2, a single case
scoring low is a signal to investigate, not an automatic failure.

## The improvement loop

Evals are run to be acted on, not just recorded:

1. **Run** the golden and faithfulness sets.
2. **Cluster** failures by cause rather than fixing them one by one.
3. **Fix the configuration, not the symptom.** Most explanation failures trace to
   the prompt, the assembled context, or the grounded-fact selection — not to the
   model. Reach for those before anything else. Most *pipeline* failures trace to a
   consumer config (visibility timeout, redrive policy, dedup key), not to logic.
4. **Guard the fix** with a regression case added to the appropriate tier.
5. **Monitor** the aggregate score over time so a quiet regression is visible.

## Golden dataset

Format: JSON Lines, one case per line. Each case is a self-contained set of claim
events plus the expected outcome:

```json
{ "case": "single-provider-mixed-outcomes",
  "claims": [ /* claim events per SPEC §1 */ ],
  "expected": {
    "provider_scores": { "P-001": { "provider_score": 0.70, "cost_efficiency": 0.65, "quality": 0.75, "claim_count": 2 } },
    "ranking": ["P-001"]
  }
}
```

Seed cases (in `benchmark/golden.seed.jsonl`) cover: a single provider with mixed
outcomes, a perfect-score provider, a multi-provider ranking with a score tie broken
by `provider_id`, an invalid claim that must be excluded from scoring, and a
duplicate `claim_id` that must not double-count. The set grows by the improvement
loop: every real failure becomes a case.

## CI gates

- **Tier 1 + Tier 2** run on every push and **block merge** on any failure.
- **Tier 3** runs on every push, prints the faithfulness report, and **blocks** only
  on regression below the committed baseline.
- A local quality gate (test + lint + build + review) runs before a branch is pushed,
  so the same checks fail fast on the developer's machine before CI ever sees them.
