# IRSA (IAM Roles for Service Accounts): each workload gets its own role,
# scoped to only the queue actions it actually performs, trust-bound to only
# its own Kubernetes ServiceAccount -- no node-wide credentials, no static
# keys (AGENTS.md: least-privilege IAM, data-security concern). The
# ServiceAccount this attaches to is infra/k8s/02-serviceaccount.yaml's
# `claims-pipeline` name; on EKS each Deployment would get its own
# ServiceAccount (one per role below) rather than sharing the single one
# infra/k8s/ uses locally, since IRSA is a role-per-ServiceAccount model.

data "aws_eks_cluster" "this" {
  name = var.eks_cluster_name
}

data "tls_certificate" "eks_oidc" {
  url = data.aws_eks_cluster.this.identity[0].oidc[0].issuer
}

resource "aws_iam_openid_connect_provider" "eks" {
  url             = data.aws_eks_cluster.this.identity[0].oidc[0].issuer
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.eks_oidc.certificates[0].sha1_fingerprint]
}

locals {
  oidc_provider_url = replace(data.aws_eks_cluster.this.identity[0].oidc[0].issuer, "https://", "")
}

# --- validation-worker ---
# Reads validation-q, forwards valid claims to scoring-q, dead-letters
# business-invalid claims to validation-dlq itself (SPEC.md §2 step 3).
# No permissions on scoring-dlq or the SNS topic -- it neither publishes nor
# consumes either.

data "aws_iam_policy_document" "validation_worker_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.eks.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_provider_url}:sub"
      values   = ["system:serviceaccount:claims-pipeline:validation-worker"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "validation_worker" {
  name               = "${var.name_prefix}-validation-worker"
  assume_role_policy = data.aws_iam_policy_document.validation_worker_trust.json
}

data "aws_iam_policy_document" "validation_worker_policy" {
  statement {
    sid    = "ConsumeValidationQueue"
    effect = "Allow"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:GetQueueUrl",
    ]
    resources = [aws_sqs_queue.validation_q.arn]
  }

  statement {
    sid       = "ForwardValidClaimsToScoring"
    effect    = "Allow"
    actions   = ["sqs:SendMessage", "sqs:GetQueueUrl"]
    resources = [aws_sqs_queue.scoring_q.arn]
  }

  statement {
    sid       = "DeadLetterBusinessInvalidClaims"
    effect    = "Allow"
    actions   = ["sqs:SendMessage", "sqs:GetQueueUrl"]
    resources = [aws_sqs_queue.validation_dlq.arn]
  }
}

resource "aws_iam_role_policy" "validation_worker" {
  name   = "${var.name_prefix}-validation-worker"
  role   = aws_iam_role.validation_worker.id
  policy = data.aws_iam_policy_document.validation_worker_policy.json
}

# --- scoring-worker ---
# Reads scoring-q only. Never sends to scoring-dlq itself -- SQS's own
# redrive policy moves poison messages there; no application-level
# sqs:SendMessage to a DLQ exists in this worker (ADR-0010).

data "aws_iam_policy_document" "scoring_worker_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.eks.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_provider_url}:sub"
      values   = ["system:serviceaccount:claims-pipeline:scoring-worker"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scoring_worker" {
  name               = "${var.name_prefix}-scoring-worker"
  assume_role_policy = data.aws_iam_policy_document.scoring_worker_trust.json
}

data "aws_iam_policy_document" "scoring_worker_policy" {
  statement {
    sid    = "ConsumeScoringQueue"
    effect = "Allow"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:GetQueueUrl",
    ]
    resources = [aws_sqs_queue.scoring_q.arn]
  }
}

resource "aws_iam_role_policy" "scoring_worker" {
  name   = "${var.name_prefix}-scoring-worker"
  role   = aws_iam_role.scoring_worker.id
  policy = data.aws_iam_policy_document.scoring_worker_policy.json
}

# --- api ---
# The ranking API touches no AWS resource directly (a pure Postgres read,
# plus the Anthropic API which isn't an AWS service -- SPEC.md §4,
# ADR-0003). It still gets its own IRSA role and ServiceAccount, scoped to
# nothing today, so it has the same identity seam as the workers if a
# future endpoint needs one (e.g. reading a CloudWatch metric) instead of
# reaching for a node-wide credential as the path of least resistance.

data "aws_iam_policy_document" "api_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.eks.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_provider_url}:sub"
      values   = ["system:serviceaccount:claims-pipeline:api"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "api" {
  name               = "${var.name_prefix}-api"
  assume_role_policy = data.aws_iam_policy_document.api_trust.json
}
