"""Public web search via Tavily. Fake HTTP in tests."""

from __future__ import annotations

from typing import Any

from coworker.evidence_envelope import (
    EvidenceParts,
    combine,
    external,
    opaque,
)

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


def web_evidence(tool_name: str, payload: dict[str, Any]) -> EvidenceParts:
    """Adapt a Tavily search into one envelope per result.

    A page title and snippet are written by the page. The URL stays in the
    reference so the operator can see where a claim came from, and
    `Envelope.reference` sanitizes it before any log or receipt renders it.
    """
    if not isinstance(payload, dict):
        return opaque("web", tool_name, payload)
    rows = payload.get("results")
    rows = rows if isinstance(rows, list) else []
    parts = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "")
        parts.append(
            external(
                "web",
                identity=("result", url, row.get("title")),
                title=str(row.get("title") or "Web result"),
                body="\n".join(
                    part
                    for part in (
                        f"Title: {row.get('title') or ''}",
                        f"URL: {url}",
                        str(row.get("snippet") or ""),
                    )
                    if part.strip()
                ),
                url=url,
            )
        )
    combined = combine(parts)
    return EvidenceParts(
        metadata={"count": len(rows)}, envelopes=combined.envelopes
    )
