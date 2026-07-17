terraform {
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  # FLAGGED (Session 6, ADR-0013): local state, deliberately. This module is
  # a reviewable cloud-deployment artifact, not an applied stack -- no state
  # file will exist to protect. A real `terraform apply` against this module
  # needs an S3 + DynamoDB (or Terraform Cloud) backend before the first
  # apply, so state is shared and locked. Left commented rather than wired
  # up because the bucket/table/lock-table names are themselves resources
  # someone has to provision and own first; adding them now, unapplied,
  # would just be more unverifiable guesswork.
  #
  # backend "s3" {
  #   bucket         = "REPLACE-ME-claims-pipeline-tfstate"
  #   key            = "eks/terraform.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "REPLACE-ME-claims-pipeline-tflock"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.aws_region
}
