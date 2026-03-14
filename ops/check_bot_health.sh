#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="$PROJECT_DIR/var"
STATE_FILE="$STATE_DIR/health-state.json"
LOG_FILE="$STATE_DIR/health-last.log"
REPORT_FILE="$STATE_DIR/health-report.txt"
SERVICE_NAME="conflict-resolution-assistant"

mkdir -p "$STATE_DIR"

STATUS="unknown"
if docker ps --format '{{.Names}}' | grep -qx "$SERVICE_NAME"; then
  STATUS="up"
else
  STATUS="down"
fi

LOGS="$(docker logs --tail 200 "$SERVICE_NAME" 2>&1 || true)"
printf '%s\n' "$LOGS" > "$LOG_FILE"

ERROR_LINES="$(printf '%s\n' "$LOGS" | grep -E 'ERROR|Traceback|Exception|Failed|CRITICAL' || true)"
ERROR_SNIPPET="$(printf '%s\n' "$ERROR_LINES" | tail -n 20 || true)"
if [[ "$STATUS" != "up" && -z "$ERROR_SNIPPET" ]]; then
  ERROR_SNIPPET="container_status:$STATUS"
fi

CURRENT_SIGNATURE="none"
if [[ -n "$ERROR_SNIPPET" ]]; then
  CURRENT_SIGNATURE="$(printf '%s' "$ERROR_SNIPPET" | sha256sum | awk '{print $1}')"
fi

LAST_SIGNATURE="none"
LAST_SNIPPET=""
if [[ -f "$STATE_FILE" ]]; then
  LAST_SIGNATURE="$(python3 - <<'PY' "$STATE_FILE"
import json,sys
p=sys.argv[1]
try:
    print(json.load(open(p)).get('last_signature','none'))
except Exception:
    print('none')
PY
)"
  LAST_SNIPPET="$(python3 - <<'PY' "$STATE_FILE"
import json,sys
p=sys.argv[1]
try:
    print(json.load(open(p)).get('last_snippet',''))
except Exception:
    print('')
PY
)"
fi

IS_NEW="false"
if [[ "$CURRENT_SIGNATURE" != "none" && "$CURRENT_SIGNATURE" != "$LAST_SIGNATURE" ]]; then
  IS_NEW="true"
fi

python3 - <<'PY' "$STATE_FILE" "$CURRENT_SIGNATURE" "$STATUS" "$IS_NEW" "$ERROR_SNIPPET"
import json,sys,datetime
path, sig, status, is_new, snippet = sys.argv[1:6]
data = {
  'last_signature': sig,
  'last_snippet': snippet,
  'status': status,
  'is_new_error': is_new == 'true',
  'checked_at': datetime.datetime.now(datetime.UTC).isoformat().replace('+00:00','Z')
}
with open(path, 'w') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
PY

python3 - <<'PY' "$REPORT_FILE" "$STATUS" "$CURRENT_SIGNATURE" "$IS_NEW" "$LOG_FILE" "$ERROR_SNIPPET" "$LAST_SNIPPET"
import sys, pathlib, datetime
report_path, status, signature, is_new, log_path, current_snippet, last_snippet = sys.argv[1:8]
text = pathlib.Path(log_path).read_text(errors='replace') if pathlib.Path(log_path).exists() else ''
lines = text.splitlines()
interesting = [ln for ln in lines if any(tok in ln for tok in ('ERROR', 'Traceback', 'Exception', 'Failed', 'CRITICAL'))]
recent = interesting[-20:] if interesting else lines[-20:]
report = [
    f'Checked at: {datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")}',
    f'Container status: {status}',
    f'New error: {is_new}',
    f'Signature: {signature}',
    '',
]
if is_new == 'true':
    report += ['New problem detected:', current_snippet or '(empty)', '']
else:
    report += ['No new problem detected.', '']
if last_snippet and current_snippet != last_snippet:
    report += ['Previous known problem snippet:', last_snippet, '']
report += ['Recent relevant log lines:', *recent]
pathlib.Path(report_path).write_text('\n'.join(report) + '\n')
PY

if [[ "$IS_NEW" == "true" ]]; then
  echo "NEW_ERROR"
  echo "$ERROR_SNIPPET"
else
  echo "OK"
fi
