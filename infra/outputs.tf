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
    POSTGRES_SERVER=localhost
    POSTGRES_PORT=15432
    POSTGRES_USER=${var.db_username}
    POSTGRES_PASSWORD=${var.db_password}
    POSTGRES_DB=${var.db_name}
  EOT
}

# Hand each developer their own pair, out of band. Read with: terraform output -json dev_credentials
output "dev_credentials" {
  description = "AWS access key per developer, for `aws configure`."
  sensitive   = true
  value = {
    for name, key in aws_iam_access_key.dev : name => {
      access_key_id     = key.id
      secret_access_key = key.secret
    }
  }
}

# Keep it running in a spare terminal while the backend runs: it forwards localhost:15432 to RDS.
output "tunnel_command" {
  description = "Opens the DB tunnel on localhost:15432."
  value       = "aws ssm start-session --region ${var.aws_region} --target ${aws_instance.tunnel.id} --document-name AWS-StartPortForwardingSessionToRemoteHost --parameters host=${aws_db_instance.postgres.address},portNumber=${aws_db_instance.postgres.port},localPortNumber=15432"
}
