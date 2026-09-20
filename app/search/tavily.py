"""Tavily search provider.

The API key is read ONLY from config.TAVILY_API_KEY, which itself reads
ONLY from the TAVILY_API_KEY environment variable -- set as a GitHub
Actions secret, never hardcoded, never committed to the repository. If the
key is unset, .available is False and every caller in this project is
required to skip Tavily gracefully rather than error.

Hard budget enforcement: this provider tracks its own credit spend and
refuses to place a call that would exceed max_credits, regardless of what
the caller asks for. A Tavily "basic" search costs 1 credit; "advanced"
costs 2. This project only ever uses "basic" to maximize query count under
a small budget.
"""
import httpx

from app.search.base import SearchProvider, SearchResult
from config import REQUEST_TIMEOUT_SECONDS, TAVILY_API_KEY, TAVILY_API_URL, TAVILY_MAX_CREDITS_PER_RUN

_CREDIT_COST = {"basic": 1, "advanced": 2}


class TavilyProvider(SearchProvider):
    name = "tavily"

    def __init__(self, api_key: str | None = None, max_credits: int = TAVILY_MAX_CREDITS_PER_RUN):
        self.api_key = api_key if api_key is not None else TAVILY_API_KEY
        self.max_credits = max_credits
        self._credits_used = 0
        self._queries_run = 0
        self._queries_skipped_over_budget = 0

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    @property
    def credits_used(self) -> int:
        return self._credits_used

    @property
    def queries_run(self) -> int:
        return self._queries_run

    @property
    def queries_skipped_over_budget(self) -> int:
        return self._queries_skipped_over_budget

    def budget_remaining(self) -> int:
        return max(0, self.max_credits - self._credits_used)

    def search(self, query: str, max_results: int = 3, search_depth: str = "basic") -> list[SearchResult]:
        if not self.available:
            return []
        cost = _CREDIT_COST[search_depth]
        if self._credits_used + cost > self.max_credits:
            self._queries_skipped_over_budget += 1
            return []  # hard stop -- never place a call that would exceed the budget

        try:
            resp = httpx.post(
                TAVILY_API_URL,
                json={
                    "api_key": self.api_key,
                    "query": query,
                    "search_depth": search_depth,
                    "max_results": max_results,
                    "include_answer": False,
                    "include_raw_content": False,
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            # Tavily only bills for calls it actually serves, so a transport
            # failure or bad response costs nothing and isn't counted here.
            return []

        self._credits_used += cost
        self._queries_run += 1

        return [
            SearchResult(url=r.get("url", ""), title=r.get("title", ""), content=r.get("content", ""))
            for r in data.get("results", [])
            if r.get("url")
        ]
