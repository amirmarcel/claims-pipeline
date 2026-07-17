variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Prefix applied to resource names/tags this module creates."
  type        = string
  default     = "claims-pipeline"
}

# FLAGGED (Session 6, ADR-0013): this module assumes an EKS cluster already
# exists and takes its name as input, rather than provisioning one. A
# from-scratch cluster module (VPC, subnets, NAT gateways, the control
# plane, node groups) is a large, separately-reviewable piece of
# infrastructure with its own cost and failure modes -- and since this
# module is not applied this session, building it now would be unverifiable
# guesswork layered on unverifiable guesswork. Recommendation: write the
# cluster module as its own Terraform root the session this actually gets
# applied, so it can be planned and reviewed against a real account.
variable "eks_cluster_name" {
  description = "Name of the existing EKS cluster this module attaches to (IRSA roles, security groups)."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID the existing EKS cluster runs in (used for the RDS security group)."
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs (from the existing VPC) for the RDS subnet group."
  type        = list(string)
}

# FLAGGED (Session 6, ADR-0013): RDS for Postgres vs. in-cluster Postgres
# (StatefulSet + PVC), see rds.tf's header comment for the full tradeoff.
# This variable defaults RDS on; set false to skip it and run Postgres
# in-cluster instead (that path is documented, not built as Terraform --
# it would be a Kubernetes manifest, not a cloud resource).
variable "use_rds" {
  description = "Provision RDS for Postgres. If false, Postgres is expected to run in-cluster (documented in infra/eks/README.md, not provisioned by this module)."
  type        = bool
  default     = true
}

variable "rds_instance_class" {
  description = "RDS instance class for the claims_pipeline database."
  type        = string
  default     = "db.t4g.micro"
}

variable "rds_allocated_storage_gb" {
  description = "RDS allocated storage in GB."
  type        = number
  default     = 20
}

variable "rds_master_username" {
  description = "RDS master username. The master password is NOT a Terraform variable -- see rds.tf (manage_master_user_password)."
  type        = string
  default     = "claims"
}

variable "environment" {
  description = "Environment tag applied to all resources."
  type        = string
  default     = "production"
}
