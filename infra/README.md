# Production infrastructure (AWS, Terraform)

Everything the deployed app runs on. Local development needs none of it: `../docker-compose.yml` brings its own
database, S3 emulator and Redis.

```text
browser ──HTTPS──> CloudFront (https://<id>.cloudfront.net)
                    ├─ /*      -> S3 bucket <bucket_name>-frontend (private; the frontend build)
                    └─ /api/*  -> EC2 wsi-backend :8000 (docker-compose.prod.yml: API + segmentation consumer)
browser ──presigned PUT──> S3 bucket <bucket_name> (slides, hotspot images, results)

wsi-backend ──> RDS PostgreSQL (private)      wsi-backend ──> SQS segmentation-jobs ──> GPU PC (ml-worker)
                                              wsi-backend <── SQS segmentation-results <──┘
```

## What it creates

| File | Resources |
|---|---|
| `main.tf` | RDS PostgreSQL 17 (`db.t4g.micro`, private, encrypted) and its security group; the slides bucket (no public access, CORS for the site's uploads, cleanup of abandoned multipart uploads); IAM users for the app's and the worker's bucket access |
| `backend.tf` | `wsi-backend`: t4g.small (ARM, 2 GB, 2 GB swap) with an Elastic IP, port 8000 open to CloudFront only; a 50 GB volume for the slide cache at `/var/lib/wsi-cache`; its role (SSM, ECR pull, the settings parameter, the queues); the ECR repository (last 10 images); the SSM SecureString `/wsi/prod/backend-env` with every app setting, including a generated JWT secret |
| `frontend.tf` | The frontend bucket, readable only by CloudFront; the distribution (SPA routes rewritten to `index.html` by a CloudFront Function, `/api/*` passed through uncached, 5xx errors never cached) |
| `segmentation.tf` | SQS `segmentation-jobs` and `segmentation-results`, each with a dead-letter queue after 5 failed deliveries; the worker's queue permissions |
| `github.tf` | GitHub's OIDC provider and one deploy role per repository, usable only by runs on `main` |

There is no SSH and no database tunnel: the host has no open port besides 8000, and a shell goes through SSM
(`terraform output -raw shell_command`, needs the
[Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)).

## The region is fixed

`aws_region` must stay `eu-north-1`: the AWS Organization's SCP denies every other region, and an explicit deny in an
SCP cannot be overridden by any IAM permission. Elsewhere the first data source fails with `UnauthorizedOperation ...
explicit deny in a service control policy`. (CloudFront and IAM are global and unaffected.)

## Prerequisites

- Terraform >= 1.5 (`winget install HashiCorp.Terraform`)
- AWS credentials for the account (the `[default]` profile of `terraform-admin` on the machine that holds the state).
  A stale key shows up as `InvalidClientTokenId` on the first plan.

## Variables

`cp terraform.tfvars.example terraform.tfvars` (gitignored):

| Variable | Notes |
|---|---|
| `bucket_name` | Globally unique across all of AWS. The frontend bucket is named `<bucket_name>-frontend`. |
| `db_password` | At least 16 characters, none of `/ " @ # ?` or space (RDS rejects some, the rest break the DSN that `app/core/config.py` builds). `openssl rand -hex 24` is safe. |
| `admin_password` | The app's administrator, created by the first deploy if the database has none. |
| `db_name`, `db_username`, `admin_username`, `github_organization`, `aws_region` | Defaults are right. |

## Apply

```bash
terraform init      # commit the .terraform.lock.hcl it writes
terraform plan
terraform apply
```

A new RDS instance takes about 10 minutes, a CloudFront distribution a few more. The host installs Docker on first
boot; it is ready for a deploy a few minutes after `apply` returns.

## Connect the repositories

```bash
terraform output -json github_variables
```

Put each value in its repository as a variable (Settings > Secrets and variables > Actions > Variables):
`AWS_ROLE_ARN`, `ECR_REPOSITORY`, `INSTANCE_ID` in **backend**; `AWS_ROLE_ARN`, `FRONTEND_BUCKET`, `DISTRIBUTION_ID` in
**frontend**. None are secret. From then on, every push to `main` deploys that repository
(`.github/workflows/deploy.yml`): the backend's runs `alembic upgrade head` on RDS before starting the new image, so
**merging to `main` migrates the production database**.

The app is at `terraform output -raw app_url`.

## The GPU worker

On the PC with the GPU, in its `ml-worker` checkout:

```bash
terraform output -raw ml_worker_env > ../../ml-worker/.env.aws   # Git Bash (PowerShell > writes UTF-16); or paste it on the PC
docker compose --profile aws up -d --build                       # in ml-worker, on the PC
```

The worker only dials out (SQS and S3), so the PC needs no open port. Its key can read hotspot images, write
segmentation results and use the two queues, nothing else. Without a worker, hotspots stay `pending`.

## Operating it

| Task | How |
|---|---|
| Logs | `terraform output -raw shell_command`, then `cd /opt/wsi && sudo docker compose -f docker-compose.prod.yml -p wsi logs -f api` |
| Change a setting | Edit `aws_ssm_parameter.backend_env` in `backend.tf`, `terraform apply`, then redeploy (re-run the last deploy workflow, or `sudo /opt/wsi/deploy.sh <current image>` on the host) |
| Roll back | `sudo /opt/wsi/deploy.sh <account>.dkr.ecr.eu-north-1.amazonaws.com/wsi-backend:<older sha>` on the host; ECR keeps the last 10 |
| Database shell | On the host: `sudo docker compose -f docker-compose.prod.yml -p wsi exec api python`, or `sudo docker run --rm -it --env-file backend.env postgres:17-alpine sh -c 'psql "postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD@$POSTGRES_SERVER/$POSTGRES_DB"'` |
| Stuck segmentation jobs | SQS console: messages in `segmentation-*-dead-letter` failed 5 times |

`user_data` (`backend-init.sh.tftpl`) only runs on the first boot, and changes to it or to the AMI are ignored so the
host (and the workflow's `INSTANCE_ID`) never changes by accident. To rebuild the host on purpose:
`terraform apply -replace=aws_instance.backend`, then update `INSTANCE_ID`; the cache volume is re-attached with its
slides.

## State

`terraform.tfstate` is local and gitignored, so **one person owns the stack**: an apply from another machine starts
from empty state and tries to create everything again (`DBInstanceAlreadyExists`, `BucketAlreadyOwnedByYou`). The state
also holds every secret (IAM keys, database and admin passwords, the JWT secret). Move it to an S3 backend with locking
if the team needs shared control.

## Cost

Roughly per month: EC2 t4g.small ~$12, its public IPv4 ~$3.6, EBS 70 GB ~$6, RDS `db.t4g.micro` + 20 GB ~$15;
CloudFront, SQS and ECR stay within their free allowances at this scale. S3 is ~$0.023 per GB-month of slides. The
account is on the AWS Free plan: usage comes out of its credits, and the plan ends on 2027-03-19, after which the
account must be upgraded to a paid plan or it is closed. Check the AWS pricing pages for current figures.

## Teardown

```bash
terraform destroy
```

Not recoverable, by design: `force_destroy` empties the buckets and ECR, and the database goes with no snapshot
(`skip_final_snapshot`, no automated backups). Export anything you care about first.

## Troubleshooting

| Error | Cause |
|---|---|
| `UnauthorizedOperation ... explicit deny in a service control policy` | `aws_region` is not `eu-north-1`. |
| `InvalidClientTokenId` | The keys in `~/.aws/credentials` are stale or deleted. |
| `Invalid value for variable ... db_password` | Too short, or contains `/ " @ # ?` or a space. |
| `BucketAlreadyExists` | S3 bucket names are global; pick another `bucket_name`. |
| Deploy workflow: `Not authorized to perform sts:AssumeRoleWithWebIdentity` | The run is not on `main`, or `AWS_ROLE_ARN` belongs to the other repository. |
| Deploy workflow: `InvalidInstanceId` | The host is stopped, still booting, or `INSTANCE_ID` is outdated. |
| Site answers 502/504 | The API is down or restarting: check its logs on the host. |
| Slide viewer says the server is copying the slide | Normal on a slide's first view on this host (or after it was evicted from the cache). |
