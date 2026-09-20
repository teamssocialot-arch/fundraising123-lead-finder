"""One-time cleanup: merges duplicate Contact rows created before
add_contact() had dedup logic (bug fixed in app.ingest.add_contact -- the
loader is meant to be idempotent, but contacts had no find-or-create check,
so every workflow re-run doubled them).

For each organization, groups contacts by normalized email, else normalized
full name, else (title, no name) -- keeps the lowest contact_id as
canonical, reassigns Evidence rows from duplicates onto the canonical id
(skipping any that would become an exact duplicate evidence row), backfills
any fields the canonical is missing from its duplicates, keeps the
stronger of the two verification levels, then deletes the duplicates.

Safe to run more than once (a no-op once there are no duplicate groups left).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_session, init_db  # noqa: E402
from app.dedupe.normalize import normalize_org_name  # noqa: E402
from app.models import Contact, Evidence, Organization  # noqa: E402
from config import VERIFICATION_LEVELS  # noqa: E402

_LEVEL_RANK = {lvl: i for i, lvl in enumerate(VERIFICATION_LEVELS)}


def _group_key(c: Contact):
    if c.email:
        return ("email", c.email.lower())
    if c.first_name or c.last_name:
        return ("name", normalize_org_name(f"{c.first_name or ''} {c.last_name or ''}"))
    return ("title", c.title)


def main():
    init_db()
    session = get_session()

    merged = 0
    for org in session.query(Organization).all():
        contacts = (
            session.query(Contact)
            .filter(Contact.organization_id == org.organization_id)
            .order_by(Contact.contact_id.asc())
            .all()
        )
        groups: dict = {}
        for c in contacts:
            groups.setdefault(_group_key(c), []).append(c)

        for group in groups.values():
            if len(group) <= 1:
                continue
            canonical, *dupes = group
            for dupe in dupes:
                if not canonical.email and dupe.email:
                    canonical.email = dupe.email
                    canonical.email_type = dupe.email_type
                    canonical.email_source_url = dupe.email_source_url
                    canonical.email_verification_level = dupe.email_verification_level
                if not canonical.title and dupe.title:
                    canonical.title = dupe.title
                if not canonical.phone and dupe.phone:
                    canonical.phone = dupe.phone
                if not canonical.contact_page_url and dupe.contact_page_url:
                    canonical.contact_page_url = dupe.contact_page_url
                if not canonical.contact_source_url and dupe.contact_source_url:
                    canonical.contact_source_url = dupe.contact_source_url
                if _LEVEL_RANK.get(dupe.source_verification_level, 99) < _LEVEL_RANK.get(canonical.source_verification_level, 99):
                    canonical.source_verification_level = dupe.source_verification_level
                if _LEVEL_RANK.get(dupe.email_verification_level, 99) < _LEVEL_RANK.get(canonical.email_verification_level, 99):
                    canonical.email_verification_level = dupe.email_verification_level
                if dupe.email_type == "VERIFIED_PUBLIC":
                    canonical.email_type = "VERIFIED_PUBLIC"

                canonical_keys = {
                    (e.claim, e.source_url_normalized, e.source_type)
                    for e in session.query(Evidence)
                    .filter(Evidence.entity_type == "contact", Evidence.entity_id == canonical.contact_id)
                    .all()
                }
                for ev in (
                    session.query(Evidence)
                    .filter(Evidence.entity_type == "contact", Evidence.entity_id == dupe.contact_id)
                    .all()
                ):
                    key3 = (ev.claim, ev.source_url_normalized, ev.source_type)
                    if key3 in canonical_keys:
                        session.delete(ev)
                        continue
                    if ev.claim.startswith("contact_identity:"):
                        ev.claim = f"contact_identity:{canonical.contact_id}"
                    ev.entity_id = canonical.contact_id
                    canonical_keys.add(key3)

                session.delete(dupe)
                merged += 1

    session.commit()
    remaining = session.query(Contact).count()
    session.close()
    print(f"Merged {merged} duplicate contact(s). {remaining} contacts remain.")


if __name__ == "__main__":
    main()
