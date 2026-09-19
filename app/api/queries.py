"""Shared query-building for the dashboard list view and CSV export, so
filters behave identically in both places."""
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import Contact, Event, Organization


def build_lead_query(session: Session, filters: dict):
    query = (
        session.query(Event, Organization)
        .join(Organization, Event.organization_id == Organization.organization_id)
    )

    if filters.get("state"):
        query = query.filter(Event.state == filters["state"])
    if filters.get("city"):
        query = query.filter(Event.city.ilike(f"%{filters['city']}%"))
    if filters.get("event_type"):
        query = query.filter(Event.event_type == filters["event_type"])
    if filters.get("silent_auction"):
        query = query.filter(Event.silent_auction == filters["silent_auction"])
    if filters.get("live_auction"):
        query = query.filter(Event.live_auction == filters["live_auction"])
    if filters.get("gala"):
        query = query.filter(Event.gala == filters["gala"])
    if filters.get("golf_tournament"):
        query = query.filter(Event.golf_tournament == filters["golf_tournament"])
    if filters.get("recurring_event") in ("true", "false"):
        query = query.filter(Event.recurring_event == (filters["recurring_event"] == "true"))
    if filters.get("verification_status"):
        query = query.filter(Event.verification_status == filters["verification_status"])
    if filters.get("lead_status"):
        query = query.filter(Event.lead_status == filters["lead_status"])
    if filters.get("timing_bucket"):
        query = query.filter(Event.timing_bucket == filters["timing_bucket"])
    if filters.get("max_days_until") not in (None, ""):
        query = query.filter(Event.days_until_event <= int(filters["max_days_until"]))

    if filters.get("q"):
        like = f"%{filters['q']}%"
        query = query.filter(
            or_(
                Event.event_name.ilike(like),
                Organization.organization_name.ilike(like),
                Event.city.ilike(like),
                Event.description.ilike(like),
            )
        )

    if filters.get("email_found") == "true":
        query = query.join(Contact, Contact.organization_id == Organization.organization_id).filter(
            Contact.email.isnot(None)
        )
    elif filters.get("named_contact_found") == "true":
        query = query.join(Contact, Contact.organization_id == Organization.organization_id).filter(
            Contact.first_name.isnot(None)
        )

    return query.order_by(Event.days_until_event.asc().nullslast(), Event.event_date.asc().nullslast())
