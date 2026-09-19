"""Free, no-API-key public sources for corroborating a claim when the target
site's own live page cannot be fetched.

Neither of these bypasses any access control:
  - The Wayback Machine serves snapshots the Internet Archive's own crawler
    already legitimately collected. We never touch the live target site to
    get them -- we read a public third-party archive instead.
  - ProPublica's Nonprofit Explorer serves public IRS Form 990 filing data
    through its own official, free API.

Both take an injected `fetch_json` callable (see scripts/verify_sources.py)
so robots.txt checks, rate limiting, retries, and anti-bot handling stay
centralized in one place rather than duplicated per source. This module has
no HTTP client of its own.

ProPublica corroborates ORGANIZATION IDENTITY only (name/EIN/city/state from
IRS filings) -- it never contains fundraiser/event details, so callers must
never cite it as evidence for an event claim (see verify_sources.py, which
enforces this at the call site).
"""
from urllib.parse import quote

from config import PROPUBLICA_NONPROFIT_SEARCH_URL, WAYBACK_AVAILABILITY_URL


def wayback_snapshot_url(fetch_json, target_url: str) -> str | None:
    """Returns the most recent archived snapshot URL for target_url, or None
    if the Internet Archive has never captured it."""
    availability_url = f"{WAYBACK_AVAILABILITY_URL}?url={quote(target_url, safe='')}"
    res = fetch_json(availability_url)
    if not res["ok"]:
        return None
    data = res["data"]
    if not isinstance(data, dict):
        return None
    snapshot = (data.get("archived_snapshots") or {}).get("closest")
    if not snapshot or not snapshot.get("available"):
        return None
    return snapshot.get("url")


def propublica_org_matches(fetch_json, org_name: str) -> list[dict]:
    """Returns candidate nonprofit records (name, ein, city, state, profile
    URL) matching org_name, built from public IRS Form 990 data."""
    search_url = f"{PROPUBLICA_NONPROFIT_SEARCH_URL}?q={quote(org_name, safe='')}"
    res = fetch_json(search_url)
    if not res["ok"]:
        return []
    data = res["data"]
    if not isinstance(data, dict):
        return []
    matches = []
    for org in data.get("organizations", []) or []:
        ein = org.get("ein")
        matches.append({
            "name": org.get("name"),
            "ein": ein,
            "city": (org.get("city") or "").title() if org.get("city") else None,
            "state": org.get("state"),
            "profile_url": f"https://projects.propublica.org/nonprofits/organizations/{ein}" if ein else None,
        })
    return matches
