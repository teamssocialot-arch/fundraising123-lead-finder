from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String

from app.models.base import Base


def _now():
    return datetime.now(timezone.utc)


class Source(Base):
    """Evidence trail: every fetched/cited URL used to populate any record."""

    __tablename__ = "sources"

    source_id = Column(Integer, primary_key=True, autoincrement=True)
    url = Column(String, nullable=False, index=True)
    domain = Column(String, nullable=True, index=True)
    fetched_at = Column(DateTime(timezone=True), default=_now)
    http_status = Column(Integer, nullable=True)
    content_hash = Column(String, nullable=True)
    robots_txt_allowed = Column(Boolean, nullable=True)

    # Lightweight polymorphic link -- what this source is evidence for.
    entity_type = Column(String, nullable=True)  # "organization" / "event" / "contact"
    entity_id = Column(Integer, nullable=True)
    purpose = Column(String, nullable=True)  # e.g. "event_details", "contact_name", "email"

    created_at = Column(DateTime(timezone=True), default=_now)
