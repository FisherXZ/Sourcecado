import json
from io import BytesIO
from urllib.parse import unquote
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi.testclient import TestClient

from coworker.apollo import FakeHttp, HttpError, _decode_response
from coworker.connectors.google_oauth import (
    CALENDAR_SCOPE,
    COMPOSE_SCOPE,
    DRIVE_SCOPE,
    READ_SCOPE,
    SEND_SCOPE,
    save_google,
)
from coworker.drive import DriveApi
from coworker.permissions import decide
from coworker.provider import FakeProvider, ToolCall
from coworker.secrets import SecretStore
from coworker.server import TOKEN_HEADER, create_app
from coworker.tools import OPENAI_TOOLS, execute

TOKEN = "test-token-drive"
FILES_URL = "https://www.googleapis.com/drive/v3/files"
FORMS_URL = "https://forms.googleapis.com/v1/forms"
FOLDER_MIME = "application/vnd.google-apps.folder"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


class DriveMediaHttp(FakeHttp):
    def __init__(self, *, file_id: str, metadata: dict, media: bytes) -> None:
        super().__init__({f"{FILES_URL}/{file_id}": metadata})
        self.file_id = file_id
        self.media = media

    def get(self, url, *, headers=None, params=None):
        if url == f"{FILES_URL}/{self.file_id}" and (params or {}).get("alt") == "media":
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
            return self.media
        return super().get(url, headers=headers, params=params)


def office_archive(path: str, xml: str) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr(path, xml)
    return output.getvalue()


def text_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 72 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{index} 0 obj\n".encode())
        payload.extend(body)
        payload.extend(b"\nendobj\n")
    xref = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode())
    payload.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(payload)


def test_live_http_decode_preserves_binary_response_bytes():
    raw = b"%PDF binary API_KEY=must-not-be-decoded"
    response = type(
        "BinaryResponse",
        (),
        {
            "headers": {"content-type": "application/pdf"},
            "text": raw.decode(),
            "content": raw,
        },
    )()

    assert _decode_response(response) == raw


def test_drive_tools_are_auto():
    assert decide("drive_search").allowed is True
    assert decide("drive_list_folder").allowed is True
    assert decide("drive_read").allowed is True
    names = {schema["function"]["name"] for schema in OPENAI_TOOLS}
    assert {"drive_search", "drive_list_folder", "drive_read"} <= names


def test_drive_list_folder_tool_passes_scope_and_page_token(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    http = FakeHttp(
        {
            FILES_URL: {
                "files": [{"id": "child-1", "name": "Child", "mimeType": "text/plain"}],
                "nextPageToken": "page-3",
            }
        }
    )
    drive = DriveApi(secrets, http=http, client_id="cid", client_secret="sec")

    ok, result = execute(
        "drive_list_folder",
        {"folder_id": "folder-1", "max_results": 20, "page_token": "page-2"},
        drive=drive,
    )

    assert ok is True
    assert result["files"][0]["id"] == "child-1"
    assert result["nextPageToken"] == "page-3"
    assert http.calls[-1]["params"]["pageToken"] == "page-2"
    assert "'folder-1' in parents" in http.calls[-1]["params"]["q"]


def test_drive_search_fake_http(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    http = FakeHttp(
        {
            FILES_URL: {
                "files": [
                    {
                        "id": "f1",
                        "name": "Q3 plan",
                        "mimeType": "text/plain",
                        "modifiedTime": "2026-08-01T00:00:00Z",
                        "parents": ["folder-1"],
                        "webViewLink": "https://drive.google.com/open?id=f1",
                    }
                ]
            }
        }
    )
    out = DriveApi(secrets, http=http, client_id="cid", client_secret="sec").search("Q3 plan")
    assert out["files"][0]["id"] == "f1"
    assert out["files"][0]["status"] == "metadata_only"
    assert out["files"][0]["parents"] == ["folder-1"]
    assert out["files"][0]["webViewLink"].endswith("id=f1")
    assert out["sources"][0]["id"] == "f1"
    assert "/upload" not in str(http.calls)


def test_drive_search_redacts_credentials_in_filenames(tmp_path):
    raw_secret = "apollo_search_filename_secret_1234567890"
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    http = FakeHttp(
        {
            FILES_URL: {
                "files": [
                    {
                        "id": "f1",
                        "name": f"APOLLO_API_KEY={raw_secret}",
                        "mimeType": "text/plain",
                    }
                ]
            }
        }
    )

    out = DriveApi(secrets, http=http, client_id="cid", client_secret="sec").search("Apollo")

    assert raw_secret not in json.dumps(out)
    assert out["files"][0]["name"] == "APOLLO_API_KEY=[REDACTED]"
    assert out["sensitive_content_redacted"] is True
    assert out["redaction_count"] == 1


def test_drive_read_exports_google_doc(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    http = FakeHttp(
        {
            f"{FILES_URL}/f1": {
                "id": "f1",
                "name": "doc",
                "mimeType": "application/vnd.google-apps.document",
                "modifiedTime": "2026-08-26T10:00:00Z",
                "parents": ["folder-1"],
                "webViewLink": "https://drive.google.com/open?id=f1",
            },
            f"{FILES_URL}/f1/export": "plain text body",
        }
    )
    out = DriveApi(secrets, http=http, client_id="cid", client_secret="sec").read("f1")
    assert out["content"] == "plain text body"
    assert out["truncated"] is False
    assert out["status"] == "read"
    assert out["modifiedTime"] == "2026-08-26T10:00:00Z"
    assert out["parents"] == ["folder-1"]
    assert out["sources"] == [
        {
            "id": "f1",
            "title": "doc",
            "url": "https://drive.google.com/open?id=f1",
            "provider": "Google Drive",
            "truncated": False,
        }
    ]


def test_drive_read_classifies_truncated_google_export(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    http = FakeHttp(
        {
            f"{FILES_URL}/f1": {
                "id": "f1",
                "name": "long doc",
                "mimeType": "application/vnd.google-apps.document",
            },
            f"{FILES_URL}/f1/export": "abcdefghij",
        }
    )

    out = DriveApi(secrets, http=http, client_id="cid", client_secret="sec").read(
        "f1", max_chars=5
    )

    assert out["content"] == "abcde"
    assert out["truncated"] is True
    assert out["status"] == "truncated"


def test_drive_read_routes_folder_ids_to_scoped_listing_without_media_download(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    http = FakeHttp(
        {
            f"{FILES_URL}/folder-1": {
                "id": "folder-1",
                "name": "Fall 2026",
                "mimeType": FOLDER_MIME,
                "parents": ["sourcing"],
            },
            FILES_URL: {
                "files": [
                    {
                        "id": "doc-1",
                        "name": "Target list",
                        "mimeType": "application/vnd.google-apps.document",
                        "parents": ["folder-1"],
                    }
                ]
            },
        }
    )

    out = DriveApi(secrets, http=http, client_id="cid", client_secret="sec").read("folder-1")

    assert out["status"] == "metadata_only"
    assert out["files"][0]["id"] == "doc-1"
    assert {source["id"] for source in out["sources"]} == {"folder-1", "doc-1"}
    assert http.calls[-1]["url"] == FILES_URL
    assert "'folder-1' in parents" in http.calls[-1]["params"]["q"]
    assert not any(call["params"].get("alt") == "media" for call in http.calls)


def test_drive_read_classifies_unsupported_binary_without_downloading_or_leaking_it(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    http = FakeHttp(
        {
            f"{FILES_URL}/image-1": {
                "id": "image-1",
                "name": "headshot.jpg",
                "mimeType": "image/jpeg",
            }
        }
    )

    out = DriveApi(secrets, http=http, client_id="cid", client_secret="sec").read("image-1")

    assert out["status"] == "unsupported"
    assert out["reason"] == "unsupported_mime_type"
    assert "content" not in out
    assert out["sources"][0]["id"] == "image-1"
    assert not any(call["params"].get("alt") == "media" for call in http.calls)


def test_drive_read_returns_form_metadata_and_linked_response_sheet(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    http = FakeHttp(
        {
            f"{FILES_URL}/form-1": {
                "id": "form-1",
                "name": "Fall interest form",
                "mimeType": "application/vnd.google-apps.form",
            },
            f"{FORMS_URL}/form-1": {
                "formId": "form-1",
                "linkedSheetId": "sheet-1",
                "responderUri": "https://docs.google.com/forms/d/form-1/viewform",
                "info": {"title": "Fall interest form"},
            },
        }
    )

    out = DriveApi(secrets, http=http, client_id="cid", client_secret="sec").read("form-1")

    assert out["status"] == "metadata_only"
    assert out["linkedSheetId"] == "sheet-1"
    assert out["responderUri"].endswith("/viewform")
    assert "content" not in out
    assert out["sources"][0]["id"] == "form-1"
    assert not any(call["params"].get("alt") == "media" for call in http.calls)


def test_drive_read_keeps_form_metadata_truthful_when_forms_detail_is_unavailable(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    http = FakeHttp(
        {
            f"{FILES_URL}/form-1": {
                "id": "form-1",
                "name": "Fall interest form",
                "mimeType": "application/vnd.google-apps.form",
            },
            f"{FORMS_URL}/form-1": HttpError(
                403, f"{FORMS_URL}/form-1", "PRIVATE_FORMS_ERROR_BODY"
            ),
        }
    )

    out = DriveApi(secrets, http=http, client_id="cid", client_secret="sec").read("form-1")

    assert out["status"] == "metadata_only"
    assert out["reason"] == "form_metadata_unavailable"
    assert out["linkedSheetId"] is None
    assert "PRIVATE_FORMS_ERROR_BODY" not in json.dumps(out)


@pytest.mark.parametrize(
    ("file_id", "mime_type", "media", "expected"),
    [
        (
            "docx-1",
            DOCX_MIME,
            office_archive(
                "word/document.xml",
                '<w:document xmlns:w="urn:w"><w:body><w:p><w:r><w:t>Board notes</w:t></w:r></w:p></w:body></w:document>',
            ),
            "Board notes",
        ),
        (
            "pptx-1",
            PPTX_MIME,
            office_archive(
                "ppt/slides/slide1.xml",
                '<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><p:cSld><a:t>Partner pitch</a:t></p:cSld></p:sld>',
            ),
            "Partner pitch",
        ),
        ("pdf-1", "application/pdf", text_pdf("Source evidence"), "Source evidence"),
    ],
)
def test_drive_read_extracts_supported_binary_documents(
    tmp_path, file_id, mime_type, media, expected
):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    http = DriveMediaHttp(
        file_id=file_id,
        metadata={"id": file_id, "name": file_id, "mimeType": mime_type},
        media=media,
    )

    out = DriveApi(secrets, http=http, client_id="cid", client_secret="sec").read(file_id)

    assert out["status"] == "read"
    assert expected in out["content"]


def test_drive_read_classifies_empty_pdf_as_metadata_only(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    http = DriveMediaHttp(
        file_id="empty-pdf",
        metadata={"id": "empty-pdf", "name": "scan.pdf", "mimeType": "application/pdf"},
        media=text_pdf(""),
    )

    out = DriveApi(secrets, http=http, client_id="cid", client_secret="sec").read("empty-pdf")

    assert out["status"] == "metadata_only"
    assert out["reason"] == "no_extractable_text"
    assert "content" not in out


@pytest.mark.parametrize("mime_type", [DOCX_MIME, PPTX_MIME])
def test_drive_read_classifies_malformed_office_archive_without_leaking_bytes(
    tmp_path, mime_type
):
    raw = b"not-a-zip API_KEY=must-not-leak"
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    http = DriveMediaHttp(
        file_id="broken-office",
        metadata={"id": "broken-office", "name": "broken", "mimeType": mime_type},
        media=raw,
    )

    out = DriveApi(secrets, http=http, client_id="cid", client_secret="sec").read(
        "broken-office"
    )

    assert out["status"] == "failed"
    assert out["reason"] == "malformed_office_archive"
    assert raw.decode() not in json.dumps(out)


def test_drive_tool_marks_extraction_failure_as_a_failed_tool_result(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    drive = DriveApi(
        secrets,
        http=DriveMediaHttp(
            file_id="broken-docx",
            metadata={"id": "broken-docx", "name": "broken.docx", "mimeType": DOCX_MIME},
            media=b"not a zip",
        ),
        client_id="cid",
        client_secret="sec",
    )

    ok, result = execute("drive_read", {"file_id": "broken-docx"}, drive=drive)

    assert ok is False
    assert result["status"] == "failed"
    assert result["reason"] == "malformed_office_archive"


def test_drive_tool_returns_sanitized_failed_status_for_genuine_auth_failure(tmp_path):
    raw_provider_body = "PRIVATE_PROVIDER_ERROR_BODY"
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    drive = DriveApi(
        secrets,
        http=FakeHttp(
            {
                f"{FILES_URL}/forbidden": HttpError(
                    403, f"{FILES_URL}/forbidden", raw_provider_body
                )
            }
        ),
        client_id="cid",
        client_secret="sec",
    )

    ok, result = execute("drive_read", {"file_id": "forbidden"}, drive=drive)

    assert ok is False
    assert result["status"] == "failed"
    assert result["error"] == "Drive API returned HTTP 403."
    assert raw_provider_body not in json.dumps(result)


@pytest.mark.parametrize(
    "source",
    [
        "APOLLO_API_KEY=apollo_live_1234567890",
        "TOKEN=generic_token_value_1234567890",
        "GITHUB_TOKEN=github_token_value_1234567890",
        "access token: ya29.example_access_token_123456",
        "secret = internal_secret_value_123456",
        "password: example_password_value_123456",
        "password: abc",
        '\"private_key\": \"private_key_material_1234567890\"',
        '\"password\": \"P@ssw0rd!with:punctuation#123\"',
    ],
)
def test_drive_read_redacts_credential_assignments(tmp_path, source):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    http = FakeHttp(
        {
            f"{FILES_URL}/f1": {
                "id": "f1",
                "name": "Sourcing SOP",
                "mimeType": "application/vnd.google-apps.document",
            },
            f"{FILES_URL}/f1/export": f"Setup\n{source}\nDone",
        }
    )

    out = DriveApi(secrets, http=http, client_id="cid", client_secret="sec").read("f1")

    assert source not in out["content"]
    assert "[REDACTED]" in out["content"]
    assert out["sensitive_content_redacted"] is True
    assert out["redaction_count"] == 1


def test_drive_read_redacts_private_key_blocks(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    private_key = (
        "-----BEGIN PRIVATE KEY-----\n"
        "private-key-material-that-must-never-leave-the-boundary\n"
        "-----END PRIVATE KEY-----"
    )
    http = FakeHttp(
        {
            f"{FILES_URL}/f1": {
                "id": "f1",
                "name": "Setup",
                "mimeType": "application/vnd.google-apps.document",
            },
            f"{FILES_URL}/f1/export": private_key,
        }
    )

    out = DriveApi(secrets, http=http, client_id="cid", client_secret="sec").read("f1")

    assert private_key not in out["content"]
    assert out["content"] == "[REDACTED PRIVATE KEY]"
    assert out["redaction_count"] == 1


def test_drive_read_redacts_encrypted_private_key_blocks(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    private_key = (
        "-----BEGIN ENCRYPTED PRIVATE KEY-----\n"
        "encrypted-private-key-material-that-must-never-leave-the-boundary\n"
        "-----END ENCRYPTED PRIVATE KEY-----"
    )
    http = FakeHttp(
        {
            f"{FILES_URL}/f1": {
                "id": "f1",
                "name": "Setup",
                "mimeType": "application/vnd.google-apps.document",
            },
            f"{FILES_URL}/f1/export": private_key,
        }
    )

    out = DriveApi(secrets, http=http, client_id="cid", client_secret="sec").read("f1")

    assert private_key not in out["content"]
    assert out["content"] == "[REDACTED PRIVATE KEY]"
    assert out["redaction_count"] == 1


def test_drive_read_does_not_redact_ordinary_security_prose(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    prose = "Keep the API key in the local secret store. Password rotation is required."
    http = FakeHttp(
        {
            f"{FILES_URL}/f1": {
                "id": "f1",
                "name": "Security guide",
                "mimeType": "application/vnd.google-apps.document",
            },
            f"{FILES_URL}/f1/export": prose,
        }
    )

    out = DriveApi(secrets, http=http, client_id="cid", client_secret="sec").read("f1")

    assert out["content"] == prose
    assert out["sensitive_content_redacted"] is False
    assert out["redaction_count"] == 0


def test_drive_read_flags_mismatched_legal_template_as_not_ready(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    http = FakeHttp(
        {
            f"{FILES_URL}/f1": {
                "id": "f1",
                "name": "Codeology NDA Template",
                "mimeType": "application/vnd.google-apps.document",
            },
            f"{FILES_URL}/f1/export": (
                "NONDISCLOSURE AGREEMENT between De Beers and Berkeley Consulting."
            ),
        }
    )

    out = DriveApi(secrets, http=http, client_id="cid", client_secret="sec").read("f1")

    assert out["source_safety"] == {
        "legal_document": True,
        "ready_to_use": False,
        "status": "party_mismatch",
        "reasons": ["unexpected_recipient_berkeley_consulting"],
    }


def test_drive_read_does_not_classify_ordinary_agreement_prose_as_legal(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    http = FakeHttp(
        {
            f"{FILES_URL}/f1": {
                "id": "f1",
                "name": "Meeting notes",
                "mimeType": "application/vnd.google-apps.document",
            },
            f"{FILES_URL}/f1/export": "We reached agreement on the project dates.",
        }
    )

    out = DriveApi(secrets, http=http, client_id="cid", client_secret="sec").read("f1")

    assert "source_safety" not in out


def test_ws_drive_read_redacts_before_provider_events_and_persistence(tmp_path):
    raw_secret = "apollo_live_secret_1234567890"
    http = FakeHttp(
        {
            f"{FILES_URL}/f1": {
                "id": "f1",
                "name": "Sourcing SOP",
                "mimeType": "application/vnd.google-apps.document",
            },
            f"{FILES_URL}/f1/export": f"APOLLO_API_KEY={raw_secret}",
        }
    )
    fake = FakeProvider(
        steps=[
            {"tool_calls": [ToolCall(id="c1", name="drive_read", arguments={"file_id": "f1"})]},
            {"deltas": ("The credential was redacted.",)},
        ]
    )
    app = create_app(token=TOKEN, provider=fake, state=tmp_path, http=http)
    save_google(app.state.secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    app.state.drive = DriveApi(app.state.secrets, http=http, client_id="cid", client_secret="sec")
    sid = app.state.store.open_session_id()

    with TestClient(app).websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "check the sourcing SOP", "session_id": sid})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] in ("turn_end", "error"):
                break

    assert raw_secret not in json.dumps(events)
    assert raw_secret not in json.dumps(fake.calls)
    assert raw_secret not in json.dumps(app.state.store.load_events(sid))
    finished = next(event for event in events if event["type"] == "tool_finished")
    assert finished["result"]["content"] == "APOLLO_API_KEY=[REDACTED]"
    assert finished["result"]["sensitive_content_redacted"] is True


def test_ws_drive_read_redacts_filename_before_provider_events_and_persistence(tmp_path):
    raw_secret = "apollo_filename_secret_1234567890"
    http = FakeHttp(
        {
            f"{FILES_URL}/f1": {
                "id": "f1",
                "name": f"APOLLO_API_KEY={raw_secret}",
                "mimeType": "application/vnd.google-apps.document",
            },
            f"{FILES_URL}/f1/export": "ordinary body",
        }
    )
    fake = FakeProvider(
        steps=[
            {"tool_calls": [ToolCall(id="c1", name="drive_read", arguments={"file_id": "f1"})]},
            {"deltas": ("The credential was redacted.",)},
        ]
    )
    app = create_app(token=TOKEN, provider=fake, state=tmp_path, http=http)
    save_google(app.state.secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    app.state.drive = DriveApi(app.state.secrets, http=http, client_id="cid", client_secret="sec")
    sid = app.state.store.open_session_id()

    with TestClient(app).websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "check the source", "session_id": sid})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] in ("turn_end", "error"):
                break

    assert raw_secret not in json.dumps(events)
    assert raw_secret not in json.dumps(fake.calls)
    assert raw_secret not in json.dumps(app.state.store.load_events(sid))
    finished = next(event for event in events if event["type"] == "tool_finished")
    assert finished["result"]["name"] == "APOLLO_API_KEY=[REDACTED]"
    assert finished["result"]["sensitive_content_redacted"] is True
    assert finished["result"]["redaction_count"] == 1


def test_drive_connect_url_requests_readonly(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "sec")
    opened_urls = []
    res = TestClient(
        create_app(
            token=TOKEN,
            state=tmp_path,
            browser_opener=lambda url: opened_urls.append(url) or True,
        )
    ).post(
        "/v1/connectors/drive/connect", headers={TOKEN_HEADER: TOKEN}
    )
    assert res.status_code == 200
    decoded = unquote(res.json()["url"])
    assert opened_urls == [res.json()["url"]]
    assert DRIVE_SCOPE in decoded
    assert COMPOSE_SCOPE in decoded
    assert READ_SCOPE in decoded
    assert SEND_SCOPE in decoded
    assert CALENDAR_SCOPE in decoded
    assert "drive.file" not in decoded


def test_ws_drive_search_does_not_ask(tmp_path):
    http = FakeHttp(
        {FILES_URL: {"files": [{"id": "f1", "name": "Q3 plan", "mimeType": "text/plain"}]}}
    )
    fake = FakeProvider(
        steps=[
            {"tool_calls": [ToolCall(id="c1", name="drive_search", arguments={"query": "Q3"})]},
            {"deltas": ("found it",)},
        ]
    )
    app = create_app(token=TOKEN, provider=fake, state=tmp_path, http=http)
    save_google(app.state.secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    app.state.drive = DriveApi(app.state.secrets, http=http, client_id="cid", client_secret="sec")
    with TestClient(app).websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "find Q3 plan"})
        events = []
        while True:
            ev = ws.receive_json()
            events.append(ev)
            if ev["type"] in ("turn_end", "error"):
                break
    assert "permission_required" not in [e["type"] for e in events]
    assert next(e for e in events if e["type"] == "tool_finished")["ok"] is True
