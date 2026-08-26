"""Apollo search + enrich — injectable HTTP, copied field map from Sourcecado.

Search never returns emails or a full last name. Enrich is the path that can
return a verified email. Missing key fails clearly.
"""

from __future__ import annotations

from typing import Any

SEARCH_URL = "https://api.apollo.io/api/v1/mixed_people/api_search"
MATCH_URL = "https://api.apollo.io/api/v1/people/match"

MISSING_KEY = "APOLLO_API_KEY is not configured."


class HttpError(RuntimeError):
    def __init__(self, status: int, url: str = "", body: object | None = None) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status
        self.url = url
        self.body = body


def _decode_response(res: Any) -> Any:
    ctype = ""
    headers = getattr(res, "headers", None) or {}
    try:
        ctype = str(headers.get("content-type") or headers.get("Content-Type") or "")
    except Exception:
        ctype = ""
    text = getattr(res, "text", "") or ""
    if "json" in ctype.lower() or text[:1] in "{[":
        return res.json()
    textual = ctype.lower().startswith("text/") or any(
        marker in ctype.lower() for marker in ("csv", "xml", "yaml")
    )
    if textual:
        return text
    content = getattr(res, "content", None)
    return content if isinstance(content, bytes) else text


class LiveHttp:
    def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        import httpx

        res = httpx.post(
            url, headers=headers, json=json, data=data, params=params, timeout=15.0
        )
        if res.status_code >= 400:
            body: object | None
            try:
                body = res.json()
            except Exception:
                body = res.text
            raise HttpError(res.status_code, url, body)
        return _decode_response(res)

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        import httpx

        res = httpx.get(url, headers=headers, params=params, timeout=15.0)
        if res.status_code >= 400:
            body: object | None
            try:
                body = res.json()
            except Exception:
                body = res.text
            raise HttpError(res.status_code, url, body)
        return _decode_response(res)

    def patch(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        import httpx

        res = httpx.patch(
            url, headers=headers, json=json, data=data, params=params, timeout=15.0
        )
        if res.status_code >= 400:
            body: object | None
            try:
                body = res.json()
            except Exception:
                body = res.text
            raise HttpError(res.status_code, url, body)
        return _decode_response(res)


class FakeHttp:
    def __init__(self, routes: dict[str, Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.routes = dict(routes or {})

    def _lookup(self, url: str) -> Any:
        if url in self.routes:
            return self.routes[url]
        path = url.split("?")[0]
        if path in self.routes:
            return self.routes[path]
        raise RuntimeError(f"unexpected url {url}")

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append(
            {
                "method": "POST",
                "url": url,
                "headers": dict(headers or {}),
                "json": dict(json or {}),
                "data": dict(data or {}),
                "params": dict(params or {}),
            }
        )
        payload = self._lookup(url)
        if isinstance(payload, Exception):
            raise payload
        return payload

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append(
            {
                "method": "GET",
                "url": url,
                "headers": dict(headers or {}),
                "json": {},
                "data": {},
                "params": dict(params or {}),
            }
        )
        payload = self._lookup(url)
        if isinstance(payload, Exception):
            raise payload
        return payload

    def patch(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append(
            {
                "method": "PATCH",
                "url": url,
                "headers": dict(headers or {}),
                "json": dict(json or {}),
                "data": dict(data or {}),
                "params": dict(params or {}),
            }
        )
        payload = self._lookup(url)
        if isinstance(payload, Exception):
            raise payload
        return payload


def search_people(
    *,
    http: Any,
    api_key: str,
    organization_name: str | None = None,
    person_titles: list[str] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    if not organization_name and not person_titles:
        raise ValueError("Provide organizationName or personTitles.")
    data = http.post(
        SEARCH_URL,
        headers={"content-type": "application/json", "x-api-key": api_key},
        json={
            "q_organization_name": organization_name,
            "person_titles": person_titles,
            "per_page": limit,
        },
    )
    people = []
    for row in (data or {}).get("people") or []:
        people.append(
            {
                "apolloId": row.get("id") or None,
                "firstName": row.get("first_name") or None,
                "lastNameObfuscated": row.get("last_name_obfuscated") or None,
                "title": row.get("title") or None,
                "organizationName": (row.get("organization") or {}).get("name") or None,
                "hasEmail": bool(row.get("has_email")),
                "directPhoneStatus": row.get("has_direct_phone") or None,
            }
        )
    return {"people": people}


def enrich_contact(
    *,
    http: Any,
    api_key: str,
    email: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    organization_name: str | None = None,
) -> dict[str, Any]:
    if not email and not (first_name and last_name):
        raise ValueError("Provide email, or firstName and lastName.")
    data = http.post(
        MATCH_URL,
        headers={"content-type": "application/json", "x-api-key": api_key},
        json={
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "organization_name": organization_name,
        },
    )
    person = (data or {}).get("person") or {}
    phones = person.get("phone_numbers") or []
    phone = phones[0].get("raw_number") if phones else None
    return {
        "name": person.get("name") or None,
        "title": person.get("title") or None,
        "organizationName": (person.get("organization") or {}).get("name") or None,
        "linkedinUrl": person.get("linkedin_url") or None,
        "email": person.get("email") or None,
        "phone": phone or None,
    }
