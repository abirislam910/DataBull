#!/bin/bash
# Packages the committed code and redeploys it to the DataBull EC2 host.
#
#   AWS_PROFILE=databull deploy/release.sh
#
# What ships: the committed HEAD, plus the working copies of the deploy/ files
# if they have uncommitted changes. Nothing else that is uncommitted is included.
# The bucket and instance are discovered (SSM parameter and Name tag), so this
# script holds no account-specific values. See docs/DEPLOYMENT.md.
set -euo pipefail

REGION="${AWS_REGION:-us-east-2}"
cd "$(git rev-parse --show-toplevel)"

bucket="$(aws ssm get-parameter --region "$REGION" --name /databull/release-bucket \
  --query Parameter.Value --output text)"
instance="$(aws ec2 describe-instances --region "$REGION" \
  --filters Name=tag:Name,Values=databull-api Name=instance-state-name,Values=running \
  --query 'Reservations[0].Instances[0].InstanceId' --output text)"
if [ -z "$instance" ] || [ "$instance" = "None" ]; then
  echo "No running instance tagged Name=databull-api in $REGION." >&2
  exit 1
fi

if [ -n "$(git status --porcelain -- . ':!deploy')" ]; then
  echo "Note: uncommitted changes outside deploy/ are NOT included in this release."
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

git archive --format=tar HEAD -o "$work/release.tar"
note="commit $(git rev-parse --short HEAD) ($(git branch --show-current))"

# macOS tar would otherwise attach Apple file metadata that Linux tar warns about.
tar_flags=()
if tar --version 2>/dev/null | grep -q bsdtar; then
  export COPYFILE_DISABLE=1
  tar_flags=(--no-mac-metadata --no-xattrs)
fi

if [ -n "$(git status --porcelain -- deploy)" ]; then
  tar ${tar_flags[@]+"${tar_flags[@]}"} --append -f "$work/release.tar" deploy/docker-compose.prod.yml deploy/bootstrap.sh
  note="$note + uncommitted deploy/ files"
fi
printf '%s, packaged %s\n' "$note" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$work/RELEASE"
tar ${tar_flags[@]+"${tar_flags[@]}"} --append -f "$work/release.tar" -C "$work" RELEASE
gzip -9 "$work/release.tar"

aws s3 cp --region "$REGION" --only-show-errors "$work/release.tar.gz" \
  "s3://${bucket}/releases/latest.tar.gz"
echo "Uploaded: $(cat "$work/RELEASE")"

command_id="$(aws ssm send-command --region "$REGION" --instance-ids "$instance" \
  --document-name AWS-RunShellScript --comment "databull: redeploy latest release" \
  --parameters '{"executionTimeout":["900"],"commands":[
    "set -euo pipefail",
    "B=$(aws ssm get-parameter --region us-east-2 --name /databull/release-bucket --query Parameter.Value --output text)",
    "aws s3 cp --region us-east-2 s3://$B/releases/latest.tar.gz - | tar -xzO deploy/bootstrap.sh > /tmp/databull-bootstrap.sh",
    "bash /tmp/databull-bootstrap.sh"]}' \
  --query Command.CommandId --output text)"
echo "Redeploying on $instance (command $command_id). The image build takes a few minutes."

status="Pending"
for _ in $(seq 1 60); do
  sleep 15
  status="$(aws ssm get-command-invocation --region "$REGION" --command-id "$command_id" \
    --instance-id "$instance" --query Status --output text 2>/dev/null || echo Pending)"
  case "$status" in Success | Failed | Cancelled | TimedOut) break ;; esac
done

echo "Status: $status"
aws ssm get-command-invocation --region "$REGION" --command-id "$command_id" \
  --instance-id "$instance" --query StandardOutputContent --output text | tail -n 5
[ "$status" = "Success" ]
