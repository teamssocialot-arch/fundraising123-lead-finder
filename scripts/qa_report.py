"""Print a QA summary + results table for the current DB contents.

If data/verification_log.json exists (written by scripts/verify_sources.py,
Phase A), also prints the inaccessible-URLs and conflicting-information
sections from the most recent verification pass. If data/tavily_log.json
exists (written by scripts/verify_with_tavily.py, Phase B), also prints
Tavily query/credit consumption and newly discovered contacts.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_session  # noqa: E402
from app.ingest import lead_evidence_summary, select_primary_contact  # noqa: E402
from app.models import Contact, Event, Evidence, Organization  # noqa: E402
from config import VERIFICATION_LEVELS  # noqa: E402

LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "verification_log.json"
TAVILY_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "tavily_log.json"


def _level_counts(rows, level_attr="source_verification_level"):
    return {level: sum(1 for r in rows if getattr(r, level_attr) == level) for level in VERIFICATION_LEVELS}


def main():
    session = get_session()

    events = session.query(Event).all()
    orgs = session.query(Organization).all()
    contacts = session.query(Contact).all()
    evidence_rows = session.query(Evidence).all()

    def count(pred):
        return sum(1 for e in events if pred(e))

    print("=" * 100)
    print("QA SUMMARY")
    print("=" * 100)
    print(f"Total events:            {len(events)}")
    print(f"Total organizations:     {len(orgs)}")
    print(f"Total contacts:          {len(contacts)}")
    print(f"Total evidence rows:     {len(evidence_rows)}")
    print(f"Silent auctions (YES):   {count(lambda e: e.silent_auction == 'YES')}")
    print(f"Live auctions (YES):     {count(lambda e: e.live_auction == 'YES')}")
    print(f"Galas (YES):             {count(lambda e: e.gala == 'YES')}")
    print(f"Golf tournaments (YES):  {count(lambda e: e.golf_tournament == 'YES')}")
    print(f"Record-complete (VERIFIED, i.e. state/date/website/url/city/fundraiser_url present): {count(lambda e: e.verification_status == 'VERIFIED')}")
    print(f"Partially complete:      {count(lambda e: e.verification_status == 'PARTIALLY_VERIFIED')}")
    print(f"Needs review:            {count(lambda e: e.verification_status == 'NEEDS_REVIEW')}")
    print(f"Fundraiser URL found:    {count(lambda e: e.fundraiser_url and e.fundraiser_url != 'NOT_FOUND')}")
    print(f"Fundraiser URL NOT_FOUND (flagged for review): {count(lambda e: not e.fundraiser_url or e.fundraiser_url == 'NOT_FOUND')}")
    named_contacts = sum(1 for c in contacts if c.first_name or c.last_name)
    print(f"Named contacts (have a first/last name, any source): {named_contacts} of {len(contacts)} total contacts")

    print()
    print("-" * 100)
    print("VERIFICATION LEVELS (how each fact was actually confirmed)")
    print("-" * 100)
    event_levels = _level_counts(events)
    org_levels = _level_counts(orgs)
    contact_levels = _level_counts(contacts, "source_verification_level")
    email_levels = _level_counts([c for c in contacts if c.email], "email_verification_level")
    print(f"{'Level':26s} {'Events':>8s} {'Orgs':>8s} {'Contact IDs':>12s} {'Emails':>8s}")
    for level in VERIFICATION_LEVELS:
        print(f"{level:26s} {event_levels[level]:>8d} {org_levels[level]:>8d} {contact_levels[level]:>12d} {email_levels.get(level, 0):>8d}")

    verified_public = sum(1 for c in contacts if c.email_type == "VERIFIED_PUBLIC")
    unverified_email = sum(1 for c in contacts if c.email_type == "UNVERIFIED")
    general_org = sum(1 for c in contacts if c.email_type == "GENERAL_ORGANIZATION")
    not_found = sum(1 for c in contacts if c.email_type == "NOT_FOUND")
    print()
    print(f"Emails VERIFIED_PUBLIC (page-content confirmed, live or archived): {verified_public}")
    print(f"Emails UNVERIFIED (named, not page-confirmed):                    {unverified_email}")
    print(f"General org emails (info@, events@, etc.):                       {general_org}")
    print(f"Contacts with NOT_FOUND email:                                   {not_found}")

    evidence_source_counts = {}
    for r in evidence_rows:
        evidence_source_counts[r.source_type] = evidence_source_counts.get(r.source_type, 0) + 1
    print()
    print("Evidence rows by source type: " + ", ".join(f"{k}={v}" for k, v in sorted(evidence_source_counts.items())) or "(none)")

    if LOG_PATH.exists():
        log = json.loads(LOG_PATH.read_text())
        print()
        print("-" * 100)
        print(f"LAST VERIFICATION RUN: {log.get('run_at')} ({log.get('scope')})")
        print("-" * 100)
        print(f"Pages fetched successfully:      {log.get('pages_fetched_ok', 0)}")
        print(f"Wayback lookups attempted:       {log.get('wayback_lookups_attempted', 0)}")
        print(f"ProPublica lookups attempted:    {log.get('propublica_lookups_attempted', 0)}")
        inaccessible = log.get("inaccessible_urls", {})
        print(f"URLs that could not be accessed: {len(inaccessible)}")
        for url, reason in inaccessible.items():
            print(f"  [{reason}] {url}")
        conflicts = log.get("conflicts", [])
        print(f"\nConflicting information discovered: {len(conflicts)}")
        for c in conflicts:
            print(f"  - {c}")
    else:
        print()
        print("(No verification_log.json found yet -- run scripts/verify_sources.py first "
              "to get real source-page verification results.)")

    if TAVILY_LOG_PATH.exists():
        tlog = json.loads(TAVILY_LOG_PATH.read_text())
        print()
        print("-" * 100)
        print(f"LAST PHASE B (TAVILY) RUN: {tlog.get('run_at')} -- status: {tlog.get('status')}")
        print("-" * 100)
        if tlog.get("status") == "SKIPPED_NO_API_KEY":
            print("Tavily was skipped: TAVILY_API_KEY was not set for this run. No calls made, no data changed.")
        else:
            print(f"Tavily queries run:              {tlog.get('queries_run', 0)}")
            print(f"Tavily credits used:              {tlog.get('credits_used', 0)} / {tlog.get('credits_budget', 'n/a')}")
            print(f"Queries skipped (over budget):    {tlog.get('queries_skipped_over_budget', 0)}")
            print(f"Stopped early (budget exhausted): {tlog.get('stopped_early', False)}")
            new_contacts = tlog.get("new_contacts_discovered", [])
            print(f"New contacts discovered via Tavily: {len(new_contacts)}")
            for nc in new_contacts:
                print(f"  - {nc.get('name') or '(no name)'} / {nc.get('title') or 'n/a'} / "
                      f"{nc.get('email') or 'no email'} -- {nc.get('organization')} "
                      f"(source: {nc.get('source_url')})")
            tconflicts = tlog.get("conflicts", [])
            print(f"\nPhase B conflicts/needs-review items: {len(tconflicts)}")
            for c in tconflicts:
                print(f"  - {c}")
    else:
        print()
        print("(No tavily_log.json found yet -- run scripts/verify_with_tavily.py to get Phase B results.)")
    print()

    print("=" * 100)
    print("RESULTS")
    print("=" * 100)
    total_evidence_sources = 0
    for e in events:
        org = session.get(Organization, e.organization_id)
        org_contacts = session.query(Contact).filter(Contact.organization_id == org.organization_id).all()
        primary = select_primary_contact(org_contacts)
        ev_summary = lead_evidence_summary(session, event=e, organization=org, contacts=org_contacts)
        total_evidence_sources += ev_summary["count"]

        print(f"\n[{e.event_id}] {e.event_name}")
        print(f"  Organization:       {org.organization_name} (org level: {org.source_verification_level})")
        print(f"  Event date:         {e.event_date or 'UNKNOWN'}  ({e.timing_bucket or 'n/a'}, {e.days_until_event if e.days_until_event is not None else 'n/a'} days)")
        print(f"  Location:           {e.city}, {e.state}")
        print(f"  Event type:         {e.event_type}")
        print(f"  Silent/Live auction:{e.silent_auction} / {e.live_auction}")
        print(f"  Fundraiser URL:     {e.fundraiser_url or 'NOT_FOUND'} ({e.fundraiser_url_type or 'n/a'}, "
              f"level: {e.fundraiser_url_verification_level})")
        print(f"  Overall event level:{e.source_verification_level}")
        print(f"  Evidence:           {ev_summary['summary']}")
        for url in ev_summary["urls"]:
            print(f"    - {url}")
        if primary:
            print(f"  Primary contact:    {primary.first_name or ''} {primary.last_name or ''} -- {primary.title or 'n/a'}")
            print(f"    Email:              {primary.email or 'NOT FOUND'} ({primary.email_type}, email level: {primary.email_verification_level})")
            print(f"    Phone:              {primary.phone or 'NOT FOUND'}")
        else:
            print("  Primary contact:    NONE FOUND")
        if len(org_contacts) > 1:
            for c in org_contacts:
                if c is primary:
                    continue
                print(f"  Other contact:      {c.first_name or ''} {c.last_name or ''} -- {c.title or 'n/a'} "
                      f"| {c.email or 'NOT FOUND'} ({c.email_type})")
        print(f"  Record-completeness status: {e.verification_status}")

    if events:
        print(f"\nAverage evidence sources per lead: {total_evidence_sources / len(events):.1f}")


if __name__ == "__main__":
    main()
