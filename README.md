# conflict-resolution-assistant

Telegram MVP for structured two-party conflict mediation.

## What it does

- supports Russian and English
- asks the user to choose a language
- shows a button-based main menu
- responds to ordinary text outside active dialogs with guidance instead of silently ignoring it
- creates a conflict discussion
- generates a shareable invite link for the second participant
- shows the conflict topic and period in the flow
- collects answers from each side
- lets each answer be marked as:
  - summary only
  - fully private
  - quotable
- asks OpenAI to produce:
  - neutral summary of each side
  - common ground
  - core differences
  - concrete next-step options
- stores lightweight user feedback about the bot

## Commands

- `/start` — intro and main menu
- `/newcase` — create a new discussion
- `/join CODE` — join a discussion
- `/mycases` — list your discussions
- `/case CODE` — view a specific discussion
- `/feedback` — leave short feedback

Unknown commands return a short help message.
Regular text outside active dialogs returns guidance and the main menu.

## Stack

- Python 3.12
- aiogram 3
- SQLite
- OpenAI API
- Docker / docker-compose

## Local run without Docker

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m app.main
```

## Docker run

```bash
cp .env.example .env
docker-compose up --build -d
```

## Environment

See `.env.example`.

Required:
- `BOT_TOKEN`
- `OPENAI_API_KEY`

## Health check

A lightweight health check script is included:

```bash
./ops/check_bot_health.sh
```

It only reports a **new** error when one appears in container state/logs, stores its last seen signature in `var/health-state.json`, and writes a short human-readable report to `var/health-report.txt`.

To run it periodically with user systemd:

```bash
./systemd/install-healthcheck.sh
systemctl --user status conflict-resolution-assistant-health.timer
```

## Current MVP limits

- no admin panel
- no web UI
- no advanced separation between internal/private analysis and external summaries yet
- no voice-specific flows inside this bot yet
- long polling only
