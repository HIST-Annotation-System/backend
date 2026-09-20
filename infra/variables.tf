# The organization SCP (o-zrf8rfaelb) denies every region except eu-north-1, so this is not a
# preference: changing it makes every ec2/rds/s3 call fail with UnauthorizedOperation.
variable "aws_region" {
  description = "AWS region for all resources. Only eu-north-1 is permitted by the org SCP."
  type        = string
  default     = "eu-north-1"
}

variable "db_name" {
  description = "Name of the PostgreSQL database."
  type        = string
  default     = "wsi"
}

variable "db_username" {
  description = "Master username for the RDS instance."
  type        = string
  default     = "wsi"
}

variable "db_password" {
  description = "Master password for the RDS instance."
  type        = string
  sensitive   = true

  # RDS itself rejects / " @ and spaces, and # / ? additionally break the DSN that
  # app/core/config.py builds with MultiHostUrl.build (it raises ValidationError instead of escaping).
  validation {
    condition     = length(var.db_password) >= 16 && !can(regex("[/\"@#? ]", var.db_password))
    error_message = "db_password must be at least 16 characters and contain none of / \" @ # ? or space."
  }
}

variable "bucket_name" {
  description = "Globally unique name for the S3 bucket."
  type        = string
}

# No default on purpose: the instance is publicly accessible, so an unset value must not silently
# open PostgreSQL to the internet.
variable "allowed_cidrs" {
  description = "CIDR blocks allowed to connect to the database: one /32 per developer (curl ifconfig.me)."
  type        = list(string)

  # An empty list would build a security group with no ingress rule at all, locking everyone out
  # with the same silent timeout as a wrong address.
  validation {
    condition     = length(var.allowed_cidrs) > 0
    error_message = "allowed_cidrs must list at least one CIDR block."
  }
}
