"""Google Calendar list/create/update. No delete."""

from __future__ import annotations

from typing import Any

from coworker.apollo import HttpError
from coworker.connectors.google_oauth import (
    google_client_credentials,
    has_calendar_access,
    load_google,
    refresh_access_token,
    save_google,
)
from coworker.evidence_envelope import (
    EvidenceParts,
    combine,
    external,
    opaque,
)
from coworker.gmail import GmailError
from coworker.secrets import SecretStore

EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


class CalendarApi:
    def __init__(self, secrets: SecretStore, *, http: Any, client_id: str, client_secret: str) -> None:
        self.secrets = secrets
        self.http = http
        self.client_id = client_id
        self.client_secret = client_secret

    def list_events(
        self,
        time_min: str | None = None,
        time_max: str | None = None,
        max_results: int = 10,
    ) -> dict[str, Any]:
        self._require()
        if not time_min:
            from datetime import datetime
            from zoneinfo import ZoneInfo

            time_min = datetime.now(ZoneInfo("America/Los_Angeles")).replace(microsecond=0).isoformat()
        params: dict[str, Any] = {
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": max(1, min(int(max_results), 10)),
            "timeMin": time_min,
        }
        if time_max:
            params["timeMax"] = time_max
        data = self._request("get", EVENTS_URL, params=params) or {}
        events = []
        for row in data.get("items") or []:
            attendees = [
                {
                    "email": attendee.get("email"),
                    "displayName": attendee.get("displayName"),
                }
                for attendee in row.get("attendees") or []
                if isinstance(attendee, dict)
                and (attendee.get("email") or attendee.get("displayName"))
            ]
            events.append(
                {
                    "id": row.get("id"),
                    "summary": row.get("summary"),
                    "start": row.get("start"),
                    "end": row.get("end"),
                    "attendees": attendees,
                    "htmlLink": row.get("htmlLink"),
                }
            )
        return {"events": events}

    def create(
        self,
        *,
        summary: str,
        start: str,
        end: str,
        timezone: str = "America/Los_Angeles",
        description: str = "",
    ) -> dict[str, Any]:
        self._require()
        body = {
            "summary": summary,
            "start": {"dateTime": start, "timeZone": timezone},
            "end": {"dateTime": end, "timeZone": timezone},
        }
        if description:
            body["description"] = description
        data = self._request("post", EVENTS_URL, json=body) or {}
        return {
            "id": data.get("id"),
            "summary": data.get("summary"),
            "htmlLink": data.get("htmlLink"),
        }

    def update(
        self,
        *,
        event_id: str,
        summary: str | None = None,
        start: str | None = None,
        end: str | None = None,
        description: str | None = None,
        timezone: str = "America/Los_Angeles",
    ) -> dict[str, Any]:
        self._require()
        patch: dict[str, Any] = {}
        if summary is not None:
            patch["summary"] = summary
        if description is not None:
            patch["description"] = description
        if start is not None:
            patch["start"] = {"dateTime": start, "timeZone": timezone}
        if end is not None:
            patch["end"] = {"dateTime": end, "timeZone": timezone}
        data = self._request("patch", f"{EVENTS_URL}/{event_id}", json=patch) or {}
        return {"id": data.get("id"), "summary": data.get("summary")}

    def _require(self) -> None:
        if not has_calendar_access(load_google(self.secrets)):
            raise GmailError("Calendar is not connected.")

    def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        profile = load_google(self.secrets)
        token = str(profile.get("access_token") or "")
        if not token:
            token = self._refresh(profile)
        headers = dict(kwargs.pop("headers", None) or {})
        headers["Authorization"] = f"Bearer {token}"
        try:
            return self._dispatch(method, url, headers=headers, **kwargs)
        except HttpError as exc:
            if exc.status == 401:
                token = self._refresh(load_google(self.secrets))
                headers["Authorization"] = f"Bearer {token}"
                return self._dispatch(method, url, headers=headers, **kwargs)
            raise GmailError(f"Calendar API returned HTTP {exc.status}.") from exc

    def _dispatch(self, method: str, url: str, **kwargs: Any) -> Any:
        if method == "get":
            return self.http.get(url, **kwargs)
        if method == "patch":
            return self.http.patch(url, **kwargs)
        return self.http.post(url, **kwargs)

    def _refresh(self, profile: dict[str, Any]) -> str:
        refresh = str(profile.get("refresh_token") or "")
        if not refresh:
            raise GmailError("Calendar is not connected.")
        tokens = refresh_access_token(
            self.http,
            client_id=self.client_id,
            client_secret=self.client_secret,
            refresh_token=refresh,
        )
        access = str(tokens.get("access_token") or "")
        profile = dict(profile)
        profile["access_token"] = access
        save_google(self.secrets, profile)
        return access


def calendar_from_secrets(secrets: SecretStore, *, http: Any | None = None) -> CalendarApi | None:
    from coworker.apollo import LiveHttp

    profile = load_google(secrets)
    if not profile.get("refresh_token") or not has_calendar_access(profile):
        return None
    client_id, client_secret = google_client_credentials()
    if not client_id or not client_secret:
        return None
    return CalendarApi(
        secrets,
        http=http if http is not None else LiveHttp(),
        client_id=client_id,
        client_secret=client_secret,
    )


def calendar_evidence(tool_name: str, payload: dict[str, Any]) -> EvidenceParts:
    """Adapt a Calendar listing or write receipt into evidence envelopes.

    An event title and an attendee display name are written by whoever created
    the invite, which is usually not the director. A create or update receipt
    echoes what Google stored, so it is classified the same way rather than
    trusted because Sourcecado started the call.
    """
    if not isinstance(payload, dict):
        return opaque("calendar", tool_name, payload)
    if tool_name == "calendar_list":
        rows = payload.get("events")
        rows = rows if isinstance(rows, list) else []
        parts = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            attendees = row.get("attendees") if isinstance(row.get("attendees"), list) else []
            lines = [f"Summary: {row.get('summary') or ''}"]
            for attendee in attendees:
                if isinstance(attendee, dict):
                    lines.append(
                        f"Attendee: {attendee.get('displayName') or ''} "
                        f"<{attendee.get('email') or ''}>"
                    )
            start = _timestamp(row.get("start"))
            parts.append(
                external(
                    "calendar",
                    identity=("event", row.get("id"), start),
                    title=str(row.get("summary") or "Calendar event"),
                    body="\n".join(lines),
                    url=row.get("htmlLink"),
                    sensitivity="sensitive",
                    source_time=start,
                )
            )
        combined = combine(parts)
        return EvidenceParts(
            metadata={
                "event_ids": [
                    str(row.get("id") or "") for row in rows if isinstance(row, dict)
                ],
                "count": len(rows),
            },
            envelopes=combined.envelopes,
        )
    if tool_name in {"calendar_create", "calendar_update"}:
        return external(
            "calendar",
            identity=("event", payload.get("id"), tool_name),
            title=str(payload.get("summary") or "Calendar event"),
            body=f"Summary: {payload.get('summary') or ''}",
            metadata={"id": payload.get("id")},
            url=payload.get("htmlLink"),
            sensitivity="sensitive",
        )
    return opaque("calendar", tool_name, payload)


def _timestamp(value: Any) -> str | None:
    if isinstance(value, dict):
        raw = value.get("dateTime") or value.get("date")
    else:
        raw = value
    text = str(raw or "").strip()
    return text or None
