"""Local record of every published post, for stats and comment alerts.

Kept outside the repo, next to the bot's other state: it changes all day
(new comments seen, stats collected) and isn't something to commit. Each
record is one platform's post of one piece.
"""

import datetime as dt
import json
from pathlib import Path

POSTS_PATH = Path.home() / ".local" / "state" / "faevad-social-bot" / "posts.json"

# Stats are collected once, when a post is this old -- by then most of the
# engagement it's going to get has happened.
METRICS_AFTER = dt.timedelta(hours=48)
# New comments are checked for this long after posting, when a quick reply
# matters most.
WATCH_COMMENTS_FOR = dt.timedelta(hours=3)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def load_posts() -> list[dict]:
    try:
        return json.loads(POSTS_PATH.read_text())
    except FileNotFoundError:
        return []


def save_posts(posts: list[dict]) -> None:
    POSTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = POSTS_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(posts, indent=1))
    tmp_path.replace(POSTS_PATH)


def record_post(entry: dict, piece_title: str, platform: str, link: str, ref: str) -> None:
    """ref is the platform's id for the post -- or Buffer's, for Buffer platforms."""
    posts = load_posts()
    posts.append({
        "catalog_number": entry["catalog_number"],
        "piece_id": entry["id"],
        "title": piece_title,
        "platform": platform,
        "link": link,
        "ref": ref,
        "posted_at": _now().isoformat(),
        "metrics": None,
        "seen_comments": [],
    })
    save_posts(posts)


def posted_at(post: dict) -> dt.datetime:
    return dt.datetime.fromisoformat(post["posted_at"])


def due_for_metrics(posts: list[dict]) -> list[dict]:
    return [p for p in posts if p["metrics"] is None and _now() - posted_at(p) >= METRICS_AFTER]


def being_watched(posts: list[dict]) -> list[dict]:
    return [p for p in posts if _now() - posted_at(p) < WATCH_COMMENTS_FOR]


def collected_since(posts: list[dict], since: dt.datetime) -> list[dict]:
    return [p for p in posts if p["metrics"] is not None and posted_at(p) >= since]
