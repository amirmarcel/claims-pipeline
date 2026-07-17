#!/usr/bin/env bash
# Idempotent local provisioning: creates the claims-raw SNS topic and the
# validation-q, scoring-q, validation-dlq, and scoring-dlq SQS queues
# (validation-q subscribed to the topic) against LocalStack, plus the redrive
# policies that send repeatedly-failing messages to the matching DLQ
# (ADR-0010). Safe to re-run against an already-provisioned LocalStack (aws
# create-* calls against SNS/SQS are idempotent by name/ARN, and
# set-queue-attributes simply overwrites with the same values).
#
# Names are dot-free (SPEC.md §2): SNS/SQS resource names are restricted to
# letters, numbers, underscores, and hyphens against real AWS.
#
# Infra provisioning, not application code (ADR-0008: local parity via
# LocalStack + docker-compose, same topology as EKS).
set -euo pipefail

ENDPOINT_URL="${LOCALSTACK_ENDPOINT_URL:-http://localhost:4566}"
REGION="${AWS_REGION:-us-east-1}"
TOPIC_NAME="claims-raw"
QUEUE_NAME="validation-q"
SCORING_QUEUE_NAME="scoring-q"
VALIDATION_DLQ_NAME="validation-dlq"
SCORING_DLQ_NAME="scoring-dlq"
# ADR-0010: three receives is enough to absorb a transient blip without
# holding a poison message in circulation long, and keeps redrive-path tests
# fast.
MAX_RECEIVE_COUNT=3
# Short on purpose for the local rig: a failed (unacked) message becomes
# visible again quickly, so a redrive-path test only waits a couple of
# seconds per receive instead of the SQS default 30s. Not a production value.
VISIBILITY_TIMEOUT=2

export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-test}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-test}"
export AWS_DEFAULT_REGION="$REGION"

aws() {
  command aws --endpoint-url "$ENDPOINT_URL" --region "$REGION" "$@"
}

topic_arn=$(aws sns create-topic --name "$TOPIC_NAME" --query 'TopicArn' --output text)
queue_url=$(aws sqs create-queue --queue-name "$QUEUE_NAME" --query 'QueueUrl' --output text)
queue_arn=$(aws sqs get-queue-attributes \
  --queue-url "$queue_url" \
  --attribute-names QueueArn \
  --query 'Attributes.QueueArn' --output text)

# scoring-q (validation worker's forwarding target), validation-dlq
# (business-invalid claims, SPEC.md §2 step 3, plus poison messages redriven
# by SQS), and scoring-dlq (scoring worker's poison messages, ADR-0010) are
# plain queues -- none is subscribed to the SNS topic; workers send to them
# directly, or SQS redrives to them per the redrive policies below.
aws sqs create-queue --queue-name "$SCORING_QUEUE_NAME" >/dev/null
aws sqs create-queue --queue-name "$VALIDATION_DLQ_NAME" >/dev/null
aws sqs create-queue --queue-name "$SCORING_DLQ_NAME" >/dev/null

scoring_queue_url=$(aws sqs get-queue-url --queue-name "$SCORING_QUEUE_NAME" --query 'QueueUrl' --output text)
validation_dlq_url=$(aws sqs get-queue-url --queue-name "$VALIDATION_DLQ_NAME" --query 'QueueUrl' --output text)
scoring_dlq_url=$(aws sqs get-queue-url --queue-name "$SCORING_DLQ_NAME" --query 'QueueUrl' --output text)

validation_dlq_arn=$(aws sqs get-queue-attributes \
  --queue-url "$validation_dlq_url" \
  --attribute-names QueueArn \
  --query 'Attributes.QueueArn' --output text)
scoring_dlq_arn=$(aws sqs get-queue-attributes \
  --queue-url "$scoring_dlq_url" \
  --attribute-names QueueArn \
  --query 'Attributes.QueueArn' --output text)

# Redrive policies (ADR-0010): a message that fails processing
# MAX_RECEIVE_COUNT times without being deleted (i.e. the worker never acked
# it) is moved by SQS itself to the matching DLQ. This is the sole retry/
# backoff mechanism -- workers do not implement their own retry loop.
# VisibilityTimeout is set alongside it (see VISIBILITY_TIMEOUT above).
redrive_policy() {
  python3 -c 'import json,sys; print(json.dumps(json.dumps({"deadLetterTargetArn": sys.argv[1], "maxReceiveCount": int(sys.argv[2])})))' "$1" "$2"
}
aws sqs set-queue-attributes \
  --queue-url "$queue_url" \
  --attributes "{\"RedrivePolicy\": $(redrive_policy "$validation_dlq_arn" "$MAX_RECEIVE_COUNT"), \"VisibilityTimeout\": \"$VISIBILITY_TIMEOUT\"}"
aws sqs set-queue-attributes \
  --queue-url "$scoring_queue_url" \
  --attributes "{\"RedrivePolicy\": $(redrive_policy "$scoring_dlq_arn" "$MAX_RECEIVE_COUNT"), \"VisibilityTimeout\": \"$VISIBILITY_TIMEOUT\"}"

# SQS access policy allowing the SNS topic to deliver messages to the queue.
policy=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowSnsFanOut",
      "Effect": "Allow",
      "Principal": {"Service": "sns.amazonaws.com"},
      "Action": "sqs:SendMessage",
      "Resource": "$queue_arn",
      "Condition": {"ArnEquals": {"aws:SourceArn": "$topic_arn"}}
    }
  ]
}
EOF
)
aws sqs set-queue-attributes \
  --queue-url "$queue_url" \
  --attributes "{\"Policy\": $(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$policy")}"

# subscribe is idempotent: re-subscribing the same endpoint to the same
# topic returns the existing subscription rather than creating a duplicate.
# RawMessageDelivery=true so consumers receive the published JSON body
# directly, without an SNS envelope wrapper.
aws sns subscribe \
  --topic-arn "$topic_arn" \
  --protocol sqs \
  --notification-endpoint "$queue_arn" \
  --attributes '{"RawMessageDelivery": "true"}' >/dev/null

echo "provisioned: topic=$topic_arn queue=$queue_url validation_dlq=$validation_dlq_url scoring_dlq=$scoring_dlq_url"
