# conflict-resolution-assistant

Telegram MVP for structured two-party conflict mediation.

## What it does

- supports Russian and English
- asks the user to choose a language
- creates a conflict case
- generates a shareable invite link for the second participant
- shows the conflict topic and period in the flow
- collects intake answers from each side
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

- `/start` — intro
- `/newcase` — create a new case
- `/join CODE` — join a case
- `/mycases` — list your cases
- `/case CODE` — view a specific case
- `/feedback` — leave short feedback

Unknown commands return a short help message.

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
- no advanced separation between internal/private analysis and external summaries yet
- no voice-specific flows inside this bot yet
- long polling only
