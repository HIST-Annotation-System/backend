# The production backend: one ARM host running docker-compose.prod.yml (API, migrations, segmentation consumer),
# deployed by the backend repository's GitHub workflow through SSM. CloudFront (frontend.tf) is its only way in.

locals {
  # The host and its cache volume must share an availability zone.
  backend_subnet_id = sort(data.aws_subnets.default.ids)[0]
  slide_cache_dir   = "/var/lib/wsi-cache"
}

data "aws_subnet" "backend" {
  id = local.backend_subnet_id
}

data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_security_group" "backend" {
  name_prefix = "wsi-backend-sg-"
  description = "Backend host: the API port, from CloudFront only"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description     = "API, from CloudFront origin-facing servers only"
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    prefix_list_ids = [data.aws_ec2_managed_prefix_list.cloudfront.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_iam_role" "backend" {
  name = "wsi-backend"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

# SSM: the deploy workflow's commands, and `aws ssm start-session` for an admin shell. No SSH, no open ports.
resource "aws_iam_role_policy_attachment" "backend_ssm" {
  role       = aws_iam_role.backend.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "backend_ecr" {
  role       = aws_iam_role.backend.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_iam_role_policy" "backend" {
  name = "wsi-backend"
  role = aws_iam_role.backend.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # deploy.sh writes the app's settings from it. The aws/ssm key's own policy allows decrypting through SSM.
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = aws_ssm_parameter.backend_env.arn
      },
      {
        # The API sends jobs; Celery's SQS transport also reads the queue's attributes.
        Effect   = "Allow"
        Action   = ["sqs:SendMessage", "sqs:GetQueueAttributes", "sqs:GetQueueUrl"]
        Resource = aws_sqs_queue.jobs.arn
      },
      {
        # The consumer takes the worker's results.
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:ChangeMessageVisibility",
          "sqs:GetQueueAttributes",
          "sqs:GetQueueUrl",
        ]
        Resource = aws_sqs_queue.results.arn
      },
    ]
  })
}

resource "aws_iam_instance_profile" "backend" {
  name = "wsi-backend"
  role = aws_iam_role.backend.name
}

# Slides are copied here from S3 for OpenSlide; the app deletes the least recently used ones when it runs short
# (app/services/slide_files.py), measuring this filesystem's free space. Its own volume, so it never fills the root
# disk, and it outlives the instance: a replaced host gets the warm cache back.
resource "aws_ebs_volume" "slide_cache" {
  availability_zone = data.aws_subnet.backend.availability_zone
  size              = 50
  type              = "gp3"
  encrypted         = true

  tags = {
    Name = "wsi-slide-cache"
  }
}

resource "aws_instance" "backend" {
  # The account is on the AWS Free plan, which only launches free-tier-eligible types; t4g.small is the largest
  # one that stays cheap (2 vCPU, 2 GB).
  ami                  = data.aws_ssm_parameter.al2023_arm64.value
  instance_type        = "t4g.small"
  subnet_id            = local.backend_subnet_id
  iam_instance_profile = aws_iam_instance_profile.backend.name

  vpc_security_group_ids = [aws_security_group.backend.id]
  # For the first boot (packages, the SSM agent) before the Elastic IP is attached.
  associate_public_ip_address = true

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
    encrypted   = true
  }

  metadata_options {
    http_tokens = "required"
    # The containers reach the instance role through the metadata service; with IMDSv2 that takes a second hop
    # (container -> docker bridge -> host).
    http_put_response_hop_limit = 2
  }

  user_data = templatefile("${path.module}/backend-init.sh.tftpl", {
    # How the attached volume shows up on a Nitro instance.
    cache_device = "/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_${replace(aws_ebs_volume.slide_cache.id, "-", "")}"
    cache_dir    = local.slide_cache_dir
  })

  # A newer AMI or an edited first-boot script must not replace the host (and change its id in the deploy
  # workflow's INSTANCE_ID); user data only runs on the first boot anyway.
  lifecycle {
    ignore_changes = [ami, user_data]
  }

  tags = {
    Name = "wsi-backend"
  }
}

resource "aws_volume_attachment" "slide_cache" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.slide_cache.id
  instance_id = aws_instance.backend.id
}

# A stable address, so CloudFront's origin (its public DNS name) survives stops and restarts.
resource "aws_eip" "backend" {
  domain   = "vpc"
  instance = aws_instance.backend.id

  tags = {
    Name = "wsi-backend"
  }
}

resource "aws_ecr_repository" "backend" {
  name = "wsi-backend"
  # Mutable: re-running a deploy workflow pushes the same commit's tag again.
  image_tag_mutability = "MUTABLE"
  # Images are rebuilt from git on every deploy; let `terraform destroy` remove them.
  force_delete = true
}

resource "aws_ecr_lifecycle_policy" "backend" {
  repository = aws_ecr_repository.backend.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 10 images, enough to roll back"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}

resource "random_password" "jwt_secret" {
  length  = 48
  special = false
}

# Every setting of the production app, written to backend.env by deploy.sh at each deploy. Changing one here takes
# effect with the next deploy (or by re-running /opt/wsi/deploy.sh with the current image).
resource "aws_ssm_parameter" "backend_env" {
  name = "/wsi/prod/backend-env"
  type = "SecureString"

  value = <<-EOT
    ENVIRONMENT=production
    PROJECT_NAME=Backend API
    API_V1_STR=/api/v1
    LOG_LEVEL=INFO
    # The frontend is served from the same origin (CloudFront), so no cross-origin requests are allowed.
    BACKEND_CORS_ORIGINS=[]
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
    JWT_SECRET=${random_password.jwt_secret.result}
    JWT_ALGORITHM=HS256
    ACCESS_TOKEN_EXPIRE_MINUTES=30
    REFRESH_TOKEN_EXPIRE_DAYS=7
    COOKIE_SECURE=true
    ADMIN_USERNAME=${var.admin_username}
    ADMIN_PASSWORD=${var.admin_password}
    SEGMENTATION_ENABLED=true
    # Celery's SQS transport takes its credentials from the instance role.
    CELERY_BROKER_URL=sqs://
    CELERY_BROKER_TRANSPORT_OPTIONS=${local.sqs_transport_options}
    SLIDE_CACHE_DIR=${local.slide_cache_dir}
  EOT
}
