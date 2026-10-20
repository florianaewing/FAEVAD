"""Art competition reminders, sent on Telegram at noon Pacific.

The list lives in social/competitions.yml. For every approved competition
the artist gets four reminders: 14 days before submissions open, the day
they open, 14 days before the deadline, and the deadline day itself. Dates
already in the past are simply skipped, so a competition added mid-way
through its application period only gets the deadline reminders. Nothing
is entered automatically -- applications are the artist's own.
"""

import datetime as dt
from pathlib import Path

import yaml

COMPETITIONS_PATH = Path(__file__).resolve().parents[1] / "competitions.yml"
HEADS_UP_DAYS = 14


def load() -> list[dict]:
    with open(COMPETITIONS_PATH) as f:
        return yaml.safe_load(f) or []


def reminder_dates(comp: dict) -> dict[str, dt.date]:
    """kind -> the day that reminder goes out."""
    two_weeks = dt.timedelta(days=HEADS_UP_DAYS)
    dates = {}
    if comp.get("opens"):
        dates["opens_soon"] = comp["opens"] - two_weeks
        dates["opens"] = comp["opens"]
    if comp.get("deadline"):
        # `deadline_reminder` overrides the two-weeks-before date, for a
        # deadline too close for two weeks' notice.
        dates["deadline_soon"] = comp.get("deadline_reminder") or comp["deadline"] - two_weeks
        dates["deadline"] = comp["deadline"]
    return dates


def due(competitions: list[dict], today: dt.date) -> list[tuple[str, dict]]:
    """(kind, competition) pairs to send today."""
    return [
        (kind, comp)
        for comp in competitions
        if comp.get("approved")
        for kind, day in reminder_dates(comp).items()
        if day == today
    ]


def _date(day: dt.date) -> str:
    return day.strftime("%B %-d, %Y")


def _time_left(today: dt.date, deadline: dt.date) -> str:
    days = (deadline - today).days
    return "two weeks" if days == HEADS_UP_DAYS else f"{days} days"


def format_reminder(kind: str, comp: dict, today: dt.date) -> str:
    """The artist's own wording for the opening reminders, filled in."""
    name, url = comp["name"], comp["url"]
    if kind == "opens_soon":
        return (
            f"Applications to the {name} art competition open on {_date(comp['opens'])}, "
            f"you have two weeks to prepare. Read the submission requirements carefully here at {url}."
        )
    if kind == "opens":
        return (
            f"The application period for the {comp.get('year', comp['opens'].year)} {name} art competition is "
            f"officially open. Apply here {url} before {_date(comp['deadline'])} for a shot at winning."
        )
    if kind == "deadline_soon":
        return (
            f"Applications to the {name} art competition close on {_date(comp['deadline'])}, "
            f"you have {_time_left(today, comp['deadline'])} left to apply. Apply here {url}."
        )
    return (
        f"Today is the last day to apply to the {name} art competition. "
        f"Apply here {url} before midnight for a shot at winning."
    )
