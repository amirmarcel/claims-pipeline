# HANDOFF

Current state, active priority, and open debt. Read this at the start of a work
session; update it when a milestone completes (`AGENTS.md` § Handoff maintenance).

This file is **mutable state**. It deliberately does not restate architecture, design
rationale, or what the system is — `README.md`, `docs/SPEC.md`, and `docs/adr/` own
those and a second copy would drift.

---

## Where the project stands

The pipeline is built and proven end-to-end on a real local Kubernetes cluster:
SNS→SQS fan-out, validation and scoring workers idempotent on `claim_id`, Postgres
persistence, SQS-native redrive with a replay CLI, a FastAPI ranking API, and a
confined explanation endpoint with three tiers of test/eval coverage. KEDA scales both
workers on real queue depth, validated by one captured load-test run
(`benchmark/reports/session7_load_test_report.md`). The EKS Terraform module
(`infra/eks/`) validates and formats cleanly and has never been applied.

`README.md` § Status has the detail. **Note:** README currently describes the project
as closed and feature-complete. That is no longer accurate and is tracked as D01.

---

## Active next priority

**Retest the scoring-throughput hypothesis from the load test (D04).**

The Session 7 run found that scoring throughput did not scale linearly with replica
count — brief drops to single digits/s with five replicas running. The stated cause is
`pg_advisory_xact_lock` contention: with a provider pool in the tens, concurrent
replicas land on the same provider often enough that the lock, not queue depth, caps
throughput.

That is a hypothesis measured once, not a confirmed cause. Re-run the same load test
with a materially larger `provider_pool_size`, same seed discipline, same monitoring,
and compare. Confirming or refuting it are both results. Scope it tight: no code
change unless the retest justifies one.

Blocked on nothing. Prerequisite chores: D01 (README accuracy) and this file existing.

---

## Debt and known gaps

Severity: **HIGH** blocks correctness or credibility · **MED** should be addressed
before the area is extended · **LOW** worth fixing when convenient · **INFO**
deliberate limitation, documented so it is not rediscovered as a surprise.

| ID | Sev | Item | Source | Trigger / when |
|---|---|---|---|---|
| D01 | HIGH | `README.md` describes the project as final and feature-complete. Untrue once work continues; a present-tense overclaim in exactly the class this repo's labeling discipline exists to prevent. | `README.md` § Status | Immediately |
| D02 | MED | CI does not run the docker build or the automated review pass — both are local-gate-only. A `Dockerfile` that no longer builds reaches `main` without failing a check. | `AGENTS.md`, `ci.yml` | Next CI change |
| D03 | MED | `queueLength: 50` is not validated against any traffic shape that would discriminate. The Session 7 burst peaked ~176× the target, so nearly any value in 10–200 would have pinned both deployments at `maxReplicaCount`. The mechanism is proven; the number is not tuned. | ADR-0014 | Before treating the value as tuned |
| D04 | MED | Scoring throughput does not scale linearly with replicas under a low-cardinality provider pool. Attributed to per-provider advisory-lock contention; measured once, cause not confirmed. | Load-test report, lesson 3 | Active priority |
| D05 | MED | No schema migration path. `CREATE TABLE IF NOT EXISTS` is sufficient only while the schema does not evolve under live data. | ADR-0009 | First column add, constraint change, or backfill |
| D06 | MED | OpenTelemetry coverage is one standalone script (`benchmark/trace_one_claim.py`), not propagation through `MessageAttributes` in the worker/publisher path. The captured trace is real code, but production instrumentation does not exist. | `README.md` § Scope | Before any observability claim beyond "one traced claim" |
| D07 | LOW | `minReplicaCount: 1` chosen for graph legibility, not because scale-to-zero is wrong for a bursty workload. | ADR-0014 | Alongside D03 |
| D08 | LOW | 30s scale-down stabilization window sized to fit inside one load-test sitting, not against a production noise profile. | ADR-0014 | Alongside D03 |
| D09 | LOW | Tier 3 faithfulness CI job is a no-op until an `ANTHROPIC_API_KEY` secret is wired into the repository. The runner exits 0 with no key by design. | `ci.yml` | When the secret is added |
| D10 | INFO | EKS is never applied. IRSA trust policies against a real OIDC provider and STS, and real SNS/SQS behavior versus LocalStack's emulation, are both unverified. Deliberate and cost-gated. | ADR-0013 | Only if a real apply is justified |
| D11 | INFO | No EKS cluster module (VPC, node groups, control plane) and no Terraform state backend — both deliberately left as their own future roots rather than unapplied guesswork. | ADR-0013, `versions.tf` | At first real apply |
| D12 | INFO | `aws_security_group.rds` ingress is deliberately empty; the cluster node/pod security group is an explicit allow rule added at apply time. | `rds.tf` | At first real apply |
| D13 | INFO | `infra/k8s/` uses one shared ServiceAccount; EKS needs one per workload to match the IRSA role-per-ServiceAccount model. Documented as a manual duplication step. | `infra/eks/README.md` | At first real apply |
| D14 | INFO | LocalStack does not fan a burst from SNS to SQS instantaneously — `validation-q` depth kept climbing for roughly a minute after all publishes were acknowledged. Emulator behavior, not real AWS behavior. | Load-test report, lesson 2; ADR-0008 | Only resolvable against real AWS |
| D15 | INFO | Any standalone consumer (`receive_message` from a script) races the deployed workers for messages. Pausing the `ScaledObject` is required, not just scaling the Deployment to 0. Documented footgun. | `infra/k8s/README.md` §9, §11 | N/A — operational note |
| D16 | INFO | `replicas: 1` in the worker Deployments is a pre-KEDA starting value and is not authoritative once a `ScaledObject`'s HPA reconciles. | `10-validation-worker.yaml` | N/A — operational note |
| D17 | ~~MED~~ | **Closed.** `.no-mistakes.yaml` written and merged; the gate runs `pytest -k "not live"`, `ruff`, `mypy`, and the `infra/eks` Terraform checks. Commands are read from `main`. Interpreter paths are absolute and machine-specific — see D20. | — | Closed |
| D18 | MED | `main` has no GitHub branch protection configured (`gh api .../branches/main/protection` → 404). `origin` currently accepts a direct push; the branch-first / PR discipline in `AGENTS.md` is convention only, not enforced by GitHub. Now that the no-mistakes gate is wired (D17), an unenforced gate is a weaker position than no gate at all — it invites the assumption that a push was checked when `origin` still accepts one that bypassed it entirely. | Confirmed via `gh api` check | Whenever repo settings are next touched |
| D19 | MED | Dependency floors are unbounded (`mypy>=1.11` resolved to 2.3.0 on a fresh install). `AGENTS.md` states dependencies are pinned and reproducible builds are a requirement; they are not. A major-version bump in a type checker can fail CI with no commit to blame. | `pyproject.toml`; observed during venv rebuild | Next dependency change, or first unexplained CI failure |
| D20 | MED | `.no-mistakes.yaml` hardcodes an absolute path to a machine-specific `.venv`. The gate is unusable from any other checkout or machine without editing a committed file. | `.no-mistakes.yaml` | If the repo is cloned elsewhere, or a second contributor appears |

---

## Open decisions for a human

- **Should D17 (wiring the no-mistakes gate) be pulled ahead of D04?** D17 changes
  how every future push happens; D04 is the higher-value technical result. Neither
  blocks the other — flagging the ordering choice rather than deciding it here.
- **Should `HANDOFF.md` and the debt register be referenced from `README.md`?** A
  pointer helps a reader; it also puts "here is what is unfinished" on the front page.
  Both defensible.
- **Does the per-session narrative in `README.md` § Status continue, or retire?** It
  is rewritten prose, so it drifts; an append-only log does not. Retiring it in favor
  of this file plus the ADRs is the cleaner shape, but it loses a readable history.

---

## Recent milestones

Newest first. One entry per completed slice; append, do not reorder.

- Configured the `no-mistakes` push gate (`.no-mistakes.yaml`: test, lint, document, and path-scoped review rules) and closed D17. Cost: opened D19 (unbounded dependency floors) and D20 (hardcoded machine-specific interpreter path), and raised D18 to MED since an unenforced gate now reads as a checked push when it may not be.
- Replaced `AGENTS.md` with an expanded contributor contract (hard rules, scope guardrails, verification loop, git/no-mistakes workflow) and introduced `HANDOFF.md` as the mutable state/debt register it references.
