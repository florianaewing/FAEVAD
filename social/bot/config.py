"""Loads bot credentials from a local .env file kept OUTSIDE this repo.

Never point this at a file inside the FAEVAD repo -- these are live API
secrets (Telegram bot token, Buffer API key) and must never be committed.
Default location: ~/.config/faevad-social-bot/.env
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "faevad-social-bot" / ".env"


@dataclass
class Config:
    telegram_bot_token: str
    telegram_chat_id: int
    buffer_api_key: str
    buffer_instagram_channel_id: str
    buffer_pinterest_channel_id: str


def load_config() -> Config:
    config_path = Path(os.environ.get("FAEVAD_BOT_ENV", DEFAULT_CONFIG_PATH))
    if not config_path.exists():
        raise FileNotFoundError(
            f"No config file at {config_path}. Copy social/bot/.env.example there "
            "and fill in real credentials (see that file for what each one is)."
        )
    values = dotenv_values(config_path)

    def require(key: str) -> str:
        val = values.get(key)
        if not val:
            raise ValueError(f"{key} is missing or empty in {config_path}")
        return val

    return Config(
        telegram_bot_token=require("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=int(require("TELEGRAM_CHAT_ID")),
        buffer_api_key=require("BUFFER_API_KEY"),
        buffer_instagram_channel_id=require("BUFFER_INSTAGRAM_CHANNEL_ID"),
        buffer_pinterest_channel_id=require("BUFFER_PINTEREST_CHANNEL_ID"),
    )
