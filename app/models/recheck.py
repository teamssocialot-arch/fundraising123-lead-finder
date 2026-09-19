from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.models.base import Base


def _now():
    return datetime.now(timezone.utc)


class RecheckQueue(Base):
    __tablename__ = "recheck_queue"

    recheck_id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.organization_id"), nullable=False)
    event_id = Column(Integer, ForeignKey("events.event_id"), nullable=True)

    reason = Column(String, nullable=False)
    # NEXT_EVENT_NOT_ANNOUNCED / CONTACT_MISSING / EMAIL_MISSING / EVENT_DATE_UNKNOWN / HISTORICAL_RECURRING_EVENT

    last_checked = Column(DateTime(timezone=True), nullable=True)
    next_check = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
