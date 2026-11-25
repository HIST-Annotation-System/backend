#!/usr/bin/env bash
# Deploys one backend image on the EC2 host. CI runs it as root through SSM (.github/workflows/deploy.yml), after
# writing it and docker-compose.prod.yml to /opt/wsi; it can be re-run by hand there to roll back to an older image.
#
# Usage: deploy.sh <image>, e.g. 566741158735.dkr.ecr.eu-north-1.amazonaws.com/wsi-backend:<commit sha>
set -euo pipefail

IMAGE="$1"
DIR=/opt/wsi
# Written by Terraform: every setting of the app, secrets included.
ENV_PARAMETER=/wsi/prod/backend-env
HEALTH_TIMEOUT_SECONDS=180
REGISTRY="${IMAGE%%/*}"
# <account>.dkr.ecr.<region>.amazonaws.com
REGION="$(cut -d. -f4 <<<"$REGISTRY")"

cd "$DIR"
umask 077
aws ssm get-parameter --region "$REGION" --name "$ENV_PARAMETER" --with-decryption \
  --query Parameter.Value --output text >backend.env
# Compose reads .env for ${IMAGE}, so `docker compose -f docker-compose.prod.yml ...` works by hand here too.
echo "IMAGE=$IMAGE" >.env
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"

compose() { docker compose -f docker-compose.prod.yml -p wsi "$@"; }
compose pull --quiet
# Runs the migrations and the admin check first (depends_on), and fails here if either does.
compose up -d --remove-orphans

for ((waited = 0; waited < HEALTH_TIMEOUT_SECONDS; waited += 5)); do
  if curl -fsS http://localhost:8000/health >/dev/null; then
    echo "Deployed $IMAGE"
    docker image prune -af --filter "until=168h" >/dev/null
    exit 0
  fi
  sleep 5
done
echo "The API did not become healthy within ${HEALTH_TIMEOUT_SECONDS}s" >&2
compose logs --tail 100 api >&2
exit 1
