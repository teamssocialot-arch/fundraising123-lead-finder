"""Multi-source evidence-based verification pass for the Florida Stage 1 set.

PHASE A scope, deliberately narrow:
  - Operates ONLY on records with state == "FL" already in the database.
    Discovers nothing new -- no new events, organizations, states, contacts,
    or leads. Never calls a paid search API (Google/Bing are NOT wired in
    yet, by explicit instruction) and never uses any scraping-proxy,
    anti-bot-bypass, CAPTCHA-solving, or contact-guessing service.
  - Free public sources only, in this order per claim:
      1. Direct live fetch of the target's own page.
      2. If that's inaccessible: a Wayback Machine snapshot of the SAME page
         (the Internet Archive's own crawler already legitimately collected
         it -- we never touch the live target site to get it).
      3. For ORGANIZATION IDENTITY only: ProPublica Nonprofit Explorer
         (public IRS Form 990 data). Never used as evidence for an event
         claim -- a nonprofit registry has no event-specific information.
  - Every successfully-read piece of content becomes an Evidence row
    (never a failed/blocked fetch attempt -- those go in the separate
    inaccessible-URLs log instead). Verification levels are always
    COMPUTED from the accumulated Evidence, never asserted directly:
    SOURCE_PAGE_VERIFIED / ARCHIVED_SOURCE_VERIFIED / MULTI_SOURCE_CONFIRMED
    / SEARCH_RESULT_SUPPORTED / UNVERIFIED / NOT_FOUND
    (see app.ingest.compute_verification_level_from_evidence).
  - Two evidence rows pointing at the same underlying page (e.g. a live copy
    and an archived copy of the identical URL) are normalized to one source,
    never counted as two independent ones for MULTI_SOURCE_CONFIRMED.
  - An email is only ever recorded as confirmed when the literal address
    string is present in fetched content -- never inferred or guessed.
  - Every fetched-but-not-confirming result is preserved as a logged
    conflict; nothing is auto-resolved by which fact repeats more often.
  - Respects robots.txt, rate-limits per domain, never bypasses CAPTCHAs,
    logins, or anti-bot challenges -- those are recorded as inaccessible and
    skipped, not retried or circumvented.
"""
import json
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

from app.crawler.public_sources import propublica_org_matches, wayback_snapshot_url  # noqa: E402
from app.db import get_session, init_db  # noqa: E402
from app.dedupe.normalize import normalize_event_name, normalize_url  # noqa: E402
from app.ingest import add_evidence, compute_verification_level_from_evidence  # noqa: E402
from app.models import Contact, Event, Evidence, Organization  # noqa: E402
from config import MIN_SECONDS_BETWEEN_REQUESTS_PER_DOMAIN, REQUEST_TIMEOUT_SECONDS, USER_AGENT  # noqa: E402

VERIFICATION_SCOPE_STATE = "FL"  # hard-coded safety net: this script must never touch other states

ANTI_BOT_MARKERS = (
    "captcha", "cloudflare", "are you a human", "access denied",
    "attention required", "verify you are human", "checking your browser",
)
_PAGE_CONTENT_LEVELS = {"SOURCE_PAGE_VERIFIED", "ARCHIVED_SOURCE_VERIFIED"}

_robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}
_last_fetch_per_domain: dict[str, float] = {}
_page_cache: dict[str, dict] = {}
_json_cache: dict[str, dict] = {}
_wayback_lookups = 0
_propublica_lookups = 0


def _robots_allowed(url: str) -> bool:
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    if base not in _robots_cache:
        rp = urllib.robotparser.RobotFileParser()
        try:
            resp = httpx.get(base + "/robots.txt", headers={"User-Agent": USER_AGENT},
                              timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True)
            rp = None if resp.status_code >= 400 else rp
            if rp is not None:
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


def _raw_fetch(url: str) -> tuple[bool, str | None, str | None]:
    """Shared robots+rate-limit+retry fetch used by both fetch_page and
    fetch_json. Returns (ok, raw_text, reason)."""
    if not _robots_allowed(url):
        return False, None, "DISALLOWED_BY_ROBOTS_TXT"
    _rate_limit(urlparse(url).netloc)
    try:
        resp = _get(url)
    except httpx.TransportError as e:
        return False, None, f"NETWORK_ERROR:{e.__class__.__name__}"
    if resp.status_code in (401, 403):
        return False, None, f"ACCESS_DENIED_HTTP_{resp.status_code}"
    if resp.status_code == 429:
        return False, None, "RATE_LIMITED_HTTP_429"
    if resp.status_code >= 400:
        return False, None, f"HTTP_{resp.status_code}"
    lower = resp.text.lower()
    if any(marker in lower for marker in ANTI_BOT_MARKERS):
        return False, None, "ANTI_BOT_CHALLENGE_DETECTED"
    return True, resp.text, None


def fetch_page(url: str) -> dict:
    """Returns {"ok", "text" (visible text), "reason"}. Cached per URL per run."""
    if url in _page_cache:
        return _page_cache[url]
    ok, raw, reason = _raw_fetch(url)
    if ok:
        try:
            visible = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
        except Exception:
            visible = raw
        result = {"ok": True, "text": visible, "reason": None}
    else:
        result = {"ok": False, "text": None, "reason": reason}
    _page_cache[url] = result
    return result


def fetch_json(url: str) -> dict:
    """Returns {"ok", "data" (parsed JSON), "reason"}. Cached per URL per run."""
    if url in _json_cache:
        return _json_cache[url]
    ok, raw, reason = _raw_fetch(url)
    if ok:
        try:
            result = {"ok": True, "data": json.loads(raw), "reason": None}
        except json.JSONDecodeError:
            result = {"ok": False, "data": None, "reason": "INVALID_JSON_RESPONSE"}
    else:
        result = {"ok": False, "data": None, "reason": reason}
    _json_cache[url] = result
    return result


def _normalized_contains(haystack: str | None, needle: str) -> bool:
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


def _excerpt(text: str | None, needle: str, window: int = 150) -> str:
    if not text:
        return ""
    idx = text.lower().find(needle.lower().strip())
    if idx == -1:
        return text[:300].strip()
    start = max(0, idx - window // 2)
    end = min(len(text), idx + len(needle) + window // 2)
    return text[start:end].strip()


def _candidate_urls(*urls: str | None) -> list[str]:
    """Dedup possibly-None/NOT_FOUND URLs by normalized form, first-seen kept."""
    seen: set[str] = set()
    result = []
    for u in urls:
        if not u or u == "NOT_FOUND":
            continue
        key = normalize_url(u)
        if key in seen:
            continue
        seen.add(key)
        result.append(u)
    return result


def _try_live_then_archive(session, *, entity_type: str, entity_id: int, claim: str, url: str,
                            match_fn, inaccessible: dict, conflicts: list, label: str) -> tuple[bool, str | None]:
    """Live fetch first; on failure, a Wayback snapshot of the SAME url --
    never a substitute page. Returns (confirmed, source_type_used)."""
    global _wayback_lookups
    live = fetch_page(url)
    if live["ok"]:
        confirmed, excerpt = match_fn(live["text"])
        add_evidence(session, entity_type=entity_type, entity_id=entity_id, claim=claim,
                     source_url=url, source_type="LIVE_SOURCE", confirmed=confirmed, excerpt=excerpt)
        if not confirmed:
            conflicts.append(f"{label}: fetched {url} but could not confirm the claim in its content -- needs manual review")
        return confirmed, "LIVE_SOURCE"

    inaccessible[url] = live["reason"]

    _wayback_lookups += 1
    snapshot_url = wayback_snapshot_url(fetch_json, url)
    if not snapshot_url:
        return False, None
    archived = fetch_page(snapshot_url)
    if not archived["ok"]:
        inaccessible[snapshot_url] = archived["reason"]
        return False, None
    confirmed, excerpt = match_fn(archived["text"])
    add_evidence(session, entity_type=entity_type, entity_id=entity_id, claim=claim,
                 source_url=snapshot_url, source_type="ARCHIVED_SOURCE", confirmed=confirmed, excerpt=excerpt)
    if not confirmed:
        conflicts.append(f"{label}: archived snapshot of {url} ({snapshot_url}) did not confirm the claim -- needs manual review")
    return confirmed, "ARCHIVED_SOURCE"


def verify_organization(session, org: Organization, inaccessible: dict, conflicts: list) -> None:
    global _propublica_lookups
    claim = "org_identity"

    def matcher(text):
        hit = _normalized_contains(text, org.organization_name)
        return hit, (_excerpt(text, org.organization_name) if hit else (text[:300].strip() if text else ""))

    confirmed_live_or_archived = False
    if org.website:
        confirmed, source_type = _try_live_then_archive(
            session, entity_type="organization", entity_id=org.organization_id, claim=claim,
            url=org.website, match_fn=matcher, inaccessible=inaccessible, conflicts=conflicts,
            label=f"ORG '{org.organization_name}'",
        )
        confirmed_live_or_archived = confirmed and source_type in ("LIVE_SOURCE", "ARCHIVED_SOURCE")

    if not confirmed_live_or_archived:
        # Live (and archived) fetch of the org's own site did not confirm identity --
        # fall back to a public-record registry. Never used for event claims (see verify_event).
        _propublica_lookups += 1
        matches = propublica_org_matches(fetch_json, org.organization_name)
        best = next(
            (m for m in matches if m.get("name")
             and (_normalized_contains(m["name"], org.organization_name) or _normalized_contains(org.organization_name, m["name"]))
             and (not org.state or not m.get("state") or m["state"] == org.state)),
            None,
        )
        if best:
            excerpt = f'ProPublica Nonprofit Explorer: "{best["name"]}" -- EIN {best["ein"]}, {best.get("city") or "?"}, {best.get("state") or "?"}'
            add_evidence(session, entity_type="organization", entity_id=org.organization_id, claim=claim,
                         source_url=best["profile_url"] or "https://projects.propublica.org/nonprofits/",
                         source_type="PUBLIC_RECORD", confirmed=True, excerpt=excerpt)
        else:
            conflicts.append(f"ORG '{org.organization_name}': no matching ProPublica Nonprofit Explorer record found")

    org.source_verification_level = compute_verification_level_from_evidence(
        session, entity_type="organization", entity_id=org.organization_id, claim=claim)


def verify_event(session, event: Event, inaccessible: dict, conflicts: list) -> None:
    claim = "event_name_date"
    event.fundraiser_url_last_checked = datetime.now(timezone.utc)

    def matcher(text):
        name_hit = _normalized_contains(text, event.event_name)
        date_hit = _date_appears(event.event_date, text) if event.event_date else True
        confirmed = name_hit and date_hit
        excerpt = _excerpt(text, event.event_name) if name_hit else (text[:300].strip() if text else "")
        return confirmed, excerpt

    candidates = _candidate_urls(event.fundraiser_url, event.event_url, event.discovery_source_url, event.ticket_url)
    if not candidates:
        conflicts.append(f"EVENT '{event.event_name}': no fundraiser_url or other event URL on file -- flagged for review")
    else:
        for url in candidates:
            confirmed, source_type = _try_live_then_archive(
                session, entity_type="event", entity_id=event.event_id, claim=claim, url=url,
                match_fn=matcher, inaccessible=inaccessible, conflicts=conflicts,
                label=f"EVENT '{event.event_name}'",
            )
            if confirmed and source_type == "LIVE_SOURCE":
                break  # already at the best possible level -- no need to hit further candidates

    event.source_verification_level = compute_verification_level_from_evidence(
        session, entity_type="event", entity_id=event.event_id, claim=claim)

    # fundraiser_url_verification_level is scoped ONLY to that one URL (never the
    # multi-URL aggregate above) -- it answers "is the clickable link itself confirmed?"
    if event.fundraiser_url and event.fundraiser_url != "NOT_FOUND":
        fu_key = normalize_url(event.fundraiser_url)
        fu_rows = session.query(Evidence).filter(
            Evidence.entity_type == "event", Evidence.entity_id == event.event_id,
            Evidence.claim == claim, Evidence.source_url_normalized == fu_key,
        ).all()
        confirmed_fu = [r for r in fu_rows if r.confirmed]
        if any(r.source_type == "LIVE_SOURCE" for r in confirmed_fu):
            event.fundraiser_url_verification_level = "SOURCE_PAGE_VERIFIED"
        elif any(r.source_type == "ARCHIVED_SOURCE" for r in confirmed_fu):
            event.fundraiser_url_verification_level = "ARCHIVED_SOURCE_VERIFIED"
        elif fu_rows:
            event.fundraiser_url_verification_level = "UNVERIFIED"
        else:
            event.fundraiser_url_verification_level = "NOT_FOUND"


def verify_contact(session, contact: Contact, inaccessible: dict, conflicts: list) -> None:
    full_name = f"{contact.first_name or ''} {contact.last_name or ''}".strip()
    candidates = _candidate_urls(contact.contact_page_url, contact.contact_source_url, contact.email_source_url)

    if full_name:
        identity_claim = f"contact_identity:{contact.contact_id}"

        def name_matcher(text):
            hit = _normalized_contains(text, full_name)
            return hit, (_excerpt(text, full_name) if hit else (text[:300].strip() if text else ""))

        for url in candidates:
            confirmed, source_type = _try_live_then_archive(
                session, entity_type="contact", entity_id=contact.contact_id, claim=identity_claim, url=url,
                match_fn=name_matcher, inaccessible=inaccessible, conflicts=conflicts,
                label=f"CONTACT '{full_name}'",
            )
            if confirmed and source_type == "LIVE_SOURCE":
                break
        contact.source_verification_level = compute_verification_level_from_evidence(
            session, entity_type="contact", entity_id=contact.contact_id, claim=identity_claim)
    else:
        contact.source_verification_level = "NOT_FOUND"  # general-org contact -- no named identity to verify

    if contact.email:
        email_claim = f"contact_email:{contact.email.lower()}"

        def email_matcher(text):
            hit = contact.email.lower() in (text or "").lower()
            return hit, (_excerpt(text, contact.email) if hit else (text[:300].strip() if text else ""))

        for url in candidates:
            confirmed, source_type = _try_live_then_archive(
                session, entity_type="contact", entity_id=contact.contact_id, claim=email_claim, url=url,
                match_fn=email_matcher, inaccessible=inaccessible, conflicts=conflicts,
                label=f"CONTACT email '{contact.email}'",
            )
            if confirmed and source_type == "LIVE_SOURCE":
                break
        email_level = compute_verification_level_from_evidence(
            session, entity_type="contact", entity_id=contact.contact_id, claim=email_claim)
        contact.email_verification_level = email_level
        if email_level in _PAGE_CONTENT_LEVELS and contact.email_type != "GENERAL_ORGANIZATION":
            contact.email_type = "VERIFIED_PUBLIC"
    else:
        contact.email_verification_level = "NOT_FOUND"


def main():
    init_db()
    session = get_session()

    inaccessible: dict[str, str] = {}
    conflicts: list[str] = []

    events = session.query(Event).filter(Event.state == VERIFICATION_SCOPE_STATE).all()
    if not events:
        print(f"No {VERIFICATION_SCOPE_STATE} events found in the database -- nothing to verify.")
        return

    seen_org_ids: set[int] = set()
    for event in events:
        org = session.get(Organization, event.organization_id)
        if org.organization_id not in seen_org_ids:
            verify_organization(session, org, inaccessible, conflicts)
            seen_org_ids.add(org.organization_id)

        verify_event(session, event, inaccessible, conflicts)

        for contact in session.query(Contact).filter(Contact.organization_id == org.organization_id).all():
            verify_contact(session, contact, inaccessible, conflicts)

    session.commit()

    evidence_total = session.query(Evidence).count()
    log = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "scope": f"state={VERIFICATION_SCOPE_STATE} only, {len(events)} events (Phase A: no search API, no new leads)",
        "pages_fetched_ok": sum(1 for r in _page_cache.values() if r["ok"]),
        "wayback_lookups_attempted": _wayback_lookups,
        "propublica_lookups_attempted": _propublica_lookups,
        "evidence_rows_total": evidence_total,
        "urls_inaccessible": len(inaccessible),
        "inaccessible_urls": inaccessible,
        "conflicts": conflicts,
    }
    log_path = Path(__file__).resolve().parent.parent / "data" / "verification_log.json"
    log_path.write_text(json.dumps(log, indent=2))

    session.close()
    print(f"Verification pass complete. {log['pages_fetched_ok']} pages fetched OK, "
          f"{_wayback_lookups} Wayback lookups attempted, {_propublica_lookups} ProPublica lookups attempted, "
          f"{evidence_total} evidence rows on file, {log['urls_inaccessible']} URLs inaccessible, "
          f"{len(conflicts)} conflicts. Log written to {log_path}.")


if __name__ == "__main__":
    main()
