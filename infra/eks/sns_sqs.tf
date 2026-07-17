# Mirrors infra/local/provision.sh exactly: same resource names, same
# topology (topic -> validation-q, two DLQs), same maxReceiveCount (ADR-0010).
# Names are dot-free (SPEC.md §2) -- already true here since they match the
# LocalStack names verbatim.

locals {
  max_receive_count = 3 # ADR-0010

  # infra/local/provision.sh uses VisibilityTimeout=2 to keep redrive-path
  # tests fast, and comments that it's "not a production value". 30s (the
  # SQS default) is used here instead -- the one deliberate topology value
  # this module does NOT mirror byte-for-byte from provision.sh, and why.
  visibility_timeout_seconds = 30
}

resource "aws_sns_topic" "claims_raw" {
  name = "claims-raw"
}

resource "aws_sqs_queue" "validation_dlq" {
  name = "validation-dlq"
}

resource "aws_sqs_queue" "scoring_dlq" {
  name = "scoring-dlq"
}

resource "aws_sqs_queue" "validation_q" {
  name                       = "validation-q"
  visibility_timeout_seconds = local.visibility_timeout_seconds

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.validation_dlq.arn
    maxReceiveCount     = local.max_receive_count
  })
}

resource "aws_sqs_queue" "scoring_q" {
  name                       = "scoring-q"
  visibility_timeout_seconds = local.visibility_timeout_seconds

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.scoring_dlq.arn
    maxReceiveCount     = local.max_receive_count
  })
}

# Allows the SNS topic to deliver to validation-q -- same shape as the
# access policy provision.sh sets via `aws sqs set-queue-attributes`.
data "aws_iam_policy_document" "validation_q_sns_delivery" {
  statement {
    sid     = "AllowSnsFanOut"
    effect  = "Allow"
    actions = ["sqs:SendMessage"]

    principals {
      type        = "Service"
      identifiers = ["sns.amazonaws.com"]
    }

    resources = [aws_sqs_queue.validation_q.arn]

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_sns_topic.claims_raw.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "validation_q" {
  queue_url = aws_sqs_queue.validation_q.id
  policy    = data.aws_iam_policy_document.validation_q_sns_delivery.json
}

# RawMessageDelivery=true -- consumers receive the published JSON body
# directly, without an SNS envelope wrapper (matches provision.sh).
resource "aws_sns_topic_subscription" "validation_q" {
  topic_arn            = aws_sns_topic.claims_raw.arn
  protocol             = "sqs"
  endpoint             = aws_sqs_queue.validation_q.arn
  raw_message_delivery = true
}
