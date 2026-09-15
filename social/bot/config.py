"""Loads bot credentials from a local .env file kept OUTSIDE this repo.

Never point this at a file inside the FAEVAD repo -- these are live API
secrets (Telegram bot token, Buffer API key, Reddit app credentials) and
must never be committed. Default location:
~/.config/faevad-social-bot/.env
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
    reddit_client_id: str
    reddit_client_secret: str
    reddit_refresh_token: str
    reddit_subreddit: str
    reddit_user_agent: str


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
        reddit_client_id=require("REDDIT_CLIENT_ID"),
        reddit_client_secret=require("REDDIT_CLIENT_SECRET"),
        reddit_refresh_token=require("REDDIT_REFRESH_TOKEN"),
        reddit_subreddit=require("REDDIT_SUBREDDIT"),
        reddit_user_agent=values.get(
            "REDDIT_USER_AGENT", "faevad-social-bot/1.0"
        ),
    )
