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

variable "admin_username" {
  description = "The application's administrator account, created on a fresh database by the first deploy."
  type        = string
  default     = "admin"
}

variable "admin_password" {
  description = "Its password. Only used when the account does not exist yet; change it in the app afterwards."
  type        = string
  sensitive   = true
}

variable "github_organization" {
  description = "GitHub organization holding the backend and frontend repositories whose main branch deploys."
  type        = string
  default     = "HIST-Annotation-System"
}

variable "grafana_cloud" {
  description = <<-EOT
    Where the host's alloy service pushes metrics and logs: the Prometheus and Loki push URLs and users of a Grafana
    Cloud stack (grafana.com > stack > Prometheus / Loki > Details) and an access policy token with metrics:write and
    logs:write (see perf/README.md).
  EOT
  type = object({
    prometheus_url  = string
    prometheus_user = string
    loki_url        = string
    loki_user       = string
    token           = string
  })
  sensitive = true
}
