"""Central configuration, env-driven so DB/backends can be swapped without code changes."""
import os

# Swap to a postgresql:// URL later without any model/query changes.
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/fundraising123.db")

# Search API — NOT enabled by default. Phase 1 validation uses manual/assisted
# web research (see scripts/load_leads.py) specifically to avoid incurring any
# paid API charges before the user approves enabling one.
GOOGLE_CSE_API_KEY = os.environ.get("GOOGLE_CSE_API_KEY")  # unset = search API disabled
GOOGLE_CSE_CX = os.environ.get("GOOGLE_CSE_CX")

# Crawler politeness defaults (used by app/crawler/fetcher.py in later phases).
MIN_SECONDS_BETWEEN_REQUESTS_PER_DOMAIN = float(os.environ.get("CRAWL_DELAY_SECONDS", "3.0"))
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "15.0"))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "3"))
USER_AGENT = os.environ.get(
    "CRAWLER_USER_AGENT",
    "Fundraising123LeadFinderBot/0.1 (+public event research; contact: teams.socialot@gmail.com)",
)

ALLOWED_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN",
    "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH",
    "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT",
    "VT", "VA", "WA", "WV", "WI", "WY",
}

FUNDRAISER_URL_TYPES = (
    "OFFICIAL_EVENT_PAGE",
    "REGISTRATION_PAGE",
    "EVENTBRITE",
    "ORGANIZATION_ANNOUNCEMENT",
    "THIRD_PARTY_EVENT_LISTING",
)

SOURCE_VERIFICATION_LEVELS = (
    "SOURCE_PAGE_VERIFIED",    # the source page was actually fetched and the fact confirmed on it
    "SEARCH_RESULT_SUPPORTED", # the fact appears in a search result/snippet; underlying page not fetched
    "UNVERIFIED",              # could not be independently confirmed by either method
    "NOT_FOUND",               # no information was found at all
)

TIMING_BUCKETS = [
    (0, 30, "0-30 days"),
    (31, 60, "31-60 days"),
    (61, 90, "61-90 days"),
    (91, 180, "91-180 days"),
    (181, 365, "181-365 days"),
    (366, None, "365+ days"),
]
