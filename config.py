"""Central configuration, env-driven so DB/backends can be swapped without code changes."""
import os

# Swap to a postgresql:// URL later without any model/query changes.
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/fundraising123.db")

# Search provider config -- Phase B. The key is read ONLY from the environment
# (set as a GitHub Actions secret; never hardcoded or committed). Leaving it
# unset disables Tavily entirely -- scripts/verify_with_tavily.py no-ops with
# a clear message rather than failing, exactly like the Google Sheets export's
# dry-run behavior when credentials aren't configured yet.
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")
TAVILY_API_URL = "https://api.tavily.com/search"
# Hard ceiling enforced by app.search.tavily.TavilyProvider itself -- it refuses
# to place a call that would exceed this, regardless of what the caller asks for.
TAVILY_MAX_CREDITS_PER_RUN = int(os.environ.get("TAVILY_MAX_CREDITS_PER_RUN", "100"))
TAVILY_QUERIES_PER_LEAD = int(os.environ.get("TAVILY_QUERIES_PER_LEAD", "6"))
TAVILY_MAX_RESULTS_PER_QUERY = int(os.environ.get("TAVILY_MAX_RESULTS_PER_QUERY", "3"))
# Of the results returned per query, how many get a full live/archive fetch
# attempt (keeps downstream fetch volume, and therefore run time, bounded --
# every result still gets a cheap SEARCH_DISCOVERY evidence check regardless).
TAVILY_RESULTS_TO_VERIFY_PER_QUERY = int(os.environ.get("TAVILY_RESULTS_TO_VERIFY_PER_QUERY", "1"))

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

EVIDENCE_SOURCE_TYPES = (
    "LIVE_SOURCE",      # fetched directly from the still-live target page
    "ARCHIVED_SOURCE",  # fetched from a legitimate public archive (e.g. Wayback Machine) of the target page
    "SEARCH_RESULT",    # a search-engine indexed snippet, underlying page not (successfully) fetched
    "PUBLIC_RECORD",    # a public-record registry (e.g. ProPublica Nonprofit Explorer / IRS data) -- org identity only
    "SEARCH_DISCOVERY", # a search PROVIDER (e.g. Tavily) surfaced this underlying URL/snippet. The provider
                         # is not itself the source -- the underlying URL is. Two SEARCH_DISCOVERY rows from
                         # the same provider pointing at the same normalized URL are NOT independent sources;
                         # independence is judged the same way as everything else, by distinct normalized URLs.
)

# Ordered best-to-worst. A claim's level is COMPUTED from its Evidence rows
# (see app.ingest.compute_verification_level_from_evidence), never asserted
# directly, so this list is also the ranking used to decide upgrades.
VERIFICATION_LEVELS = (
    "SOURCE_PAGE_VERIFIED",     # >=1 LIVE_SOURCE evidence row confirms the claim
    "ARCHIVED_SOURCE_VERIFIED", # no successful live fetch, but an ARCHIVED_SOURCE (Wayback) row confirms it --
                                 # kept distinct from SOURCE_PAGE_VERIFIED per explicit requirement: an archived
                                 # snapshot is not indistinguishable from a currently-accessible live source.
    "MULTI_SOURCE_CONFIRMED",   # no direct/archived fetch, but >=2 evidence rows with DISTINCT normalized
                                 # source URLs independently confirm the same claim
    "SEARCH_RESULT_SUPPORTED",  # exactly one evidence row, snippet-only
    "UNVERIFIED",               # evidence exists but is insufficient or contradictory
    "NOT_FOUND",                # no evidence at all
)

# Backward-compatible alias; existing columns/validators were written against this name.
SOURCE_VERIFICATION_LEVELS = VERIFICATION_LEVELS

# Contact relevance ranking (lower = more relevant), independent of how well-verified
# a contact is -- used so a highly relevant event contact is never displaced by an
# easier-to-verify executive. Matched against contact.title, case-insensitive substring.
CONTACT_ROLE_PRIORITY = (
    ("event_contact", ("event contact", "fundraiser contact", "gala contact", "event chair", "gala chair")),
    ("development_director", ("development director",)),
    ("director_of_development", ("director of development",)),
    ("events_director", ("events director", "special events manager", "special events director")),
    ("fundraising_director", ("fundraising director", "fundraising coordinator")),
    ("executive_director", ("executive director", "ceo", "chief executive")),
    ("other_named_contact", ()),  # fallback for any other named person -- matches nothing, always last resort
    ("general_organization", ()),  # fallback for contacts with no name at all
)

# Free, no-API-key public sources used for corroboration when a live fetch is
# blocked. Neither bypasses any access control: Wayback serves archives the
# Internet Archive's own crawler already legitimately collected; ProPublica
# serves public IRS Form 990 filing data. Used ONLY as described in
# scripts/verify_sources.py -- ProPublica never counts as event evidence.
WAYBACK_AVAILABILITY_URL = "https://archive.org/wayback/available"
PROPUBLICA_NONPROFIT_SEARCH_URL = "https://projects.propublica.org/nonprofits/api/v2/search.json"

TIMING_BUCKETS = [
    (0, 30, "0-30 days"),
    (31, 60, "31-60 days"),
    (61, 90, "61-90 days"),
    (91, 180, "91-180 days"),
    (181, 365, "181-365 days"),
    (366, None, "365+ days"),
]
