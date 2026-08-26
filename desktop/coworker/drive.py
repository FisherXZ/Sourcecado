"""Google Drive search/read. Readonly. No writes."""

from __future__ import annotations

import re
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
_CREDENTIAL_PREFIX = (
    r"(?P<prefix>[\"']?(?:[A-Za-z][A-Za-z0-9]*[_-])*"
    r"(?:api[\s_-]*key|access[\s_-]*token|auth[\s_-]*token|token|secret|password|"
    r"private[\s_-]*key)[\"']?\s*[:=]\s*)"
)
_QUOTED_CREDENTIAL_ASSIGNMENT_RE = re.compile(
    _CREDENTIAL_PREFIX + r"(?P<quote>[\"'])(?P<value>[^\r\n]*?)(?P=quote)",
    re.IGNORECASE | re.MULTILINE,
)
_UNQUOTED_CREDENTIAL_ASSIGNMENT_RE = re.compile(
    _CREDENTIAL_PREFIX + r"(?P<value>[^\s\"']+)",
    re.IGNORECASE | re.MULTILINE,
)
_PRIVATE_KEY_BLOCK_RE = re.compile(
    r"-----BEGIN (?:(?:RSA|EC|DSA|OPENSSH|ENCRYPTED) )?PRIVATE KEY-----.*?"
    r"-----END (?:(?:RSA|EC|DSA|OPENSSH|ENCRYPTED) )?PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_LEGAL_NAME_RE = re.compile(
    r"\b(?:nda|non[ -]?disclosure|agreement|contract|statement of work|sow)\b",
    re.IGNORECASE,
)
_LEGAL_BODY_RE = re.compile(
    r"\b(?:non[ -]?disclosure agreement|statement of work|this agreement|"
    r"agreement between|terms and conditions)\b",
    re.IGNORECASE,
)


def _redact_credentials(text: str) -> tuple[str, int]:
    redaction_count = 0

    def redact_private_key(_match: re.Match[str]) -> str:
        nonlocal redaction_count
        redaction_count += 1
        return "[REDACTED PRIVATE KEY]"

    def redact_assignment(match: re.Match[str]) -> str:
        nonlocal redaction_count
        redaction_count += 1
        quote = match.groupdict().get("quote") or ""
        return f"{match.group('prefix')}{quote}[REDACTED]{quote}"

    safe = _PRIVATE_KEY_BLOCK_RE.sub(redact_private_key, text)
    safe = _QUOTED_CREDENTIAL_ASSIGNMENT_RE.sub(redact_assignment, safe)
    safe = _UNQUOTED_CREDENTIAL_ASSIGNMENT_RE.sub(redact_assignment, safe)
    return safe, redaction_count


def _legal_source_safety(name: str, text: str) -> dict[str, Any] | None:
    if not (_LEGAL_NAME_RE.search(name) or _LEGAL_BODY_RE.search(text[:4000])):
        return None
    reasons: list[str] = []
    if "codeology" in name.lower() and "berkeley consulting" in text.lower():
        reasons.append("unexpected_recipient_berkeley_consulting")
    return {
        "legal_document": True,
        "ready_to_use": False,
        "status": "party_mismatch" if reasons else "unverified",
        "reasons": reasons,
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
        files = []
        redaction_count = 0
        for row in data.get("files") or []:
            raw_name = row.get("name")
            safe_name, name_redaction_count = _redact_credentials(str(raw_name or ""))
            redaction_count += name_redaction_count
            files.append(
                {
                    "id": row.get("id"),
                    "name": safe_name if raw_name is not None else None,
                    "mimeType": row.get("mimeType"),
                    "modifiedTime": row.get("modifiedTime"),
                }
            )
        return {
            "files": files,
            "sensitive_content_redacted": redaction_count > 0,
            "redaction_count": redaction_count,
        }

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
        safe_text, redaction_count = _redact_credentials(text)
        raw_name = meta.get("name")
        name = str(raw_name or "")
        safe_name, name_redaction_count = _redact_credentials(name)
        redaction_count += name_redaction_count
        result = {
            "id": str(meta.get("id") or file_id),
            "name": safe_name if raw_name is not None else None,
            "mimeType": mime,
            "content": safe_text[:max_chars],
            "truncated": truncated,
            "sensitive_content_redacted": redaction_count > 0,
            "redaction_count": redaction_count,
        }
        source_safety = _legal_source_safety(safe_name, safe_text)
        if source_safety is not None:
            result["source_safety"] = source_safety
        return result

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
