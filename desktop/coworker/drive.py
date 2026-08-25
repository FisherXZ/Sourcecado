"""Google Drive search/read. Readonly. No writes."""

from __future__ import annotations

from typing import Any

from coworker.apollo import HttpError
from coworker.connectors.google_oauth import (
    DRIVE_SCOPE,
    google_client_credentials,
    has_scope,
    load_google,
    refresh_access_token,
    save_google,
)
from coworker.gmail import GmailError
from coworker.secrets import SecretStore

FILES_URL = "https://www.googleapis.com/drive/v3/files"
EXPORT = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}


class DriveApi:
    def __init__(self, secrets: SecretStore, *, http: Any, client_id: str, client_secret: str) -> None:
        self.secrets = secrets
        self.http = http
        self.client_id = client_id
        self.client_secret = client_secret

    def search(self, query: str, max_results: int = 10) -> dict[str, Any]:
        self._require()
        q = query.replace("'", "\\'")
        data = self._request(
            "get",
            FILES_URL,
            params={
                "q": f"(name contains '{q}' or fullText contains '{q}') and trashed=false",
                "pageSize": max(1, min(int(max_results), 10)),
                "fields": "files(id,name,mimeType,modifiedTime,size,webViewLink)",
            },
        ) or {}
        files = [
            {
                "id": row.get("id"),
                "name": row.get("name"),
                "mimeType": row.get("mimeType"),
                "modifiedTime": row.get("modifiedTime"),
            }
            for row in data.get("files") or []
        ]
        return {"files": files}

    def read(self, file_id: str, max_chars: int = 20000) -> dict[str, Any]:
        self._require()
        meta = self._request("get", f"{FILES_URL}/{file_id}") or {}
        mime = str(meta.get("mimeType") or "")
        export = EXPORT.get(mime)
        if export:
            content = self._request("get", f"{FILES_URL}/{file_id}/export", params={"mimeType": export})
        else:
            content = self._request("get", f"{FILES_URL}/{file_id}", params={"alt": "media"})
        text = content if isinstance(content, str) else str(content or "")
        truncated = len(text) > max_chars
        return {
            "id": str(meta.get("id") or file_id),
            "name": meta.get("name"),
            "mimeType": mime,
            "content": text[:max_chars],
            "truncated": truncated,
        }

    def _require(self) -> None:
        if not has_scope(load_google(self.secrets), DRIVE_SCOPE):
            raise GmailError("Drive is not connected.")

    def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        profile = load_google(self.secrets)
        token = str(profile.get("access_token") or "")
        if not token:
            token = self._refresh(profile)
        headers = dict(kwargs.pop("headers", None) or {})
        headers["Authorization"] = f"Bearer {token}"
        try:
            if method == "get":
                return self.http.get(url, headers=headers, **kwargs)
            return self.http.post(url, headers=headers, **kwargs)
        except HttpError as exc:
            if exc.status == 401:
                token = self._refresh(load_google(self.secrets))
                headers["Authorization"] = f"Bearer {token}"
                if method == "get":
                    return self.http.get(url, headers=headers, **kwargs)
                return self.http.post(url, headers=headers, **kwargs)
            raise GmailError(f"Drive API returned HTTP {exc.status}.") from exc

    def _refresh(self, profile: dict[str, Any]) -> str:
        refresh = str(profile.get("refresh_token") or "")
        if not refresh:
            raise GmailError("Drive is not connected.")
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


def drive_from_secrets(secrets: SecretStore, *, http: Any | None = None) -> DriveApi | None:
    from coworker.apollo import LiveHttp

    profile = load_google(secrets)
    if not profile.get("refresh_token") or not has_scope(profile, DRIVE_SCOPE):
        return None
    client_id, client_secret = google_client_credentials()
    if not client_id or not client_secret:
        return None
    return DriveApi(
        secrets,
        http=http if http is not None else LiveHttp(),
        client_id=client_id,
        client_secret=client_secret,
    )
