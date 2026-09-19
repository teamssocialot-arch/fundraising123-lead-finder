"""Google Sheets export architecture.

Phase A: no Google credentials are required or expected yet. When
GOOGLE_SHEETS_CREDENTIALS_JSON and GOOGLE_SHEETS_SPREADSHEET_ID are unset
(the default), this builds the exact rows the eventual Sheet will contain and
writes them to a local CSV preview instead of calling any Google API -- so
the architecture can be reviewed and tested end to end before the user
configures real credentials. gspread/google-auth are only imported lazily,
inside push_to_google_sheets(), so nothing here requires them to be
installed or configured to run the dry run.

Once the user is ready:
  1. Create a Google Cloud service account, enable the Sheets API, and share
     the target spreadsheet with the service account's email.
  2. Set GOOGLE_SHEETS_CREDENTIALS_JSON (the service account's JSON key, as a
     single-line string) and GOOGLE_SHEETS_SPREADSHEET_ID (from the sheet's URL).
  3. Re-run this script -- it will then push instead of previewing.
"""
import csv
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_session, init_db  # noqa: E402
from app.ingest import lead_evidence_summary, select_primary_contact  # noqa: E402
from app.models import Contact, Event, Organization  # noqa: E402

SHEET_COLUMNS = [
    "Organization", "Fundraiser Name", "Event Date", "Days Until Event", "City", "State",
    "Event Type", "Silent Auction", "Live Auction", "Contact Name", "Contact Title", "Email",
    "Email Verification", "Phone", "Verification Level", "Fundraiser URL",
    "Evidence/Source URLs", "Lead Status", "Notes", "Evidence Count", "Evidence Summary",
    "Last Verified",
]


def build_rows(session) -> list[dict]:
    rows = []
    events = (
        session.query(Event)
        .filter(Event.state == "FL")
        .order_by(Event.event_date.asc().nullslast())
        .all()
    )
    for event in events:
        org = session.get(Organization, event.organization_id)
        contacts = session.query(Contact).filter(Contact.organization_id == org.organization_id).all()
        contact = select_primary_contact(contacts)
        evidence = lead_evidence_summary(session, event=event, organization=org, contacts=contacts)

        fundraiser_url = event.fundraiser_url or "NOT_FOUND"
        # Fundraiser Name must be clickable, opening the direct fundraiser URL --
        # a Sheets HYPERLINK formula, evaluated only when actually pushed with
        # value_input_option="USER_ENTERED" (see push_to_google_sheets).
        fundraiser_name_cell = (
            f'=HYPERLINK("{fundraiser_url}", "{event.event_name}")'
            if fundraiser_url != "NOT_FOUND" else event.event_name
        )

        rows.append({
            "Organization": org.organization_name,
            "Fundraiser Name": fundraiser_name_cell,
            "Event Date": str(event.event_date) if event.event_date else "UNKNOWN",
            "Days Until Event": event.days_until_event if event.days_until_event is not None else "",
            "City": event.city or "",
            "State": event.state or "",
            "Event Type": event.event_type or "",
            "Silent Auction": event.silent_auction,
            "Live Auction": event.live_auction,
            "Contact Name": (f"{contact.first_name or ''} {contact.last_name or ''}".strip() if contact else ""),
            "Contact Title": contact.title if contact else "",
            "Email": (contact.email if contact and contact.email else "NOT FOUND"),
            "Email Verification": contact.email_verification_level if contact else "NOT_FOUND",
            "Phone": (contact.phone if contact and contact.phone else ""),
            "Verification Level": event.source_verification_level,
            "Fundraiser URL": fundraiser_url,
            "Evidence/Source URLs": " | ".join(evidence["urls"]),
            "Lead Status": event.lead_status,
            "Notes": event.notes or "",
            "Evidence Count": evidence["count"],
            "Evidence Summary": evidence["summary"],
            "Last Verified": (str(event.fundraiser_url_last_checked) if event.fundraiser_url_last_checked else "never"),
        })
    return rows


def dry_run_preview(rows: list[dict], out_path: Path) -> Path:
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SHEET_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return out_path


def push_to_google_sheets(rows: list[dict], spreadsheet_id: str, credentials_json: str) -> str:
    """Only runs once real credentials are configured -- not exercised in Phase A."""
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(json.loads(credentials_json), scopes=scopes)
    gc = gspread.authorize(creds)
    worksheet = gc.open_by_key(spreadsheet_id).sheet1
    worksheet.clear()
    values = [SHEET_COLUMNS] + [[row[c] for c in SHEET_COLUMNS] for row in rows]
    worksheet.update(values, value_input_option="USER_ENTERED")  # USER_ENTERED evaluates the HYPERLINK formula
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"


def main():
    init_db()
    session = get_session()
    rows = build_rows(session)
    session.close()

    credentials_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS_JSON")
    spreadsheet_id = os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID")

    if credentials_json and spreadsheet_id:
        url = push_to_google_sheets(rows, spreadsheet_id, credentials_json)
        print(f"Pushed {len(rows)} rows to {url}")
    else:
        out_path = Path(__file__).resolve().parent.parent / "data" / "sheets_export_preview.csv"
        dry_run_preview(rows, out_path)
        print(
            "GOOGLE_SHEETS_CREDENTIALS_JSON / GOOGLE_SHEETS_SPREADSHEET_ID are not set -- "
            f"no Google API was called. Wrote a local dry-run preview of exactly what would "
            f"be pushed to {out_path} ({len(rows)} rows)."
        )


if __name__ == "__main__":
    main()
