"""Brave Search API client."""

from __future__ import annotations

from typing import Any

import httpx

BRAVE_WEB_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
BRAVE_MAX_COUNT_PER_PAGE = 20
BRAVE_MAX_OFFSET = 9
MAX_WEB_SEARCH_RESULTS = (BRAVE_MAX_OFFSET + 1) * BRAVE_MAX_COUNT_PER_PAGE
DEFAULT_WEB_SEARCH_COUNT = 10


async def _brave_web_search_page(
    *,
    api_key: str,
    query: str,
    count: int,
    offset: int,
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    response = await client.get(
        BRAVE_WEB_SEARCH_URL,
        headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        params={"q": query, "count": count, "offset": offset},
    )
    response.raise_for_status()
    return response.json()


async def brave_web_search(
    *,
    api_key: str,
    query: str,
    count: int = DEFAULT_WEB_SEARCH_COUNT,
    timeout: float = 15.0,
) -> dict[str, Any]:
    if not api_key:
        raise ValueError("Brave Search API key is not configured")

    requested = max(1, min(int(count), MAX_WEB_SEARCH_RESULTS))
    all_results: list[dict[str, Any]] = []
    last_payload: dict[str, Any] = {}
    offset = 0

    async with httpx.AsyncClient(timeout=timeout) as client:
        while len(all_results) < requested and offset <= BRAVE_MAX_OFFSET:
            page_size = min(BRAVE_MAX_COUNT_PER_PAGE, requested - len(all_results))
            payload = await _brave_web_search_page(
                api_key=api_key,
                query=query,
                count=page_size,
                offset=offset,
                client=client,
            )
            last_payload = payload
            page_results = (payload.get("web") or {}).get("results") or []
            if not page_results:
                break
            all_results.extend(page_results)
            if len(all_results) >= requested:
                break
            if not (payload.get("query") or {}).get("more_results_available"):
                break
            offset += 1

    merged = dict(last_payload)
    merged["web"] = {"results": all_results[:requested]}
    query_meta = dict(merged.get("query") or {})
    query_meta["requested_count"] = requested
    query_meta["returned_count"] = len(all_results[:requested])
    if len(all_results) < requested:
        query_meta["more_results_available"] = False
    merged["query"] = query_meta
    return merged


def format_brave_results(payload: dict[str, Any]) -> str:
    web = payload.get("web") or {}
    results = web.get("results") or []
    if not results:
        return "No results found."

    lines: list[str] = []
    for index, item in enumerate(results, start=1):
        title = item.get("title") or "(no title)"
        url = item.get("url") or ""
        desc = item.get("description") or ""
        lines.append(f"{index}. {title}\n   {url}\n   {desc}")

    query_meta = payload.get("query") or {}
    requested = query_meta.get("requested_count")
    returned = query_meta.get("returned_count", len(results))
    if requested and returned < requested:
        lines.append(
            f"\n(Requested {requested} results; Brave returned {returned} before results were exhausted.)"
        )
    return "\n\n".join(lines)
