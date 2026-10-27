"""Reads one PDF and writes one JSON line. Runs in its own process.

``pdf_isolation`` starts this module as a child and kills it when it takes too
long or grows too large. Nothing here may depend on the rest of Sourcecado: it
imports the standard library and ``pypdf`` only, and it never sees a secret.

A PDF can make ``pypdf`` spend minutes on a few kilobytes: a small compressed
stream that expands to megabytes of text operators, shared by many pages. Two
checks stop the worst of it early, and the parent's wall clock stops the rest.

- A page whose content stream expands past ``--max-stream`` bytes ends the
  read with ``pdf_too_complex``. Each stream is measured once, so pages that
  share one stream cost one measure, not one per page.
- The read stops after ``--seconds`` and returns the text found so far.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from io import BytesIO


def _oversized(page, limit: int, measured: set[int]) -> bool:
    """True when a content stream of this page expands past ``limit`` bytes."""
    from pypdf.generic import ArrayObject

    contents = page.get("/Contents")
    if contents is None:
        return False
    contents = contents.get_object()
    items = list(contents) if isinstance(contents, ArrayObject) else [contents]
    for item in items:
        stream = item.get_object()
        if id(stream) in measured or not hasattr(stream, "get_data"):
            continue
        measured.add(id(stream))
        try:
            if len(stream.get_data()) > limit:
                return True
        except Exception:
            # A stream that will not decode is for `extract_text` to judge.
            continue
    return False


def extract(
    data: bytes, *, max_chars: int, max_pages: int, max_stream: int, seconds: float
) -> dict:
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data), strict=False)
    if reader.is_encrypted and not reader.decrypt(""):
        return {"ok": False, "code": "pdf_encrypted"}
    page_count = len(reader.pages)
    parts: list[str] = []
    chars = 0
    truncated = False
    measured: set[int] = set()
    started = time.monotonic()
    for index, page in enumerate(reader.pages):
        if index >= max_pages or chars > max_chars or time.monotonic() - started > seconds:
            truncated = True
            break
        if _oversized(page, max_stream, measured):
            return {"ok": False, "code": "pdf_too_complex"}
        text = (page.extract_text() or "").strip()
        if text:
            parts.append(text)
            chars += len(text) + 2
    text = "\n\n".join(parts)
    if not text.strip():
        return {"ok": False, "code": "no_text"}
    return {
        "ok": True,
        "text": text[:max_chars],
        "total_chars": None if truncated else len(text),
        "truncated": truncated or len(text) > max_chars,
        "pages": page_count,
    }


def serve(argv: list[str]) -> None:
    """Read a PDF from stdin, write the result to stdout, and exit."""
    logging.disable(logging.CRITICAL)
    options = dict(zip(argv[::2], argv[1::2]))
    try:
        result = extract(
            sys.stdin.buffer.read(),
            max_chars=int(options["--max-chars"]),
            max_pages=int(options["--max-pages"]),
            max_stream=int(options["--max-stream"]),
            seconds=float(options["--seconds"]),
        )
    except Exception:
        result = {"ok": False, "code": "corrupt_pdf"}
    sys.stdout.write(json.dumps(result))
    sys.stdout.flush()


if __name__ == "__main__":
    serve(sys.argv[1:])
