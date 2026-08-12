"""Pulumi port of infra/eks/sns_sqs.tf and infra/eks/iam.tf.

Coexists with infra/eks/ (Terraform) -- neither replaces the other. See
docs/adr/0015-pulumi-for-sns-sqs-iam.md for why this exists alongside the
Terraform module and what the two programs deliberately do not share.

Not applied. AGENTS.md hard rule 7 permits `pulumi preview` against
LocalStack or with no configured credentials; `pulumi up` against a real
AWS account is out of scope for this program, same as infra/eks/.
"""

import json

import pulumi
import pulumi_aws as aws

config = pulumi.Config()

# Deliberately NOT a live `aws.eks.get_cluster` lookup (unlike iam.tf's
# `data "aws_eks_cluster" "this"`). See ADR-0015 axis 4: this program takes
# the OIDC issuer as configuration precisely so it never needs a reachable
# EKS control plane to preview. Set with:
#   pulumi config set oidcIssuerUrl https://oidc.eks.<region>.amazonaws.com/id/<id>
oidc_issuer_url = config.require("oidcIssuerUrl")

# Terraform's iam.tf derives the OIDC provider's thumbprint from a live TLS
# handshake against the issuer (`data "tls_certificate"`). That is itself a
# live network call, not a resource lookup, but it is still a real endpoint
# this program has no way to reach for an issuer URL that (in this session)
# names a cluster that does not exist. Rather than silently reproducing a
# call that would only ever fail here, the thumbprint is also taken as
# configuration. The placeholder below is exactly 40 hex characters (the
# shape SHA-1 thumbprints take) so it type-checks against the resource, and
# is obviously not a real thumbprint -- replace it with the issuing CA's
# actual thumbprint before any real apply.
oidc_thumbprint = config.get("oidcThumbprint") or "0" * 40

aws_region = config.get("awsRegion") or "us-east-1"
name_prefix = config.get("namePrefix") or "claims-pipeline"

# LocalStack is a preview affordance, not the default target. Unconfigured,
# this program talks to real AWS via the provider's normal credential chain
# -- exactly like infra/eks/. Setting `useLocalstack` true switches the
# single `aws.Provider` below to LocalStack's endpoint overrides, matching
# infra/local/provision.sh's conventions (dummy test/test credentials,
# skip_credentials_validation, skip_requesting_account_id).
use_localstack = config.get_bool("useLocalstack") or False

if use_localstack:
    localstack_endpoint = config.get("localstackEndpoint") or "http://localhost:4566"
    provider = aws.Provider(
        "aws-localstack",
        region=aws_region,
        access_key="test",
        secret_key="test",
        skip_credentials_validation=True,
        skip_requesting_account_id=True,
        skip_metadata_api_check=True,
        s3_use_path_style=True,
        endpoints=[
            aws.ProviderEndpointArgs(
                sns=localstack_endpoint,
                sqs=localstack_endpoint,
                iam=localstack_endpoint,
                sts=localstack_endpoint,
            )
        ],
    )
else:
    provider = aws.Provider("aws-real", region=aws_region)

provider_opts = pulumi.ResourceOptions(provider=provider)

# --- SNS / SQS topology (sns_sqs.tf) ---

MAX_RECEIVE_COUNT = 3  # ADR-0010
# 30s (the SQS default), matching sns_sqs.tf -- not provision.sh's 2s,
# which is a fast-test value that sns_sqs.tf's own header comment already
# declines to mirror.
VISIBILITY_TIMEOUT_SECONDS = 30

claims_raw = aws.sns.Topic("claims-raw", name="claims-raw", opts=provider_opts)


class QueueWithDLQ(pulumi.ComponentResource):
    """A queue plus its own dead-letter queue and redrive policy.

    Mirrors the pattern sns_sqs.tf repeats by hand for validation-q and
    scoring-q: a plain DLQ, and a source queue whose redrive_policy points
    at it.
    """

    def __init__(
        self,
        resource_name: str,
        *,
        queue_name: str,
        dlq_name: str,
        max_receive_count: int,
        visibility_timeout_seconds: int,
        provider: aws.Provider,
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("claims-pipeline:queue:QueueWithDLQ", resource_name, None, opts)
        child_opts = pulumi.ResourceOptions(parent=self, provider=provider)

        self.dlq = aws.sqs.Queue(dlq_name, name=dlq_name, opts=child_opts)

        self.queue = aws.sqs.Queue(
            queue_name,
            name=queue_name,
            visibility_timeout_seconds=visibility_timeout_seconds,
            redrive_policy=self.dlq.arn.apply(
                lambda dlq_arn: json.dumps(
                    {
                        "deadLetterTargetArn": dlq_arn,
                        "maxReceiveCount": max_receive_count,
                    }
                )
            ),
            opts=child_opts,
        )

        self.register_outputs({"queue_arn": self.queue.arn, "dlq_arn": self.dlq.arn})


validation = QueueWithDLQ(
    "validation",
    queue_name="validation-q",
    dlq_name="validation-dlq",
    max_receive_count=MAX_RECEIVE_COUNT,
    visibility_timeout_seconds=VISIBILITY_TIMEOUT_SECONDS,
    provider=provider,
)

scoring = QueueWithDLQ(
    "scoring",
    queue_name="scoring-q",
    dlq_name="scoring-dlq",
    max_receive_count=MAX_RECEIVE_COUNT,
    visibility_timeout_seconds=VISIBILITY_TIMEOUT_SECONDS,
    provider=provider,
)

# Allows the SNS topic to deliver to validation-q -- same shape as
# sns_sqs.tf's aws_sqs_queue_policy / provision.sh's set-queue-attributes.
validation_q_sns_delivery_policy = pulumi.Output.all(
    queue_arn=validation.queue.arn, topic_arn=claims_raw.arn
).apply(
    lambda args: json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "AllowSnsFanOut",
                    "Effect": "Allow",
                    "Principal": {"Service": "sns.amazonaws.com"},
                    "Action": "sqs:SendMessage",
                    "Resource": args["queue_arn"],
                    "Condition": {"ArnEquals": {"aws:SourceArn": args["topic_arn"]}},
                }
            ],
        }
    )
)

validation_q_policy = aws.sqs.QueuePolicy(
    "validation-q",
    queue_url=validation.queue.id,
    policy=validation_q_sns_delivery_policy,
    opts=pulumi.ResourceOptions(provider=provider, parent=validation),
)

# RawMessageDelivery=true -- consumers receive the published JSON body
# directly, without an SNS envelope wrapper (matches sns_sqs.tf).
validation_q_subscription = aws.sns.TopicSubscription(
    "validation-q",
    topic=claims_raw.arn,
    protocol="sqs",
    endpoint=validation.queue.arn,
    raw_message_delivery=True,
    opts=pulumi.ResourceOptions(provider=provider, parent=validation),
)

# --- IRSA (iam.tf) ---
#
# Each workload gets its own role, scoped to only the queue actions it
# actually performs, trust-bound to only its own Kubernetes ServiceAccount.
# See iam.tf's header comment for the full reasoning; unchanged here.

oidc_provider = aws.iam.OpenIdConnectProvider(
    "eks",
    url=oidc_issuer_url,
    client_id_lists=["sts.amazonaws.com"],
    thumbprint_lists=[oidc_thumbprint],
    opts=provider_opts,
)

oidc_provider_url = oidc_issuer_url.replace("https://", "")


def irsa_trust_policy(service_account: str) -> pulumi.Output[str]:
    return oidc_provider.arn.apply(
        lambda oidc_arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Federated": oidc_arn},
                        "Action": "sts:AssumeRoleWithWebIdentity",
                        "Condition": {
                            "StringEquals": {
                                f"{oidc_provider_url}:sub": (
                                    f"system:serviceaccount:claims-pipeline:{service_account}"
                                ),
                                f"{oidc_provider_url}:aud": "sts.amazonaws.com",
                            }
                        },
                    }
                ],
            }
        )
    )


# --- validation-worker ---
# Reads validation-q, forwards valid claims to scoring-q, dead-letters
# business-invalid claims to validation-dlq itself. No permissions on
# scoring-dlq or the SNS topic -- it neither publishes nor consumes either.

validation_worker_role = aws.iam.Role(
    "validation-worker",
    name=f"{name_prefix}-validation-worker",
    assume_role_policy=irsa_trust_policy("validation-worker"),
    opts=provider_opts,
)

validation_worker_policy = pulumi.Output.all(
    validation_q_arn=validation.queue.arn,
    scoring_q_arn=scoring.queue.arn,
    validation_dlq_arn=validation.dlq.arn,
).apply(
    lambda args: json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "ConsumeValidationQueue",
                    "Effect": "Allow",
                    "Action": [
                        "sqs:ReceiveMessage",
                        "sqs:DeleteMessage",
                        "sqs:GetQueueAttributes",
                        "sqs:GetQueueUrl",
                    ],
                    "Resource": [args["validation_q_arn"]],
                },
                {
                    "Sid": "ForwardValidClaimsToScoring",
                    "Effect": "Allow",
                    "Action": ["sqs:SendMessage", "sqs:GetQueueUrl"],
                    "Resource": [args["scoring_q_arn"]],
                },
                {
                    "Sid": "DeadLetterBusinessInvalidClaims",
                    "Effect": "Allow",
                    "Action": ["sqs:SendMessage", "sqs:GetQueueUrl"],
                    "Resource": [args["validation_dlq_arn"]],
                },
            ],
        }
    )
)

validation_worker_role_policy = aws.iam.RolePolicy(
    "validation-worker",
    name=f"{name_prefix}-validation-worker",
    role=validation_worker_role.id,
    policy=validation_worker_policy,
    opts=provider_opts,
)

# --- scoring-worker ---
# Reads scoring-q only. Never sends to scoring-dlq itself -- SQS's own
# redrive policy moves poison messages there; no sqs:SendMessage anywhere
# in this role (ADR-0010), including to scoring-q or its own DLQ.

scoring_worker_role = aws.iam.Role(
    "scoring-worker",
    name=f"{name_prefix}-scoring-worker",
    assume_role_policy=irsa_trust_policy("scoring-worker"),
    opts=provider_opts,
)

scoring_worker_policy = scoring.queue.arn.apply(
    lambda scoring_q_arn: json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "ConsumeScoringQueue",
                    "Effect": "Allow",
                    "Action": [
                        "sqs:ReceiveMessage",
                        "sqs:DeleteMessage",
                        "sqs:GetQueueAttributes",
                        "sqs:GetQueueUrl",
                    ],
                    "Resource": [scoring_q_arn],
                }
            ],
        }
    )
)

scoring_worker_role_policy = aws.iam.RolePolicy(
    "scoring-worker",
    name=f"{name_prefix}-scoring-worker",
    role=scoring_worker_role.id,
    policy=scoring_worker_policy,
    opts=provider_opts,
)

# --- api ---
# Touches no AWS resource directly (a pure Postgres read, plus the
# Anthropic API, which isn't an AWS service). Gets its own IRSA role and
# ServiceAccount, scoped to nothing today, so it has the same identity seam
# as the workers if a future endpoint needs one.

api_role = aws.iam.Role(
    "api",
    name=f"{name_prefix}-api",
    assume_role_policy=irsa_trust_policy("api"),
    opts=provider_opts,
)

pulumi.export("sns_topic_arn", claims_raw.arn)
pulumi.export("validation_q_arn", validation.queue.arn)
pulumi.export("validation_dlq_arn", validation.dlq.arn)
pulumi.export("scoring_q_arn", scoring.queue.arn)
pulumi.export("scoring_dlq_arn", scoring.dlq.arn)
pulumi.export("validation_worker_role_arn", validation_worker_role.arn)
pulumi.export("scoring_worker_role_arn", scoring_worker_role.arn)
pulumi.export("api_role_arn", api_role.arn)
