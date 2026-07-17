output "sns_topic_arn" {
  value = aws_sns_topic.claims_raw.arn
}

output "queue_urls" {
  value = {
    validation_q   = aws_sqs_queue.validation_q.id
    scoring_q      = aws_sqs_queue.scoring_q.id
    validation_dlq = aws_sqs_queue.validation_dlq.id
    scoring_dlq    = aws_sqs_queue.scoring_dlq.id
  }
}

output "irsa_role_arns" {
  description = "Annotate each Kubernetes ServiceAccount with eks.amazonaws.com/role-arn = this value (see infra/eks/README.md)."
  value = {
    validation_worker = aws_iam_role.validation_worker.arn
    scoring_worker    = aws_iam_role.scoring_worker.arn
    api               = aws_iam_role.api.arn
  }
}

output "rds_endpoint" {
  value       = var.use_rds ? aws_db_instance.claims_pipeline[0].endpoint : null
  description = "null when var.use_rds = false (in-cluster Postgres path -- see rds.tf)."
}

output "rds_master_user_secret_arn" {
  value       = var.use_rds ? aws_db_instance.claims_pipeline[0].master_user_secret[0].secret_arn : null
  description = "Secrets Manager ARN holding the RDS-managed master password. Never a Terraform variable or plan/state literal."
}
