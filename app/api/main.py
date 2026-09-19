import csv
import io
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.queries import build_lead_query
from app.db import get_session, init_db
from app.models import Contact, Event, Organization

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Fundraising123 Lead Finder")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

FILTER_KEYS = [
    "state", "city", "event_type", "silent_auction", "live_auction", "gala",
    "golf_tournament", "recurring_event", "verification_status", "lead_status",
    "timing_bucket", "max_days_until", "q", "email_found", "named_contact_found",
]


@app.on_event("startup")
def _startup():
    init_db()


def _extract_filters(request: Request) -> dict:
    return {k: v for k, v in request.query_params.items() if k in FILTER_KEYS and v != ""}


def _primary_contact(session, organization_id):
    return (
        session.query(Contact)
        .filter(Contact.organization_id == organization_id)
        .order_by(Contact.email.isnot(None).desc())
        .first()
    )


@app.get("/")
def dashboard(request: Request):
    session = get_session()
    filters = _extract_filters(request)
    rows = build_lead_query(session, filters).all()

    all_events = session.query(Event).all()
    stats = {
        "total_organizations": session.query(Organization).count(),
        "total_events": len(all_events),
        "upcoming_events": sum(1 for e in all_events if (e.days_until_event or -1) >= 0),
        "silent_auctions": sum(1 for e in all_events if e.silent_auction == "YES"),
        "live_auctions": sum(1 for e in all_events if e.live_auction == "YES"),
        "galas": sum(1 for e in all_events if e.gala == "YES"),
        "golf_fundraisers": sum(1 for e in all_events if e.golf_tournament == "YES"),
        "verified_emails": session.query(Contact).filter(Contact.email_type == "VERIFIED_PUBLIC").count(),
        "unverified_emails": session.query(Contact).filter(Contact.email_type == "UNVERIFIED").count(),
        "general_emails": session.query(Contact).filter(Contact.email_type == "GENERAL_ORGANIZATION").count(),
        "contacts_missing_email": session.query(Contact).filter(Contact.email_type == "NOT_FOUND").count(),
    }
    state_counts = {}
    for e in all_events:
        if e.state:
            state_counts[e.state] = state_counts.get(e.state, 0) + 1

    leads = []
    for event, org in rows:
        contact = _primary_contact(session, org.organization_id)
        leads.append({"event": event, "org": org, "contact": contact})

    session.close()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "leads": leads, "stats": stats, "state_counts": state_counts,
            "filters": filters, "query_string": str(request.query_params),
        },
    )


@app.get("/leads/{event_id}")
def lead_detail(request: Request, event_id: int):
    session = get_session()
    event = session.get(Event, event_id)
    if event is None:
        session.close()
        return templates.TemplateResponse(request, "not_found.html", status_code=404)
    org = session.get(Organization, event.organization_id)
    contacts = session.query(Contact).filter(Contact.organization_id == org.organization_id).all()
    other_events = (
        session.query(Event)
        .filter(Event.organization_id == org.organization_id, Event.event_id != event.event_id)
        .all()
    )
    session.close()
    return templates.TemplateResponse(
        request,
        "detail.html",
        {"event": event, "org": org, "contacts": contacts, "other_events": other_events},
    )


CSV_COLUMNS = [
    "Organization", "Event", "Event Date", "Days Until Event", "Event Type",
    "Silent Auction", "Live Auction", "Gala", "Golf Tournament", "City", "State", "ZIP",
    "Contact First Name", "Contact Last Name", "Contact Title", "Email", "Email Type",
    "Phone", "Website", "Event URL", "Email Source URL", "Contact Source URL",
    "Verification Status", "Event Source Verification Level", "Contact Source Verification Level",
    "Lead Status", "Notes",
]


@app.get("/export.csv")
def export_csv(request: Request):
    session = get_session()
    filters = _extract_filters(request)
    rows = build_lead_query(session, filters).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    for event, org in rows:
        contact = _primary_contact(session, org.organization_id)
        writer.writerow([
            org.organization_name, event.event_name, event.event_date or "UNKNOWN",
            event.days_until_event if event.days_until_event is not None else "",
            event.event_type, event.silent_auction, event.live_auction, event.gala,
            event.golf_tournament, event.city, event.state, event.zip,
            contact.first_name if contact else "", contact.last_name if contact else "",
            contact.title if contact else "", contact.email if contact else "NOT FOUND",
            contact.email_type if contact else "NOT_FOUND", contact.phone if contact else "",
            org.website or "", event.event_url or "",
            contact.email_source_url if contact else "", contact.contact_source_url if contact else "",
            event.verification_status, event.source_verification_level,
            contact.source_verification_level if contact else "NOT_FOUND",
            event.lead_status, event.notes or "",
        ])
    session.close()

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=fundraising123_leads.csv"},
    )
