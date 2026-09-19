"""Print a QA summary + results table for the current DB contents.

If data/verification_log.json exists (written by scripts/verify_sources.py),
also prints the inaccessible-URLs and conflicting-information sections from
the most recent verification pass.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_session  # noqa: E402
from app.models import Contact, Event, Organization  # noqa: E402

LEVELS = ("SOURCE_PAGE_VERIFIED", "SEARCH_RESULT_SUPPORTED", "UNVERIFIED", "NOT_FOUND")
LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "verification_log.json"


def _level_counts(rows, level_attr="source_verification_level"):
    return {level: sum(1 for r in rows if getattr(r, level_attr) == level) for level in LEVELS}


def main():
    session = get_session()

    events = session.query(Event).all()
    orgs = session.query(Organization).all()
    contacts = session.query(Contact).all()

    def count(pred):
        return sum(1 for e in events if pred(e))

    print("=" * 100)
    print("QA SUMMARY")
    print("=" * 100)
    print(f"Total events:            {len(events)}")
    print(f"Total organizations:     {len(orgs)}")
    print(f"Total contacts:          {len(contacts)}")
    print(f"Silent auctions (YES):   {count(lambda e: e.silent_auction == 'YES')}")
    print(f"Live auctions (YES):     {count(lambda e: e.live_auction == 'YES')}")
    print(f"Galas (YES):             {count(lambda e: e.gala == 'YES')}")
    print(f"Golf tournaments (YES):  {count(lambda e: e.golf_tournament == 'YES')}")
    print(f"Record-complete (VERIFIED, i.e. state/date/website/url/city/fundraiser_url present): {count(lambda e: e.verification_status == 'VERIFIED')}")
    print(f"Partially complete:      {count(lambda e: e.verification_status == 'PARTIALLY_VERIFIED')}")
    print(f"Needs review:            {count(lambda e: e.verification_status == 'NEEDS_REVIEW')}")
    print(f"Fundraiser URL found:    {count(lambda e: e.fundraiser_url and e.fundraiser_url != 'NOT_FOUND')}")
    print(f"Fundraiser URL NOT_FOUND (flagged for review): {count(lambda e: not e.fundraiser_url or e.fundraiser_url == 'NOT_FOUND')}")

    print()
    print("-" * 100)
    print("SOURCE VERIFICATION LEVELS (how each fact was actually confirmed)")
    print("-" * 100)
    event_levels = _level_counts(events)
    org_levels = _level_counts(orgs)
    contact_levels = _level_counts(contacts)
    print(f"{'Level':26s} {'Events':>8s} {'Orgs':>8s} {'Contacts':>10s}")
    for level in LEVELS:
        print(f"{level:26s} {event_levels[level]:>8d} {org_levels[level]:>8d} {contact_levels[level]:>10d}")

    verified_public = sum(1 for c in contacts if c.email_type == "VERIFIED_PUBLIC")
    unverified_email = sum(1 for c in contacts if c.email_type == "UNVERIFIED")
    general_org = sum(1 for c in contacts if c.email_type == "GENERAL_ORGANIZATION")
    not_found = sum(1 for c in contacts if c.email_type == "NOT_FOUND")
    print()
    print(f"Emails SOURCE_PAGE_VERIFIED + VERIFIED_PUBLIC: {verified_public}")
    print(f"Emails UNVERIFIED (named, not page-confirmed): {unverified_email}")
    print(f"General org emails (info@, events@, etc.):     {general_org}")
    print(f"Contacts with NOT_FOUND email:                 {not_found}")

    if LOG_PATH.exists():
        log = json.loads(LOG_PATH.read_text())
        print()
        print("-" * 100)
        print(f"LAST VERIFICATION RUN: {log.get('run_at')} ({log.get('scope')})")
        print("-" * 100)
        print(f"Pages fetched successfully: {log.get('urls_fetched_ok', 0)}")
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
    print()

    print("=" * 100)
    print("RESULTS")
    print("=" * 100)
    for e in events:
        org = session.get(Organization, e.organization_id)
        org_contacts = session.query(Contact).filter(Contact.organization_id == org.organization_id).all()
        print(f"\n[{e.event_id}] {e.event_name}")
        print(f"  Organization:       {org.organization_name} (org source level: {org.source_verification_level})")
        print(f"  Event date:         {e.event_date or 'UNKNOWN'}  ({e.timing_bucket or 'n/a'}, {e.days_until_event if e.days_until_event is not None else 'n/a'} days)")
        print(f"  Location:           {e.city}, {e.state}")
        print(f"  Event type:         {e.event_type}")
        print(f"  Silent/Live auction:{e.silent_auction} / {e.live_auction}")
        print(f"  Org website:        {org.website}")
        print(f"  Event URL:          {e.event_url}")
        print(f"  Fundraiser URL:     {e.fundraiser_url or 'NOT_FOUND'} ({e.fundraiser_url_type or 'n/a'}, "
              f"level: {e.fundraiser_url_verification_level}, last checked: {e.fundraiser_url_last_checked or 'never'})")
        if e.discovery_source_url and e.discovery_source_url != e.fundraiser_url:
            print(f"  Discovery source URL (differs from fundraiser_url): {e.discovery_source_url}")
        print(f"  Event source level: {e.source_verification_level}")
        if org_contacts:
            for c in org_contacts:
                print(f"  Contact:            {c.first_name or ''} {c.last_name or ''} -- {c.title or 'n/a'}")
                print(f"    Email:              {c.email or 'NOT FOUND'} ({c.email_type}, source level: {c.source_verification_level})")
                print(f"    Phone:              {c.phone or 'NOT FOUND'}")
                print(f"    Contact source URL: {c.contact_source_url}")
                print(f"    Email source URL:   {c.email_source_url}")
        else:
            print("  Contact:            NONE FOUND")
        print(f"  Record-completeness status: {e.verification_status}")


if __name__ == "__main__":
    main()
