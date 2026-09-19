from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.models.base import Base


def _now():
    return datetime.now(timezone.utc)


class Contact(Base):
    __tablename__ = "contacts"

    contact_id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.organization_id"), nullable=False, index=True)

    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    title = Column(String, nullable=True)

    email = Column(String, nullable=True)  # NULL/"NOT FOUND" when absent -- never guessed
    email_type = Column(String, default="NOT_FOUND")  # VERIFIED_PUBLIC / GENERAL_ORGANIZATION / UNVERIFIED / NOT_FOUND
    phone = Column(String, nullable=True)

    contact_page_url = Column(String, nullable=True)
    email_source_url = Column(String, nullable=True)
    contact_source_url = Column(String, nullable=True)

    verification_status = Column(String, default="NEEDS_REVIEW")  # record completeness only (name/title/source present)
    # SOURCE_PAGE_VERIFIED / SEARCH_RESULT_SUPPORTED / UNVERIFIED / NOT_FOUND -- how the contact/email was actually confirmed
    source_verification_level = Column(String, default="UNVERIFIED")
    last_verified = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    organization = relationship("Organization", back_populates="contacts")
