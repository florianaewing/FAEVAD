"""Builds each platform's post from the artist's caption, per social/strategy.yml.

The caption is always used verbatim and always comes first. After it, a
platform may get the piece's page link (tagged with utm_source so site
analytics can tell which platform sent a visitor) and a few hashtags. When
a platform's length limit is tight, tags are dropped from the end -- the
caption is never shortened.
"""

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import yaml

STRATEGY_PATH = Path(__file__).resolve().parents[1] / "strategy.yml"

# Platforms whose caption gets the piece link. Instagram captions don't make
# links clickable, and Pinterest carries the link as the pin's destination.
LINKED_PLATFORMS = ("x", "bluesky", "mastodon", "threads", "facebook")

# Post length limits. X and Mastodon count every link as 23 characters.
LENGTH_LIMITS = {
    "x": 280,
    "bluesky": 300,
    "mastodon": 500,
    "threads": 500,
    "facebook": 5000,
    "instagram": 2200,
    "pinterest": 500,
}
SHORTENED_LINK_LENGTH = 23
LINK_SHORTENING_PLATFORMS = ("x", "mastodon")


@dataclass
class PlatformPost:
    text: str
    link: str | None = None       # the utm-tagged piece link, if the text includes it
    hashtags: list[str] = field(default_factory=list)
    topic_tag: str | None = None  # Threads only
    title: str | None = None      # Pinterest only
    destination_url: str | None = None  # Pinterest only: where the pin links to


def load_strategy() -> dict:
    with open(STRATEGY_PATH) as f:
        return yaml.safe_load(f)


WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def posts_on(strategy: dict, day: dt.date) -> bool:
    days = [d.lower() for d in strategy.get("post_days", WEEKDAYS)]
    unknown = set(days) - set(WEEKDAYS)
    if unknown:
        raise ValueError(f"unknown post_days in strategy.yml: {', '.join(sorted(unknown))}")
    return WEEKDAYS[day.weekday()] in days


def post_time(strategy: dict, platform: str) -> dt.time:
    hour, minute = strategy["post_times"][platform].split(":")
    return dt.time(int(hour), int(minute))


def tracked_link(piece_url: str, platform: str) -> str:
    return f"{piece_url}?utm_source={platform}&utm_medium=social&utm_campaign=daily_post"


def _length(platform: str, text: str, link: str | None) -> int:
    if link and platform in LINK_SHORTENING_PLATFORMS:
        return len(text) - len(link) + SHORTENED_LINK_LENGTH
    return len(text)


def pick_tags(strategy: dict, platform: str, collection: str, day: dt.date) -> list[str]:
    settings = strategy["platforms"].get(platform, {})
    tags_for = strategy["collections"].get(collection, {})
    candidates = list(settings.get("always", []))
    if day.weekday() == 2:
        candidates += tags_for.get("wednesday_tags", [])
    candidates += tags_for.get("tags", [])
    picked, seen = [], set()
    for tag in candidates:
        if tag.lower() not in seen:
            seen.add(tag.lower())
            picked.append(tag)
    return picked[: settings.get("max_tags", 0)]


def build_post(
    strategy: dict,
    platform: str,
    caption: str,
    piece_url: str,
    piece_title: str,
    collection: str,
    day: dt.date,
) -> PlatformPost:
    link = tracked_link(piece_url, platform) if platform in LINKED_PLATFORMS else None
    base = f"{caption}\n\n{link}" if link else caption

    hashtags = pick_tags(strategy, platform, collection, day)
    limit = LENGTH_LIMITS[platform]
    while hashtags:
        text = f"{base}\n\n" + " ".join(f"#{tag}" for tag in hashtags)
        if _length(platform, text, link) <= limit:
            break
        hashtags.pop()
    text = f"{base}\n\n" + " ".join(f"#{tag}" for tag in hashtags) if hashtags else base

    post = PlatformPost(text=text, link=link, hashtags=hashtags)
    collection_settings = strategy["collections"].get(collection, {})
    if platform == "threads":
        post.topic_tag = collection_settings.get("threads_topic")
    if platform == "pinterest":
        suffix = collection_settings.get("pinterest_title_suffix")
        post.title = f"{piece_title} | {suffix}"[:100] if suffix else piece_title
        post.destination_url = tracked_link(piece_url, "pinterest")
    return post
