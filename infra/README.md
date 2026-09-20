# Deploying the AWS infrastructure

Terraform config for the cloud resources the WSI backend runs on. Apply this before starting the backend:
`docker-compose.yml` runs only the FastAPI service, so the database and the bucket both have to exist
first.

## What it creates

| Resource | Purpose |
|---|---|
| `aws_db_instance.postgres` | PostgreSQL 17 on `db.t4g.micro`, 20 GB gp3, encrypted at rest. Slide metadata, annotations, accounts. |
| `aws_db_subnet_group.default`, `aws_security_group.rds` | Places the instance in the default VPC and opens 5432 to one CIDR only. |
| `aws_s3_bucket.slides` | The slide objects themselves. `force_destroy = true`. |
| `aws_s3_bucket_public_access_block.slides` | Blocks every form of public access; the app only ever uses presigned URLs. |
| `aws_s3_bucket_cors_configuration.slides` | Lets the browser `PUT` upload parts directly and read the `ETag` back. |
| `aws_s3_bucket_lifecycle_configuration.slides` | Aborts incomplete multipart uploads after 7 days. |
| `aws_iam_user.app` + access key + `aws_iam_user_policy.app_s3` | The credentials the backend puts in `BUCKET_ACCESS_KEY`/`BUCKET_SECRET_KEY`, scoped to this one bucket. |

A full `terraform apply` from empty state is **10 to add**.

## Prerequisites

- Terraform >= 1.5 — `winget install HashiCorp.Terraform`
- AWS credentials. This machine already has them: `~/.aws/credentials` holds a `[default]` profile for
  `arn:aws:iam::566741158735:user/terraform-admin`, which the provider picks up with no extra config.
  A stale key shows up as `InvalidClientTokenId` on the first plan.

## The region is fixed — do not change it

`aws_region` must stay `eu-north-1`. The account belongs to an AWS Organization whose management
account attaches a Service Control Policy denying every other region. Point it anywhere else and the
very first data source fails:

```
Error: reading EC2 VPC: ... api error UnauthorizedOperation: You are not authorized to perform this
operation. User: arn:aws:iam::566741158735:user/terraform-admin is not authorized to perform:
ec2:DescribeVpcs with an explicit deny in a service control policy:
arn:aws:organizations::653240454801:policy/o-zrf8rfaelb/service_control_policy/p-9qupd6wf
```

An **explicit deny in an SCP sits above IAM** in the evaluation order, so this is not fixable by
granting `terraform-admin` more permissions — only the organization administrator can change the
policy. IAM itself is global and escapes the region condition, which is why `iam:GetUser` succeeds
while `ec2:*`, `rds:*` and `s3:*` do not.

To re-check whether the policy has changed, probe with the backend's own boto3 (read-only):

```bash
cd ..
./.venv/Scripts/python.exe -c "import boto3; print(boto3.client('ec2', region_name='eu-central-1').describe_vpcs())"
```

## 1. Fill in the variables

```bash
cp terraform.tfvars.example terraform.tfvars   # gitignored by *.tfvars
```

| Variable | Notes |
|---|---|
| `aws_region` | Leave at `eu-north-1`. See above. |
| `bucket_name` | Globally unique across all of AWS, not just your account. A taken name fails with `BucketAlreadyExists`. |
| `db_password` | At least 16 characters, and none of `/ " @ # ?` or space. RDS rejects some of those outright, and the rest break the DSN that `app/core/config.py` builds — `MultiHostUrl.build` raises `ValidationError` instead of percent-encoding them. `openssl rand -hex 24` is a safe generator, and a `validation` block in `variables.tf` enforces the rule at plan time. |
| `db_name`, `db_username` | Default to `wsi`. |
| `allowed_cidr` | Your own public address as a `/32`: `curl ifconfig.me`. See the next section. |

## 2. Why `allowed_cidr` matters even though the database is public

These are two different layers, and only one of them is a lock.

**`publicly_accessible = true` is addressing.** It decides whether the instance gets a public DNS name
and a routable public IP *at all*. With `false` it resolves only to a private address inside the VPC,
and nothing outside that VPC can send it a packet — you would need the backend running inside the same
VPC, or a VPN, or a bastion host. The backend runs on your laptop, so the instance has to be publicly
addressable for you to reach it.

**The security group is filtering.** Of everyone who *can* route to that public address, it decides
whose TCP connection to port 5432 AWS will accept.

So `publicly_accessible` gives the database a street address; the security group decides who gets past
the front door. With `0.0.0.0/0` every internet-wide Postgres scanner reaches the login prompt and the
master password is the only thing between them and the data — and new RDS endpoints get found within
hours, on predictable hostnames. With your `/32`, everyone else gets a silent timeout instead.

The price you pay: your IP changes when the router reboots, the DHCP lease renews, or you move to
university wifi or a phone hotspot. Connections then hang until you update `allowed_cidr` and
re-apply — a ~30 second security-group rule update, not a database restart.

## 3. Apply

```bash
terraform init      # commit the .terraform.lock.hcl this generates
terraform plan      # expect: 10 to add
terraform apply
```

`apply` sits on `aws_db_instance.postgres` for about 10 minutes. That is normal.

## 4. Wire up the backend

```bash
terraform output -raw env_local
```

That prints the `BUCKET_*` and `POSTGRES_*` block. **Replace** the matching block in
`backend/.env.local` with it — do not append, because those keys are already defined there (with
`TODO_after_terraform_apply` placeholders) and you would end up with two definitions of each. Keep
the `JWT_*` and `ADMIN_*` lines, which Terraform does not provision.

Paste it in an editor rather than redirecting: PowerShell's `>>` writes a BOM that breaks the first
key in the file.

## 5. Migrate and run

```bash
cd ..
docker compose up -d --build
```

That runs `migrate` (`alembic upgrade head`) against RDS and starts the API on
<http://localhost:8000>. There is no database container: the containers reach RDS through Docker's
NAT, so their source address is the host's public IP and the `/32` rule from step 2 already covers
them.

On a brand new database the admin account has to be created once - compose no longer does it:

```bash
docker compose run --rm api python -m scripts.create_admin
```

Without Docker:

```bash
uv sync
uv run alembic upgrade head
uv run python -m scripts.create_admin        # once: creates ADMIN_USERNAME
uv run uvicorn app.main:app --reload --no-access-log
```

## 6. Verify

1. The startup log (`docker compose logs api`) must **not** contain `Bucket ... is not reachable`. That line comes from
   `check_bucket()` in `app/services/storage.py`, and its absence proves the `s3:ListBucket` grant
   works.
2. Start the frontend (`npm run dev`; `VITE_API_URL` defaults to `http://localhost:8000/api/v1`),
   upload a slide, then delete it. That exercises the whole IAM policy: `s3:PutObject` via presigned
   part PUTs plus the CORS rule, `s3:GetObject` during ingestion, and `s3:DeleteObject` on delete.

## When your IP changes

Symptom: the API or `psql` hangs and eventually times out, with no error from AWS — the security group
drops the packets rather than rejecting them.

```bash
curl ifconfig.me                 # update allowed_cidr in terraform.tfvars
terraform apply                  # ~30s, only the security group rule changes
```

## Teardown

```bash
terraform destroy
```

This is not recoverable, by design: `force_destroy = true` empties the bucket and deletes every slide
in it, and `skip_final_snapshot = true` with `backup_retention_period = 0` means the database goes
with no snapshot and no automated backup. Export anything you care about first.

## Troubleshooting

| Error | Cause |
|---|---|
| `UnauthorizedOperation ... explicit deny in a service control policy` | `aws_region` is not `eu-north-1`. |
| `InvalidClientTokenId` | The keys in `~/.aws/credentials` are stale or deleted. |
| `Invalid value for variable ... db_password must be at least 16 characters` | The password contains `/ " @ # ?` or a space, or is too short. |
| `BucketAlreadyExists` | S3 bucket names are global; someone else has it. Pick another `bucket_name`. |
| `no matching EC2 VPC found` | The account's default VPC in `eu-north-1` was deleted. Recreate it, or set explicit subnet IDs. |
| Connection to 5432 times out | `allowed_cidr` no longer matches your IP, or the network blocks outbound 5432 — common on university and corporate wifi. A phone hotspot tells the two apart. |

## Cost and state

New AWS accounts get 12 months of free tier covering `db.t4g.micro` and 20 GB of storage; outside
that, expect roughly $15/month for the instance plus storage, and S3 at about $0.023 per GB-month.
Slides are large, so run `terraform destroy` when the project is done. Treat these as ballpark
figures and check the AWS pricing page.

`terraform.tfstate` is local and gitignored, so **one person owns the stack**. A `terraform apply`
from a second machine starts from empty state and tries to create everything again, failing with
`DBInstanceAlreadyExists` and `BucketAlreadyOwnedByYou`. That state file is also the only copy of the
IAM secret key — AWS will not show it again. If the team needs shared control, move to an S3 backend
with locking.
