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

import asyncio
import datetime as dt
import json
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

# The catalog_number currently awaiting a caption reply, if any, is kept on
# disk so a restart between the morning prompt and the reply doesn't lose it.
# A message from the artist only counts as a caption while this is set --
# unsolicited messages are ignored rather than accidentally consumed. Lives
# outside the repo since it's local runtime state, not something to commit.
STATE_PATH = Path.home() / ".local" / "state" / "faevad-social-bot" / "state.json"

# After handing posts to Buffer, poll until each is published or failed.
STATUS_POLL_INTERVAL_SECONDS = 15
STATUS_POLL_TIMEOUT_SECONDS = 5 * 60


def load_queue() -> list[dict]:
    with open(QUEUE_PATH) as f:
        return yaml.safe_load(f)


def save_queue(queue: list[dict]) -> None:
    with open(QUEUE_PATH, "w") as f:
        yaml.dump(queue, f, sort_keys=False, allow_unicode=True, default_flow_style=False)


def load_pending_catalog_number() -> int | None:
    try:
        return json.loads(STATE_PATH.read_text()).get("awaiting_catalog_number")
    except FileNotFoundError:
        return None


def save_pending_catalog_number(catalog_number: int | None) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = STATE_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps({"awaiting_catalog_number": catalog_number}))
    tmp_path.replace(STATE_PATH)


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
    save_pending_catalog_number(entry["catalog_number"])
    log.info("sent daily prompt for catalog #%s (%s)", entry["catalog_number"], entry["id"])


async def wait_for_buffer_results(created: dict[str, dict]) -> dict[str, str]:
    """Polls Buffer until each accepted post is published or failed.

    Returns one result string per platform: "published: <link>",
    "error: <reason>", or "pending: ..." if Buffer still hadn't finished
    by the timeout.
    """
    results = {p: f"error: {r['error']}" for p, r in created.items() if "error" in r}
    waiting = {p: r["post_id"] for p, r in created.items() if "post_id" in r}
    deadline = asyncio.get_running_loop().time() + STATUS_POLL_TIMEOUT_SECONDS
    while waiting:
        await asyncio.sleep(STATUS_POLL_INTERVAL_SECONDS)
        for platform, post_id in list(waiting.items()):
            try:
                status = await asyncio.to_thread(
                    platforms.get_buffer_post_status, cfg.buffer_api_key, post_id
                )
            except Exception:
                log.exception("checking %s post %s failed; will retry", platform, post_id)
                continue
            if status["status"] == "sent":
                results[platform] = f"published: {status['link'] or '(no link from Buffer)'}"
                del waiting[platform]
            elif status["status"] == "error":
                results[platform] = f"error: {status['error'] or 'Buffer reported an error'}"
                del waiting[platform]
        if asyncio.get_running_loop().time() >= deadline:
            for platform, post_id in waiting.items():
                results[platform] = (
                    f"pending: Buffer hadn't published it after "
                    f"{STATUS_POLL_TIMEOUT_SECONDS // 60} min -- check Buffer (post {post_id})"
                )
            break
    return {p: results[p] for p in created}


def format_report(piece_title: str, results: dict[str, str]) -> str:
    icons = {"published": "✅", "error": "❌", "pending": "⏳"}
    lines = [
        f"{icons.get(result.split(':', 1)[0], '❓')} {platform}: {result.split(': ', 1)[1]}"
        for platform, result in results.items()
    ]
    if all(result.startswith("published") for result in results.values()):
        headline = f"✅ {piece_title} is live everywhere."
    else:
        headline = f"⚠️ Problem posting {piece_title} -- see below."
    return "\n".join([headline, *lines])


async def handle_caption_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != cfg.telegram_chat_id:
        return
    pending_number = load_pending_catalog_number()
    if pending_number is None:
        return
    # Clear before posting, so a second message sent while this one is still
    # posting can't post the same piece twice.
    save_pending_catalog_number(None)

    caption = update.message.text
    queue = load_queue()
    entry = next(e for e in queue if e["catalog_number"] == pending_number)
    artwork = load_artwork()
    piece = find_piece(artwork, entry["id"])
    image_url = f"{SITE_BASE_URL}/{piece['image']}"

    await update.message.reply_text("Posting now... I'll report back once Buffer has published it.")

    created = await asyncio.to_thread(
        platforms.post_to_buffer,
        cfg,
        image_url=image_url,
        caption=caption,
        title=piece["title"],
        alt_text=piece["alt"],
        piece_url=f"{SITE_BASE_URL}/piece-{entry['id']}.html",
    )
    results = await wait_for_buffer_results(created)
    for platform, result in results.items():
        if not result.startswith("published"):
            log.error("%s post for catalog #%s: %s", platform, pending_number, result)

    entry["posted"] = True
    entry["post_date"] = dt.date.today().isoformat()
    entry["caption"] = caption
    entry["platforms"] = results
    save_queue(queue)
    report = format_report(piece["title"], results)
    try:
        git_commit_and_push(entry, piece["title"])
    except subprocess.CalledProcessError:
        log.exception("git commit/push of social/queue.yml failed")
        report += "\n❌ git: queue.yml saved locally but not pushed -- check the bot's log"

    await update.message.reply_text(report)


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
