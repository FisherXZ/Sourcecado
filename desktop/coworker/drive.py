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
from coworker.drive_extract import (
    DriveExtractionError,
    EXTRACTABLE_MIMES,
    evidence_for_status,
    extract_drive_text,
)
from coworker.evidence_envelope import (
    EvidenceParts,
    combine,
    external,
    opaque,
)
from coworker.run_evidence import Evidence

FILES_URL = "https://www.googleapis.com/drive/v3/files"
FORMS_URL = "https://forms.googleapis.com/v1/forms"
FOLDER_MIME = "application/vnd.google-apps.folder"
FORM_MIME = "application/vnd.google-apps.form"
FILE_FIELDS = "id,name,mimeType,modifiedTime,parents,webViewLink"
EXPORT = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}
TEXT_MIMES = frozenset(
    {
        "application/csv",
        "application/json",
        "application/xml",
        "application/yaml",
        "text/csv",
    }
)
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


def _source_reference(
    file_id: str, name: str | None, url: Any, *, truncated: bool = False
) -> dict[str, Any]:
    return {
        "id": file_id,
        "title": name or "Drive file",
        "url": url,
        "provider": "Google Drive",
        "truncated": truncated,
    }


def _file_row(row: dict[str, Any]) -> tuple[dict[str, Any], int]:
    parents = row.get("parents")
    raw_name = row.get("name")
    safe_name, redaction_count = _redact_credentials(str(raw_name or ""))
    return (
        {
            "id": row.get("id"),
            "name": safe_name if raw_name is not None else None,
            "mimeType": row.get("mimeType"),
            "modifiedTime": row.get("modifiedTime"),
            "parents": list(parents) if isinstance(parents, list) else [],
            "webViewLink": row.get("webViewLink"),
        },
        redaction_count,
    )


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
                "fields": f"files({FILE_FIELDS})",
            },
        ) or {}
        files: list[dict[str, Any]] = []
        redaction_count = 0
        for row in data.get("files") or []:
            if not isinstance(row, dict):
                continue
            safe_row, row_redaction_count = _file_row(row)
            safe_row["status"] = "metadata_only"
            files.append(safe_row)
            redaction_count += row_redaction_count
        return {
            "files": files,
            "sources": [
                _source_reference(
                    str(row.get("id") or ""),
                    str(row.get("name") or "") or None,
                    row.get("webViewLink"),
                )
                for row in files
                if row.get("id")
            ],
            "sensitive_content_redacted": redaction_count > 0,
            "redaction_count": redaction_count,
        }

    def read(self, file_id: str, max_chars: int = 20000) -> dict[str, Any]:
        self._require()
        meta = self._request(
            "get",
            f"{FILES_URL}/{file_id}",
            params={"fields": FILE_FIELDS},
        ) or {}
        mime = str(meta.get("mimeType") or "")
        if mime == FOLDER_MIME:
            return self.list_folder(file_id, folder_meta=meta)
        if mime == FORM_MIME:
            form_reason = None
            try:
                form = self._request("get", f"{FORMS_URL}/{file_id}") or {}
            except GmailError:
                form = {}
                form_reason = "form_metadata_unavailable"
            raw_name = meta.get("name")
            safe_name, redaction_count = _redact_credentials(str(raw_name or ""))
            result = {
                "id": str(meta.get("id") or file_id),
                "name": safe_name if raw_name is not None else None,
                "mimeType": mime,
                "modifiedTime": meta.get("modifiedTime"),
                "parents": list(meta.get("parents") or []),
                "webViewLink": meta.get("webViewLink"),
                "status": "metadata_only",
                "linkedSheetId": form.get("linkedSheetId"),
                "responderUri": form.get("responderUri"),
                "sources": [
                    _source_reference(
                        str(meta.get("id") or file_id), safe_name, meta.get("webViewLink")
                    )
                ],
                "truncated": False,
                "sensitive_content_redacted": redaction_count > 0,
                "redaction_count": redaction_count,
            }
            if form_reason:
                result["reason"] = form_reason
            return result
        export = EXPORT.get(mime)
        if (
            export is None
            and mime not in EXTRACTABLE_MIMES
            and not (mime.startswith("text/") or mime in TEXT_MIMES)
        ):
            raw_name = meta.get("name")
            safe_name, redaction_count = _redact_credentials(str(raw_name or ""))
            return {
                "id": str(meta.get("id") or file_id),
                "name": safe_name if raw_name is not None else None,
                "mimeType": mime,
                "modifiedTime": meta.get("modifiedTime"),
                "parents": list(meta.get("parents") or []),
                "webViewLink": meta.get("webViewLink"),
                "status": "unsupported",
                "reason": "unsupported_mime_type",
                "sources": [
                    _source_reference(
                        str(meta.get("id") or file_id), safe_name, meta.get("webViewLink")
                    )
                ],
                "truncated": False,
                "sensitive_content_redacted": redaction_count > 0,
                "redaction_count": redaction_count,
            }
        if export:
            content = self._request("get", f"{FILES_URL}/{file_id}/export", params={"mimeType": export})
        else:
            content = self._request("get", f"{FILES_URL}/{file_id}", params={"alt": "media"})
        if mime in EXTRACTABLE_MIMES:
            try:
                text = extract_drive_text(
                    mime,
                    content if isinstance(content, bytes) else bytes(content or b""),
                )
            except (DriveExtractionError, TypeError, ValueError) as exc:
                reason = exc.reason if isinstance(exc, DriveExtractionError) else "invalid_binary_payload"
                raw_name = meta.get("name")
                safe_name, redaction_count = _redact_credentials(str(raw_name or ""))
                return {
                    "id": str(meta.get("id") or file_id),
                    "name": safe_name if raw_name is not None else None,
                    "mimeType": mime,
                    "modifiedTime": meta.get("modifiedTime"),
                    "parents": list(meta.get("parents") or []),
                    "webViewLink": meta.get("webViewLink"),
                    "status": "failed",
                    "reason": reason,
                    "sources": [
                        _source_reference(
                            str(meta.get("id") or file_id), safe_name, meta.get("webViewLink")
                        )
                    ],
                    "truncated": False,
                    "sensitive_content_redacted": redaction_count > 0,
                    "redaction_count": redaction_count,
                }
            if not text.strip():
                raw_name = meta.get("name")
                safe_name, redaction_count = _redact_credentials(str(raw_name or ""))
                return {
                    "id": str(meta.get("id") or file_id),
                    "name": safe_name if raw_name is not None else None,
                    "mimeType": mime,
                    "modifiedTime": meta.get("modifiedTime"),
                    "parents": list(meta.get("parents") or []),
                    "webViewLink": meta.get("webViewLink"),
                    "status": "metadata_only",
                    "reason": "no_extractable_text",
                    "sources": [
                        _source_reference(
                            str(meta.get("id") or file_id), safe_name, meta.get("webViewLink")
                        )
                    ],
                    "truncated": False,
                    "sensitive_content_redacted": redaction_count > 0,
                    "redaction_count": redaction_count,
                }
        else:
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
            "modifiedTime": meta.get("modifiedTime"),
            "parents": list(meta.get("parents") or []),
            "webViewLink": meta.get("webViewLink"),
            "content": safe_text[:max_chars],
            "truncated": truncated,
            "status": "truncated" if truncated else "read",
            "sources": [
                _source_reference(
                    str(meta.get("id") or file_id),
                    safe_name,
                    meta.get("webViewLink"),
                    truncated=truncated,
                )
            ],
            "sensitive_content_redacted": redaction_count > 0,
            "redaction_count": redaction_count,
        }
        source_safety = _legal_source_safety(safe_name, safe_text)
        if source_safety is not None:
            result["source_safety"] = source_safety
        return result

    def list_folder(
        self,
        folder_id: str,
        max_results: int = 100,
        page_token: str | None = None,
        *,
        folder_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require()
        fid = folder_id.strip()
        if not fid:
            raise GmailError("folder_id is required")
        escaped = fid.replace("'", "\\'")
        limit = max(1, min(int(max_results), 1000))
        files: list[dict[str, Any]] = []
        redaction_count = 0
        current_token: str | None = page_token
        next_page_token: Any = None
        follow_pages = page_token is None
        while len(files) < limit:
            params: dict[str, Any] = {
                "q": f"'{escaped}' in parents and trashed=false",
                "pageSize": min(limit - len(files), 100),
                "fields": f"nextPageToken,files({FILE_FIELDS})",
            }
            if current_token:
                params["pageToken"] = current_token
            data = self._request("get", FILES_URL, params=params) or {}
            for row in data.get("files") or []:
                if not isinstance(row, dict):
                    continue
                safe_row, row_redaction_count = _file_row(row)
                files.append(safe_row)
                redaction_count += row_redaction_count
                if len(files) >= limit:
                    break
            next_page_token = data.get("nextPageToken")
            next_token = str(next_page_token or "")
            if not follow_pages or not next_token or next_token == current_token:
                break
            current_token = next_token
        meta = folder_meta or {}
        safe_folder_name, folder_count = _redact_credentials(str(meta.get("name") or ""))
        redaction_count += folder_count
        return {
            "id": str(meta.get("id") or fid),
            "folder_id": fid,
            "name": safe_folder_name if meta.get("name") is not None else None,
            "mimeType": FOLDER_MIME,
            "modifiedTime": meta.get("modifiedTime"),
            "parents": list(meta.get("parents") or []),
            "webViewLink": meta.get("webViewLink"),
            "status": "metadata_only",
            "files": files[:limit],
            "sources": [
                _source_reference(
                    str(meta.get("id") or fid),
                    safe_folder_name,
                    meta.get("webViewLink"),
                ),
                *[
                    _source_reference(
                        str(file.get("id") or ""),
                        str(file.get("name") or "") or None,
                        file.get("webViewLink"),
                    )
                    for file in files[:limit]
                    if file.get("id")
                ],
            ],
            "nextPageToken": next_page_token,
            "truncated": False,
            "sensitive_content_redacted": redaction_count > 0,
            "redaction_count": redaction_count,
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


def drive_evidence(tool_name: str, payload: dict[str, Any]) -> EvidenceParts:
    """Adapt a Drive read, search, or folder listing into evidence envelopes.

    Drive file names are author-controlled, so they belong inside the fence
    alongside the body. The sensitivity rule is the one `drive_evidence.normalize`
    already applies to a person file, so a Drive source reads the same way in
    both places.
    """
    if not isinstance(payload, dict):
        return opaque("drive", tool_name, payload)
    sensitivity = "standard"
    if payload.get("sensitive_content_redacted"):
        sensitivity = "restricted"
    elif payload.get("source_safety"):
        sensitivity = "sensitive"
    if tool_name == "drive_read":
        file_id = str(payload.get("id") or "")
        status = str(payload.get("status") or "metadata_only")
        name = str(payload.get("name") or "")
        text = str(payload.get("text") or "")
        return external(
            "drive",
            identity=("file", file_id, payload.get("modifiedTime")),
            title=name or "Drive file",
            body="\n\n".join(part for part in (f"Name: {name}", text) if part.strip()),
            metadata={
                "id": file_id,
                "mimeType": payload.get("mimeType"),
                "status": status,
                "reason": payload.get("reason"),
                "sensitive_content_redacted": bool(
                    payload.get("sensitive_content_redacted")
                ),
                "source_safety": payload.get("source_safety"),
            },
            url=payload.get("webViewLink"),
            sensitivity=sensitivity,
            content=evidence_for_status(status),
            truncated=bool(payload.get("truncated")),
            source_time=str(payload.get("modifiedTime") or "") or None,
        )
    if tool_name in {"drive_search", "drive_list_folder"}:
        rows = payload.get("files")
        rows = rows if isinstance(rows, list) else []
        parts = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            parts.append(
                external(
                    "drive",
                    identity=("file", row.get("id"), row.get("modifiedTime")),
                    title=str(row.get("name") or "Drive file"),
                    body=f"Name: {row.get('name') or ''}",
                    url=row.get("webViewLink"),
                    sensitivity=sensitivity,
                    content=Evidence.ABSENT,
                    source_time=str(row.get("modifiedTime") or "") or None,
                )
            )
        combined = combine(parts)
        return EvidenceParts(
            metadata={
                "file_ids": [
                    str(row.get("id") or "") for row in rows if isinstance(row, dict)
                ],
                "count": len(rows),
                "status": payload.get("status"),
                "nextPageToken": payload.get("nextPageToken"),
            },
            envelopes=combined.envelopes,
        )
    return opaque("drive", tool_name, payload)
