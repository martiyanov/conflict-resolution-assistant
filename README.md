# conflict-resolution-assistant

Telegram MVP for structured two-party conflict mediation.

## What it does

- creates a conflict case
- invites a second participant with a join code
- collects intake answers from each side
- asks OpenAI to produce:
  - neutral summary of each side
  - common ground
  - core differences
  - concrete next-step options

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

## Current MVP limits

- no admin panel
- no web UI
- no advanced privacy policy separation between internal/external summaries yet
- no voice-specific flows inside this bot yet
- long polling only
