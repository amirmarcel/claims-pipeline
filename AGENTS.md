# AGENTS.md

Conventions for building this repository. The durable deliverable here is the
specification, the contracts, the tests, and the quality gates. Implementation is
written *against* those artifacts — if code and `docs/SPEC.md` disagree, the spec is
right and the code is a bug.

## Division of labor

- **Contracts, decision records, and the eval plan** are authored and reviewed by a
  human. They change deliberately, with an ADR when the change is architectural.
- **Implementation** (workers, API, infrastructure-as-code, tests) is written against
  those contracts. An agent may generate it; a human conducts and reviews. No
  implementation lands without passing the quality gate below.

## Language and tooling

- **Python** for workers and the API (ADR-0006). Type hints required; code is checked
  under a type checker.
- **pytest** for tests. **ruff** for lint and format.
- **Infrastructure as code** for all cloud resources — no console-clicked resources.
  Local parity via LocalStack + docker-compose; the same topology on EKS.
- **Dependencies** pinned. Reproducible builds are a requirement, not a preference.

## Conventions that are not negotiable

1. **The scoring function stays deterministic and pure.** It takes claim data in and
   returns numbers out. No I/O, no clock, no randomness, no model call. This is what
   makes it testable against the golden dataset.
2. **The language model never decides order.** It receives grounded facts and
   produces prose. If a change would let the model influence the ranking, it is
   wrong. See ADR-0003.
3. **Consumers are idempotent on `claim_id`.** Every consumer must tolerate
   redelivery and replay without changing aggregates. See ADR-0007.
4. **Nothing is dropped silently.** Invalid or unprocessable messages are
   dead-lettered with a structured reason, never swallowed.
5. **No real protected health information, ever.** All data is synthetic. See
   ADR-0005.

## Quality gate

Before a branch is pushed, a local gate runs **test, lint, build, and an automated
review pass**. Only a branch that passes opens a pull request. CI (`.github/workflows/ci.yml`)
runs `ruff check`, `mypy`, and `pytest` against real Postgres and LocalStack service
containers -- not a docker build and not the automated review pass, both of which are
local-gate-only steps -- plus the eval tiers (`docs/EVAL_PLAN.md`). Merge to `main` is
a squash-merge; the feature branch is deleted after merge.

Tier 1 and Tier 2 checks block merge unconditionally. Tier 3 (faithfulness) blocks
only on regression below the committed baseline.

## Where to look

- `docs/SPEC.md` — the contracts. Start here.
- `docs/EVAL_PLAN.md` — how correctness is proven and where the model boundary is
  measured.
- `docs/adr/` — why the architecture is shaped the way it is.
- `benchmark/golden.seed.jsonl` — the hand-verified test oracle.
