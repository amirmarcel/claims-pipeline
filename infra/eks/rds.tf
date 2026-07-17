# FLAGGED (Session 6, ADR-0013): RDS for Postgres vs. in-cluster Postgres.
#
# Chosen: RDS (var.use_rds = true by default). The scoring worker's
# correctness rests on `pg_advisory_xact_lock` + a real transactional upsert
# (ADR-0007, ADR-0009) -- durability and backup are load-bearing, not
# incidental, and claim data (patient_ref is PHI-shaped, ADR-0005) belongs on
# a component with automated backups, point-in-time recovery, and encryption
# at rest managed outside the cluster's own lifecycle. An in-cluster
# StatefulSet+PVC alternative is real and cheaper, but ties the database's
# durability to the same cluster whose pods/nodes churn for autoscaling
# (ADR-0004/Session 7) -- an operational coupling this system doesn't need
# to accept for the cost difference. Recommendation: keep RDS.
#
# storage_encrypted + manage_master_user_password (RDS-managed, rotated
# Secrets Manager secret, never a Terraform variable or state-file value) --
# no static DB password enters this module at all.

resource "aws_db_subnet_group" "claims_pipeline" {
  count      = var.use_rds ? 1 : 0
  name       = "${var.name_prefix}-postgres"
  subnet_ids = var.private_subnet_ids
}

resource "aws_security_group" "rds" {
  count       = var.use_rds ? 1 : 0
  name        = "${var.name_prefix}-rds"
  description = "Postgres access for the claims-pipeline scoring worker and API"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # Ingress deliberately left empty here: least-privilege means the cluster
  # node/pod security group is added as an explicit allow rule at apply
  # time (once the real node security group ID is known), not opened to a
  # CIDR block. See infra/eks/README.md.
}

resource "aws_db_instance" "claims_pipeline" {
  count      = var.use_rds ? 1 : 0
  identifier = "${var.name_prefix}-postgres"

  engine         = "postgres"
  engine_version = "16"
  instance_class = var.rds_instance_class

  allocated_storage           = var.rds_allocated_storage_gb
  storage_encrypted           = true
  db_name                     = "claims_pipeline"
  username                    = var.rds_master_username
  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.claims_pipeline[0].name
  vpc_security_group_ids = [aws_security_group.rds[0].id]

  backup_retention_period   = 7
  skip_final_snapshot       = false
  final_snapshot_identifier = "${var.name_prefix}-postgres-final"
  deletion_protection       = true

  tags = {
    Environment = var.environment
  }
}
