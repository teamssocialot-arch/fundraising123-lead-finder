import csv
import io
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, or_

from app.api.queries import build_lead_query
from app.db import get_session, init_db
from app.ingest import lead_evidence_summary, select_primary_contact
from app.models import Contact, Event, Evidence, Organization

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


def _contacts_for_org(session, organization_id):
    return session.query(Contact).filter(Contact.organization_id == organization_id).all()


def _primary_contact(session, organization_id):
    # Relevance-ranked (event contact > development director > ... > general org
    # inbox), never just "whichever has the easiest-to-verify email" -- see
    # app.ingest.select_primary_contact / contact_role_rank.
    return select_primary_contact(_contacts_for_org(session, organization_id))


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
        contacts = _contacts_for_org(session, org.organization_id)
        contact = select_primary_contact(contacts)
        evidence = lead_evidence_summary(session, event=event, organization=org, contacts=contacts)
        leads.append({"event": event, "org": org, "contact": contact, "evidence": evidence})

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
    primary_contact = select_primary_contact(contacts)
    if primary_contact:
        contacts = [primary_contact] + [c for c in contacts if c is not primary_contact]
    other_events = (
        session.query(Event)
        .filter(Event.organization_id == org.organization_id, Event.event_id != event.event_id)
        .all()
    )
    evidence = lead_evidence_summary(session, event=event, organization=org, contacts=contacts)
    evidence_rows = (
        session.query(Evidence)
        .filter(
            or_(
                and_(Evidence.entity_type == "event", Evidence.entity_id == event.event_id),
                and_(Evidence.entity_type == "organization", Evidence.entity_id == org.organization_id),
                and_(Evidence.entity_type == "contact", Evidence.entity_id.in_([c.contact_id for c in contacts])),
            )
        )
        .order_by(Evidence.fetched_at.desc())
        .all()
    )
    session.close()
    return templates.TemplateResponse(
        request,
        "detail.html",
        {
            "event": event, "org": org, "contacts": contacts, "other_events": other_events,
            "evidence": evidence, "evidence_rows": evidence_rows,
        },
    )


CSV_COLUMNS = [
    "Organization", "Event", "Event Date", "Days Until Event", "Event Type",
    "Silent Auction", "Live Auction", "Gala", "Golf Tournament", "City", "State", "ZIP",
    "Contact First Name", "Contact Last Name", "Contact Title", "Email", "Email Type",
    "Email Verification Level", "Phone", "Website", "Event URL", "Fundraiser URL", "Fundraiser URL Type",
    "Fundraiser URL Verification Level", "Fundraiser URL Last Checked", "Discovery Source URL",
    "Email Source URL", "Contact Source URL",
    "Verification Status", "Overall Verification Level", "Contact Identity Verification Level",
    "Evidence Count", "Evidence Summary",
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
        contacts = _contacts_for_org(session, org.organization_id)
        contact = select_primary_contact(contacts)
        evidence = lead_evidence_summary(session, event=event, organization=org, contacts=contacts)
        writer.writerow([
            org.organization_name, event.event_name, event.event_date or "UNKNOWN",
            event.days_until_event if event.days_until_event is not None else "",
            event.event_type, event.silent_auction, event.live_auction, event.gala,
            event.golf_tournament, event.city, event.state, event.zip,
            contact.first_name if contact else "", contact.last_name if contact else "",
            contact.title if contact else "", contact.email if contact else "NOT FOUND",
            contact.email_type if contact else "NOT_FOUND",
            contact.email_verification_level if contact else "NOT_FOUND",
            contact.phone if contact else "",
            org.website or "", event.event_url or "",
            event.fundraiser_url or "NOT_FOUND", event.fundraiser_url_type or "",
            event.fundraiser_url_verification_level, event.fundraiser_url_last_checked or "",
            event.discovery_source_url or "",
            contact.email_source_url if contact else "", contact.contact_source_url if contact else "",
            event.verification_status, event.source_verification_level,
            contact.source_verification_level if contact else "NOT_FOUND",
            evidence["count"], evidence["summary"],
            event.lead_status, event.notes or "",
        ])
    session.close()

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=fundraising123_leads.csv"},
    )
