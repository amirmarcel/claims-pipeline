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
| D21 | INFO | `infra/pulumi/`'s IRSA trust policies and OIDC provider are unverifiable in this session's LocalStack config for two independent reasons, not one: `iam` and `sts` are both `"disabled"` (confirmed via the health check), and separately, `pulumi preview` on a brand-new stack makes no network call to the provider at all regardless of reachability (confirmed by pointing the endpoint at an unreachable port and getting an identical plan). Real verification needs a live OIDC issuer, STS, and either an enabled LocalStack IAM or a real AWS account. | ADR-0015 axis 3, lesson 1; `infra/pulumi/README.md` | Only if a real apply, or a LocalStack config with iam/sts enabled, is justified |
| D22 | INFO | Whether `mypy src` should extend to `infra/pulumi/` is an open decision, deliberately left undecided rather than defaulted either way. | ADR-0015 | Next time `infra/pulumi/` or the mypy config is touched |
| D23 | INFO | `infra/pulumi/` and `infra/eks/` describe the same SNS/SQS + IRSA topology in two languages with no generation step between them; a topology change (new queue, changed IAM statement) must be hand-applied to both. | ADR-0015 | Next topology change to either program |
| D24 | LOW | `AGENTS.md` is internally inconsistent about the test command: "Verification loop" says `python -m pytest -q`, "no-mistakes gate" says `pytest -q -k "not live"` two sections later. The unfiltered form runs the live-Anthropic tests, which fail with a 401 rather than skipping when a stale or invalid `ANTHROPIC_API_KEY` is in the shell — caused real confusion during the ADR-0015 session. | `AGENTS.md` | Next `AGENTS.md` edit |
| D25 | MED | Infra-backed tests error rather than skip when AWS credentials are absent. Observed across three runs in the ADR-0015 session: no `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` → 4 tests (`test_integration_pipeline`, `test_integration_smoke`, 2× `test_reliability_e2e`) raise `botocore.exceptions.NoCredentialsError`; credentials exported + LocalStack up → all 4 pass (91 total); credentials exported + LocalStack down → all 4 skip cleanly (76 passed, 15 skipped). `AGENTS.md`'s "one-directional and accepted" claim about the gate's test-count variance holds only on the reachability axis, not the credential axis. | Observed during the ADR-0015 session | Any gate or CI environment change, or a gate run from a shell without AWS_* exports |

---

## Open decisions for a human

- **Should `HANDOFF.md` and the debt register be referenced from `README.md`?** A
  pointer helps a reader; it also puts "here is what is unfinished" on the front page.
  Both defensible.
- **Does the per-session narrative in `README.md` § Status continue, or retire?** It
  is rewritten prose, so it drifts; an append-only log does not. Retiring it in favor
  of this file plus the ADRs is the cleaner shape, but it loses a readable history.

---

## Recent milestones

Newest first. One entry per completed slice; append, do not reorder.

- Ported `infra/eks/sns_sqs.tf` and `infra/eks/iam.tf` (not `rds.tf`) to a Pulumi Python program (`infra/pulumi/`), coexisting with the Terraform module. `pulumi preview` ran cleanly against a LocalStack-configured provider (17 resources, zero errors), but a follow-up check (pointing the endpoint at an unreachable port) found preview makes no network calls at all on a brand-new stack — reshaping the verification claims into three explicit tiers (`infra/pulumi/README.md`, ADR-0015) rather than the reachability-based split originally assumed. Opened D21 (IRSA/OIDC unverifiable here, for two independent reasons), D22 (mypy-scope decision deferred), and D23 (no generation step keeps the two IaC programs in sync by hand).
- Configured the `no-mistakes` push gate (`.no-mistakes.yaml`: test, lint, document, and path-scoped review rules) and closed D17. Cost: opened D19 (unbounded dependency floors) and D20 (hardcoded machine-specific interpreter path), and raised D18 to MED since an unenforced gate now reads as a checked push when it may not be.
- Replaced `AGENTS.md` with an expanded contributor contract (hard rules, scope guardrails, verification loop, git/no-mistakes workflow) and introduced `HANDOFF.md` as the mutable state/debt register it references.
