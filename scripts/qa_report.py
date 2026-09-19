"""Print a QA summary + results table for the current DB contents."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_session  # noqa: E402
from app.models import Contact, Event, Organization  # noqa: E402


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
    print(f"Verified events:         {count(lambda e: e.verification_status == 'VERIFIED')}")
    print(f"Partially verified:      {count(lambda e: e.verification_status == 'PARTIALLY_VERIFIED')}")
    print(f"Needs review:            {count(lambda e: e.verification_status == 'NEEDS_REVIEW')}")

    verified_public = sum(1 for c in contacts if c.email_type == "VERIFIED_PUBLIC")
    general_org = sum(1 for c in contacts if c.email_type == "GENERAL_ORGANIZATION")
    not_found = sum(1 for c in contacts if c.email_type == "NOT_FOUND")
    print(f"Verified public emails:  {verified_public}")
    print(f"General org emails:      {general_org}")
    print(f"Contacts missing email:  {not_found}")
    print()

    print("=" * 100)
    print("RESULTS")
    print("=" * 100)
    for e in events:
        org = session.get(Organization, e.organization_id)
        c = session.query(Contact).filter(Contact.organization_id == org.organization_id).first()
        print(f"\n[{e.event_id}] {e.event_name}")
        print(f"  Organization:       {org.organization_name}")
        print(f"  Event date:         {e.event_date or 'UNKNOWN'}  ({e.timing_bucket or 'n/a'}, {e.days_until_event if e.days_until_event is not None else 'n/a'} days)")
        print(f"  Location:           {e.city}, {e.state}")
        print(f"  Event type:         {e.event_type}")
        print(f"  Silent/Live auction:{e.silent_auction} / {e.live_auction}")
        print(f"  Org website:        {org.website}")
        print(f"  Event URL:          {e.event_url}")
        if c:
            print(f"  Contact:            {c.first_name or ''} {c.last_name or ''} -- {c.title or 'n/a'}")
            print(f"  Email:              {c.email or 'NOT FOUND'} ({c.email_type})")
            print(f"  Phone:              {c.phone or 'NOT FOUND'}")
            print(f"  Contact source URL: {c.contact_source_url}")
            print(f"  Email source URL:   {c.email_source_url}")
        else:
            print("  Contact:            NONE FOUND")
        print(f"  Verification status:{e.verification_status}")


if __name__ == "__main__":
    main()
