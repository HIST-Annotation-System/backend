# Lets the deploy workflows of the backend and frontend repositories act in this account without stored keys: GitHub
# issues each run a short-lived OIDC token, which AWS exchanges for a role. Only runs on `main` qualify.

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # AWS verifies GitHub's certificate itself; the provider resource still requires a thumbprint.
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

locals {
  # GitHub's numeric ids (https://api.github.com/repos/<organization>/<repository>, field `id`). This organization's
  # tokens name each run's repository as `<organization>@<id>/<repository>@<id>`, which a deleted and re-created
  # namesake cannot match.
  github_organization_id = 323730843
  github_repositories = {
    backend  = { name = "backend", id = 1353986392 }
    frontend = { name = "frontend", id = 1353987027 }
  }
}

data "aws_iam_policy_document" "github_deploy_trust" {
  for_each = local.github_repositories

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Exact matches only (no wildcards): a pattern would also admit look-alike organizations and repositories.
    # The plain `owner/repository` form too, in case the organization switches back to GitHub's default claim.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${var.github_organization}@${local.github_organization_id}/${each.value.name}@${each.value.id}:ref:refs/heads/main",
        "repo:${var.github_organization}/${each.value.name}:ref:refs/heads/main",
      ]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  for_each = local.github_repositories

  name               = "wsi-github-deploy-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.github_deploy_trust[each.key].json
}

# Push the image, then run deploy.sh on the host through SSM and read back how it went.
resource "aws_iam_role_policy" "github_deploy_backend" {
  name = "wsi-deploy-backend"
  role = aws_iam_role.github_deploy["backend"].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:BatchGetImage",
          "ecr:CompleteLayerUpload",
          "ecr:GetDownloadUrlForLayer",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart",
        ]
        Resource = aws_ecr_repository.backend.arn
      },
      {
        Effect = "Allow"
        Action = "ssm:SendCommand"
        Resource = [
          aws_instance.backend.arn,
          "arn:aws:ssm:${var.aws_region}::document/AWS-RunShellScript",
        ]
      },
      {
        Effect   = "Allow"
        Action   = "ssm:GetCommandInvocation"
        Resource = "*"
      },
    ]
  })
}

# Upload the build and refresh the CDN.
resource "aws_iam_role_policy" "github_deploy_frontend" {
  name = "wsi-deploy-frontend"
  role = aws_iam_role.github_deploy["frontend"].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = aws_s3_bucket.frontend.arn
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:DeleteObject"]
        Resource = "${aws_s3_bucket.frontend.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = "cloudfront:CreateInvalidation"
        Resource = aws_cloudfront_distribution.app.arn
      },
    ]
  })
}
