"""Site traffic from GoatCounter (faevad.goatcounter.com), for the weekly report.

Read-only: the API key only needs GoatCounter's "Read statistics" permission.
The social links carry utm_source=<platform>, which GoatCounter lists as the
visit's source, and the buy buttons are counted as "stripe-<piece>-<size>"
click events -- so this shows which platforms send visitors and which
pieces get checkout clicks.
"""

import datetime as dt

import requests

GOATCOUNTER_API = "https://faevad.goatcounter.com/api/v0"
# How many top pages/sources to list in the report.
TOP_N = 5


def _get(api_key: str, path: str, start: dt.date, end: dt.date) -> dict:
    response = requests.get(
        f"{GOATCOUNTER_API}{path}",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        params={"start": start.isoformat(), "end": end.isoformat()},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def fetch(api_key: str, days: int = 7) -> dict:
    """Totals, top pages, top sources and buy-button clicks for the past `days` days."""
    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    total = _get(api_key, "/stats/total", start, end)
    hits = _get(api_key, "/stats/hits", start, end).get("hits", [])
    refs = _get(api_key, "/stats/toprefs", start, end).get("stats", [])
    pages = [h for h in hits if not h.get("event")]
    clicks = [h for h in hits if h.get("event") and h.get("path", "").startswith("stripe-")]
    return {
        "visits": total.get("total", 0),
        "pages": [(h.get("path", ""), h.get("count", 0)) for h in pages],
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
