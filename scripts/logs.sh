#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
docker-compose logs --tail=200 -f
