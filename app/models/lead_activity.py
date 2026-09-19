from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.models.base import Base


def _now():
    return datetime.now(timezone.utc)


class LeadActivity(Base):
    __tablename__ = "lead_activity"

    activity_id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.organization_id"), nullable=True)
    event_id = Column(Integer, ForeignKey("events.event_id"), nullable=True)
    contact_id = Column(Integer, ForeignKey("contacts.contact_id"), nullable=True)

    activity_type = Column(String, nullable=False)  # STATUS_CHANGE / NOTE / RECHECK_SCHEDULED / INGESTED / ...
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    created_by = Column(String, default="system")
