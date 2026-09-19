"""Real source-page verification pass for the Florida Stage 1 validation set.

Scope, deliberately narrow:
  - Operates ONLY on records with state == "FL" already in the database
    (loaded from data/seed/florida_stage1_leads.json). It discovers nothing
    new -- no new events, organizations, states, or search-API calls.
  - For every organization website, event page, and contact/email source
    URL already on file, attempts a direct HTTP fetch and checks whether the
    stored fact is actually present on that page.
  - Upgrades a record's source_verification_level to SOURCE_PAGE_VERIFIED
    ONLY when the page was successfully fetched AND the specific fact
    (org name, event name/date, contact name, or exact email string) was
    found on it. Otherwise the record is left at its current level.
  - Never bypasses robots.txt, CAPTCHAs, logins, or anti-bot challenges --
    any such case is recorded as inaccessible and skipped, not retried.
  - Never invents or infers an email address; only confirms or fails to
    confirm an email that was already on file.

Requires no API keys and makes no calls to any paid service.
"""
import json
import re
import sys
import time
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_session, init_db  # noqa: E402
from app.dedupe.normalize import normalize_event_name  # noqa: E402
from app.ingest import upgrade_source_verification_level  # noqa: E402
from app.models import Contact, Event, Organization  # noqa: E402
from config import (  # noqa: E402
    MIN_SECONDS_BETWEEN_REQUESTS_PER_DOMAIN, REQUEST_TIMEOUT_SECONDS,
    SOURCE_VERIFICATION_LEVELS, USER_AGENT,
)

VERIFICATION_SCOPE_STATE = "FL"  # hard-coded safety net: this script must never touch other states

ANTI_BOT_MARKERS = (
    "captcha", "cloudflare", "are you a human", "access denied",
    "attention required", "verify you are human", "checking your browser",
)

_robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}
_last_fetch_per_domain: dict[str, float] = {}
_page_cache: dict[str, dict] = {}


def _robots_allowed(url: str) -> bool:
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    if base not in _robots_cache:
        rp = urllib.robotparser.RobotFileParser()
        try:
            # Fetch via httpx (not RobotFileParser.read(), which uses urllib and
            # does not reliably honor our timeout/proxy config) so a missing or
            # slow robots.txt can never hang the run.
            resp = httpx.get(base + "/robots.txt", headers={"User-Agent": USER_AGENT},
                              timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True)
            if resp.status_code >= 400:
                rp = None  # no robots.txt -- most sites with none permit crawling
            else:
                rp.parse(resp.text.splitlines())
        except Exception:
            rp = None
        _robots_cache[base] = rp
    rp = _robots_cache[base]
    return True if rp is None else rp.can_fetch(USER_AGENT, url)


def _rate_limit(domain: str) -> None:
    last = _last_fetch_per_domain.get(domain)
    if last is not None:
        wait = MIN_SECONDS_BETWEEN_REQUESTS_PER_DOMAIN - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
    _last_fetch_per_domain[domain] = time.monotonic()


@retry(
    retry=retry_if_exception_type(httpx.TransportError),
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=1, max=3),
    reraise=True,
)
def _get(url: str) -> httpx.Response:
    return httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True)


def fetch_page(url: str) -> dict:
    """Returns {"ok": bool, "text": str | None, "reason": str | None}. Cached per URL per run."""
    if url in _page_cache:
        return _page_cache[url]

    result = {"ok": False, "text": None, "reason": None}

    if not _robots_allowed(url):
        result["reason"] = "DISALLOWED_BY_ROBOTS_TXT"
        _page_cache[url] = result
        return result

    _rate_limit(urlparse(url).netloc)

    try:
        resp = _get(url)
    except httpx.TransportError as e:
        result["reason"] = f"NETWORK_ERROR:{e.__class__.__name__}"
        _page_cache[url] = result
        return result

    if resp.status_code in (401, 403):
        result["reason"] = f"ACCESS_DENIED_HTTP_{resp.status_code}"
    elif resp.status_code == 429:
        result["reason"] = "RATE_LIMITED_HTTP_429"
    elif resp.status_code >= 400:
        result["reason"] = f"HTTP_{resp.status_code}"
    else:
        lower = resp.text.lower()
        if any(marker in lower for marker in ANTI_BOT_MARKERS):
            result["reason"] = "ANTI_BOT_CHALLENGE_DETECTED"
        else:
            try:
                visible_text = BeautifulSoup(resp.text, "html.parser").get_text(" ", strip=True)
            except Exception:
                visible_text = resp.text
            result["ok"] = True
            result["text"] = visible_text

    _page_cache[url] = result
    return result


def _normalized_contains(haystack: str, needle: str) -> bool:
    if not haystack or not needle:
        return False
    return normalize_event_name(needle) in normalize_event_name(haystack)


def _date_appears(event_date, text: str) -> bool:
    if event_date is None:
        return False
    candidates = {
        event_date.strftime("%B %-d"), event_date.strftime("%B %-d, %Y"),
        event_date.strftime("%b %-d"), event_date.strftime("%b. %-d"),
        event_date.strftime("%m/%d/%Y"), event_date.strftime("%m/%d/%y"),
        event_date.strftime("%-m/%-d/%Y"), event_date.strftime("%-m/%-d/%y"),
        event_date.strftime("%Y-%m-%d"),
    }
    normalized_text = normalize_event_name(text)
    return any(normalize_event_name(c) in normalized_text for c in candidates)


def verify_organization(org: Organization, inaccessible: dict, conflicts: list) -> None:
    if not org.website:
        return
    res = fetch_page(org.website)
    if not res["ok"]:
        inaccessible[org.website] = res["reason"]
        return
    if _normalized_contains(res["text"], org.organization_name):
        upgrade_source_verification_level(org, "SOURCE_PAGE_VERIFIED")
    else:
        conflicts.append(
            f"ORG '{org.organization_name}': organization name not found on its own website "
            f"homepage ({org.website}) -- needs manual review"
        )


def _upgrade_level_field(entity, field_name: str, new_level: str) -> None:
    current = getattr(entity, field_name) or "NOT_FOUND"
    if SOURCE_VERIFICATION_LEVELS.index(new_level) < SOURCE_VERIFICATION_LEVELS.index(current):
        setattr(entity, field_name, new_level)


def verify_event(event: Event, org: Organization, inaccessible: dict, conflicts: list) -> None:
    """Per requirement: an event can only reach SOURCE_PAGE_VERIFIED by successfully
    fetching its fundraiser_url (the best fundraiser-specific page) and confirming
    the event details on it -- event_url/org homepage are not substitutes."""
    event.fundraiser_url_last_checked = datetime.now(timezone.utc)

    if not event.fundraiser_url or event.fundraiser_url == "NOT_FOUND":
        conflicts.append(
            f"EVENT '{event.event_name}': no fundraiser_url on file -- cannot reach "
            f"SOURCE_PAGE_VERIFIED until a fundraiser-specific page is identified; flagged for review"
        )
        return

    res = fetch_page(event.fundraiser_url)
    if not res["ok"]:
        inaccessible[event.fundraiser_url] = res["reason"]
        return

    name_hit = _normalized_contains(res["text"], event.event_name)
    date_hit = _date_appears(event.event_date, res["text"]) if event.event_date else None

    if name_hit and (date_hit is None or date_hit):
        upgrade_source_verification_level(event, "SOURCE_PAGE_VERIFIED")
        _upgrade_level_field(event, "fundraiser_url_verification_level", "SOURCE_PAGE_VERIFIED")
    elif name_hit and date_hit is False:
        conflicts.append(
            f"EVENT '{event.event_name}': fundraiser page confirmed at {event.fundraiser_url}, but "
            f"stored date {event.event_date} could not be matched in the page text -- left at "
            f"{event.source_verification_level}, needs manual review"
        )
    else:
        conflicts.append(
            f"EVENT '{event.event_name}': event name not found on cited fundraiser_url "
            f"({event.fundraiser_url}) -- left at {event.source_verification_level}, needs manual review"
        )


def verify_contact(contact: Contact, inaccessible: dict, conflicts: list) -> None:
    urls = [u for u in {contact.email_source_url, contact.contact_source_url, contact.contact_page_url} if u]
    if not urls:
        return

    full_name = f"{contact.first_name or ''} {contact.last_name or ''}".strip()

    for url in urls:
        res = fetch_page(url)
        if not res["ok"]:
            inaccessible[url] = res["reason"]
            continue

        page_text = res["text"]

        if contact.email:
            email_confirmed = contact.email.lower() in page_text.lower()
            if email_confirmed:
                upgrade_source_verification_level(contact, "SOURCE_PAGE_VERIFIED", email_confirmed=True)
            else:
                conflicts.append(
                    f"CONTACT email '{contact.email}' ({full_name or contact.title}): not found on "
                    f"cited source page ({url}) -- staying {contact.email_type}/"
                    f"{contact.source_verification_level}, needs manual review"
                )
        elif full_name and _normalized_contains(page_text, full_name):
            upgrade_source_verification_level(contact, "SOURCE_PAGE_VERIFIED")
        elif full_name:
            conflicts.append(
                f"CONTACT '{full_name}' ({contact.title or 'no title'}): name not found on cited "
                f"source page ({url}) -- needs manual review"
            )


def main():
    init_db()
    session = get_session()

    inaccessible: dict[str, str] = {}
    conflicts: list[str] = []

    events = session.query(Event).filter(Event.state == VERIFICATION_SCOPE_STATE).all()
    if not events:
        print(f"No {VERIFICATION_SCOPE_STATE} events found in the database -- nothing to verify.")
        return

    seen_org_ids = set()
    for event in events:
        org = session.get(Organization, event.organization_id)

        if org.organization_id not in seen_org_ids:
            verify_organization(org, inaccessible, conflicts)
            seen_org_ids.add(org.organization_id)

        verify_event(event, org, inaccessible, conflicts)

        for contact in session.query(Contact).filter(Contact.organization_id == org.organization_id).all():
            verify_contact(contact, inaccessible, conflicts)

    session.commit()

    log = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "scope": f"state={VERIFICATION_SCOPE_STATE} only, {len(events)} events",
        "urls_fetched_ok": sum(1 for r in _page_cache.values() if r["ok"]),
        "urls_inaccessible": len(inaccessible),
        "inaccessible_urls": inaccessible,
        "conflicts": conflicts,
    }
    log_path = Path(__file__).resolve().parent.parent / "data" / "verification_log.json"
    log_path.write_text(json.dumps(log, indent=2))

    session.close()
    print(f"Verification pass complete. Fetched {log['urls_fetched_ok']} pages OK, "
          f"{log['urls_inaccessible']} inaccessible, {len(conflicts)} conflicts. "
          f"Log written to {log_path}.")


if __name__ == "__main__":
    main()
