"""Which piece posts on a given day, and how long each gallery's queue lasts.

next_unposted_entry is the bot's real rule for picking a day's piece. The
runway forecast replays that rule day by day (one post every posting day,
Goblin Tales on its day until the comics run out) to find the date each
gallery's last queued piece goes out. When a gallery is down to its last
RUNWAY_WARNING_POSTS unposted pieces, the artist gets one Telegram warning
with that date; adding pieces to the gallery re-arms it.
"""

import datetime as dt

import goblintales
import strategy

# Watercolors get Wednesdays; the other posting days post from the other
# collections (inks). When a day's own pool runs dry it falls back to the
# other one, so the queue only reports empty once everything is posted.
WEDNESDAY_COLLECTION = "watercolors"

RUNWAY_WARNING_POSTS = 10
# Far enough ahead to cover any realistic queue, short enough to stay quick.
FORECAST_LIMIT = dt.timedelta(days=5 * 365)


def collection_of(artwork: dict, piece_id: str) -> str | None:
    for collection in artwork["collections"]:
        if any(piece["id"] == piece_id for piece in collection["pieces"]):
            return collection["slug"]
    return None


def next_unposted_entry(queue: list[dict], artwork: dict, day: dt.date) -> dict | None:
    unposted = sorted(
        (e for e in queue if not e.get("posted")), key=lambda e: e["catalog_number"]
    )
    # A piece with `post_on` is held for that date and goes first on it. If
    # that day was missed, it falls back into normal queue order.
    pinned = [e for e in unposted if e.get("post_on") == day]
    if pinned:
        return pinned[0]
    unposted = [e for e in unposted if not (e.get("post_on") and e["post_on"] > day)]
    is_wednesday = day.weekday() == 2
    todays = [
        e for e in unposted
        if (collection_of(artwork, e["id"]) == WEDNESDAY_COLLECTION) == is_wednesday
    ]
    return (todays or unposted or [None])[0]


def last_post_dates(queue: list[dict], artwork: dict, plan: dict, start: dt.date) -> dict[str, dt.date]:
    """Gallery slug -> the day its last queued piece is expected to post,
    counting from start (inclusive), for galleries with anything left."""
    queue = [dict(e) for e in queue]  # the forecast marks copies as posted
    posted = {p["filename"] for p in goblintales.load_posted()}
    comics = [c for c in goblintales.load_comics() if c.filename not in posted]
    holidays = plan.get("goblintales", {}).get("holidays", {})
    last: dict[str, dt.date] = {}
    day = start
    while any(not e.get("posted") for e in queue) and day - start < FORECAST_LIMIT:
        if strategy.posts_on(plan, day):
            comic = None
            if strategy.is_goblintales_day(plan, day):
                comic = goblintales.pick_comic(comics, day, holidays)
            if comic:
                comics.remove(comic)
            else:
                entry = next_unposted_entry(queue, artwork, day)
                if entry:
                    entry["posted"] = True
                    last[collection_of(artwork, entry["id"])] = day
        day += dt.timedelta(days=1)
    return last


def warning_text(gallery_title: str, before: dt.date) -> str:
    """The artist's own wording."""
    return (
        f"Only ten posts left from the {gallery_title} gallery, upload your new work "
        f"before {before.strftime('%B %-d, %Y')} to continue your streak."
    )


def warnings(
    queue: list[dict], artwork: dict, plan: dict, today: dt.date, warned: set[str]
) -> tuple[list[str], set[str]]:
    """Messages to send today, and the updated set of galleries already warned.

    A gallery is warned once when it reaches RUNWAY_WARNING_POSTS unposted
    pieces, and becomes warnable again once it's back above that.
    """
    left: dict[str, int] = {}
    for entry in queue:
        if not entry.get("posted"):
            slug = collection_of(artwork, entry["id"])
            left[slug] = left.get(slug, 0) + 1
    warned = {slug for slug in warned if left.get(slug, 0) <= RUNWAY_WARNING_POSTS}
    due = [
        c for c in artwork["collections"]
        if left.get(c["slug"]) == RUNWAY_WARNING_POSTS and c["slug"] not in warned
    ]
    if not due:
        return [], warned
    last = last_post_dates(queue, artwork, plan, today)
    messages = []
    for collection in due:
        messages.append(warning_text(collection["title"], last[collection["slug"]]))
        warned.add(collection["slug"])
    return messages, warned
