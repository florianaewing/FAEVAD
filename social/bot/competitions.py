"""Art competition reminders, sent on Telegram at noon Pacific.

The list lives in social/competitions.yml. For every approved competition
the artist gets a heads-up 14 days before submissions open, and a reminder
on the day they open. Nothing is entered automatically -- applications are
the artist's own.
"""

import datetime as dt
from pathlib import Path

import yaml

COMPETITIONS_PATH = Path(__file__).resolve().parents[1] / "competitions.yml"
HEADS_UP_DAYS = 14


def load() -> list[dict]:
    with open(COMPETITIONS_PATH) as f:
        return yaml.safe_load(f) or []


def due(competitions: list[dict], today: dt.date) -> list[tuple[str, dict]]:
    """(kind, competition) pairs to send today; kind is "heads_up" or "opens"."""
    reminders = []
    for comp in competitions:
        if not comp.get("approved"):
            continue
        opens = comp["opens"]
        if today == opens - dt.timedelta(days=HEADS_UP_DAYS):
            reminders.append(("heads_up", comp))
        elif today == opens:
            reminders.append(("opens", comp))
    return reminders


def _details(comp: dict) -> list[str]:
    lines = [f"Where: {comp['where']}", f"Who can enter: {comp['eligibility']}", f"Media: {comp['media']}",
             f"Fee: {comp['fee']}"]
    if comp.get("deadline"):
        lines.append(f"Deadline: {comp['deadline']}")
    if comp.get("notes"):
        lines.append(comp["notes"])
    lines.append(comp["url"])
    return lines


def format_reminder(kind: str, comp: dict) -> str:
    opens = comp["opens"].strftime("%A %b %-d")
    estimated = comp.get("opens_estimated")
    if kind == "heads_up":
        head = f"📅 In two weeks ({opens}): submissions open for {comp['name']}."
        if estimated:
            head += " That date is estimated from past years -- check the site to confirm."
    else:
        head = f"🎨 Submissions open today for {comp['name']}. Time to submit!"
        if estimated:
            head += " (Today was estimated from past years -- if it isn't open yet, check the site.)"
    return "\n".join([head, "", *_details(comp)])
