terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  # Tags every resource, so the whole stack can be found (and billed, and torn down) as one unit.
  default_tags {
    tags = {
      Project = "wsi"
    }
  }
}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_security_group" "rds" {
  # name_prefix + create_before_destroy: a ForceNew edit (e.g. the description) would otherwise try to
  # delete a group the DB still references, and deadlock the apply.
  name_prefix = "wsi-rds-sg-"
  description = "Allow PostgreSQL inbound from the SSM tunnel host"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description     = "PostgreSQL"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.tunnel.id]
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

# The tunnel host devs port-forward through with SSM Session Manager. It accepts no inbound traffic at all:
# the SSM agent dials out to AWS, and AWS relays each session after checking the dev's IAM user.
resource "aws_security_group" "tunnel" {
  name_prefix = "wsi-tunnel-sg-"
  description = "SSM tunnel host: no inbound"
  vpc_id      = data.aws_vpc.default.id

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

resource "aws_iam_role" "tunnel" {
  name = "wsi-tunnel"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "tunnel_ssm" {
  role       = aws_iam_role.tunnel.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "tunnel" {
  name = "wsi-tunnel"
  role = aws_iam_role.tunnel.name
}

# Amazon Linux ships with the SSM agent, so the instance needs no user data.
data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

resource "aws_instance" "tunnel" {
  # The account is on the AWS Free plan, which only launches free-tier-eligible types (t4g.nano is not).
  ami                  = data.aws_ssm_parameter.al2023_arm64.value
  instance_type        = "t4g.micro"
  subnet_id            = data.aws_subnets.default.ids[0]
  iam_instance_profile = aws_iam_instance_profile.tunnel.name

  vpc_security_group_ids = [aws_security_group.tunnel.id]
  # Only so the agent can reach the SSM endpoints; VPC endpoints would avoid it but cost more.
  associate_public_ip_address = true

  metadata_options {
    http_tokens = "required"
  }

  # A newer AMI must not replace the instance (and change its id in everyone's tunnel command).
  lifecycle {
    ignore_changes = [ami]
  }

  tags = {
    Name = "wsi-tunnel"
  }
}

resource "aws_db_subnet_group" "default" {
  name_prefix = "wsi-db-subnet-group-"
  subnet_ids  = data.aws_subnets.default.ids

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_db_instance" "postgres" {
  identifier = "wsi-db"

  engine         = "postgres"
  engine_version = "17"
  instance_class = "db.t4g.micro"

  allocated_storage = 20
  storage_type      = "gp3"

  db_name  = var.db_name
  username = var.db_username
  password = var.db_password

  db_subnet_group_name   = aws_db_subnet_group.default.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  # Reachable only from inside the VPC, i.e. through the tunnel host.
  publicly_accessible = false
  # Otherwise modifications like the one above wait for the weekly maintenance window.
  apply_immediately = true

  multi_az            = false
  skip_final_snapshot = true
  # Free, and it cannot be switched on in place later (that needs a snapshot restore into a new instance).
  storage_encrypted = true

  backup_retention_period = 0
}

resource "aws_s3_bucket" "slides" {
  bucket = var.bucket_name

  # Slides are re-uploadable test data: let `terraform destroy` empty the bucket instead of failing on it.
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "slides" {
  bucket = aws_s3_bucket.slides.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# The browser only ever does presigned part PUTs (`uploadChunkToStorage` in the frontend) and reads
# back the part ETag; everything else goes through the API.
resource "aws_s3_bucket_cors_configuration" "slides" {
  bucket = aws_s3_bucket.slides.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["PUT"]
    allowed_origins = ["*"] # Tighten to your frontend domain in production.
    expose_headers  = ["ETag"]
    max_age_seconds = 3600
  }
}

# A browser tab closed mid-upload leaves multipart parts that are billed but invisible in the object
# listing. The app's own abort is best-effort (explicit cancel only), so this is the reliable cleanup.
resource "aws_s3_bucket_lifecycle_configuration" "slides" {
  bucket = aws_s3_bucket.slides.id

  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_iam_user" "app" {
  name = "wsi-backend-app"
}

resource "aws_iam_access_key" "app" {
  user = aws_iam_user.app.name
}

# Exactly what app/services/storage.py calls. Note that IAM has no CreateMultipartUpload,
# CompleteMultipartUpload or HeadBucket action: those APIs are authorized by PutObject and ListBucket.
resource "aws_iam_user_policy" "app_s3" {
  name = "wsi-s3-access"
  user = aws_iam_user.app.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",    # get_bytes, download_file
          "s3:PutObject",    # put_bytes, and every multipart step
          "s3:DeleteObject", # delete_objects
          "s3:AbortMultipartUpload",
        ]
        Resource = "${aws_s3_bucket.slides.arn}/*"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:ListBucket",                 # iter_keys, and head_bucket in check_bucket()
          "s3:ListBucketMultipartUploads", # abort_multipart_uploads
        ]
        Resource = aws_s3_bucket.slides.arn
      },
    ]
  })
}

# One IAM user per developer, allowed nothing but a port-forward session through the tunnel host.
resource "aws_iam_user" "dev" {
  for_each = toset(var.developers)
  name     = "wsi-dev-${each.key}"
}

resource "aws_iam_access_key" "dev" {
  for_each = aws_iam_user.dev
  user     = each.value.name
}

resource "aws_iam_user_policy" "dev_tunnel" {
  for_each = aws_iam_user.dev
  name     = "wsi-db-tunnel"
  user     = each.value.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = "ssm:StartSession"
        Resource = [
          aws_instance.tunnel.arn,
          "arn:aws:ssm:${var.aws_region}::document/AWS-StartPortForwardingSessionToRemoteHost",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:TerminateSession", "ssm:ResumeSession"]
        Resource = "arn:aws:ssm:${var.aws_region}:*:session/$${aws:username}-*"
      },
    ]
  })
}
