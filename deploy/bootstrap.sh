#!/bin/bash
# Installs or redeploys DataBull on an Amazon Linux 2023 (arm64) EC2 instance.
#
# Runs as root: once as EC2 user data on first boot, and again through SSM Run
# Command for each redeploy. Every step is safe to repeat.
#
# Nothing secret or account-specific lives in this file. It reads:
#   /databull/release-bucket        SSM String: the S3 bucket holding releases
#   /databull/secret-key            SSM SecureString: signs JWTs
#   /databull/db-password           SSM SecureString: the Postgres password
#   /databull/anthropic-api-key     SSM SecureString, optional: enables the assistant
#   /databull/cors-allow-origins    SSM String, optional: browser origins allowed
#                                   to call the API (comma-separated)
#   s3://<bucket>/releases/latest.tar.gz   the code to run
#
# See docs/DEPLOYMENT.md.
set -euo pipefail
exec > >(tee -a /var/log/databull-bootstrap.log) 2>&1
echo "=== databull bootstrap: $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

REGION="us-east-2"
APP_DIR="/opt/databull"
COMPOSE_VERSION="v5.7.0"
# Pinned when this script was written, so a changed binary is rejected rather
# than trusted because it arrived alongside a matching checksum file.
COMPOSE_SHA256="15861535d14e26555736b49376c95e14bfd94285366c8446bfac7156a972e76f"
COMPOSE_BIN="/usr/local/lib/docker/cli-plugins/docker-compose"
# Compose builds through buildx, and the Amazon Linux 2023 docker package ships
# an older buildx than Compose requires (0.17+). Pinned the same way.
BUILDX_VERSION="v0.38.2"
BUILDX_SHA256="46c0f334772de3cb0b341cc415caf7339fb5cfc8b8438ebcb5a10e7f23800d36"
BUILDX_BIN="/usr/local/lib/docker/cli-plugins/docker-buildx"

ssm_get() {
  aws ssm get-parameter --region "$REGION" --name "$1" --with-decryption \
    --query Parameter.Value --output text
}

# 1. Docker engine.
if ! command -v docker >/dev/null 2>&1; then
  dnf install -y docker
fi
systemctl enable --now docker

# 2. Docker Compose plugin (Amazon Linux 2023 does not package it).
if ! docker compose version 2>/dev/null | grep -q "${COMPOSE_VERSION#v}"; then
  mkdir -p "$(dirname "$COMPOSE_BIN")"
  tmp="$(mktemp)"
  curl -fsSL -o "$tmp" \
    "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-aarch64"
  echo "${COMPOSE_SHA256}  ${tmp}" | sha256sum -c -
  install -m 0755 "$tmp" "$COMPOSE_BIN"
  rm -f "$tmp"
fi
docker compose version

# 2b. Docker buildx plugin, which Compose uses to build images.
if ! docker buildx version 2>/dev/null | grep -q "${BUILDX_VERSION}"; then
  tmp="$(mktemp)"
  curl -fsSL -o "$tmp" \
    "https://github.com/docker/buildx/releases/download/${BUILDX_VERSION}/buildx-${BUILDX_VERSION}.linux-arm64"
  echo "${BUILDX_SHA256}  ${tmp}" | sha256sum -c -
  install -m 0755 "$tmp" "$BUILDX_BIN"
  rm -f "$tmp"
fi
docker buildx version

# 3. Fetch the release into a fresh directory.
bucket="$(ssm_get /databull/release-bucket)"
release_dir="${APP_DIR}/releases/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$release_dir"
aws s3 cp --region "$REGION" "s3://${bucket}/releases/latest.tar.gz" - \
  | tar -xz -C "$release_dir"
echo "release: $(cat "${release_dir}/RELEASE" 2>/dev/null || echo unknown)"

# 4. Secrets into a root-only env file. Docker also keeps these in the running
#    containers' configuration, which only root can read.
env_file="${APP_DIR}/.env"
anthropic_key="$(ssm_get /databull/anthropic-api-key 2>/dev/null || true)"
cors_origins="$(ssm_get /databull/cors-allow-origins 2>/dev/null || true)"
(
  umask 077
  {
    echo "SECRET_KEY=$(ssm_get /databull/secret-key)"
    echo "DB_PASSWORD=$(ssm_get /databull/db-password)"
    echo "ANTHROPIC_API_KEY=${anthropic_key}"
    echo "CORS_ALLOW_ORIGINS=${cors_origins}"
  } > "$env_file"
)
if [ -z "$anthropic_key" ]; then
  echo "no Anthropic key in SSM: /chat/stream will return 503"
fi
echo "CORS allowed origins: ${cors_origins:-none}"

# 5. Build and (re)start. The fixed project name in the compose file keeps the
#    database volume across releases.
docker compose -f "${release_dir}/deploy/docker-compose.prod.yml" \
  --env-file "$env_file" up -d --build --remove-orphans

# 6. Wait for the API to answer.
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
    echo "API healthy"
    break
  fi
  sleep 5
done
curl -fsS http://127.0.0.1:8000/health

# 7. Keep the two most recent releases and drop unused images.
ls -1dt "${APP_DIR}"/releases/* | tail -n +3 | xargs -r rm -rf
docker image prune -f >/dev/null

echo "=== bootstrap finished ==="
