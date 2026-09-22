"""Apollo search + enrich — injectable HTTP, copied field map from Sourcecado.

Search never returns emails or a full last name. Enrich is the path that can
return a verified email. Missing key fails clearly.
"""

from __future__ import annotations

from typing import Any

from coworker.evidence_envelope import (
    EvidenceParts,
    combine,
    external,
    opaque,
)
from coworker.person_identity import (
    apollo_surname_is_masked,
    without_apollo_name_masks,
)

SEARCH_URL = "https://api.apollo.io/api/v1/mixed_people/api_search"
MATCH_URL = "https://api.apollo.io/api/v1/people/match"

MISSING_KEY = "APOLLO_API_KEY is not configured."

# One people/match call spends one Apollo credit. The approval card states the
# cost before the director allows it; nothing here decides to spend on its own.
ENRICH_CREDIT_COST = 1

# How the approval card says a match key was chosen.
MATCH_LABELS = {"email": "email", "apollo_id": "Apollo ID", "name": "name"}


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


def masked_name(value: str | None) -> bool:
    """True when Apollo obfuscated this name, for example ``Zh***g``.

    The shortlist hides surnames on purpose. Sending one to people/match
    spends a credit on a lookup that cannot succeed, so a masked name is
    never a usable match key.
    """
    return "*" in str(value or "")


def enrich_contact(
    *,
    http: Any,
    api_key: str,
    email: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    organization_name: str | None = None,
    apollo_id: str | None = None,
) -> dict[str, Any]:
    if masked_name(last_name):
        raise ValueError(
            "That surname is still obfuscated by Apollo, so it cannot match. "
            "Enrich by Apollo ID or by email instead."
        )
    if not apollo_id and not email and not (first_name and last_name):
        raise ValueError("Provide apolloId, email, or firstName and lastName.")
    data = http.post(
        MATCH_URL,
        headers={"content-type": "application/json", "x-api-key": api_key},
        json={
            "id": apollo_id,
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
        "apolloId": person.get("id") or None,
        "name": person.get("name") or None,
        "title": person.get("title") or None,
        "organizationName": (person.get("organization") or {}).get("name") or None,
        "linkedinUrl": person.get("linkedin_url") or None,
        "email": person.get("email") or None,
        "phone": phone or None,
    }


def enrichment_match(person: dict[str, Any]) -> dict[str, Any] | None:
    """The lookup Apollo will be asked for, taken from the person file.

    Returns None when the person file cannot identify anybody. Apollo's match
    endpoint needs an email, or a first and last name; guessing either one is
    how the wrong contact gets enriched and the credit gets wasted.
    """
    email = str(person.get("email") or "").strip()
    first = str(person.get("first_name") or "").strip()
    last = str(person.get("last_name") or "").strip()
    company = str(person.get("company") or "").strip()
    apollo_id = str(person.get("apollo_id") or "").strip()
    if email:
        return {"email": email, "organization_name": company or None}
    if first and last and not masked_name(last):
        return {
            "first_name": first,
            "last_name": last,
            "organization_name": company or None,
        }
    # A kept shortlist candidate has a masked surname and no email. The Apollo
    # ID keep already stored identifies the exact record, so it is what is left.
    if apollo_id:
        return {"apollo_id": apollo_id}
    return None


def enrichment_resource(
    person: dict[str, Any], match: dict[str, Any]
) -> dict[str, Any]:
    """The approval card for one enrichment: which person, and what it costs.

    Names the exact person file being changed, not a search row, so Allow can
    never land the facts on somebody else.
    """
    display = " ".join(
        part
        for part in (person.get("first_name"), person.get("last_name"))
        if part
    ).strip()
    approval_display = display or "Unnamed person"
    if person.get("last_name_status") == "hidden_by_apollo":
        approval_display = f"{approval_display} (surname hidden by Apollo)"
    if match.get("email"):
        matched_on = "email"
    elif match.get("apollo_id"):
        matched_on = "apollo_id"
    else:
        matched_on = "name"
    return {
        "kind": "apollo_enrichment",
        "person_id": str(person.get("person_id") or ""),
        "person": approval_display,
        "title": person.get("title") or None,
        "company": person.get("company") or None,
        "matched_on": matched_on,
        "credits": ENRICH_CREDIT_COST,
        "reason": (
            f"Spends {ENRICH_CREDIT_COST} Apollo credit to look up "
            f"{approval_display} by {MATCH_LABELS[matched_on]} and write the result "
            "to this person file only."
        ),
    }
def apollo_evidence(tool_name: str, payload: dict[str, Any]) -> EvidenceParts:
    """Adapt an Apollo search or enrichment into evidence envelopes.

    Apollo is a vendor claim about a person, not a fact about them. Names,
    titles, employers, and contact details are all vendor-authored, so the
    person file records them with a source reference rather than as something
    Sourcecado knows.
    """
    if not isinstance(payload, dict):
        return opaque("apollo", tool_name, payload)
    if tool_name == "apollo_search_people":
        rows = payload.get("people")
        rows = rows if isinstance(rows, list) else []
        parts = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            last_name = row.get("lastNameObfuscated")
            surname_hidden = apollo_surname_is_masked(last_name)
            safe_name = " ".join(
                str(part or "")
                for part in (
                    row.get("firstName"),
                    None if surname_hidden else last_name,
                )
            ).strip()
            body_fields = [
                ("firstName", row.get("firstName")),
                (
                    "lastNameStatus",
                    "surname hidden by Apollo" if surname_hidden else last_name,
                ),
                ("title", row.get("title")),
                ("organizationName", row.get("organizationName")),
            ]
            parts.append(
                external(
                    "apollo",
                    identity=("person", row.get("apolloId")),
                    title=(
                        f"{safe_name} (surname hidden by Apollo)"
                        if surname_hidden and safe_name
                        else safe_name or "Apollo candidate"
                    ),
                    body="\n".join(
                        f"{key}: {value}" for key, value in body_fields if value
                    ),
                    sensitivity="sensitive",
                )
            )
        combined = combine(parts)
        return EvidenceParts(
            metadata={
                "apollo_ids": [
                    str(row.get("apolloId") or "")
                    for row in rows
                    if isinstance(row, dict)
                ],
                "count": len(rows),
                "has_email": [
                    bool(row.get("hasEmail"))
                    for row in rows
                    if isinstance(row, dict)
                ],
            },
            envelopes=combined.envelopes,
        )
    if tool_name == "apollo_enrich_contact":
        raw_name = str(payload.get("name") or "").strip()
        safe_name = without_apollo_name_masks(raw_name) or "Apollo contact"
        return external(
            "apollo",
            identity=("contact", payload.get("email"), payload.get("linkedinUrl")),
            title=safe_name,
            body="\n".join(
                f"{key}: {value}"
                for key, value in (
                    ("name", safe_name if raw_name else None),
                    ("title", payload.get("title")),
                    ("organizationName", payload.get("organizationName")),
                    ("email", payload.get("email")),
                    ("phone", payload.get("phone")),
                )
                if value
            ),
            url=payload.get("linkedinUrl"),
            sensitivity="sensitive",
        )
    return opaque("apollo", tool_name, payload)
