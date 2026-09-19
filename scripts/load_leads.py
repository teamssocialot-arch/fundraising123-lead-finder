"""Load researched leads (JSON) through the real ingest/dedupe pipeline.

Usage: python scripts/load_leads.py data/seed/florida_stage1_leads.json

Each lead in the JSON must carry its own source URLs -- this script does not
fetch anything itself; it persists already-researched, human/agent-verified
data and records the evidence trail (Source rows) that was collected during
research.
"""
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_session, init_db  # noqa: E402
from app.ingest import add_contact, find_or_create_event, find_or_create_organization, log_activity, record_source  # noqa: E402


def parse_date(value):
    if not value or value == "UNKNOWN":
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def load_file(path: str):
    with open(path) as f:
        leads = json.load(f)

    init_db()
    session = get_session()

    stats = {
        "leads_in_file": len(leads),
        "organizations_created": 0,
        "organizations_deduped": 0,
        "events_created": 0,
        "events_deduped": 0,
        "contacts_created": 0,
        "sources_recorded": 0,
    }

    for lead in leads:
        org_data = lead["organization"]
        event_data = lead["event"]
        contacts_data = lead.get("contacts") or ([lead["contact"]] if lead.get("contact") else [])
        sources = lead.get("sources", [])

        org, org_created = find_or_create_organization(
            session,
            name=org_data["name"],
            website=org_data.get("website"),
            city=org_data.get("city"),
            state=org_data.get("state"),
            zip_code=org_data.get("zip"),
            address=org_data.get("address"),
            phone=org_data.get("phone"),
            organization_type=org_data.get("organization_type"),
        )
        stats["organizations_created" if org_created else "organizations_deduped"] += 1

        event, event_created = find_or_create_event(
            session,
            organization=org,
            event_name=event_data["name"],
            event_date=parse_date(event_data.get("date")),
            city=event_data.get("city") or org_data.get("city"),
            state=event_data.get("state") or org_data.get("state"),
            zip_code=event_data.get("zip"),
            address=event_data.get("address"),
            venue=event_data.get("venue"),
            event_type=event_data.get("event_type"),
            description=event_data.get("description"),
            event_url=event_data.get("event_url"),
            ticket_url=event_data.get("ticket_url"),
            silent_auction=event_data.get("silent_auction", "UNKNOWN"),
            live_auction=event_data.get("live_auction", "UNKNOWN"),
            raffle=event_data.get("raffle", "UNKNOWN"),
            gala=event_data.get("gala", "NO"),
            golf_tournament=event_data.get("golf_tournament", "NO"),
            casino_night=event_data.get("casino_night", "NO"),
            travel_packages=event_data.get("travel_packages", "UNKNOWN"),
            sponsors=event_data.get("sponsors", "UNKNOWN"),
            discovery_source=event_data.get("discovery_source"),
        )
        stats["events_created" if event_created else "events_deduped"] += 1

        first_contact = None
        for contact_data in contacts_data:
            contact = add_contact(
                session,
                organization=org,
                first_name=contact_data.get("first_name"),
                last_name=contact_data.get("last_name"),
                title=contact_data.get("title"),
                email=contact_data.get("email"),
                email_type=contact_data.get("email_type", "NOT_FOUND"),
                phone=contact_data.get("phone"),
                contact_page_url=contact_data.get("contact_page_url"),
                email_source_url=contact_data.get("email_source_url"),
                contact_source_url=contact_data.get("contact_source_url"),
            )
            stats["contacts_created"] += 1
            first_contact = first_contact or contact

        for src in sources:
            record_source(
                session,
                url=src["url"],
                entity_type=src["entity_type"],
                entity_id=org.organization_id if src["entity_type"] == "organization" else event.event_id,
                purpose=src["purpose"],
            )
            stats["sources_recorded"] += 1

        log_activity(
            session,
            activity_type="INGESTED",
            notes=f"Loaded from {Path(path).name}",
            organization_id=org.organization_id,
            event_id=event.event_id,
            contact_id=first_contact.contact_id if first_contact else None,
            created_by="manual_research_stage1",
        )

    session.commit()
    session.close()
    return stats


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/load_leads.py <path_to_json>")
        sys.exit(1)
    result = load_file(sys.argv[1])
    print(json.dumps(result, indent=2))
