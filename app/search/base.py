"""Provider-agnostic search interface.

Any search provider (Tavily today, Brave/others later) implements this same
shape so the research pipeline (scripts/verify_with_tavily.py and whatever
follows it) never has to change when a provider is added or swapped -- only
a new app/search/<provider>.py module and a one-line pick of which provider
to instantiate.
"""
from dataclasses import dataclass


@dataclass
class SearchResult:
    url: str
    title: str = ""
    content: str = ""  # snippet/excerpt text the provider returned


class SearchProvider:
    """Base interface. A provider is always attributable: entity_type is not
    "search_result" alone -- see EVIDENCE_SOURCE_TYPES.SEARCH_DISCOVERY. The
    provider is how a URL was found, never itself the evidence source."""

    name: str = "base"

    def search(self, query: str, max_results: int = 3) -> list[SearchResult]:
        raise NotImplementedError

    @property
    def available(self) -> bool:
        """False when no API key/credentials are configured -- callers must
        check this and skip gracefully rather than erroring."""
        raise NotImplementedError

    @property
    def credits_used(self) -> int:
        raise NotImplementedError

    @property
    def queries_run(self) -> int:
        raise NotImplementedError

    def budget_remaining(self) -> int:
        raise NotImplementedError
