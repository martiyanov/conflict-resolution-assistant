# conflict-resolution-assistant

Telegram MVP for structured two-party conflict mediation.

## What it does

- creates a conflict case
- invites a second participant with a join code
- collects 4 intake answers from each side
- asks OpenAI to produce:
  - neutral summary of each side
  - common ground
  - core differences
  - 3 concrete next-step options

## Stack

- Python 3.12
- aiogram 3
- SQLite
- OpenAI API
- Docker / docker compose

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

## Deploy helpers

```bash
./scripts/deploy.sh
./scripts/status.sh
./scripts/logs.sh
./scripts/restart.sh
```

`deploy.sh` pulls latest code, rebuilds the image, restarts the container, and prints status.

## systemd user service

To make the container start on login/reboot via your user systemd:

```bash
./systemd/install-user-service.sh
systemctl --user status conflict-resolution-assistant.service
```

The unit file lives in `systemd/conflict-resolution-assistant.service`.

## Environment

See `.env.example`.

Required:
- `BOT_TOKEN`
- `OPENAI_API_KEY`

## Current MVP limits

- no admin panel
- no web UI
- no fine-grained privacy rules yet
- no voice-specific flows inside this bot yet
- long polling only
