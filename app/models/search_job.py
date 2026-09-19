from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.models.base import Base


def _now():
    return datetime.now(timezone.utc)


class SearchJob(Base):
    __tablename__ = "search_jobs"

    job_id = Column(Integer, primary_key=True, autoincrement=True)
    state = Column(String, nullable=False, index=True)
    query_text = Column(String, nullable=True)
    source_type = Column(String, nullable=True)  # SEARCH_API / DIRECT_CRAWL / EVENTBRITE / MANUAL_RESEARCH
    status = Column(String, default="QUEUED")  # QUEUED / RUNNING / DONE / FAILED / SOURCE_UNAVAILABLE

    results_found = Column(Integer, default=0)
    events_created = Column(Integer, default=0)
    orgs_created = Column(Integer, default=0)
    contacts_created = Column(Integer, default=0)
    duplicates_prevented = Column(Integer, default=0)
    errors = Column(Text, nullable=True)  # JSON-encoded list

    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
