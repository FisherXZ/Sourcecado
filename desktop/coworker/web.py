"""Public web search via Tavily. Fake HTTP in tests."""

from __future__ import annotations

from typing import Any

SEARCH_URL = "https://api.tavily.com/search"
MISSING_KEY = "TAVILY_API_KEY is not configured."


def search_web(
    *,
    http: Any,
    api_key: str,
    query: str,
    max_results: int = 5,
) -> dict[str, Any]:
    data = http.post(
        SEARCH_URL,
        headers={
            "content-type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        json={"query": query, "max_results": max(1, min(int(max_results), 10))},
    )
    results = []
    for row in (data or {}).get("results") or []:
        if not isinstance(row, dict):
            continue
        results.append(
            {
                "title": row.get("title") or "",
                "url": row.get("url") or "",
                "snippet": row.get("content") or "",
            }
        )
    return {"results": results}
