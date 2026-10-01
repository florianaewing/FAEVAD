"""Loads bot credentials from a local .env file kept OUTSIDE this repo.

Never point this at a file inside the FAEVAD repo -- these are live API
secrets (Telegram bot token, Buffer API key, platform tokens) and must
never be committed.
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
    buffer_pinterest_board_id: str
    # Optional platforms: each one is only posted to once all of its keys
    # are filled in, so they can be switched on one at a time.
    buffer_x_channel_id: str | None = None
    bluesky_handle: str | None = None
    bluesky_app_password: str | None = None
    mastodon_instance_url: str | None = None
    mastodon_access_token: str | None = None
    threads_user_id: str | None = None
    threads_access_token: str | None = None
    facebook_page_id: str | None = None
    facebook_page_access_token: str | None = None
    # Bug reports are also filed as GitHub issues once this is set.
    github_token: str | None = None

    @property
    def x_enabled(self) -> bool:
        return bool(self.buffer_x_channel_id)

    @property
    def bluesky_enabled(self) -> bool:
        return bool(self.bluesky_handle and self.bluesky_app_password)

    @property
    def mastodon_enabled(self) -> bool:
        return bool(self.mastodon_instance_url and self.mastodon_access_token)

    @property
    def threads_enabled(self) -> bool:
        return bool(self.threads_user_id and self.threads_access_token)

    @property
    def facebook_enabled(self) -> bool:
        return bool(self.facebook_page_id and self.facebook_page_access_token)


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

    def optional(key: str) -> str | None:
        return values.get(key) or None

    return Config(
        telegram_bot_token=require("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=int(require("TELEGRAM_CHAT_ID")),
        buffer_api_key=require("BUFFER_API_KEY"),
        buffer_instagram_channel_id=require("BUFFER_INSTAGRAM_CHANNEL_ID"),
        buffer_pinterest_channel_id=require("BUFFER_PINTEREST_CHANNEL_ID"),
        buffer_pinterest_board_id=require("BUFFER_PINTEREST_BOARD_ID"),
        buffer_x_channel_id=optional("BUFFER_X_CHANNEL_ID"),
        bluesky_handle=optional("BLUESKY_HANDLE"),
        bluesky_app_password=optional("BLUESKY_APP_PASSWORD"),
        mastodon_instance_url=(optional("MASTODON_INSTANCE_URL") or "").rstrip("/") or None,
        mastodon_access_token=optional("MASTODON_ACCESS_TOKEN"),
        threads_user_id=optional("THREADS_USER_ID"),
        threads_access_token=optional("THREADS_ACCESS_TOKEN"),
        facebook_page_id=optional("FACEBOOK_PAGE_ID"),
        facebook_page_access_token=optional("FACEBOOK_PAGE_ACCESS_TOKEN"),
        github_token=optional("GITHUB_TOKEN"),
    )
