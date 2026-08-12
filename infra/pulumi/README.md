# Pulumi port of SNS/SQS + IRSA IAM (cloud-deployment artifact — NOT applied)

This program ports `infra/eks/sns_sqs.tf` and `infra/eks/iam.tf` — the SNS/SQS
topology and least-privilege IRSA IAM — to Pulumi (Python). It **coexists**
with `infra/eks/`; it does not replace it, and nothing in `infra/eks/`
changes. `infra/eks/rds.tf` was deliberately not ported — see
`docs/adr/0015-pulumi-for-sns-sqs-iam.md`.

**This is reviewable, not deployed.** `pulumi preview` was run against a
LocalStack-configured provider (see Verification below); `pulumi up` was not
run against LocalStack or any real AWS account this session. AGENTS.md hard
rule 7 permits `pulumi preview` against LocalStack or with no configured
credentials; it does not permit `pulumi up` against a real account, and
running EKS control planes at ~$73/month isn't justified for a build with no
production traffic — see ADR-0013.

## What this program assumes vs. provisions

Same posture as `infra/eks/README.md`'s table, for the slice this program covers:

| Concern | This program | Why |
|---|---|---|
| EKS cluster / OIDC issuer | **Assumed** — `oidcIssuerUrl` is a required Pulumi config value, not a live `aws.eks.get_cluster` lookup | Deliberate boundary vs. the Terraform version, which does look the cluster up live. See ADR-0015 axis 4. |
| OIDC provider thumbprint | **Assumed** — `oidcThumbprint` config value, defaults to a 40-zero placeholder | The Terraform version derives this from a live TLS handshake against the issuer (`data "tls_certificate"`); reproducing that here would only ever fail against a placeholder issuer URL, so it's config too. Replace with the real thumbprint before any real apply. |
| SNS topic, SQS queues, DLQs, redrive policies | **Ported** (`__main__.py`) | Mirrors `sns_sqs.tf` by name and topology |
| IAM (IRSA roles for validation-worker, scoring-worker, api) | **Ported** (`__main__.py`) | Least-privilege, scoped per-workload, same as `iam.tf` |
| RDS Postgres | **Not ported** | Out of scope for this slice — see ADR-0015 |
| State backend | **Local file backend** (`pulumi login`), gitignored | See "Local backend" below |

## Structure

A single `__main__.py` rather than a package: the program is short enough
(topology + three IAM roles) that splitting it into modules would add
indirection without buying reviewability — the Terraform version's own
file split (`sns_sqs.tf` / `iam.tf`) already draws that same line as
comments, not as separate importable units.

`QueueWithDLQ` is a `pulumi.ComponentResource` used for both `validation-q`
and `scoring-q`, wrapping the queue + DLQ + redrive-policy pattern
`sns_sqs.tf` writes out twice by hand.

## Provider: LocalStack vs. real AWS

A single `aws.Provider` is config-switched:

- **Unconfigured (default): real AWS.** Uses the provider's normal
  credential chain, exactly like `infra/eks/`. This program does not "only
  know how to talk to an emulator" — LocalStack is an opt-in preview
  affordance, not the default target.
- **`pulumi config set useLocalstack true`: LocalStack.** Endpoint overrides
  for `sns`/`sqs`/`iam`/`sts` (default `http://localhost:4566`, overridable
  via `localstackEndpoint`), dummy `test`/`test` credentials,
  `skip_credentials_validation`, `skip_requesting_account_id` — matching
  `infra/local/provision.sh`'s conventions.

## Config

```sh
pulumi config set oidcIssuerUrl https://oidc.eks.<region>.amazonaws.com/id/<id>
pulumi config set oidcThumbprint <40-hex-char-thumbprint>   # optional, real apply only
pulumi config set awsRegion us-east-1                        # optional, defaults us-east-1
pulumi config set namePrefix claims-pipeline                 # optional, defaults claims-pipeline
pulumi config set useLocalstack true                          # optional, defaults false (real AWS)
pulumi config set localstackEndpoint http://localhost:4566   # optional, defaults shown
```

## Local backend

`pulumi login file://./.pulumi-state` (run from `infra/pulumi/`) — a
file-based backend rooted inside this directory, not the default
`~/.pulumi`, so state stays co-located with the checkout and is easy to
find or wipe. Required by AGENTS.md hard rule 8 (everything runs locally,
no cloud account) instead of Pulumi's default hosted backend.

**Gitignored** (added to the repo's `.gitignore`):

- `infra/pulumi/.pulumi-state/` — the backend directory itself (stack
  state, checkpoints).
- `infra/pulumi/Pulumi.*.yaml` — per-stack config files. These can hold
  plaintext values (`oidcIssuerUrl` is not secret) but are excluded
  wholesale rather than trusting every future config value added to stay
  non-secret; a stack config file with a real value in it also isn't
  something to commit under hard rule 6 regardless of encryption.
- `infra/pulumi/.venv/` — already covered by the repo's existing blanket
  `.venv/` ignore rule.

**`PULUMI_CONFIG_PASSPHRASE`**: the local backend's default secrets
provider encrypts stack secrets with a passphrase-derived key. That
passphrase is supplied only via the `PULUMI_CONFIG_PASSPHRASE` (or
`PULUMI_CONFIG_PASSPHRASE_FILE`) environment variable, set by whoever runs
`pulumi` commands against this stack — never hardcoded in any script or
committed file in this repo.

## Verification — three tiers, plus one fact preview doesn't establish

Every claim below is placed in exactly one tier. None is rounded up.

| Tier | Covers | What it proves | What it does NOT prove |
|---|---|---|---|
| **1. Plan-graph verified** | All 17 resources: SNS topic, 4 SQS queues, 2 redrive policies, delivery policy + subscription, OIDC provider, 3 IAM roles + role policies | `pulumi preview` ran in this session and produced a complete, type-checked plan (`+ 17 to create`, zero errors) for every resource in the program | **Reachability of anything.** Confirmed by pointing `localstackEndpoint` at an unreachable port (`http://localhost:1`) and re-running preview: the plan was byte-for-byte identical. On a brand-new stack with no prior state, `pulumi preview`'s `Create` path is entirely client-side — it makes no network call to the provider at all. Preview succeeding here is evidence the program is well-formed, not evidence LocalStack (or any endpoint) was actually contacted. See ADR-0015's lessons-learned. |
| **2. Inspected statically, not emulator-verified** | The IAM policy documents' JSON content (trust policies, permission policies) — e.g. that `scoring-worker` has no `sqs:SendMessage` statement anywhere | Reviewable by reading `__main__.py` and the JSON each `Output.apply` builds | That Pulumi can actually submit these documents to an IAM service — LocalStack's `iam` is disabled in this environment (see tier below), and tier 1 already establishes preview wouldn't have exercised that path even if it were enabled |
| **3. Reviewed only, requires a real AWS environment** | The OIDC provider resource and the IRSA trust policies' `StringEquals` conditions against a real OIDC issuer and STS | Read-reviewed for correctness against `iam.tf`'s equivalent | Anything at runtime — explicitly blocked from any preview *or* real check in this session, for two independent reasons: `iam` and `sts` are both disabled in this LocalStack configuration (not just IAM), and even if enabled, tier 1's finding means preview alone still wouldn't exercise them |

**LocalStack service availability (independent of preview)** — not itself a
verification tier, included only because it's why tier 3 is blocked for two
reasons rather than one: the health check (`curl
http://localhost:4566/_localstack/health`) at the start of this session
showed `sns` and `sqs` as `"available"`, and `iam` and `sts` as both
`"disabled"`. Tier 1's finding means this distinction was never actually
exercised by `pulumi preview` — no resource in this program, SNS/SQS or
IAM, triggered a network call during preview of a brand-new stack.

## Running preview yourself

```sh
cd infra/pulumi
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pulumi login file://./.pulumi-state
pulumi stack init dev   # or: pulumi stack select dev
pulumi config set oidcIssuerUrl https://oidc.eks.us-east-1.amazonaws.com/id/EXAMPLEID
pulumi config set useLocalstack true
pulumi preview
```

`PULUMI_CONFIG_PASSPHRASE` must be set in the environment first (see Local
backend above).
