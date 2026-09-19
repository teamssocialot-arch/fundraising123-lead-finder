"""Normalization helpers used for deduplication matching.

These intentionally do light, safe normalization only (case, punctuation,
whitespace, common suffixes) -- never fuzzy/semantic guessing that could
silently merge two distinct organizations.
"""
import re
from urllib.parse import urlparse

_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")
_ORG_SUFFIXES = (" inc", " incorporated", " llc", " ltd", " corp", " corporation")


def normalize_org_name(name: str) -> str:
    if not name:
        return ""
    n = name.strip().lower()
    n = n.replace("&", "and")
    n = _PUNCT_RE.sub(" ", n)
    n = _WS_RE.sub(" ", n).strip()
    for suffix in _ORG_SUFFIXES:
        if n.endswith(suffix):
            n = n[: -len(suffix)].strip()
    return n


def normalize_event_name(name: str) -> str:
    if not name:
        return ""
    n = name.strip().lower()
    n = _PUNCT_RE.sub(" ", n)
    n = _WS_RE.sub(" ", n).strip()
    return n


def normalize_domain(website: str) -> str | None:
    if not website:
        return None
    website = website.strip()
    if not website:
        return None
    if not website.startswith("http"):
        website = "https://" + website
    try:
        netloc = urlparse(website).netloc.lower()
    except ValueError:
        return None
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc or None


_WAYBACK_WRAPPER_RE = re.compile(r"^https?://web\.archive\.org/web/\d+[a-z_]*/(https?://.+)$", re.IGNORECASE)


def normalize_url(url: str) -> str:
    """Canonical form used to decide whether two evidence rows point at the
    SAME underlying page (required before they can count as independent
    sources for MULTI_SOURCE_CONFIRMED). Unwraps a Wayback Machine snapshot to
    its original URL first -- an archived copy and a live copy of the same
    page are the same underlying source, not two independent ones -- then
    drops scheme, "www.", query string, fragment, and trailing slash.
    """
    if not url:
        return ""
    match = _WAYBACK_WRAPPER_RE.match(url.strip())
    target = match.group(1) if match else url.strip()
    if not target.startswith("http"):
        target = "https://" + target
    try:
        parsed = urlparse(target)
    except ValueError:
        return target.lower()
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parsed.path.rstrip("/")
    return f"{netloc}{path}".lower()


def normalize_phone(phone: str) -> str | None:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits or None
