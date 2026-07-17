# 0013 — kind for local Kubernetes validation; EKS as a reviewable, unapplied artifact

**Status:** Accepted

## Context

The system needs to prove it runs on Kubernetes (ADR-0008's final layer: LocalStack +
host processes → real K8s → EKS) and needs an EKS deployment path recorded as
infrastructure-as-code (AGENTS.md: no console-clicked resources). Running an actual
EKS control plane costs ~\$73/month with no production traffic behind it, which isn't
justified for a build with no live account or users.

Separately, three scoping questions came up while writing the EKS Terraform: whether
to provision a cluster from scratch, whether Postgres should be RDS or in-cluster, and
what to do about Terraform state when nothing gets applied.

## Decision

**Local validation runs on a real Kubernetes cluster via kind** (Kubernetes-in-Docker),
at zero cost — genuine pods, Services, Deployments, `kubectl`, not a simulation.
LocalStack and Postgres (already running as host-level Docker containers per
`infra/local/`) are attached to the kind cluster's Docker network and wired into the
cluster's DNS (`infra/k8s/connect-local-services.sh`), so the full pipeline runs
end-to-end on K8s pods talking to the same emulated AWS/Postgres Sessions 1-5 already
validated against.

**EKS is written as Terraform (`infra/eks/`) and reviewed, not applied.** It documents
the AWS path this session's kind deployment would take in production: the same
SNS/SQS/DLQ topology (`sns_sqs.tf`, byte-for-byte matching `provision.sh`'s names), and
least-privilege IRSA IAM per workload (`iam.tf`). `terraform validate` and
`terraform fmt -check` are the verification bar for this session; `terraform apply`
against a live account is future work, gated on that cost being justified.

Three scoping decisions inside `infra/eks/`, each flagged in the module's own comments:

1. **Cluster: assumed to exist, not provisioned.** `var.eks_cluster_name` and VPC/subnet
   variables take an existing cluster as input. A from-scratch cluster module (VPC,
   NAT gateways, node groups, control plane) is large, separately reviewable
   infrastructure, and since nothing here is applied this session, building a full
   cluster module now would be unverifiable guesswork layered on unverifiable
   guesswork. Recommendation: write it as its own Terraform root the session this
   actually gets applied, against a real account, so it can be planned and reviewed
   for real.
2. **RDS for Postgres, in-cluster Postgres documented as the alternative, not built.**
   The scoring worker's correctness rests on a real transactional upsert with an
   advisory lock (ADR-0007, ADR-0009), and claim data is PHI-shaped (ADR-0005) —
   durability, encryption at rest, and automated backups belong on infrastructure whose
   lifecycle isn't coupled to the same cluster whose pods/nodes churn under autoscaling
   (ADR-0004, Session 7). RDS costs more than a StatefulSet+PVC; that cost buys
   decoupling the database's durability from the cluster's own scaling behavior, judged
   worth it here.
3. **Terraform state: local, documented as a placeholder.** A real first apply needs an
   S3 + DynamoDB (or Terraform Cloud) backend so state is shared and locked — but the
   bucket/table are themselves resources someone has to provision and own first.
   Wiring up a backend block against infrastructure that doesn't exist yet would be
   more unverifiable guesswork; `versions.tf` documents the exact backend block to
   uncomment once that infrastructure is real.

## Consequences

The kind deployment is the actual proof: pods run the same container image, same
`command`/`args`-switched entrypoints, same environment-variable configuration
(`ConfigMap` for endpoints/region, `Secret` for the DSN and `ANTHROPIC_API_KEY`,
sourced from a documented manual `kubectl create secret` step, never plaintext in a
manifest) that EKS would use — the only difference between this session's kind
deployment and a real EKS deployment is which values those variables hold, per
ADR-0008. The existing integration/reliability test suite runs unmodified against the
kind deployment by pointing `LOCALSTACK_ENDPOINT_URL` / `CLAIMS_PIPELINE_DATABASE_URL`
at the same LocalStack/Postgres containers the pods use (`infra/k8s/README.md` §9) —
no test code forks on environment, which is the whole payoff this session set out to
prove.

The residual risk is exactly the one ADR-0008 already named for LocalStack itself,
one layer further out: kind is not EKS. Two things kind cannot exercise that a real
apply eventually must: whether the IRSA trust policies actually work against a real
OIDC provider and real STS (the EKS README's manual-verification notes describe the
cheapest live checks — a role that can't do what it's not scoped for should get
`AccessDenied`, not silently succeed), and whether the SNS/SQS topology behaves
identically against real AWS rather than LocalStack's emulation (the same residual
LocalStack-fidelity risk ADR-0008 already accepted, now inherited by this Terraform
artifact until it's actually applied).

No application code changed to make Kubernetes work, with one narrow, explicitly
flagged exception: `src/claims_pipeline/workers/__init__.py:touch_heartbeat`, an
unconditional file write on every poll cycle so a liveness probe has a real signal for
a process with no HTTP surface. It does not branch on environment (it runs identically
as a host process or in a pod) and does not touch the config-only-difference boundary
ADR-0008 protects, but it is still new application behavior added to satisfy a K8s
requirement, so it's recorded here rather than silently folded in. Two entrypoint
additions (`workers/validation.py` and `workers/scoring.py` gaining a `__main__` guard
that reads `LOCALSTACK_ENDPOINT_URL`/`AWS_REGION` from the environment) are not
flagged as the same kind of change — they mirror the CLI pattern
`generator/cli.py` and `replay/cli.py` already established, and were a pre-existing
gap (nothing invoked `workers.*.run()` outside of tests) rather than new
container-specific behavior.
