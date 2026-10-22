# Deploying the AWS infrastructure

Terraform config for the cloud resources the WSI backend runs on. Apply this before starting the backend:
`docker-compose.yml` runs only the FastAPI service, so the database and the bucket both have to exist
first.

## What it creates

| Resource | Purpose |
|---|---|
| `aws_db_instance.postgres` | PostgreSQL 17 on `db.t4g.micro`, 20 GB gp3, encrypted at rest. Slide metadata, annotations, accounts. |
| `aws_db_subnet_group.default`, `aws_security_group.rds` | Places the instance in the default VPC and opens 5432 to the tunnel host only; the instance has no public address. |
| `aws_instance.tunnel` + `aws_security_group.tunnel`, `aws_iam_role.tunnel` + policy attachment + instance profile | `t4g.micro` that devs port-forward through with SSM Session Manager. No inbound rules; the public IP is only for reaching the SSM service. |
| `aws_iam_user.dev` + access key + `aws_iam_user_policy.dev_tunnel`, one set per `developers` entry | Per-person AWS login allowed to do nothing but open the DB tunnel. |
| `aws_s3_bucket.slides` | The slide objects themselves. `force_destroy = true`. |
| `aws_s3_bucket_public_access_block.slides` | Blocks every form of public access; the app only ever uses presigned URLs. |
| `aws_s3_bucket_cors_configuration.slides` | Lets the browser `PUT` upload parts directly and read the `ETag` back. |
| `aws_s3_bucket_lifecycle_configuration.slides` | Aborts incomplete multipart uploads after 7 days. |
| `aws_iam_user.app` + access key + `aws_iam_user_policy.app_s3` | The credentials the backend puts in `BUCKET_ACCESS_KEY`/`BUCKET_SECRET_KEY`, scoped to this one bucket. |

A full `terraform apply` from empty state is **15 to add**, plus 3 per developer.

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
| `developers` | One short name per person who runs the backend; each becomes IAM user `wsi-dev-<name>`. See the next section. |

## 2. How developers reach the database

Through a tunnel, not by IP. `aws_instance.tunnel` sits in the same VPC as RDS and runs the SSM agent,
which dials *out* to AWS; it has no inbound rules at all. A developer runs

```bash
aws ssm start-session ...   # the full line: terraform output -raw tunnel_command
```

and AWS, after checking that developer's IAM user, relays `localhost:15432` on their laptop to RDS port
5432 through that instance. The RDS security group admits the tunnel host's security group, so the
developer's own IP never matters: home, campus wifi and a phone hotspot all work with no apply.

The lock is the IAM user. Each `developers` entry gets one whose policy allows `ssm:StartSession` on
this one instance with the port-forwarding document, and nothing else. Removing someone is deleting
their name from the list and re-applying.

RDS itself is `publicly_accessible = false`: its endpoint name resolves to a private VPC address, so
nothing outside the VPC can even send it a packet, whatever the password. The tunnel command passes
that name to the tunnel host, which resolves it from inside the VPC.

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
<http://localhost:8000>. There is no database container: the containers reach RDS through the tunnel
from step 2, so start `tunnel_command` first. Compose points them at `host.docker.internal:15432`.

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

## Adding a teammate

```bash
# add their name to developers in terraform.tfvars
terraform apply                              # creates wsi-dev-<name> and its access key
terraform output -json dev_credentials       # send them their pair, out of band
terraform output -raw tunnel_command         # and this line
```

Only whoever holds `terraform.tfstate` can do this, because Terraform runs from one machine. It is needed
once per person, never when someone changes network.

On their machine: install the AWS CLI v2 and the
[Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html),
run `aws configure` with their key pair and region `eu-north-1`, then keep `tunnel_command` running in a
spare terminal whenever the backend runs. Their `.env.local` uses `POSTGRES_SERVER=localhost` and
`POSTGRES_PORT=15432`.

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
| Connection to `localhost:15432` refused | The tunnel is not running, or it closed after being idle. Re-run `tunnel_command`; the API's `pool_pre_ping` reconnects by itself. |
| `TargetNotConnected` from `start-session` | The tunnel instance is stopped or its SSM agent has not registered yet (give a new instance a couple of minutes). |
| `AccessDeniedException` from `start-session` | Wrong keys in `aws configure`, or the region is not `eu-north-1`. |
| `SessionManagerPlugin is not found` | The Session Manager plugin is not installed, or the terminal was opened before installing it. |

## Cost and state

New AWS accounts get 12 months of free tier covering `db.t4g.micro` and 20 GB of storage; outside
that, expect roughly $15/month for the instance plus storage, and S3 at about $0.023 per GB-month.
The always-on tunnel host adds about $11/month at list price: ~$8 for the `t4g.micro` and ~$3.6 for
its public IPv4 address. While the account is on the AWS Free plan, that comes out of its credits.
Slides are large, so run `terraform destroy` when the project is done. Treat these as ballpark
figures and check the AWS pricing page.

`terraform.tfstate` is local and gitignored, so **one person owns the stack**. A `terraform apply`
from a second machine starts from empty state and tries to create everything again, failing with
`DBInstanceAlreadyExists` and `BucketAlreadyOwnedByYou`. That state file is also the only copy of the
IAM secret keys (the app's and every developer's) — AWS will not show them again. If the team needs shared control, move to an S3 backend
with locking.
