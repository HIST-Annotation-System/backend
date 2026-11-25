# The broker between the backend and the GPU worker (ml-worker repository), which runs on a teammate's PC outside AWS.
# Queue names are part of the contract in backend/app/services/segmentation.py and ml-worker/segmentation/contract.py.

locals {
  # A job can take minutes on a large image; a message is redelivered only if not finished within this time.
  sqs_visibility_timeout = 3600

  # Celery's SQS transport options for both sides. Predefined queues: neither side lists or creates queues.
  # wait_time_seconds: long polling, so idle consumers stay well inside SQS's free million requests a month.
  sqs_transport_options = jsonencode({
    region             = var.aws_region
    visibility_timeout = local.sqs_visibility_timeout
    wait_time_seconds  = 20
    predefined_queues = {
      "segmentation-jobs"    = { url = aws_sqs_queue.jobs.url }
      "segmentation-results" = { url = aws_sqs_queue.results.url }
    }
  })
}

# A message that keeps failing (its consumer crashes on it) ends up here after 5 tries, for inspection, instead of
# being retried forever.
resource "aws_sqs_queue" "jobs_dead_letter" {
  name                      = "segmentation-jobs-dead-letter"
  message_retention_seconds = 1209600
}

resource "aws_sqs_queue" "results_dead_letter" {
  name                      = "segmentation-results-dead-letter"
  message_retention_seconds = 1209600
}

resource "aws_sqs_queue" "jobs" {
  name                       = "segmentation-jobs"
  visibility_timeout_seconds = local.sqs_visibility_timeout
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.jobs_dead_letter.arn
    maxReceiveCount     = 5
  })
}

resource "aws_sqs_queue" "results" {
  name                       = "segmentation-results"
  visibility_timeout_seconds = local.sqs_visibility_timeout
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.results_dead_letter.arn
    maxReceiveCount     = 5
  })
}

# The worker takes jobs and posts results; nothing else.
resource "aws_iam_user_policy" "ml_worker_sqs" {
  name = "wsi-ml-worker-sqs-access"
  user = aws_iam_user.ml_worker.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:ChangeMessageVisibility",
          "sqs:GetQueueAttributes",
          "sqs:GetQueueUrl",
        ]
        Resource = aws_sqs_queue.jobs.arn
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage", "sqs:GetQueueAttributes", "sqs:GetQueueUrl"]
        Resource = aws_sqs_queue.results.arn
      },
    ]
  })
}
