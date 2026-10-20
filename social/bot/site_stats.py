"""Site traffic from GoatCounter (faevad.goatcounter.com), for the weekly report.

Read-only: the API key only needs GoatCounter's "Read statistics" permission.
The social links carry utm_source=<platform>, which GoatCounter lists as the
visit's source, and the buy buttons are counted as click events
("stripe-<piece>-<size>" for card checkout, "venmo-<piece>" for Venmo) -- so this shows which platforms send visitors and which
pieces get checkout clicks.
"""

import datetime as dt
import time

import requests

GOATCOUNTER_API = "https://faevad.goatcounter.com/api/v0"
# How many top pages/sources to list in the report.
TOP_N = 5


def _get(api_key: str, path: str, start: dt.date, end: dt.date, **extra) -> dict:
    params = {"start": start.isoformat(), "end": end.isoformat(), **extra}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    for attempt in range(2):
        response = requests.get(f"{GOATCOUNTER_API}{path}", headers=headers, params=params, timeout=30)
        # GoatCounter sometimes answers a good request with a one-off 404;
        # asking again a moment later works.
        if response.ok or attempt == 1:
            break
        time.sleep(5)
    response.raise_for_status()
    return response.json()


def fetch(api_key: str, days: int = 7) -> dict:
    """Totals, top pages, top sources and buy-button clicks for the past `days` days."""
    # GoatCounter leaves out the end date itself, so end on tomorrow to include today.
    end = dt.date.today() + dt.timedelta(days=1)
    start = end - dt.timedelta(days=days)
    total = _get(api_key, "/stats/total", start, end)
    # 100 is the API's maximum, so buy clicks aren't cut off past the first
    # page of results. /stats/total rejects limit, so it's only sent here.
    hits = _get(api_key, "/stats/hits", start, end, limit=100).get("hits", [])
    refs = _get(api_key, "/stats/toprefs", start, end, limit=100).get("stats", [])
    pages = [h for h in hits if not h.get("event")]
    clicks = [h for h in hits if h.get("event") and h.get("path", "").startswith(("stripe-", "venmo-"))]
    return {
        "visits": total.get("total", 0),
        # The site lives under /FAEVAD/; drop that so page names read short.
        "pages": [(h.get("path", "").removeprefix("/FAEVAD").strip("/") or "home", h.get("count", 0)) for h in pages],
        "sources": [(r.get("name") or "(direct)", r.get("count", 0)) for r in refs],
        "buy_clicks": [(h.get("title") or h.get("path", ""), h.get("count", 0)) for h in clicks],
    }


def format_section(stats: dict) -> str:
    lines = ["", f"🌐 Site, past 7 days: {stats['visits']} visits"]
    if stats["sources"]:
        lines.append("  sources: " + ", ".join(f"{name} {n}" for name, n in stats["sources"][:TOP_N]))
    if stats["pages"]:
        lines.append("  top pages: " + ", ".join(f"{path} {n}" for path, n in stats["pages"][:TOP_N]))
    if stats["buy_clicks"]:
        lines.append("  buy clicks: " + ", ".join(f"{name} {n}" for name, n in stats["buy_clicks"]))
    else:
        lines.append("  buy clicks: none")
    return "\n".join(lines)
