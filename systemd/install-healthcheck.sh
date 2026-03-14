#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$HOME/.config/systemd/user"
cp "$PROJECT_DIR/systemd/conflict-resolution-assistant-health.service" "$HOME/.config/systemd/user/"
cp "$PROJECT_DIR/systemd/conflict-resolution-assistant-health.timer" "$HOME/.config/systemd/user/"
chmod +x "$PROJECT_DIR/ops/check_bot_health.sh"
systemctl --user daemon-reload
systemctl --user enable --now conflict-resolution-assistant-health.timer
systemctl --user list-timers --all | grep conflict-resolution-assistant-health || true
