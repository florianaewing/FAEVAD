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


def _date(day: dt.date) -> str:
    return day.strftime("%B %-d, %Y")


def format_reminder(kind: str, comp: dict) -> str:
    """The artist's own wording, filled in."""
    if kind == "heads_up":
        return (
            f"Applications to the {comp['name']} art competition open on {_date(comp['opens'])}, "
            f"you have two weeks to prepare. Read the submission requirements carefully here at "
            f"{comp['url']}."
        )
    return (
        f"The application period for the {comp.get('year', comp['opens'].year)} {comp['name']} art competition is "
        f"officially open. Apply here {comp['url']} before {_date(comp['deadline'])} for a shot at winning."
    )
