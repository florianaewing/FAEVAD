#!/usr/bin/env python3
"""FAEVAD daily social posting bot.

Runs as a long-lived local process. Each day at DAILY_SEND_HOUR:MINUTE it
finds the next un-posted piece in social/queue.yml (lowest catalog_number
with posted: false), and messages the artist on Telegram asking for that
day's caption. Whatever the artist replies with is used verbatim as the
caption -- this bot never drafts or edits post text.

Once a caption reply arrives, each platform posts at its own best time of
day, per social/strategy.yml: Instagram, Pinterest and X via Buffer, and
Bluesky, Mastodon, Threads and the Facebook Page directly -- each optional
platform only once its credentials are configured. strategy.py adds the
piece link and hashtags under the caption per platform. A failed platform
sends a Telegram bug report right away; once all have posted, the results
go into social/queue.yml, get committed + pushed, and a summary is sent.

After posting, the bot forwards new comments from the direct platforms for
a few hours, collects each post's stats two days later, and sends a weekly
stats summary on Sundays (tracking.py). Reddit and Facebook Marketplace are
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
import strategy
import tracking
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

# Early enough that a reply usually comes in before the first posting slot
# (9am, see social/strategy.yml).
DAILY_SEND_HOUR = 8
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


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except FileNotFoundError:
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = STATE_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(state, indent=1))
    tmp_path.replace(STATE_PATH)


def load_pending_catalog_number() -> int | None:
    return load_state().get("awaiting_catalog_number")


def save_pending_catalog_number(catalog_number: int | None) -> None:
    state = load_state()
    state["awaiting_catalog_number"] = catalog_number
    save_state(state)


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


DIRECT_PLATFORMS = ("bluesky", "mastodon", "threads", "facebook")

# Serializes read-modify-write of the day's schedule in state.json, since
# several platforms can share a posting slot and finish at the same time.
schedule_lock = asyncio.Lock()


def enabled_platforms() -> list[str]:
    enabled = ["instagram", "pinterest"]
    for platform in ("x", *DIRECT_PLATFORMS):
        if getattr(cfg, f"{platform}_enabled"):
            enabled.append(platform)
    return enabled


async def wait_for_buffer_post(post_id: str) -> str:
    """Polls Buffer until a post is published or failed.

    Returns "published: <link>", "error: <reason>", or "pending: ..." if
    Buffer still hadn't finished by the timeout.
    """
    deadline = asyncio.get_running_loop().time() + STATUS_POLL_TIMEOUT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(STATUS_POLL_INTERVAL_SECONDS)
        try:
            status = await asyncio.to_thread(platforms.get_buffer_post_status, cfg.buffer_api_key, post_id)
        except Exception:
            log.exception("checking Buffer post %s failed; will retry", post_id)
            continue
        if status["status"] == "sent":
            return f"published: {status['link'] or '(no link from Buffer)'}"
        if status["status"] == "error":
            return f"error: {status['error'] or 'Buffer reported an error'}"
    return (
        f"pending: Buffer hadn't published it after "
        f"{STATUS_POLL_TIMEOUT_SECONDS // 60} min -- check Buffer (post {post_id})"
    )


def publish_directly(platform: str, post: strategy.PlatformPost, image_path: Path, image_url: str, alt_text: str) -> platforms.Published:
    if platform == "bluesky":
        return platforms.post_to_bluesky(
            cfg.bluesky_handle, cfg.bluesky_app_password, post.text, image_path, alt_text,
            post.link, post.hashtags,
        )
    if platform == "mastodon":
        return platforms.post_to_mastodon(
            cfg.mastodon_instance_url, cfg.mastodon_access_token, post.text, image_path, alt_text
        )
    if platform == "threads":
        return platforms.post_to_threads(
            cfg.threads_user_id, current_threads_token(), post.text, image_url, alt_text, post.topic_tag
        )
    return platforms.post_to_facebook(
        cfg.facebook_page_id, cfg.facebook_page_access_token, post.text, image_url, alt_text
    )


async def publish(platform: str, entry: dict, piece: dict, caption: str, day: dt.date) -> str:
    """Posts one platform's version of the day's piece; returns its result string."""
    artwork = load_artwork()
    image_url = f"{SITE_BASE_URL}/{piece['image']}"
    piece_url = f"{SITE_BASE_URL}/piece-{entry['id']}.html"
    post = strategy.build_post(
        strategy.load_strategy(), platform, caption, piece_url, piece["title"],
        collection_of(artwork, entry["id"]), day,
    )
    try:
        if platform in platforms.BUFFER_PLATFORMS:
            post_id = await asyncio.to_thread(
                platforms.post_to_buffer_channel, cfg, platform, post.text, image_url,
                piece["alt"], post.title, post.destination_url,
            )
            result = await wait_for_buffer_post(post_id)
            ref = post_id
        else:
            published = await asyncio.to_thread(
                publish_directly, platform, post, REPO_ROOT / piece["image"], image_url, piece["alt"]
            )
            result, ref = f"published: {published.link}", published.ref
    except Exception as exc:
        log.error("%s post failed:\n%s", platform, redact("".join(traceback.format_exception(exc))))
        return f"error: {redact(str(exc))}"
    if result.startswith("published"):
        tracking.record_post(entry, piece["title"], platform, result.split(": ", 1)[1], ref)
    return redact(result)


def format_bug_report(platform: str, entry: dict, piece: dict, caption: str, error: str) -> str:
    now = dt.datetime.now(DAILY_SEND_TZ).strftime("%Y-%m-%d %H:%M %Z")
    route = "Buffer" if platform in platforms.BUFFER_PLATFORMS else "direct API"
    report = "\n".join([
        f"🐞 Bug report: {platform} ({route})",
        f"Piece: #{entry['catalog_number']} {piece['title']} ({entry['id']})",
        f"Time: {now}",
        f"Image: {SITE_BASE_URL}/{piece['image']}",
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


def slot_datetime(platform: str, day: dt.date, now: dt.datetime) -> dt.datetime:
    """Today's posting slot for a platform, or now if it has already passed."""
    slot = dt.datetime.combine(day, strategy.post_time(strategy.load_strategy(), platform), DAILY_SEND_TZ)
    return max(slot, now)


def arm_slot(job_queue, platform: str, due: dt.datetime) -> None:
    job_queue.run_once(run_slot, when=due, data=platform, name=f"post-{platform}")


async def run_slot(context: ContextTypes.DEFAULT_TYPE) -> None:
    platform = context.job.data
    schedule = load_state().get("schedule")
    if not schedule or schedule["platforms"][platform]["result"] is not None:
        return  # already done, e.g. re-armed twice around a restart
    queue = load_queue()
    entry = next(e for e in queue if e["catalog_number"] == schedule["catalog_number"])
    piece = find_piece(load_artwork(), entry["id"])
    day = dt.date.fromisoformat(schedule["date"])

    result = await publish(platform, entry, piece, schedule["caption"], day)
    if result.startswith("error"):
        log.error("%s post for catalog #%s: %s", platform, entry["catalog_number"], result)
        await context.bot.send_message(
            chat_id=cfg.telegram_chat_id,
            text=format_bug_report(platform, entry, piece, schedule["caption"], result.split(": ", 1)[1]),
        )

    async with schedule_lock:
        state = load_state()
        state["schedule"]["platforms"][platform]["result"] = result
        save_state(state)
        results = {p: s["result"] for p, s in state["schedule"]["platforms"].items()}
        if any(r is None for r in results.values()):
            return
        state.pop("schedule")
        save_state(state)
    await finish_day(context, schedule, results)


async def finish_day(context: ContextTypes.DEFAULT_TYPE, schedule: dict, results: dict[str, str]) -> None:
    """Every platform has answered: record the day in queue.yml and report."""
    queue = load_queue()
    entry = next(e for e in queue if e["catalog_number"] == schedule["catalog_number"])
    piece = find_piece(load_artwork(), entry["id"])
    entry["posted"] = True
    entry["post_date"] = schedule["date"]
    entry["caption"] = schedule["caption"]
    entry["platforms"] = {p: r[:QUEUE_RESULT_MAX_CHARS] for p, r in results.items()}
    save_queue(queue)
    report = format_report(piece["title"], entry["platforms"])
    try:
        git_commit_and_push(entry, piece["title"])
    except subprocess.CalledProcessError:
        log.exception("git commit/push of social/queue.yml failed")
        report += "\n❌ git: queue.yml saved locally but not pushed -- check the bot's log"
    await context.bot.send_message(chat_id=cfg.telegram_chat_id, text=report)


async def handle_caption_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != cfg.telegram_chat_id:
        return
    pending_number = load_pending_catalog_number()
    if pending_number is None:
        return
    # Clear before scheduling, so a second message can't schedule the same
    # piece twice.
    save_pending_catalog_number(None)

    now = dt.datetime.now(DAILY_SEND_TZ)
    due = {platform: slot_datetime(platform, now.date(), now) for platform in enabled_platforms()}
    async with schedule_lock:
        state = load_state()
        state["schedule"] = {
            "catalog_number": pending_number,
            "caption": update.message.text,
            "date": now.date().isoformat(),
            "platforms": {p: {"due": when.isoformat(), "result": None} for p, when in due.items()},
        }
        save_state(state)
    for platform, when in due.items():
        arm_slot(context.job_queue, platform, when)

    lines = [
        f"{when.strftime('%-I:%M %p') if when > now else 'now'} -- {platform}"
        for platform, when in sorted(due.items(), key=lambda item: item[1])
    ]
    await update.message.reply_text(
        "Got it. Posting schedule (Pacific):\n" + "\n".join(lines)
        + "\n\nI'll send a bug report right away if anything fails, and a summary once they're all out."
    )


async def resume_schedule(application: Application) -> None:
    """Re-arms any posting slots left over from before a restart."""
    schedule = load_state().get("schedule")
    if not schedule:
        return
    now = dt.datetime.now(DAILY_SEND_TZ)
    for platform, slot in schedule["platforms"].items():
        if slot["result"] is None:
            arm_slot(application.job_queue, platform, max(dt.datetime.fromisoformat(slot["due"]), now))
            log.info("re-armed %s post for catalog #%s", platform, schedule["catalog_number"])


# --- Comment alerts and stats ----------------------------------------------

COMMENT_CHECK_INTERVAL_SECONDS = 5 * 60


def fetch_comments(post: dict) -> list[platforms.Comment]:
    ref = post["ref"]
    if post["platform"] == "bluesky":
        own = f"@{cfg.bluesky_handle}"
        return [c for c in platforms.bluesky_comments(ref) if c.author != own]
    if post["platform"] == "mastodon":
        return platforms.mastodon_comments(cfg.mastodon_instance_url, cfg.mastodon_access_token, ref)
    if post["platform"] == "threads":
        return platforms.threads_comments(current_threads_token(), ref)
    return platforms.facebook_comments(cfg.facebook_page_access_token, ref)


async def check_comments(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forwards new comments on recent posts, so they can be answered quickly.

    Only the direct platforms: Buffer has no API for comments, so Instagram,
    Pinterest and X comments come through those apps' own notifications.
    """
    posts = tracking.load_posts()
    changed = False
    for post in tracking.being_watched(posts):
        if post["platform"] not in DIRECT_PLATFORMS:
            continue
        try:
            comments = await asyncio.to_thread(fetch_comments, post)
        except Exception as exc:
            log.error("checking %s comments failed: %s", post["platform"], redact(str(exc)))
            continue
        for comment in comments:
            if comment.ref in post["seen_comments"]:
                continue
            post["seen_comments"].append(comment.ref)
            changed = True
            await context.bot.send_message(
                chat_id=cfg.telegram_chat_id,
                text=(
                    f"💬 {post['platform']} -- {comment.author} on {post['title']}:\n"
                    f"{comment.text}\n\n{comment.link}"
                )[:TELEGRAM_MAX_CHARS],
            )
    if changed:
        tracking.save_posts(posts)


def fetch_metrics(post: dict) -> dict[str, float]:
    ref = post["ref"]
    if post["platform"] in platforms.BUFFER_PLATFORMS:
        return platforms.get_buffer_post_metrics(cfg.buffer_api_key, ref)
    if post["platform"] == "bluesky":
        return platforms.bluesky_metrics(ref)
    if post["platform"] == "mastodon":
        return platforms.mastodon_metrics(cfg.mastodon_instance_url, cfg.mastodon_access_token, ref)
    if post["platform"] == "threads":
        return platforms.threads_metrics(current_threads_token(), ref)
    return platforms.facebook_metrics(cfg.facebook_page_access_token, ref)


async def collect_metrics(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Records each post's stats once it's two days old."""
    posts = tracking.load_posts()
    changed = False
    for post in tracking.due_for_metrics(posts):
        try:
            post["metrics"] = await asyncio.to_thread(fetch_metrics, post)
        except Exception as exc:
            log.error("collecting %s stats failed: %s", post["platform"], redact(str(exc)))
            continue
        changed = True
    if changed:
        tracking.save_posts(posts)


# Numbers that count people seeing a post rather than acting on it; left out
# of the engagement score used to rank posts.
REACH_METRIC_WORDS = ("impression", "reach", "view")


def engagement_score(metrics: dict[str, float]) -> float:
    return sum(
        value for name, value in metrics.items()
        if not any(word in name.lower() for word in REACH_METRIC_WORDS)
    )


def format_weekly_report(posts: list[dict]) -> str:
    if not posts:
        return "📊 Weekly stats: no posts with collected stats this week."
    lines = ["📊 Weekly stats (posts from the past week, measured 2 days after posting)"]
    for platform in sorted({p["platform"] for p in posts}):
        mine = [p for p in posts if p["platform"] == platform]
        totals: dict[str, float] = {}
        for post in mine:
            for name, value in post["metrics"].items():
                totals[name] = totals.get(name, 0) + value
        best = max(mine, key=lambda p: engagement_score(p["metrics"]))
        numbers = ", ".join(f"{name} {value:g}" for name, value in sorted(totals.items()))
        lines += [
            "",
            f"{platform} ({len(mine)} posts): {numbers or 'no numbers yet'}",
            f"  best: {best['title']} -- {best['link']}",
        ]
    return "\n".join(lines)[:TELEGRAM_MAX_CHARS]


async def send_weekly_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=9)
    posts = tracking.collected_since(tracking.load_posts(), since)
    await context.bot.send_message(chat_id=cfg.telegram_chat_id, text=format_weekly_report(posts))


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
    application = (
        Application.builder().token(cfg.telegram_bot_token).post_init(resume_schedule).build()
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_caption_reply)
    )
    application.add_error_handler(report_crash)
    jobs = application.job_queue
    jobs.run_daily(
        send_daily_prompt,
        time=dt.time(hour=DAILY_SEND_HOUR, minute=DAILY_SEND_MINUTE, tzinfo=DAILY_SEND_TZ),
    )
    jobs.run_repeating(check_comments, interval=COMMENT_CHECK_INTERVAL_SECONDS, first=60)
    jobs.run_daily(collect_metrics, time=dt.time(hour=7, minute=30, tzinfo=DAILY_SEND_TZ))
    # PTB numbers weekdays 0-6 from Sunday.
    jobs.run_daily(send_weekly_report, time=dt.time(hour=18, tzinfo=DAILY_SEND_TZ), days=(0,))
    log.info("faevad-social-bot starting, daily prompt at %02d:%02d", DAILY_SEND_HOUR, DAILY_SEND_MINUTE)
    application.run_polling()


if __name__ == "__main__":
    main()
