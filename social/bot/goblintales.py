"""Goblin Tales Fridays: one of Sam Thwaits' comics, posted every Friday.

The comics come from the goblintales site's own repo (a local clone, pulled
before each Friday post), in data.json order -- a comic's number is its
place in that file, starting at 1. The caption is filled in automatically
from the template in social/strategy.yml, so unlike the artist's own pieces
there's nothing to reply to on Telegram.

Every copy online stays at 500px on its longest side, like FAEVAD's own web
images: the goblintales repo shrinks new uploads itself, and the bot refuses
to post anything bigger. Full-size originals live only in
~/Pictures/GoblinTales; before a comic is posted, the largest version ever
committed to the goblintales repo is copied there if it's missing.

What has been posted is recorded in social/goblintales.yml, which the bot
commits and pushes like queue.yml.
"""

import datetime as dt
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
POSTED_PATH = REPO_ROOT / "social" / "goblintales.yml"
GOBLINTALES_REPO = Path.home() / "Documents" / "Github" / "goblintales"
ORIGINALS_DIR = Path.home() / "Pictures" / "GoblinTales"
SITE_URL = "https://goblin-tales.com"
COMICS_DIR = "images/artwork"
MAX_LONGEST_SIDE = 500
# A holiday comic whose holiday was missed (e.g. the bot was off) still
# posts if it's this soon after; otherwise it waits for next year's.
HOLIDAY_WINDOW = dt.timedelta(days=14)


@dataclass
class Comic:
    number: int
    filename: str
    title: str

    @property
    def image_path(self) -> Path:
        return GOBLINTALES_REPO / COMICS_DIR / self.filename

    @property
    def image_url(self) -> str:
        return f"{SITE_URL}/{COMICS_DIR}/{self.filename}"

    @property
    def label(self) -> str:
        return f"Goblin Tales #{self.number}: {self.title}"


def pull() -> None:
    """Brings the local clone up to date with whatever Sam has uploaded."""
    subprocess.run(["git", "pull", "--ff-only", "--quiet"], cwd=GOBLINTALES_REPO, check=True)


def load_comics() -> list[Comic]:
    entries = json.loads((GOBLINTALES_REPO / "data.json").read_text())
    return [Comic(i, e["filename"], e["title"]) for i, e in enumerate(entries, start=1)]


def load_posted() -> list[dict]:
    try:
        with open(POSTED_PATH) as f:
            return yaml.safe_load(f) or []
    except FileNotFoundError:
        return []


def save_posted(posted: list[dict]) -> None:
    header = []
    for line in POSTED_PATH.read_text().splitlines(keepends=True):
        if line.startswith("#") or not line.strip():
            header.append(line)
        else:
            break
    body = yaml.dump(posted, sort_keys=False, allow_unicode=True, default_flow_style=False) if posted else ""
    body = body.replace("\n- number:", "\n\n- number:")
    POSTED_PATH.write_text("".join(header) + body)


def _holiday_is_now(month_day: str, day: dt.date) -> bool:
    """Whether day falls within HOLIDAY_WINDOW on or after the holiday's latest
    occurrence -- so a 12-31 holiday counts on January 1st too."""
    month, dom = (int(part) for part in month_day.split("-"))
    latest = dt.date(day.year, month, dom)
    if latest > day:
        latest = dt.date(day.year - 1, month, dom)
    return day - latest < HOLIDAY_WINDOW


def next_comic(day: dt.date, holidays: dict[str, str]) -> Comic | None:
    """The comic for a Goblin Tales day: a holiday comic whose holiday it is,
    otherwise the first unposted comic that isn't waiting for its holiday.

    holidays maps a comic's filename to its holiday's date (MM-DD).
    """
    posted = {p["filename"] for p in load_posted()}
    unposted = [c for c in load_comics() if c.filename not in posted]
    for comic in unposted:
        if comic.filename in holidays and _holiday_is_now(holidays[comic.filename], day):
            return comic
    return next((c for c in unposted if c.filename not in holidays), None)


def comic_by_number(number: int) -> Comic:
    return load_comics()[number - 1]


def caption(template: str, comic: Comic) -> str:
    return template.format(number=comic.number, title=comic.title)


def image_problem(comic: Comic) -> str | None:
    """Why the comic's image can't be posted yet, or None if it's fine."""
    if not comic.image_path.exists():
        return f"{COMICS_DIR}/{comic.filename} isn't in the goblintales repo"
    with Image.open(comic.image_path) as im:
        size = im.size
    if max(size) > MAX_LONGEST_SIDE:
        return (
            f"{comic.filename} is {size[0]}x{size[1]} on goblin-tales.com -- over the "
            f"{MAX_LONGEST_SIDE}px limit, so it wasn't posted. The repo's shrink-comics "
            f"workflow should have shrunk it; check its Actions tab."
        )
    return None


def save_original(comic: Comic) -> None:
    """Copies the comic's biggest committed version to ~/Pictures/GoblinTales,
    unless it's already there."""
    dest = ORIGINALS_DIR / comic.filename
    if dest.exists():
        return
    wanted = f"{COMICS_DIR}/{comic.filename}".lower()
    commits = subprocess.run(
        ["git", "log", "--all", "--format=%H", "--", COMICS_DIR],
        cwd=GOBLINTALES_REPO, check=True, capture_output=True, text=True,
    ).stdout.split()
    best = None  # (size, blob)
    for commit in commits:
        listing = subprocess.run(
            ["git", "ls-tree", "-r", "-l", commit, COMICS_DIR],
            cwd=GOBLINTALES_REPO, check=True, capture_output=True, text=True,
        ).stdout
        for line in listing.splitlines():
            meta, path = line.split("\t", 1)
            _mode, _type, blob, size = meta.split()
            # Some comics were renamed only in capitalization.
            if path.lower() == wanted and (best is None or int(size) > best[0]):
                best = (int(size), blob)
    if best is None:
        raise FileNotFoundError(f"no version of {comic.filename} in the goblintales history")
    data = subprocess.run(
        ["git", "cat-file", "blob", best[1]], cwd=GOBLINTALES_REPO, check=True, capture_output=True
    ).stdout
    ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
