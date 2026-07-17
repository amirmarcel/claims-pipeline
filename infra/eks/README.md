# EKS (cloud-deployment artifact -- NOT applied this session)

This module documents and codifies the AWS path for the same system running
locally on kind (`infra/k8s/`): SNS/SQS/DLQs mirroring
`infra/local/provision.sh` exactly, least-privilege IRSA IAM, and RDS for
Postgres. See ADR-0013 for the kind-for-local / EKS-as-artifact strategy and
the three scoping decisions flagged below.

**This is reviewable, not deployed.** `terraform validate` and
`terraform fmt -check` pass; `terraform apply` was not run against a live
AWS account this session. Running EKS control planes at ~$73/month isn't
justified for a build with no production traffic -- see ADR-0013.

## What this module assumes vs. provisions

| Concern | This module | Why |
|---|---|---|
| EKS cluster (control plane, node groups, VPC) | **Assumed to exist** -- `var.eks_cluster_name`, `var.vpc_id`, `var.private_subnet_ids` | A from-scratch cluster module is large, separately reviewable infrastructure; building it unapplied is unverifiable guesswork on top of unverifiable guesswork (`versions.tf` / `variables.tf` header comments) |
| SNS topic, SQS queues, DLQs, redrive policies | **Provisioned** (`sns_sqs.tf`) | Mirrors `provision.sh` by name and topology, the thing this artifact most needs to prove |
| IAM (IRSA roles for validation-worker, scoring-worker, api) | **Provisioned** (`iam.tf`) | Least-privilege, scoped per-workload -- the data-security-relevant piece |
| RDS Postgres | **Provisioned by default** (`var.use_rds = true`), in-cluster alternative documented, not built | See `rds.tf` header comment for the tradeoff |
| Terraform state backend | **Documented, not wired up** (`versions.tf`) | Local state for an artifact with no applies to protect; a real first apply needs S3+DynamoDB first |

## Applying this for real (not this session)

1. Have an EKS cluster (`var.eks_cluster_name`) and its VPC/subnet IDs ready.
2. Uncomment and fill in the `backend "s3"` block in `versions.tf`; provision
   the state bucket + lock table first (outside this module, or as its own
   bootstrap root -- state backends can't provision their own backend).
3. `terraform init && terraform plan` and review the plan against the actual
   account. In particular: `aws_security_group.rds`'s ingress rule is
   deliberately empty (see `rds.tf`) -- add an explicit allow rule for the
   cluster's node/pod security group once that ID is known, rather than
   opening a CIDR block.
4. `terraform apply`.
5. Annotate each Kubernetes ServiceAccount with its IRSA role ARN (from
   `terraform output irsa_role_arns`):

   ```yaml
   metadata:
     annotations:
       eks.amazonaws.com/role-arn: arn:aws:iam::<account>:role/claims-pipeline-validation-worker
   ```

   `infra/k8s/02-serviceaccount.yaml` uses a single shared ServiceAccount for
   local kind; EKS wants one ServiceAccount per workload (one per IRSA role)
   -- duplicate that manifest three ways with the annotation above, one per
   role in `irsa_role_arns`.
6. Push the image built by the root `Dockerfile` to ECR instead of
   `kind load docker-image`:

   ```sh
   aws ecr get-login-password --region <region> | docker login --username AWS --password-stdin <account>.dkr.ecr.<region>.amazonaws.com
   docker build -t <account>.dkr.ecr.<region>.amazonaws.com/claims-pipeline:<tag> .
   docker push <account>.dkr.ecr.<region>.amazonaws.com/claims-pipeline:<tag>
   ```

7. Create the DB/Anthropic secret the same way as locally
   (`infra/k8s/02-secret.example.yaml`), with the RDS-managed password read
   from `terraform output rds_master_user_secret_arn` (Secrets Manager, not
   a Terraform variable or state literal -- see `rds.tf`) rather than typed
   by hand.
8. Apply the three per-workload Deployment/Service manifests (image
   reference pointed at ECR, `LOCALSTACK_ENDPOINT_URL` in the ConfigMap
   replaced with real SNS/SQS -- i.e. removed, since the AWS SDK talks to
   the real regional endpoint by default with no override needed. This is
   the entire cloud/local difference: one env var present vs. absent,
   exactly ADR-0008's claim).
9. Verify: pods healthy, `kubectl exec` a one-off publish or run the
   generator from a bastion/CI runner, confirm the ranking API responds.
10. **Manual-verification notes for IAM/IRSA** (only checkable against a
    live cluster+account, not by `terraform validate`):
    - Each pod's `aws sts get-caller-identity` (via `AWS_WEB_IDENTITY_TOKEN_FILE`,
      injected automatically once the ServiceAccount is annotated) should
      show the correct per-workload role ARN, not a shared/node role.
    - `validation-worker`'s role should fail (`AccessDenied`) a
      `sqs:ReceiveMessage` against `scoring-q` -- it has no policy statement
      granting that action on that resource. This is the cheapest way to
      confirm least-privilege actually holds, not just that the policy
      document reads that way.
    - `scoring-worker`'s role should similarly fail any `sqs:SendMessage`
      call -- it never sends anywhere, including to its own DLQ (SQS's
      redrive policy moves messages there, not the worker, ADR-0010).

## Teardown (once applied)

```sh
terraform destroy
```

`rds.tf` sets `deletion_protection = true` and a `final_snapshot_identifier`
-- `terraform destroy` on the RDS instance will fail until deletion
protection is turned off first (`terraform apply -var` or a manual console
step), which is intentional friction against an accidental destroy of
claim data.

## Deferred (Session 7)

KEDA install + `ScaledObject` per worker Deployment, and everything the
load-test/benchmark run needs. Not part of this Terraform module at all --
KEDA is a cluster add-on (Helm chart or its own Terraform), not a change to
the SNS/SQS/IAM/RDS resources here.
