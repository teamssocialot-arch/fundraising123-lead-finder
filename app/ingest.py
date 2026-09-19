"""Ingestion pipeline: turns researched lead data into DB rows.

This is the single place that (a) applies dedup rules before creating
organizations/events, (b) computes derived fields (days_until_event,
timing_bucket, verification_status), and (c) records source URLs as
evidence. Both the automated crawler (future phases) and manual/assisted
research (Phase 1 validation) go through this same code path so the schema
and dedup guarantees are exercised identically.
"""
import json
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.dedupe.normalize import normalize_domain, normalize_event_name, normalize_org_name, normalize_phone
from app.models import Contact, Event, LeadActivity, Organization, RecheckQueue, Source
from config import ALLOWED_STATES, FUNDRAISER_URL_TYPES, SOURCE_VERIFICATION_LEVELS, TIMING_BUCKETS


class IngestError(ValueError):
    pass


def _check_source_verification_level(value: str) -> str:
    if value not in SOURCE_VERIFICATION_LEVELS:
        raise IngestError(f"source_verification_level must be one of {SOURCE_VERIFICATION_LEVELS}, got {value!r}")
    return value


def compute_timing_bucket(days_until_event: int | None) -> str | None:
    if days_until_event is None:
        return None
    for low, high, label in TIMING_BUCKETS:
        if high is None:
            if days_until_event >= low:
                return label
        elif low <= days_until_event <= high:
            return label
    return None


def compute_days_until(event_date: date | None, today: date | None = None) -> int | None:
    if event_date is None:
        return None
    today = today or datetime.now(timezone.utc).date()
    return (event_date - today).days


def compute_verification_status(*, state: str | None, event_date: date | None, event_url: str | None,
                                 org_website: str | None, city: str | None,
                                 fundraiser_url: str | None = None) -> str:
    """Record-completeness signal only (state/date/website/event_url/city/fundraiser_url present).

    This is NOT a claim that any fact was confirmed by visiting its source
    page -- that confidence level is tracked separately in
    source_verification_level (SOURCE_PAGE_VERIFIED / SEARCH_RESULT_SUPPORTED
    / UNVERIFIED / NOT_FOUND). A "VERIFIED" record-completeness status can and
    often will coexist with a SEARCH_RESULT_SUPPORTED source_verification_level.

    A missing or NOT_FOUND fundraiser_url keeps the record out of "VERIFIED"
    even if everything else is present -- every lead needs a fundraiser-specific
    page a human can click through to before outreach.
    """
    if not state or state not in ALLOWED_STATES:
        return "NEEDS_REVIEW"
    if event_date is None:
        return "NEEDS_REVIEW"
    has_fundraiser_url = bool(fundraiser_url) and fundraiser_url != "NOT_FOUND"
    if org_website and event_url and city and has_fundraiser_url:
        return "VERIFIED"
    return "PARTIALLY_VERIFIED"


def find_or_create_organization(session: Session, *, name: str, website: str | None = None,
                                 city: str | None = None, state: str | None = None,
                                 zip_code: str | None = None, address: str | None = None,
                                 phone: str | None = None, organization_type: str | None = None,
                                 source_verification_level: str = "UNVERIFIED",
                                 ) -> tuple[Organization, bool]:
    """Returns (organization, created). Matches on domain first, then
    normalized name + state, before creating a new row."""
    if not name or not name.strip():
        raise IngestError("organization name is required")
    _check_source_verification_level(source_verification_level)

    normalized_name = normalize_org_name(name)
    domain = normalize_domain(website) if website else None

    existing = None
    if domain:
        existing = session.query(Organization).filter(Organization.domain == domain).first()
    if existing is None:
        existing = (
            session.query(Organization)
            .filter(Organization.normalized_name == normalized_name, Organization.state == state)
            .first()
        )

    if existing:
        # Backfill any fields we now know but didn't before -- never overwrite known data.
        if not existing.website and website:
            existing.website = website
            existing.domain = domain
        if not existing.address and address:
            existing.address = address
        if not existing.city and city:
            existing.city = city
        if not existing.zip and zip_code:
            existing.zip = zip_code
        if not existing.phone and phone:
            existing.phone = phone
        if not existing.organization_type and organization_type:
            existing.organization_type = organization_type
        if SOURCE_VERIFICATION_LEVELS.index(source_verification_level) < SOURCE_VERIFICATION_LEVELS.index(existing.source_verification_level or "NOT_FOUND"):
            existing.source_verification_level = source_verification_level
        return existing, False

    org = Organization(
        organization_name=name.strip(),
        normalized_name=normalized_name,
        website=website,
        domain=domain,
        organization_type=organization_type,
        address=address,
        city=city,
        state=state,
        zip=zip_code,
        phone=phone,
        source_verification_level=source_verification_level,
    )
    session.add(org)
    session.flush()
    return org, True


def find_or_create_event(session: Session, *, organization: Organization, event_name: str,
                          event_date: date | None, city: str | None, state: str | None,
                          zip_code: str | None = None, address: str | None = None, venue: str | None = None,
                          event_type: str | None = None, description: str | None = None,
                          event_url: str | None = None, ticket_url: str | None = None,
                          silent_auction: str = "UNKNOWN", live_auction: str = "UNKNOWN",
                          raffle: str = "UNKNOWN", gala: str = "NO", golf_tournament: str = "NO",
                          casino_night: str = "NO", travel_packages: str = "UNKNOWN",
                          sponsors: str = "UNKNOWN", discovery_source: str | None = None,
                          source_verification_level: str = "UNVERIFIED",
                          fundraiser_url: str | None = None, fundraiser_url_type: str | None = None,
                          fundraiser_url_verification_level: str = "UNVERIFIED",
                          discovery_source_url: str | None = None,
                          today: date | None = None) -> tuple[Event, bool]:
    if not event_name or not event_name.strip():
        raise IngestError("event name is required")
    if state and state not in ALLOWED_STATES:
        raise IngestError(f"state {state!r} is outside the U.S. 50-states + D.C. scope")
    _check_source_verification_level(source_verification_level)
    _check_source_verification_level(fundraiser_url_verification_level)
    if fundraiser_url and fundraiser_url != "NOT_FOUND":
        if not fundraiser_url_type:
            raise IngestError("fundraiser_url provided without a fundraiser_url_type")
        if fundraiser_url_type not in FUNDRAISER_URL_TYPES:
            raise IngestError(f"fundraiser_url_type must be one of {FUNDRAISER_URL_TYPES}, got {fundraiser_url_type!r}")
        if fundraiser_url == organization.website:
            raise IngestError("fundraiser_url must not be just the organization's homepage")

    normalized_name = normalize_event_name(event_name)

    query = session.query(Event).filter(
        Event.organization_id == organization.organization_id,
        Event.normalized_event_name == normalized_name,
    )
    if event_date is not None:
        query = query.filter(Event.event_date == event_date)
    existing = query.first()
    if existing:
        return existing, False

    days_until = compute_days_until(event_date, today=today)
    timing_bucket = compute_timing_bucket(days_until)
    verification_status = compute_verification_status(
        state=state, event_date=event_date, event_url=event_url,
        org_website=organization.website, city=city, fundraiser_url=fundraiser_url,
    )

    event = Event(
        organization_id=organization.organization_id,
        event_name=event_name.strip(),
        normalized_event_name=normalized_name,
        event_date=event_date,
        venue=venue,
        address=address,
        city=city,
        state=state,
        zip=zip_code,
        event_type=event_type,
        description=description,
        event_url=event_url,
        ticket_url=ticket_url,
        silent_auction=silent_auction,
        live_auction=live_auction,
        raffle=raffle,
        gala=gala,
        golf_tournament=golf_tournament,
        casino_night=casino_night,
        travel_packages=travel_packages,
        sponsors=sponsors,
        days_until_event=days_until,
        timing_bucket=timing_bucket,
        discovery_source=discovery_source,
        verification_status=verification_status,
        source_verification_level=source_verification_level,
        fundraiser_url=fundraiser_url,
        fundraiser_url_type=fundraiser_url_type,
        fundraiser_url_verification_level=fundraiser_url_verification_level,
        discovery_source_url=discovery_source_url,
        lead_status="NEW",
    )
    session.add(event)
    session.flush()
    return event, True


def add_contact(session: Session, *, organization: Organization, first_name: str | None = None,
                 last_name: str | None = None, title: str | None = None, email: str | None = None,
                 email_type: str = "NOT_FOUND", phone: str | None = None,
                 contact_page_url: str | None = None, email_source_url: str | None = None,
                 contact_source_url: str | None = None,
                 source_verification_level: str = "UNVERIFIED") -> Contact:
    if email and email_type == "NOT_FOUND":
        # An email value implies it was actually found somewhere -- classification bug if left NOT_FOUND.
        raise IngestError("email provided but email_type is NOT_FOUND")
    if email and not email_source_url:
        raise IngestError("email provided without an email_source_url -- cannot claim it's public/sourced")
    _check_source_verification_level(source_verification_level)
    if email_type == "VERIFIED_PUBLIC" and source_verification_level != "SOURCE_PAGE_VERIFIED":
        # VERIFIED_PUBLIC asserts the email was confirmed on a legitimate public source page.
        # A search-result snippet alone is not sufficient evidence for that claim.
        raise IngestError(
            "email_type VERIFIED_PUBLIC requires source_verification_level=SOURCE_PAGE_VERIFIED "
            "(the source page must have actually been fetched and the email confirmed on it)"
        )

    verification_status = "VERIFIED" if email_type == "VERIFIED_PUBLIC" and email_source_url else (
        "PARTIALLY_VERIFIED" if (first_name or last_name) and contact_source_url else "NEEDS_REVIEW"
    )

    contact = Contact(
        organization_id=organization.organization_id,
        first_name=first_name,
        last_name=last_name,
        title=title,
        email=email,
        email_type=email_type,
        phone=phone,
        contact_page_url=contact_page_url,
        email_source_url=email_source_url,
        contact_source_url=contact_source_url,
        verification_status=verification_status,
        source_verification_level=source_verification_level,
        last_verified=datetime.now(timezone.utc) if email or first_name else None,
    )
    session.add(contact)
    session.flush()
    return contact


def upgrade_source_verification_level(entity, new_level: str, *, email_confirmed: bool = False) -> bool:
    """Promote entity.source_verification_level to new_level if that's a real improvement; never downgrades.

    This is the one place that may raise a Contact's email_type to VERIFIED_PUBLIC
    outside of add_contact() -- e.g. a later verification pass that re-checks an
    already-created contact. It enforces the exact same non-negotiable rule as
    add_contact(): VERIFIED_PUBLIC requires the new level to be SOURCE_PAGE_VERIFIED
    *and* email_confirmed=True (the exact email string was actually found on the
    fetched page), never a search-snippet inference. Returns True if a change was made.
    """
    _check_source_verification_level(new_level)
    current = entity.source_verification_level or "NOT_FOUND"
    changed = SOURCE_VERIFICATION_LEVELS.index(new_level) < SOURCE_VERIFICATION_LEVELS.index(current)
    if changed:
        entity.source_verification_level = new_level
    if isinstance(entity, Contact) and entity.email and new_level == "SOURCE_PAGE_VERIFIED" and email_confirmed:
        if entity.email_type != "GENERAL_ORGANIZATION":
            entity.email_type = "VERIFIED_PUBLIC"
            changed = True
    return changed


def record_source(session: Session, *, url: str, entity_type: str, entity_id: int, purpose: str,
                   http_status: int | None = 200, robots_txt_allowed: bool | None = True) -> Source:
    domain = normalize_domain(url)
    src = Source(
        url=url,
        domain=domain,
        http_status=http_status,
        robots_txt_allowed=robots_txt_allowed,
        entity_type=entity_type,
        entity_id=entity_id,
        purpose=purpose,
    )
    session.add(src)
    return src


def log_activity(session: Session, *, activity_type: str, notes: str | None = None,
                  organization_id: int | None = None, event_id: int | None = None,
                  contact_id: int | None = None, created_by: str = "system") -> LeadActivity:
    activity = LeadActivity(
        organization_id=organization_id,
        event_id=event_id,
        contact_id=contact_id,
        activity_type=activity_type,
        notes=notes,
        created_by=created_by,
    )
    session.add(activity)
    return activity


def schedule_recheck(session: Session, *, organization: Organization, reason: str,
                      event_id: int | None = None, next_check: datetime | None = None,
                      notes: str | None = None) -> RecheckQueue:
    entry = RecheckQueue(
        organization_id=organization.organization_id,
        event_id=event_id,
        reason=reason,
        last_checked=datetime.now(timezone.utc),
        next_check=next_check,
        notes=notes,
    )
    session.add(entry)
    return entry
