#!/usr/bin/env python3
"""FAEVAD daily social posting bot.

Runs as a long-lived local process. Each day at DAILY_SEND_HOUR:MINUTE it
finds the next un-posted piece in social/queue.yml (lowest catalog_number
with posted: false), and messages the artist on Telegram asking for that
day's caption. Whatever the artist replies with is used verbatim as the
caption -- this bot never drafts or edits post text.

Once a caption reply arrives, it posts to Instagram, Pinterest and X (via
Buffer) and to Bluesky, Mastodon, Threads and the Facebook Page (directly)
-- each optional platform only once its credentials are configured. Every
platform except Instagram and Pinterest gets a link to the piece's page
added under the caption. It records the results in social/queue.yml,
commits + pushes that change to the repo, and sends a separate Telegram bug
report for each platform that failed. Reddit and Facebook Marketplace are
intentionally not part of this automated flow -- see platforms.py.

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
import traceback
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

# Threads tokens expire after 60 days unless refreshed; the current one is
# kept next to the state file, and refreshed whenever it's a week old.
THREADS_TOKEN_PATH = STATE_PATH.parent / "threads_token.json"
THREADS_TOKEN_REFRESH_AFTER = dt.timedelta(days=7)

# queue.yml keeps a short version of each failure; the full text goes in
# the bug report. Telegram rejects messages over 4096 characters.
QUEUE_RESULT_MAX_CHARS = 300
TELEGRAM_MAX_CHARS = 4000


def redact(text: str) -> str:
    """Strips every configured secret out of text bound for Telegram or the log.

    Error messages can quote request URLs, and some APIs (Threads, Facebook)
    take the access token as a URL parameter.
    """
    secrets = [
        cfg.telegram_bot_token,
        cfg.buffer_api_key,
        cfg.bluesky_app_password,
        cfg.mastodon_access_token,
        cfg.threads_access_token,
        cfg.facebook_page_access_token,
    ]
    try:
        secrets.append(json.loads(THREADS_TOKEN_PATH.read_text())["token"])
    except (FileNotFoundError, KeyError, ValueError):
        pass
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


def current_threads_token() -> str:
    """Returns a usable Threads token, refreshing it if it's a week old.

    If the artist puts a new token in the .env (e.g. after the old one
    lapsed), that one takes over from whatever was refreshed before.
    """
    try:
        saved = json.loads(THREADS_TOKEN_PATH.read_text())
    except FileNotFoundError:
        saved = None
    if saved is None or saved["seed"] != cfg.threads_access_token:
        saved = {"seed": cfg.threads_access_token, "token": cfg.threads_access_token, "refreshed_at": None}

    refreshed_at = saved["refreshed_at"] and dt.datetime.fromisoformat(saved["refreshed_at"])
    if not refreshed_at or dt.datetime.now(dt.timezone.utc) - refreshed_at > THREADS_TOKEN_REFRESH_AFTER:
        try:
            saved["token"] = platforms.refresh_threads_token(saved["token"])
            saved["refreshed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        except Exception as exc:
            # Posting with the current token may still work; if it doesn't,
            # the Threads bug report will say so.
            log.error("Threads token refresh failed: %s", redact(str(exc)))
        THREADS_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = THREADS_TOKEN_PATH.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(saved))
        tmp_path.chmod(0o600)
        tmp_path.replace(THREADS_TOKEN_PATH)
    return saved["token"]


def load_queue() -> list[dict]:
    with open(QUEUE_PATH) as f:
        return yaml.safe_load(f)


class _QueueDumper(yaml.SafeDumper):
    """Writes empty fields as blank (`caption:`) rather than `null`, matching the file."""


_QueueDumper.add_representer(
    type(None), lambda dumper, _: dumper.represent_scalar("tag:yaml.org,2002:null", "")
)


def save_queue(queue: list[dict]) -> None:
    # yaml.dump drops comments, so carry the file's leading comment block
    # over by hand, and keep a blank line between entries for readability.
    header = []
    for line in QUEUE_PATH.read_text().splitlines(keepends=True):
        if line.startswith("#") or not line.strip():
            header.append(line)
        else:
            break
    body = yaml.dump(
        queue, Dumper=_QueueDumper, sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    body = body.replace("\n- catalog_number:", "\n\n- catalog_number:")
    QUEUE_PATH.write_text("".join(header) + body)


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


# Watercolors get Wednesdays; every other day posts from the other
# collections (inks). When a day's own pool runs dry it falls back to the
# other one, so the queue only reports empty once everything is posted.
WEDNESDAY_COLLECTION = "watercolors"


def collection_of(artwork: dict, piece_id: str) -> str | None:
    for collection in artwork["collections"]:
        if any(piece["id"] == piece_id for piece in collection["pieces"]):
            return collection["slug"]
    return None


def next_unposted_entry(queue: list[dict], artwork: dict, day: dt.date) -> dict | None:
    unposted = sorted(
        (e for e in queue if not e.get("posted")), key=lambda e: e["catalog_number"]
    )
    is_wednesday = day.weekday() == 2
    todays = [
        e for e in unposted
        if (collection_of(artwork, e["id"]) == WEDNESDAY_COLLECTION) == is_wednesday
    ]
    return (todays or unposted or [None])[0]


def git_commit_and_push(entry: dict, piece_title: str) -> None:
    message = f"Post catalog #{entry['catalog_number']} ({piece_title}) to social media"
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
    artwork = load_artwork()
    entry = next_unposted_entry(queue, artwork, dt.datetime.now(DAILY_SEND_TZ).date())
    if entry is None:
        await context.bot.send_message(
            chat_id=cfg.telegram_chat_id,
            text="Queue is empty -- every catalogued piece has been posted. "
            "Add more pieces to social/queue.yml to keep the daily posts going.",
        )
        return

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
            + (
                ""
                if piece.get("alt")
                else "\n\n⚠️ This piece has no alt text yet -- add it to "
                "_data/artwork.yml before replying, or the post goes out without it."
            )
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


async def post_to_buffer_and_wait(**post_args) -> dict[str, str]:
    created = await asyncio.to_thread(platforms.post_to_buffer, cfg, **post_args)
    return await wait_for_buffer_results(created)


async def post_directly(platform: str, post, *args) -> tuple[str, str]:
    """Runs one direct platform post; returns (platform, result string)."""
    try:
        link = await asyncio.to_thread(post, *args)
    except Exception as exc:
        log.error("%s post failed:\n%s", platform, redact("".join(traceback.format_exception(exc))))
        return platform, f"error: {redact(str(exc))}"
    return platform, f"published: {link}"


def direct_posts(caption: str, image_path: Path, image_url: str, alt_text: str, piece_url: str) -> list:
    """The direct-platform posts to run today, for whichever are configured."""
    posts = []
    if cfg.bluesky_enabled:
        posts.append(post_directly(
            "bluesky", platforms.post_to_bluesky,
            cfg.bluesky_handle, cfg.bluesky_app_password, caption, image_path, alt_text, piece_url,
        ))
    if cfg.mastodon_enabled:
        posts.append(post_directly(
            "mastodon", platforms.post_to_mastodon,
            cfg.mastodon_instance_url, cfg.mastodon_access_token, caption, image_path, alt_text,
        ))
    if cfg.threads_enabled:
        # The token refresh is a network call too, so it runs in the same
        # worker thread as the post rather than blocking the bot.
        posts.append(post_directly(
            "threads",
            lambda: platforms.post_to_threads(
                cfg.threads_user_id, current_threads_token(), caption, image_url, alt_text
            ),
        ))
    if cfg.facebook_enabled:
        posts.append(post_directly(
            "facebook", platforms.post_to_facebook,
            cfg.facebook_page_id, cfg.facebook_page_access_token, caption, image_url, alt_text,
        ))
    return posts


def format_bug_report(platform: str, entry: dict, piece: dict, image_url: str, caption: str, error: str) -> str:
    now = dt.datetime.now(DAILY_SEND_TZ).strftime("%Y-%m-%d %H:%M %Z")
    route = "Buffer" if platform in platforms.BUFFER_PLATFORMS else "direct API"
    report = "\n".join([
        f"🐞 Bug report: {platform} ({route})",
        f"Piece: #{entry['catalog_number']} {piece['title']} ({entry['id']})",
        f"Time: {now}",
        f"Image: {image_url}",
        f"Caption: {len(caption)} characters",
        "",
        error,
    ])
    return report[:TELEGRAM_MAX_CHARS]


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
    image_path = REPO_ROOT / piece["image"]
    piece_url = f"{SITE_BASE_URL}/piece-{entry['id']}.html"
    linked_caption = f"{caption}\n\n{piece_url}"

    await update.message.reply_text("Posting now... I'll report back once every platform has answered.")

    buffer_results, *direct_results = await asyncio.gather(
        post_to_buffer_and_wait(
            image_url=image_url,
            caption=caption,
            linked_caption=linked_caption,
            title=piece["title"],
            alt_text=piece["alt"],
            piece_url=piece_url,
        ),
        *direct_posts(linked_caption, image_path, image_url, piece["alt"], piece_url),
    )
    results = {p: redact(r) for p, r in {**buffer_results, **dict(direct_results)}.items()}
    for platform, result in results.items():
        if not result.startswith("published"):
            log.error("%s post for catalog #%s: %s", platform, pending_number, result)

    entry["posted"] = True
    entry["post_date"] = dt.date.today().isoformat()
    entry["caption"] = caption
    entry["platforms"] = {p: r[:QUEUE_RESULT_MAX_CHARS] for p, r in results.items()}
    save_queue(queue)
    report = format_report(piece["title"], {p: r[:QUEUE_RESULT_MAX_CHARS] for p, r in results.items()})
    try:
        git_commit_and_push(entry, piece["title"])
    except subprocess.CalledProcessError:
        log.exception("git commit/push of social/queue.yml failed")
        report += "\n❌ git: queue.yml saved locally but not pushed -- check the bot's log"

    await update.message.reply_text(report)
    for platform, result in results.items():
        if result.startswith("error"):
            await update.message.reply_text(
                format_bug_report(platform, entry, piece, image_url, caption, result.split(": ", 1)[1])
            )


async def report_crash(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a bug report for anything that crashes the bot mid-task."""
    trace = redact("".join(traceback.format_exception(context.error)))
    log.error("unhandled error:\n%s", trace)
    text = f"🐞 Bug report: the bot crashed\n\n{trace}"
    # Keep the end of the traceback -- that's where the actual error is.
    if len(text) > TELEGRAM_MAX_CHARS:
        text = "🐞 Bug report: the bot crashed\n\n..." + text[-(TELEGRAM_MAX_CHARS - 40):]
    try:
        await context.bot.send_message(chat_id=cfg.telegram_chat_id, text=text)
    except Exception:
        log.exception("couldn't send the crash report to Telegram")


def main() -> None:
    application = Application.builder().token(cfg.telegram_bot_token).build()
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_caption_reply)
    )
    application.add_error_handler(report_crash)
    application.job_queue.run_daily(
        send_daily_prompt,
        time=dt.time(hour=DAILY_SEND_HOUR, minute=DAILY_SEND_MINUTE, tzinfo=DAILY_SEND_TZ),
    )
    log.info("faevad-social-bot starting, daily prompt at %02d:%02d", DAILY_SEND_HOUR, DAILY_SEND_MINUTE)
    application.run_polling()


if __name__ == "__main__":
    main()
