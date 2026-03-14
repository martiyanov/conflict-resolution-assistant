from dataclasses import dataclass
import os

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    bot_token: str
    openai_api_key: str
    openai_model: str = "gpt-4o-mini"
    database_path: str = "/app/data/app.db"
    log_level: str = "INFO"


settings = Settings(
    bot_token=os.environ["BOT_TOKEN"],
    openai_api_key=os.environ["OPENAI_API_KEY"],
    openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
    database_path=os.getenv("DATABASE_PATH", "/app/data/app.db"),
    log_level=os.getenv("LOG_LEVEL", "INFO"),
)
