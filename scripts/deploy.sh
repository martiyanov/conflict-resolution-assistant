#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

export GIT_SSH_COMMAND="ssh -i ~/.openclaw/workspace/conflict-resolution-assistant/.keys/github_ed25519 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

echo ">>> Pulling latest code"
git pull

echo ">>> Rebuilding and restarting container"
docker-compose up --build -d

echo ">>> Current status"
docker-compose ps
