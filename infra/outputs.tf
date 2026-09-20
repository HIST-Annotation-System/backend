# The infra-derived half of backend/.env.local, ready to paste (the auth/admin settings are not
# provisioned here). Sensitive as a whole, so read it with: terraform output -raw env_local
output "env_local" {
  description = "The BUCKET_*/POSTGRES_* block for backend/.env.local."
  sensitive   = true
  value       = <<-EOT
    BUCKET_ENDPOINT=https://s3.${var.aws_region}.amazonaws.com
    BUCKET_ACCESS_KEY=${aws_iam_access_key.app.id}
    BUCKET_SECRET_KEY=${aws_iam_access_key.app.secret}
    BUCKET_NAME=${aws_s3_bucket.slides.id}
    BUCKET_REGION=${var.aws_region}
    POSTGRES_SERVER=${aws_db_instance.postgres.address}
    POSTGRES_PORT=${aws_db_instance.postgres.port}
    POSTGRES_USER=${var.db_username}
    POSTGRES_PASSWORD=${var.db_password}
    POSTGRES_DB=${var.db_name}
  EOT
}
