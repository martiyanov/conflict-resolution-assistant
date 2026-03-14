#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_SRC="$PROJECT_DIR/systemd/conflict-resolution-assistant.service"
UNIT_DST="$HOME/.config/systemd/user/conflict-resolution-assistant.service"

mkdir -p "$HOME/.config/systemd/user"
cp "$UNIT_SRC" "$UNIT_DST"

systemctl --user daemon-reload
systemctl --user enable --now conflict-resolution-assistant.service
systemctl --user status conflict-resolution-assistant.service --no-pager
