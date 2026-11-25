output "app_url" {
  description = "The deployed app (frontend and /api/v1)."
  value       = "https://${aws_cloudfront_distribution.app.domain_name}"
}

# Repository variables (Settings > Secrets and variables > Actions > Variables) for the two deploy workflows.
# Not secret: they only name resources, which only the OIDC roles may use.
output "github_variables" {
  description = "Variables to set in the backend and frontend repositories."
  value = {
    backend = {
      AWS_ROLE_ARN   = aws_iam_role.github_deploy["backend"].arn
      ECR_REPOSITORY = aws_ecr_repository.backend.name
      INSTANCE_ID    = aws_instance.backend.id
    }
    frontend = {
      AWS_ROLE_ARN    = aws_iam_role.github_deploy["frontend"].arn
      FRONTEND_BUCKET = aws_s3_bucket.frontend.id
      DISTRIBUTION_ID = aws_cloudfront_distribution.app.id
    }
  }
}

# A root shell on the backend host, e.g. for `docker compose -f docker-compose.prod.yml -p wsi logs` in /opt/wsi.
output "shell_command" {
  description = "Opens a shell on the backend host through SSM (needs the Session Manager plugin)."
  value       = "aws ssm start-session --region ${var.aws_region} --target ${aws_instance.backend.id}"
}

# ml-worker/.env.aws: the settings of the worker that serves the deployed app. Read with: terraform output -raw ml_worker_env
output "ml_worker_env" {
  description = "The broker and bucket settings for ml-worker/.env.aws."
  sensitive   = true
  value       = <<-EOT
    CELERY_BROKER_URL=sqs://
    CELERY_BROKER_TRANSPORT_OPTIONS=${local.sqs_transport_options}
    AWS_ACCESS_KEY_ID=${aws_iam_access_key.ml_worker.id}
    AWS_SECRET_ACCESS_KEY=${aws_iam_access_key.ml_worker.secret}
    BUCKET_ENDPOINT=https://s3.${var.aws_region}.amazonaws.com
    BUCKET_ACCESS_KEY=${aws_iam_access_key.ml_worker.id}
    BUCKET_SECRET_KEY=${aws_iam_access_key.ml_worker.secret}
    BUCKET_NAME=${aws_s3_bucket.slides.id}
    BUCKET_REGION=${var.aws_region}
  EOT
}
