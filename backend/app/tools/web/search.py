"""Web search over a pluggable provider.

One web_search tool is exposed to the model regardless of which engine backs
it; the provider is deployment configuration (SEARCH_PROVIDER env var, or
auto-detected from whichever API key is present). Add a provider by writing an
async function and adding a _Provider entry to _PROVIDERS.
"""

import os
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx
from langchain_core.tools import tool

from ..base import ToolSpec, register

MAX_RESULTS = int(os.getenv("SEARCH_MAX_RESULTS", "5"))
TIMEOUT = 15.0


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


async def _search_serpapi(query: str, limit: int) -> list[SearchResult]:
    """Google results via SerpAPI."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.get(
            "https://serpapi.com/search",
            params={
                "engine": "google",
                "q": query,
                "num": limit,
                "api_key": os.environ["SERPAPI_API_KEY"],
            },
        )
        resp.raise_for_status()
        data = resp.json()
    return [
        SearchResult(
            title=item.get("title", ""),
            url=item.get("link", ""),
            snippet=item.get("snippet", ""),
        )
        for item in data.get("organic_results", [])[:limit]
    ]


async def _search_brave(query: str, limit: int) -> list[SearchResult]:
    """Brave Search API."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": limit},
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": os.environ["BRAVE_API_KEY"],
            },
        )
        resp.raise_for_status()
        data = resp.json()
    return [
        SearchResult(
            title=item.get("title", ""),
            url=item.get("url", ""),
            snippet=item.get("description", ""),
        )
        for item in data.get("web", {}).get("results", [])[:limit]
    ]


@dataclass(frozen=True)
class _Provider:
    name: str
    required_env: str
    search: Callable[[str, int], Awaitable[list[SearchResult]]]


_PROVIDERS = [
    _Provider("serpapi", "SERPAPI_API_KEY", _search_serpapi),
    _Provider("brave", "BRAVE_API_KEY", _search_brave),
]


def _active_provider() -> _Provider | None:
    wanted = os.getenv("SEARCH_PROVIDER", "").strip().lower()
    for provider in _PROVIDERS:
        if wanted and provider.name != wanted:
            continue
        if os.getenv(provider.required_env):
            return provider
    return None


@tool
async def web_search(query: str) -> str:
    """Search the web and return the top results as title, URL, and snippet. Use this for questions about current events or facts you are not sure about."""
    provider = _active_provider()
    if provider is None:
        return "Error: no search provider is configured."
    try:
        results = await provider.search(query, MAX_RESULTS)
    except httpx.HTTPError as exc:
        return f"Error: search request failed: {exc}."
    if not results:
        return "No results found."
    return "\n\n".join(
        f"{i}. {r.title}\n{r.url}\n{r.snippet}" for i, r in enumerate(results, 1)
    )


register(
    ToolSpec(
        tool=web_search,
        prompt_hint=(
            "For current events or facts you are unsure about, call web_search, "
            "then fetch_page on a promising result if more detail is needed."
        ),
        available=lambda: _active_provider() is not None,
    )
)
