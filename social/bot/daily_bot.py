#!/usr/bin/env python3
"""FAEVAD daily social posting bot.

Runs as a long-lived local process. Each day at DAILY_SEND_HOUR:MINUTE it
finds the next un-posted piece in social/queue.yml (lowest catalog_number
with posted: false), and messages the artist on Telegram asking for that
day's caption. Whatever the artist replies with is used verbatim as the
caption -- this bot never drafts or edits post text.

Once a caption reply arrives, it posts to Instagram + Pinterest (via
Buffer), records the result in social/queue.yml, and commits + pushes that
change to the repo. Reddit and Facebook Marketplace are intentionally not
part of this automated flow -- see platforms.py's module docstring.

Setup: copy .env.example to ~/.config/faevad-social-bot/.env and fill in
real credentials (never commit that file). See platforms.py for what each
credential is for.
"""

import datetime as dt
import logging
import os
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

import platforms
from config import load_config

REPO_ROOT = Path(__file__).resolve().parents[2]
QUEUE_PATH = REPO_ROOT / "social" / "queue.yml"
ARTWORK_PATH = REPO_ROOT / "_data" / "artwork.yml"
SITE_BASE_URL = "https://florianaewing.github.io/FAEVAD"

# The bot pushes over SSH with its own deploy key (write access to this repo
# only, added under the repo's Settings -> Deploy keys), so it doesn't depend
# on whatever credentials the artist uses for their own pushes.
PUSH_URL = "git@github.com:florianaewing/FAEVAD.git"
DEPLOY_KEY_PATH = Path.home() / ".ssh" / "faevad_bot_deploy"

DAILY_SEND_HOUR = 9
DAILY_SEND_MINUTE = 0
# Without an explicit tzinfo, the job queue treats the send time as UTC.
DAILY_SEND_TZ = ZoneInfo("America/Los_Angeles")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("faevad-social-bot")
# httpx logs every request URL at INFO, and Telegram API URLs contain the
# bot token -- keep those (and the constant long-polling noise) out of the log.
logging.getLogger("httpx").setLevel(logging.WARNING)

cfg = load_config()

# Tracks the catalog_number currently awaiting a caption reply, if any.
# A message from the artist only counts as a caption while this is set --
# unsolicited messages are ignored rather than accidentally consumed.
_awaiting_caption_for = {"catalog_number": None}


def load_queue() -> list[dict]:
    with open(QUEUE_PATH) as f:
        return yaml.safe_load(f)


def save_queue(queue: list[dict]) -> None:
    with open(QUEUE_PATH, "w") as f:
        yaml.dump(queue, f, sort_keys=False, allow_unicode=True, default_flow_style=False)


def load_artwork() -> dict:
    with open(ARTWORK_PATH) as f:
        return yaml.safe_load(f)


def find_piece(artwork: dict, piece_id: str) -> dict | None:
    for collection in artwork["collections"]:
        for piece in collection["pieces"]:
            if piece["id"] == piece_id:
                return piece
    return None


def next_unposted_entry(queue: list[dict]) -> dict | None:
    unposted = [e for e in queue if not e.get("posted")]
    if not unposted:
        return None
    return min(unposted, key=lambda e: e["catalog_number"])


def git_commit_and_push(entry: dict, piece_title: str) -> None:
    message = (
        f"Post catalog #{entry['catalog_number']} ({piece_title}) "
        f"to Instagram/Pinterest"
    )
    subprocess.run(["git", "add", "social/queue.yml"], cwd=REPO_ROOT, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=REPO_ROOT, check=True)
    subprocess.run(
        ["git", "push", PUSH_URL, "HEAD:main"],
        cwd=REPO_ROOT,
        check=True,
        env={
            **os.environ,
            "GIT_SSH_COMMAND": f"ssh -i {DEPLOY_KEY_PATH} -o IdentitiesOnly=yes",
        },
    )


async def send_daily_prompt(context: ContextTypes.DEFAULT_TYPE) -> None:
    queue = load_queue()
    entry = next_unposted_entry(queue)
    if entry is None:
        await context.bot.send_message(
            chat_id=cfg.telegram_chat_id,
            text="Queue is empty -- every catalogued piece has been posted. "
            "Add more pieces to social/queue.yml to keep the daily posts going.",
        )
        return

    artwork = load_artwork()
    piece = find_piece(artwork, entry["id"])
    if piece is None:
        log.error("queue entry %s has no matching artwork.yml piece", entry["id"])
        return

    piece_url = f"{SITE_BASE_URL}/piece-{entry['id']}.html"
    await context.bot.send_message(
        chat_id=cfg.telegram_chat_id,
        text=(
            f"Day's piece (#{entry['catalog_number']}): {piece['title']}\n"
            f"{piece_url}\n\n"
            f"Reply with today's caption to post it."
        ),
    )
    _awaiting_caption_for["catalog_number"] = entry["catalog_number"]
    log.info("sent daily prompt for catalog #%s (%s)", entry["catalog_number"], entry["id"])


async def handle_caption_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != cfg.telegram_chat_id:
        return
    pending_number = _awaiting_caption_for["catalog_number"]
    if pending_number is None:
        return

    caption = update.message.text
    queue = load_queue()
    entry = next(e for e in queue if e["catalog_number"] == pending_number)
    artwork = load_artwork()
    piece = find_piece(artwork, entry["id"])
    image_url = f"{SITE_BASE_URL}/{piece['image']}"

    await update.message.reply_text("Posting now...")

    results = platforms.post_to_buffer(
        cfg,
        image_url=image_url,
        caption=caption,
        title=piece["title"],
        alt_text=piece["alt"],
        piece_url=f"{SITE_BASE_URL}/piece-{entry['id']}.html",
    )
    for platform, result in results.items():
        if result.startswith("error"):
            log.error("%s post failed: %s", platform, result)

    entry["posted"] = True
    entry["post_date"] = dt.date.today().isoformat()
    entry["caption"] = caption
    entry["platforms"] = results
    save_queue(queue)
    try:
        git_commit_and_push(entry, piece["title"])
    except subprocess.CalledProcessError:
        log.exception("git commit/push of social/queue.yml failed")
        results["git"] = "error: queue.yml saved locally but not pushed -- check the bot's log"

    _awaiting_caption_for["catalog_number"] = None

    summary = "\n".join(f"{k}: {v}" for k, v in results.items())
    await update.message.reply_text(f"Done. Results:\n{summary}")


def main() -> None:
    application = Application.builder().token(cfg.telegram_bot_token).build()
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_caption_reply)
    )
    application.job_queue.run_daily(
        send_daily_prompt,
        time=dt.time(hour=DAILY_SEND_HOUR, minute=DAILY_SEND_MINUTE, tzinfo=DAILY_SEND_TZ),
    )
    log.info("faevad-social-bot starting, daily prompt at %02d:%02d", DAILY_SEND_HOUR, DAILY_SEND_MINUTE)
    application.run_polling()


if __name__ == "__main__":
    main()
