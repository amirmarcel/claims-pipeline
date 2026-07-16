#!/usr/bin/env bash
# Idempotent local provisioning: creates the claims.raw SNS topic and the
# validation.q SQS queue, subscribed to the topic, against LocalStack.
# Safe to re-run against an already-provisioned LocalStack (aws create-*
# calls against SNS/SQS are idempotent by name/ARN).
#
# Infra provisioning, not application code (ADR-0008: local parity via
# LocalStack + docker-compose, same topology as EKS).
set -euo pipefail

ENDPOINT_URL="${LOCALSTACK_ENDPOINT_URL:-http://localhost:4566}"
REGION="${AWS_REGION:-us-east-1}"
TOPIC_NAME="claims.raw"
QUEUE_NAME="validation.q"

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

echo "provisioned: topic=$topic_arn queue=$queue_url"
