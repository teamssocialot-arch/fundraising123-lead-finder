from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.models.base import Base


def _now():
    return datetime.now(timezone.utc)


class Event(Base):
    __tablename__ = "events"

    event_id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.organization_id"), nullable=False, index=True)

    event_name = Column(String, nullable=False)
    normalized_event_name = Column(String, nullable=False, index=True)
    event_date = Column(Date, nullable=True)  # NULL = UNKNOWN, never fabricated
    event_time = Column(String, nullable=True)
    venue = Column(String, nullable=True)
    address = Column(String, nullable=True)
    city = Column(String, nullable=True)
    state = Column(String, nullable=True, index=True)
    zip = Column(String, nullable=True)

    event_type = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    event_url = Column(String, nullable=True)
    ticket_url = Column(String, nullable=True)

    # YES / NO / UNKNOWN
    silent_auction = Column(String, default="UNKNOWN")
    live_auction = Column(String, default="UNKNOWN")
    raffle = Column(String, default="UNKNOWN")
    travel_packages = Column(String, default="UNKNOWN")
    sponsors = Column(String, default="UNKNOWN")
    # YES / NO
    gala = Column(String, default="NO")
    golf_tournament = Column(String, default="NO")
    casino_night = Column(String, default="NO")

    days_until_event = Column(Integer, nullable=True)
    timing_bucket = Column(String, nullable=True)

    recurring_event = Column(Boolean, default=False)
    typical_month = Column(String, nullable=True)
    historical_dates = Column(Text, nullable=True)  # JSON-encoded list
    most_recent_event = Column(String, nullable=True)
    next_event = Column(String, nullable=True)  # date string or "NOT ANNOUNCED"

    discovery_source = Column(String, nullable=True)
    verification_status = Column(String, default="NEEDS_REVIEW")  # VERIFIED / PARTIALLY_VERIFIED / NEEDS_REVIEW -- record completeness only
    # SOURCE_PAGE_VERIFIED / SEARCH_RESULT_SUPPORTED / UNVERIFIED / NOT_FOUND -- how the event facts were actually confirmed
    source_verification_level = Column(String, default="UNVERIFIED")
    lead_status = Column(String, default="NEW")

    notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    organization = relationship("Organization", back_populates="events")
