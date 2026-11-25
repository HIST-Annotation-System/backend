terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
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
  description = "Allow PostgreSQL inbound from the backend host"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description     = "PostgreSQL"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.backend.id]
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

  # Reachable only from inside the VPC, i.e. from the backend host.
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
    # Only the deployed site uploads here; local development uses its own S3 emulator.
    allowed_origins = ["https://${aws_cloudfront_distribution.app.domain_name}"]
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

# The segmentation worker runs outside AWS, on a machine with a GPU, so it gets the least it can work with: it reads
# hotspot images, writes its results and uses the two segmentation queues (segmentation.tf), with no database access.
resource "aws_iam_user" "ml_worker" {
  name = "wsi-ml-worker"
}

resource "aws_iam_access_key" "ml_worker" {
  user = aws_iam_user.ml_worker.name
}

resource "aws_iam_user_policy" "ml_worker_s3" {
  name = "wsi-ml-worker-s3-access"
  user = aws_iam_user.ml_worker.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = "${aws_s3_bucket.slides.arn}/hotspots/*"
      },
      {
        Effect   = "Allow"
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.slides.arn}/hotspots/*/segmentation.json"
      },
      {
        # Without it S3 answers 403 instead of 404 for a missing object (a hotspot deleted before its job ran).
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = aws_s3_bucket.slides.arn
        Condition = {
          StringLike = { "s3:prefix" = "hotspots/*" }
        }
      },
    ]
  })
}

