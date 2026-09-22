"""Bounded text extraction for Drive blob documents."""

from __future__ import annotations

from io import BytesIO
from xml.etree import ElementTree
from typing import Any
from zipfile import BadZipFile, ZipFile

from coworker.run_evidence import Evidence

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
PDF_MIME = "application/pdf"
EXTRACTABLE_MIMES = frozenset({DOCX_MIME, PPTX_MIME, PDF_MIME})


class DriveExtractionError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _xml_text(payload: bytes) -> str:
    root = ElementTree.fromstring(payload)
    return "\n".join(
        text.strip()
        for node in root.iter()
        if (node.tag == "t" or node.tag.endswith("}t"))
        if (text := (node.text or "").strip())
    )


def _office_text(mime_type: str, payload: bytes) -> str:
    try:
        with ZipFile(BytesIO(payload)) as archive:
            if mime_type == DOCX_MIME:
                return _xml_text(archive.read("word/document.xml"))
            slide_names = sorted(
                name
                for name in archive.namelist()
                if name.startswith("ppt/slides/slide") and name.endswith(".xml")
            )
            if not slide_names:
                raise KeyError("no slides")
            return "\n".join(_xml_text(archive.read(name)) for name in slide_names)
    except (BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise DriveExtractionError("malformed_office_archive") from exc


def _pdf_text(payload: bytes) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(payload))
        return "\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()
    except Exception as exc:
        raise DriveExtractionError("malformed_pdf") from exc


def extract_drive_text(mime_type: str, payload: bytes) -> str:
    if mime_type in {DOCX_MIME, PPTX_MIME}:
        return _office_text(mime_type, payload)
    if mime_type == PDF_MIME:
        return _pdf_text(payload)
    raise DriveExtractionError("unsupported_mime_type")


# How Drive's own extraction outcome reads as evidence. `unsupported` is the
# reason this map is here rather than inlined: "this build cannot read a .pages
# file" must never render as "the document said nothing".
_STATUS_EVIDENCE = {
    "read": Evidence.PRESENT,
    "truncated": Evidence.PARTIAL,
    "metadata_only": Evidence.ABSENT,
    "unsupported": Evidence.UNSUPPORTED,
    "failed": Evidence.MISSING,
}


def evidence_for_status(status: Any) -> Evidence:
    """Read an extraction status as one value from the run evidence vocabulary."""
    return _STATUS_EVIDENCE.get(str(status or ""), Evidence.AMBIGUOUS)
