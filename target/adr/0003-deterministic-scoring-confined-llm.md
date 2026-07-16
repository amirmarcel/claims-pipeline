# 0003 — Deterministic scoring; language model confined to explanation

**Status:** Accepted

## Context

The system ranks providers, and it can also explain a ranking in natural language.
There are two very different jobs here: *computing* the rank, and *describing* it. A
language model is good at the second and unaccountable at the first — its output is
not reproducible, not directly testable against expected values, and can be steered
by the content it is fed.

If the model computes or influences the score, the ranking becomes non-reproducible
and unverifiable, and any data-borne instruction in a claim field could in principle
move a provider up or down.

## Decision

The score and the ranking are computed by a deterministic, pure function of claim
data (`SPEC.md` §3). The language model is used only at `GET
/providers/{id}/explanation`, where it receives a fixed set of grounded facts (the
already-computed score, sub-signals, rank, and neighbor figures) and produces prose
describing them. It has no access to the scoring path and cannot change an order.

## Consequences

The ranking is reproducible and testable against a golden dataset (it is a pure
function, so its tests are exact assertions, not evals). The model's contribution is
isolated to a boundary where its failure mode is "unfaithful description," which is
measurable (`EVAL_PLAN.md` Tier 2–3) and contained. The cost is that the explanation
cannot introduce insight beyond the grounded facts — which is the point: it explains
the computation, it does not perform it.
