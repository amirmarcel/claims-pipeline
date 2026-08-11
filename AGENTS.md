# AGENTS.md

Read `docs/SPEC.md` before any task. It is the contract the implementation is built
against, and it is authoritative over code — if code and the spec disagree, the spec
is right and the code is a bug. Read `HANDOFF.md` for current state and the active
priority. Read the relevant ADR in `docs/adr/` before touching anything it decided.

The durable deliverable here is the specification, the contracts, the tests, and the
quality gates. Implementation is written *against* those artifacts.

## Division of labor

- **Contracts, decision records, and the eval plan** are authored and reviewed by a
  human. They change deliberately, with an ADR when the change is architectural.
- **Implementation** (workers, API, infrastructure-as-code, tests) is written against
  those contracts. An agent may generate it; a human conducts and reviews. No
  implementation lands without passing the quality gate below.

## Language and tooling

- **Python** for workers and the API (ADR-0006). Type hints required; code is checked
  under a type checker.
- **pytest** for tests. **ruff** for lint and format. **mypy** for types.
- **Infrastructure as code** for all cloud resources — no console-clicked resources.
  Local parity via LocalStack + docker-compose; the same topology on kind and EKS.
- **Dependencies** pinned. Reproducible builds are a requirement, not a preference.

## Hard rules

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
5. **No real protected health information, ever.** All data is synthetic, in every
   environment. See ADR-0005.
6. **No secrets in the repository.** No real credentials, API keys, ARNs, account
   IDs, or cluster endpoints — not in code, not in manifests, not in test fixtures.
   `infra/k8s/02-secret.example.yaml` documents the *shape*; the real secret is
   created imperatively (`kubectl create secret`, `infra/k8s/README.md` §5).
   Placeholders in committed files must be obviously placeholders.
7. **No cloud provisioning.** Terraform in `infra/eks/` is validated and formatted,
   never planned against a live account and never applied. Do not run `terraform
   plan` or `terraform apply`. Do not create AWS resources. See ADR-0013 for why —
   this is a cost decision, not an oversight, and reversing it is a human's call.
8. **Everything runs locally.** LocalStack, Postgres, and a `kind` cluster. If a task
   cannot be demonstrated on a laptop with no cloud account, it is out of scope for
   now — say so rather than reaching for a real account.

## Scope guardrails

Out of scope. Do not build these, and do not suggest them:

- **A golden path, self-service surface, or paved-road layer.** This repository is a
  workload, not a platform. It has one application and no second consumer.
- **Additional processing lanes** (enrichment, audit, analytics taps). Two stages,
  validate and score. The SNS topic is the proven seam; a third subscriber is
  additive whenever one is actually needed, not before (ADR-0002).
- **A log-based broker** (Kafka, Kinesis). Rejected explicitly in ADR-0002 against a
  requirement profile this system does not have. Adopting one is a reversal recorded
  as a new ADR, not a stretch of the existing one.
- **A PHI detection or de-identification component.** Synthetic data sidesteps it
  entirely; building it turns the project into a graded NLP problem (ADR-0005).
- **Multi-region, multi-cluster, or federation.** Single cluster, single region.
- **Orchestration or transformation tooling, streaming analytics.** Downstream of a
  correct pipeline, not part of proving one.

If a task seems to require something on this list, stop and say so rather than
expanding scope.

## Validation and tests

- **The golden seed is the oracle.** `benchmark/golden.seed.jsonl` is hand-verified.
  A scoring change that requires editing it is a spec change first (`docs/SPEC.md`
  §3), not a test fix.
- **Every validation rule in SPEC.md §1 has a test, including the failure case.** A
  rule with no failing-input test is not enforced.
- **Reliability behavior is asserted, not assumed.** Dead-lettering, redrive, replay,
  and idempotency under duplicate `claim_id` are tests (`docs/EVAL_PLAN.md` Tier 1).
- **The model boundary is measured, not asserted.** Tier 2 guardrails run keylessly
  against a committed fixture; Tier 3 faithfulness is an eval with a baseline
  (ADR-0011, ADR-0012).
- **No test code forks on environment.** The suite runs unmodified against host
  processes, kind, or CI by pointing `LOCALSTACK_ENDPOINT_URL` /
  `CLAIMS_PIPELINE_DATABASE_URL` at the right endpoints. That is ADR-0008's whole
  payoff — a branch on environment inside a test discards it.
- Infra-backed tests **skip cleanly** when LocalStack/Postgres are unreachable. Keep
  it that way: it is what would let a future local gate run the suite in a
  disposable worktree (see the no-mistakes note below).

## Review discipline

Applies to every slice, before it is considered complete:

- **Grep the diff for anything that breaks determinism in the scoring path** — no
  clock, no randomness, no I/O, no model client reachable from `scoring.py` or the
  ranking router. Hard rules 1 and 2 are the ones most worth guarding on every pass.
- **Confirm idempotency survives the change**, including under concurrent replicas —
  the per-provider advisory lock is load-bearing, not decorative (ADR-0007).
- **Regression tests must be discriminating.** Revert the fix, watch the test fail,
  restore, watch it pass. Especially for anything touching a guardrail or the
  golden seed.
- **Any new failure path dead-letters with structured context** (hard rule 4).
- **Any number that reaches a document is measured**, with the conditions it was
  measured under stated in the same place. Never round, never estimate, never
  present a kind run as anything other than a kind run.

## Verification loop

Run all of these locally before considering a task complete:

```sh
python -m ruff check .
python -m mypy src
python -m pytest -q
terraform -chdir=infra/eks init -backend=false
terraform -chdir=infra/eks validate
terraform -chdir=infra/eks fmt -check -recursive
```

`terraform validate` from the repository root silently no-ops — there are no `.tf`
files there. `-chdir=infra/eks` is required, and `init -backend=false` must run first
in a fresh checkout or `validate` fails on uninitialized providers.

## Quality gate

There is currently **no automated local gate** enforcing the Verification Loop above
— it is discipline, not a tool-enforced blocker, until the no-mistakes gate (below)
is actually configured. Run it yourself before every push.

**CI** (`.github/workflows/ci.yml`) runs `ruff check`, `mypy`, and `pytest` against
real Postgres and LocalStack service containers, plus the eval tiers
(`docs/EVAL_PLAN.md`). CI does **not** run a docker build or a review pass — a broken
`Dockerfile` will not fail CI (tracked in `HANDOFF.md`).

Tier 1 and Tier 2 checks block merge unconditionally. Tier 3 (faithfulness) blocks
only on regression below the committed baseline.

## Git and push workflow

- **Always branch first.** `git checkout -b <type>/<name>` (`feat/`, `fix/`, `docs/`,
  `refactor/`-style prefixes). Never edit or commit on `main`.
- **The human runs every git command** — add, commit, push — after reviewing the
  diff. Commit authority does not go to the agent.
- Commits follow conventional prefixes: `feat:`, `fix:`, `docs:`, `refactor:`,
  `test:`, `ci:`, `chore:`.
- **Push target: `origin`, for now.** See the no-mistakes note directly below before
  assuming otherwise.
- `main` currently has **no GitHub branch protection** — `origin` will accept a
  direct push. That is a gap, not a feature: treat the branch-first / PR discipline
  above as binding regardless of what GitHub will technically allow.
- Open a PR manually (`gh pr create`) once a branch is reviewed and pushed to
  `origin`.
- Merge to `main` is a squash-merge; the branch is deleted after merge.
- **Show each command before running it.**

### The no-mistakes gate (not yet active)

A `no-mistakes` remote exists on this repo (`git remote -v`) and the tool is
installed, matching the workflow already in use on `switchyard`. **It is not
functional here yet:** there is no `.no-mistakes.yaml`, so the gate has no
repository-specific test, lint, or Terraform configuration to run. Do not push to
the `no-mistakes` remote until that file exists and has been reviewed — an
unconfigured gate is not a working one. Wiring it up (including deciding what runs
in its disposable worktree, given this repo's infra-backed tests self-skip without
LocalStack/Postgres) is tracked as debt in `HANDOFF.md`, not assumed to already be
done.

## Decision records

- Anything architectural gets an ADR in `docs/adr/`, in the existing numbered style
  and voice: status, the context that forced a choice, the decision, the consequences
  accepted.
- An ADR is **immutable once accepted**. A later decision that reverses it is a new
  ADR that supersedes it (ADR-0000).
- Next free number: **0015**.

## Handoff maintenance

On completing a milestone or task, update `HANDOFF.md`: mark completed items, add any
newly introduced technical debt with its severity and trigger, and set the active next
priority. A slice that adds debt without recording it has not finished.

## Repository hygiene

This repository is a self-contained engineering project. It contains no references to
companies, job descriptions, interviews, hiring, or the process by which it was built.
That applies to code, comments, documentation, commit messages, and branch names.

## Where to look

- `docs/SPEC.md` — the contracts. Start here.
- `HANDOFF.md` — current state, active priority, open debt.
- `docs/EVAL_PLAN.md` — how correctness is proven and where the model boundary is
  measured.
- `docs/adr/` — why the architecture is shaped the way it is.
- `benchmark/golden.seed.jsonl` — the hand-verified test oracle.
