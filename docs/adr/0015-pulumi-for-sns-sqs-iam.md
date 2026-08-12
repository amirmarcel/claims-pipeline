# 0015 — Pulumi port of SNS/SQS + IRSA IAM, alongside Terraform

**Status:** _(unset — human sets on review)_

## Context

`infra/eks/` (Terraform, ADR-0013) is a reviewable, unapplied cloud-deployment
artifact: SNS/SQS topology, least-privilege IRSA IAM, and RDS. A second,
smaller program (`infra/pulumi/`) ports the SNS/SQS topology (`sns_sqs.tf`)
and the IRSA IAM (`iam.tf`) — not RDS — to Pulumi (Python), at the same
unapplied posture: `pulumi preview` may run against LocalStack or with no
configured credentials (AGENTS.md hard rule 7, amended to say so
explicitly); `pulumi up` against a real account does not run, same as
`terraform apply` never runs against `infra/eks/`.

The two programs coexist. Neither replaces the other. This ADR covers why a
second infrastructure-as-code tool was introduced for the same topology, and
five axes where the two programs' answers to the same problem diverge.

## Decision

Build `infra/pulumi/__main__.py` covering the SNS/SQS topology and the three
IRSA roles, deliberately scoped smaller than `infra/eks/` (no RDS, no
cluster-existence variables beyond what IRSA's trust policy needs).

### 1. ComponentResource / typed abstraction vs. Terraform's HCL declarative model

Terraform's `sns_sqs.tf` writes the queue+DLQ+redrive-policy pattern out
twice, by hand, for `validation-q` and `scoring-q` — HCL has no
function-like abstraction for "a resource plus a related resource," only
`count`/`for_each` over otherwise-identical resource blocks, which doesn't
fit two resources that reference each other's attributes non-uniformly. The
Pulumi program factors that repetition into a `QueueWithDLQ`
`pulumi.ComponentResource`, instantiated twice. This is a real difference in
what each tool's model makes cheap: Pulumi trades HCL's flat declarative
transparency (every resource visible at the top level of the file) for a
general-purpose language's abstraction facilities (a class, reused). Neither
is strictly better — the Terraform version is arguably easier to review at a
glance for exactly two instances; the Pulumi version would stay flat rather
than grow if a third queue-with-DLQ appeared.

### 2. State ownership and backend bootstrap — the same problem, reproduced

`infra/eks/versions.tf` documents local Terraform state as a placeholder,
flagged because a real first apply needs an S3+DynamoDB (or Terraform Cloud)
backend for shared, locked state — and provisioning that backend is itself
infrastructure someone has to own first, so it's left commented rather than
wired up unapplied.

`infra/pulumi/` uses `pulumi login file://...` (a local, file-based backend)
specifically because AGENTS.md hard rule 8 requires everything to run with
no cloud account — Pulumi's default is a hosted backend (app.pulumi.com),
which would violate that rule outright, not just be inconvenient. So the
local backend isn't a stand-in for "the real backend, later" the way
`infra/eks/`'s commented S3 block is — it's the intended target for this
repo's constraints. The irony: it reproduces the exact bootstrap problem
`versions.tf` names for Terraform, just single-machine instead of
shared/locked. A second contributor, or this contributor on a second
machine, has no shared state to work from — `infra/pulumi/.pulumi-state/`
is gitignored, same reasoning as Terraform's `*.tfstate`. Neither program
solves multi-contributor state in this session; Pulumi's version of the
problem is scoped down to "single machine" rather than "nonexistent,"
which is a smaller version of the same gap, not a different one.

### 3. Secret handling / encryption in state

Terraform state is plaintext by default — anything that flows through a
resource's attributes (including a password, if one were ever passed as a
plain variable) is readable in the state file. `rds.tf` sidesteps this
entirely for the one place it would matter: `manage_master_user_password`
means the RDS master password is AWS-generated and stored in Secrets
Manager, never entering a Terraform variable, plan, or state file at all —
avoiding the plaintext-state problem by never letting the secret reach
Terraform's state in the first place, rather than by encrypting the state.

Pulumi's local backend encrypts values marked as `pulumi.Secret` (or config
set with `--secret`) using a passphrase-derived key
(`PULUMI_CONFIG_PASSPHRASE`) by default — state-level encryption Terraform's
default backend doesn't have. This program doesn't currently need it: no
value here is a real secret (`oidcIssuerUrl` and `oidcThumbprint` are
identifiers, not credentials), so this axis is currently moot in practice,
but is a real capability difference that would matter the moment this
program grew a genuine secret — unlike `rds.tf`'s dodge, Pulumi's answer is
"encrypt it in state," not "never let it reach state."

### 4. Reviewability and the verification bar

Terraform's bar here is `terraform validate` + `terraform fmt -check`
(`infra/eks/README.md`), both fully static — no provider contacted, ever,
for either check.

Pulumi's equivalent bar is `pulumi preview`, and it is **not** the same kind
of check, in a way this session found out empirically rather than assumed.
`pulumi preview` was run against a LocalStack-configured provider and
produced a clean plan (17 resources to create, zero errors) — but a
follow-up test (pointing the configured endpoint at an unreachable port and
re-running preview) produced a byte-for-byte identical plan. On a brand-new
stack with no prior resource state, `pulumi preview`'s `Create` path never
calls the provider's API at all — it is exactly as static as `terraform
validate`, just via a different mechanism (dry-run resource providers vs. a
config-only parser), not the live-plan-against-a-provider check the name
"preview" suggests. See `infra/pulumi/README.md`'s verification table for
the three-tier split this produced:

1. **Plan-graph verified**: all 17 resources, proven only to be a
   well-formed, type-checked plan — not proven reachable, per the finding
   above.
2. **Inspected statically, not emulator-verified**: the IAM policy
   documents' JSON content, reviewable by reading, not confirmed
   submittable to any IAM service.
3. **Reviewed only, requires a real AWS environment**: the OIDC provider
   resource and the IRSA trust policies' `StringEquals` conditions — blocked
   for two independent reasons in this LocalStack configuration (`iam` and
   `sts` are both `"disabled"` per the health check, not just IAM), and
   doubly moot given finding (1) — even an enabled IAM service wouldn't have
   been exercised by preview on a fresh stack.

### 5. OIDC issuer as configuration, not a live lookup

`iam.tf` calls `data "aws_eks_cluster" "this"` to read the cluster's OIDC
issuer URL live, then `data "tls_certificate"` to fetch that issuer's TLS
thumbprint over a real network connection — both are live lookups against
infrastructure `infra/eks/`'s own README already documents as "assumed to
exist, not provisioned" (its own flagged scoping decision, ADR-0013 axis 1).
Terraform can express this cleanly because a live cluster+region is exactly
what a real `terraform plan` would run against, once one exists.

`infra/pulumi/__main__.py` takes `oidcIssuerUrl` as a required Pulumi
config value instead (`pulumi config set oidcIssuerUrl ...`), and
`oidcThumbprint` similarly (defaulting to an obviously-placeholder 40 zero
hex string). This is **not parity** with the Terraform version — it is a
deliberate boundary chosen so this program can be previewed with zero live
dependencies, not even a reachable OIDC issuer over HTTPS, which the
Terraform version's `tls_certificate` data source would require even before
touching IAM/STS at all. The cost is that this program cannot express "look
up whatever cluster's issuer is currently live" the way `iam.tf` can; the
benefit is that `pulumi preview` never has a live-lookup failure mode to
diagnose in a LocalStack-only session. Should this program ever grow a live
lookup for parity with `iam.tf`, that is a reversal of this axis and belongs
in a new ADR, not a silent edit here.

## Lessons learned

1. **`pulumi preview` on a brand-new stack makes no network calls to the
   configured provider, for any resource, including ones targeting a
   disabled/unreachable service.** Verified by pointing
   `localstackEndpoint` at `http://localhost:1` (nothing listening) and
   observing an identical plan to the one against the real LocalStack
   endpoint. This is the single fact that reshaped this ADR's axis 4 and
   `infra/pulumi/README.md`'s verification table from a two-way
   SNS/SQS-vs-IAM split (mirroring LocalStack's service availability) into
   a three-tier split where tier 1 covers all resources equally and
   explicitly disclaims reachability. Anyone reaching for `pulumi preview`
   as a live-connectivity check against LocalStack (or any endpoint) on a
   stack with no existing resources should not expect it to behave like
   one.

## Consequences

`infra/pulumi/` and `infra/eks/` now describe the same SNS/SQS + IRSA
topology in two languages, reviewed independently, applied by neither. Any
future topology change (a new queue, a changed IAM statement) must be made
in both, by hand — there is no generation step from one to the other, and
none is proposed here. The mypy-scope question (`mypy src` does not cover
`infra/pulumi/`) is left open, tracked in `HANDOFF.md` as new debt rather
than decided silently either way.
