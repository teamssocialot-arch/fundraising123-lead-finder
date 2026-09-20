"""Phase B: Tavily-powered search discovery, layered on top of Phase A's
evidence (scripts/verify_sources.py must have already run at least once).

Scope, per explicit instruction:
  - Only the existing 10 Florida leads. Discovers no new leads/events/states.
  - The Tavily API key comes ONLY from the TAVILY_API_KEY environment
    variable (a GitHub Actions secret) -- never hardcoded, never committed.
    If it's unset, this script no-ops with a clear message rather than
    failing (same pattern as scripts/export_to_sheets.py's dry run).
  - Hard credit budget: TAVILY_MAX_CREDITS_PER_RUN (default 100, enforced
    inside app.search.tavily.TavilyProvider itself -- it refuses to place a
    call that would exceed it). ~5-6 combined queries per lead keeps the
    10-lead test around 50-60 credits, safely under budget.
  - Tavily itself is a discovery mechanism, not evidence. The underlying URL
    it returns is the evidence source (source_type=SEARCH_DISCOVERY, storing
    that URL) -- and we still attempt our own live/archive fetch of that
    same URL for a stronger LIVE_SOURCE/ARCHIVED_SOURCE confirmation exactly
    like Phase A does for pre-known URLs. Two Tavily results pointing at the
    same underlying normalized URL are the SAME source, never two.
  - Contact/email discovery is conservative: a literal email found in
    returned content may be recorded; a name is only attributed to that
    email when both appear together in the same returned snippet. Nothing
    is ever constructed or pattern-guessed.
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.verify_sources as vs  # noqa: E402 -- reuses fetch_page/fetch_json/robots/rate-limit/_try_live_then_archive

from app.db import get_session, init_db  # noqa: E402
from app.dedupe.normalize import normalize_event_name, normalize_url  # noqa: E402
from app.ingest import add_contact, add_evidence, compute_verification_level_from_evidence  # noqa: E402
from app.models import Contact, Event, Evidence, Organization  # noqa: E402
from app.search import TavilyProvider  # noqa: E402
from config import (  # noqa: E402
    TAVILY_MAX_RESULTS_PER_QUERY, TAVILY_QUERIES_PER_LEAD, TAVILY_RESULTS_TO_VERIFY_PER_QUERY,
)

VERIFICATION_SCOPE_STATE = "FL"  # hard-coded safety net, same as Phase A

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

ROLE_KEYWORDS = (
    "development director", "director of development", "events director",
    "special events manager", "executive director", "fundraising director",
    "fundraising coordinator", "event chair", "event contact", "gala chair",
)
_ROLE_PATTERN = "|".join(re.escape(k) for k in ROLE_KEYWORDS)
# The role keyword is matched case-insensitively via a *scoped* inline flag
# ((?i:...)), not a global re.IGNORECASE -- a global flag would also make the
# "[A-Z]" in the name pattern match lowercase letters, letting stray words
# like "at" or "the" get captured as a fake first name.
NAME_THEN_ROLE_RE = re.compile(
    r"([A-Z][a-zA-Z'\-]+ [A-Z][a-zA-Z'\-]+)[,:\s\-]{1,4}(?i:(" + _ROLE_PATTERN + r"))"
)
ROLE_THEN_NAME_RE = re.compile(
    r"(?i:(" + _ROLE_PATTERN + r"))[,:\s\-]{1,4}([A-Z][a-zA-Z'\-]+ [A-Z][a-zA-Z'\-]+)"
)


def build_queries(event: Event, org: Organization, contacts: list[Contact]) -> list[str]:
    """~5-6 combined queries per lead -- deliberately combines the
    user's requested search categories rather than firing all of them
    literally, to avoid unnecessary duplicate searches."""
    queries = [f'"{event.event_name}" {org.organization_name}']
    if event.city and event.state:
        queries.append(f'"{event.event_name}" {event.city} {event.state}')
    queries.append(f'{org.organization_name} "development director" OR "director of development"')
    queries.append(f'{org.organization_name} "events director" OR "special events manager" OR "event chair"')
    queries.append(f'{org.organization_name} contact email fundraiser OR gala OR event')

    named = next((c for c in contacts if c.first_name or c.last_name), None)
    if named:
        full_name = f"{named.first_name or ''} {named.last_name or ''}".strip()
        queries.append(f'"{full_name}" {org.organization_name} email')

    return queries[:TAVILY_QUERIES_PER_LEAD]


def _event_match_fn(event: Event):
    def matcher(text):
        name_hit = vs._normalized_contains(text, event.event_name)
        date_hit = vs._date_appears(event.event_date, text) if event.event_date else True
        confirmed = name_hit and date_hit
        excerpt = vs._excerpt(text, event.event_name) if name_hit else (text[:300].strip() if text else "")
        return confirmed, excerpt
    return matcher


def _org_match_fn(org: Organization):
    def matcher(text):
        hit = vs._normalized_contains(text, org.organization_name)
        return hit, (vs._excerpt(text, org.organization_name) if hit else (text[:300].strip() if text else ""))
    return matcher


def _name_match_fn(full_name: str):
    def matcher(text):
        hit = vs._normalized_contains(text, full_name)
        return hit, (vs._excerpt(text, full_name) if hit else (text[:300].strip() if text else ""))
    return matcher


def _email_match_fn(email: str):
    def matcher(text):
        hit = email.lower() in (text or "").lower()
        return hit, (vs._excerpt(text, email) if hit else (text[:300].strip() if text else ""))
    return matcher


def _known_urls_for_lead(event: Event, org: Organization, contacts: list[Contact]) -> set[str]:
    urls = [event.fundraiser_url, event.event_url, event.discovery_source_url, event.ticket_url, org.website]
    for c in contacts:
        urls += [c.contact_page_url, c.contact_source_url, c.email_source_url]
    return {normalize_url(u) for u in urls if u and u != "NOT_FOUND"}


def _record_discovery_and_maybe_upgrade(session, *, entity_type, entity_id, claim, result, match_fn,
                                         known_urls, inaccessible, conflicts, label, upgrade_budget: list) -> None:
    confirmed, excerpt = match_fn(result.content)
    add_evidence(
        session, entity_type=entity_type, entity_id=entity_id, claim=claim,
        source_url=result.url, source_type="SEARCH_DISCOVERY", confirmed=confirmed,
        excerpt=excerpt or (result.content[:300] if result.content else result.title or ""),
    )
    if not confirmed:
        conflicts.append(f"{label}: Tavily-discovered page {result.url} did not confirm the claim -- needs manual review")

    already_known = normalize_url(result.url) in known_urls
    if already_known or upgrade_budget[0] <= 0:
        return  # Phase A already tried this URL, or we've spent this lead's upgrade-fetch allowance
    upgrade_budget[0] -= 1
    vs._try_live_then_archive(
        session, entity_type=entity_type, entity_id=entity_id, claim=claim, url=result.url,
        match_fn=match_fn, inaccessible=inaccessible, conflicts=conflicts, label=label,
    )


def _extract_new_contacts(content: str, org_name: str) -> list[tuple[str | None, str | None, str | None]]:
    """Conservative extraction: (name, title, email) tuples. A name/title is
    only attributed when it and the person co-occur in the same snippet, and
    an email is only ever the literal string found in content -- never
    constructed. Returns [] when nothing solid can be extracted.

    Each regex has a fixed, known group order (no heuristic needed to tell
    name from title), and any candidate "name" that's actually the
    organization's own name is rejected -- this catches sentence patterns
    like "SebastianStrong Foundation Development Director Maria Gomez" where
    NAME_THEN_ROLE_RE would otherwise match the org name as if it were a
    person's name."""
    found = []
    emails = EMAIL_RE.findall(content or "")
    org_key = normalize_event_name(org_name) if org_name else None

    named_roles = []
    for m in NAME_THEN_ROLE_RE.finditer(content or ""):
        name, title = m.group(1), m.group(2)
        named_roles.append((name.strip(), title.strip()))
    for m in ROLE_THEN_NAME_RE.finditer(content or ""):
        title, name = m.group(1), m.group(2)
        named_roles.append((name.strip(), title.strip()))

    named_roles = [
        (name, title) for name, title in named_roles
        if not (org_key and normalize_event_name(name) == org_key)
    ]

    if named_roles and emails:
        # Only pair a name+title with an email when exactly one candidate email
        # exists in this snippet -- ambiguous multi-email snippets are skipped
        # rather than guessing which email belongs to which name.
        email = emails[0] if len(emails) == 1 else None
        for name, title in named_roles:
            found.append((name, title, email))
    elif emails and not named_roles:
        for email in emails:
            found.append((None, None, email))

    return found


def process_lead(session, event: Event, org: Organization, provider: TavilyProvider,
                  inaccessible: dict, conflicts: list, tavily_log: dict) -> None:
    contacts = session.query(Contact).filter(Contact.organization_id == org.organization_id).all()
    known_urls = _known_urls_for_lead(event, org, contacts)
    queries = build_queries(event, org, contacts)

    lead_log = {"event": event.event_name, "queries": []}
    tavily_log["leads"].append(lead_log)

    for query in queries:
        if provider.budget_remaining() <= 0:
            tavily_log["stopped_early"] = True
            break

        results = provider.search(query, max_results=TAVILY_MAX_RESULTS_PER_QUERY)
        lead_log["queries"].append({"query": query, "results_returned": len(results)})
        upgrade_budget = [TAVILY_RESULTS_TO_VERIFY_PER_QUERY]

        # refresh contacts in case earlier results in this same loop added one
        contacts = session.query(Contact).filter(Contact.organization_id == org.organization_id).all()

        for result in results:
            content = result.content or ""

            if vs._normalized_contains(content, event.event_name) and (
                event.event_date is None or vs._date_appears(event.event_date, content)
            ):
                _record_discovery_and_maybe_upgrade(
                    session, entity_type="event", entity_id=event.event_id, claim="event_name_date",
                    result=result, match_fn=_event_match_fn(event), known_urls=known_urls,
                    inaccessible=inaccessible, conflicts=conflicts, label=f"EVENT '{event.event_name}'",
                    upgrade_budget=upgrade_budget,
                )

            if vs._normalized_contains(content, org.organization_name):
                _record_discovery_and_maybe_upgrade(
                    session, entity_type="organization", entity_id=org.organization_id, claim="org_identity",
                    result=result, match_fn=_org_match_fn(org), known_urls=known_urls,
                    inaccessible=inaccessible, conflicts=conflicts, label=f"ORG '{org.organization_name}'",
                    upgrade_budget=upgrade_budget,
                )

            for c in contacts:
                full_name = f"{c.first_name or ''} {c.last_name or ''}".strip()
                if full_name and vs._normalized_contains(content, full_name):
                    _record_discovery_and_maybe_upgrade(
                        session, entity_type="contact", entity_id=c.contact_id, claim=f"contact_identity:{c.contact_id}",
                        result=result, match_fn=_name_match_fn(full_name), known_urls=known_urls,
                        inaccessible=inaccessible, conflicts=conflicts, label=f"CONTACT '{full_name}'",
                        upgrade_budget=upgrade_budget,
                    )
                if c.email and c.email.lower() in content.lower():
                    _record_discovery_and_maybe_upgrade(
                        session, entity_type="contact", entity_id=c.contact_id, claim=f"contact_email:{c.email.lower()}",
                        result=result, match_fn=_email_match_fn(c.email), known_urls=known_urls,
                        inaccessible=inaccessible, conflicts=conflicts, label=f"CONTACT email '{c.email}'",
                        upgrade_budget=upgrade_budget,
                    )

            # New-contact discovery: literal emails only, name/title only when
            # co-occurring with the email in this same returned snippet.
            for name, title, email in _extract_new_contacts(content, org.organization_name):
                if email and any(c.email and c.email.lower() == email.lower() for c in contacts):
                    continue  # already on file
                first_name, last_name = (None, None)
                if name:
                    parts = name.split(maxsplit=1)
                    first_name = parts[0]
                    last_name = parts[1] if len(parts) > 1 else None
                new_contact, created = add_contact(
                    session, organization=org, first_name=first_name, last_name=last_name, title=title,
                    email=email, email_type=("GENERAL_ORGANIZATION" if not name else "UNVERIFIED") if email else "NOT_FOUND",
                    email_source_url=(result.url if email else None), contact_page_url=result.url,
                    contact_source_url=result.url, source_verification_level="SEARCH_RESULT_SUPPORTED",
                    email_verification_level="SEARCH_RESULT_SUPPORTED" if email else "NOT_FOUND",
                )
                if created:
                    tavily_log.setdefault("new_contacts_discovered", []).append(
                        {"organization": org.organization_name, "name": name, "title": title,
                         "email": email, "source_url": result.url}
                    )
                    add_evidence(
                        session, entity_type="contact", entity_id=new_contact.contact_id,
                        claim=(f"contact_email:{email.lower()}" if email else f"contact_identity:{new_contact.contact_id}"),
                        source_url=result.url, source_type="SEARCH_DISCOVERY", confirmed=True,
                        excerpt=content[:300],
                    )

    # Recompute levels for everything this lead touched.
    org.source_verification_level = compute_verification_level_from_evidence(
        session, entity_type="organization", entity_id=org.organization_id, claim="org_identity")
    event.source_verification_level = compute_verification_level_from_evidence(
        session, entity_type="event", entity_id=event.event_id, claim="event_name_date")
    if event.fundraiser_url and event.fundraiser_url != "NOT_FOUND":
        fu_key = normalize_url(event.fundraiser_url)
        fu_rows = session.query(Evidence).filter(
            Evidence.entity_type == "event", Evidence.entity_id == event.event_id,
            Evidence.claim == "event_name_date", Evidence.source_url_normalized == fu_key,
        ).all()
        confirmed_fu = [r for r in fu_rows if r.confirmed]
        if any(r.source_type == "LIVE_SOURCE" for r in confirmed_fu):
            event.fundraiser_url_verification_level = "SOURCE_PAGE_VERIFIED"
        elif any(r.source_type == "ARCHIVED_SOURCE" for r in confirmed_fu):
            event.fundraiser_url_verification_level = "ARCHIVED_SOURCE_VERIFIED"
        elif fu_rows:
            event.fundraiser_url_verification_level = event.fundraiser_url_verification_level or "UNVERIFIED"

    for c in session.query(Contact).filter(Contact.organization_id == org.organization_id).all():
        full_name = f"{c.first_name or ''} {c.last_name or ''}".strip()
        if full_name:
            c.source_verification_level = compute_verification_level_from_evidence(
                session, entity_type="contact", entity_id=c.contact_id, claim=f"contact_identity:{c.contact_id}")
        if c.email:
            level = compute_verification_level_from_evidence(
                session, entity_type="contact", entity_id=c.contact_id, claim=f"contact_email:{c.email.lower()}")
            c.email_verification_level = level
            if level in ("SOURCE_PAGE_VERIFIED", "ARCHIVED_SOURCE_VERIFIED") and c.email_type != "GENERAL_ORGANIZATION":
                c.email_type = "VERIFIED_PUBLIC"


def main():
    init_db()
    session = get_session()
    provider = TavilyProvider()

    if not provider.available:
        print(
            "TAVILY_API_KEY is not set -- Phase B skipped entirely (no calls made, no data changed). "
            "Set it as a GitHub Actions secret and re-run this workflow to perform Tavily-based verification."
        )
        log = {
            "run_at": datetime.now(timezone.utc).isoformat(),
            "status": "SKIPPED_NO_API_KEY",
            "queries_run": 0, "credits_used": 0,
        }
        (Path(__file__).resolve().parent.parent / "data" / "tavily_log.json").write_text(json.dumps(log, indent=2))
        session.close()
        return

    events = session.query(Event).filter(Event.state == VERIFICATION_SCOPE_STATE).all()
    if not events:
        print(f"No {VERIFICATION_SCOPE_STATE} events found -- nothing to research.")
        session.close()
        return

    inaccessible: dict[str, str] = {}
    conflicts: list[str] = []
    tavily_log: dict = {"run_at": datetime.now(timezone.utc).isoformat(), "status": "RAN", "leads": []}

    seen_org_ids: set[int] = set()
    for event in events:
        org = session.get(Organization, event.organization_id)
        if org.organization_id in seen_org_ids:
            continue  # one lead per org in this test set; avoids double-spending its query budget
        seen_org_ids.add(org.organization_id)
        if provider.budget_remaining() <= 0:
            tavily_log["stopped_early"] = True
            break
        process_lead(session, event, org, provider, inaccessible, conflicts, tavily_log)

    session.commit()

    tavily_log.update({
        "queries_run": provider.queries_run,
        "credits_used": provider.credits_used,
        "credits_budget": provider.max_credits,
        "queries_skipped_over_budget": provider.queries_skipped_over_budget,
        "inaccessible_urls": inaccessible,
        "conflicts": conflicts,
    })
    log_path = Path(__file__).resolve().parent.parent / "data" / "tavily_log.json"
    log_path.write_text(json.dumps(tavily_log, indent=2))

    session.close()
    print(
        f"Phase B complete. Tavily queries run: {provider.queries_run}, credits used: "
        f"{provider.credits_used}/{provider.max_credits}, new contacts discovered: "
        f"{len(tavily_log.get('new_contacts_discovered', []))}. Log written to {log_path}."
    )


if __name__ == "__main__":
    main()
