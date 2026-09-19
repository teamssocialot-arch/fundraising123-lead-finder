from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from app.models.base import Base


def _now():
    return datetime.now(timezone.utc)


class Evidence(Base):
    """One independent fact-check for one claim about one entity.

    A claim's overall verification level (SOURCE_PAGE_VERIFIED /
    ARCHIVED_SOURCE_VERIFIED / MULTI_SOURCE_CONFIRMED / SEARCH_RESULT_SUPPORTED
    / UNVERIFIED / NOT_FOUND) is never stored here directly -- it's computed
    from the full set of Evidence rows for that (entity_type, entity_id, claim)
    by app.ingest.compute_verification_level_from_evidence(). This table is
    the append-only audit trail a human can inspect; nothing here is ever
    silently overwritten or deleted.
    """

    __tablename__ = "evidence"

    evidence_id = Column(Integer, primary_key=True, autoincrement=True)

    entity_type = Column(String, nullable=False, index=True)  # "event" / "organization" / "contact"
    entity_id = Column(Integer, nullable=False, index=True)
    claim = Column(String, nullable=False, index=True)  # e.g. "event_name_date", "org_identity", "contact_email:jane@x.org"

    source_url = Column(String, nullable=False)
    source_url_normalized = Column(String, nullable=False, index=True)  # dedup key -- see app.dedupe.normalize.normalize_url
    # LIVE_SOURCE / ARCHIVED_SOURCE / SEARCH_RESULT / PUBLIC_RECORD
    source_type = Column(String, nullable=False)

    fetched_at = Column(DateTime(timezone=True), default=_now)
    confirmed = Column(Boolean, nullable=False)  # was the exact claimed fact actually present in this source?
    excerpt = Column(Text, nullable=True)  # the literal matched text, for manual review -- never fabricated

    created_at = Column(DateTime(timezone=True), default=_now)
