"""Small synthetic files for the attachment tests. No private document is used.

Every file is built here from a few bytes, so a test never reads a real
document and a fixture cannot drift from what the test says it holds.
"""

from __future__ import annotations

import base64
import struct
import zlib


def pdf_bytes(pages: list[list[str]]) -> bytes:
    """A valid text PDF. ``pages`` is a list of pages, each a list of lines."""
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # the page tree, filled in below
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    kids: list[str] = []
    for lines in pages:
        page_number = len(objects) + 1
        kids.append(f"{page_number} 0 R")
        body = ["BT", "/F1 12 Tf", "14 TL", "72 720 Td"]
        for line in lines:
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            body.append(f"({escaped}) Tj T*")
        body.append("ET")
        stream = "\n".join(body).encode("latin-1")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {page_number + 1} 0 R >>".encode()
        )
        objects.append(
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
        )
    objects[1] = (
        f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()
    )
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += (
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objects) + 1, xref_at)
    )
    return bytes(out)


def pdf_without_text() -> bytes:
    """A valid one-page PDF with nothing drawn on it, like a blank scan."""
    return pdf_bytes([[]])


def corrupt_pdf() -> bytes:
    """Starts like a PDF and is not one."""
    return b"%PDF-1.4\n1 0 obj << /Type /Catalog\n" + b"\x00garbage" * 20


def png_bytes(width: int = 8, height: int = 8, rgb: tuple[int, int, int] = (91, 140, 42)) -> bytes:
    """A valid solid-colour PNG."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(
            ">I", zlib.crc32(body) & 0xFFFFFFFF
        )

    row = b"\x00" + bytes(rgb) * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


# The smallest valid JPEG and WebP images that browsers decode.
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////"
    "////////////////////////////////////////////////////wgALCAABAAEBAREA/8QA"
    "FBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA="
)
WEBP = base64.b64decode("UklGRhoAAABXRUJQVlA4TA0AAAAvAAAAEAcQERGIiP4HAA==")

CSV = (
    "company,contact,role,status\n"
    "Acme Robotics,Maya Chen,Founder,warm intro\n"
    'Linear,"Alvarez, Jon",Head of Engineering,cold\n'
    "Nimbus Labs,Priya Raman,CTO,replied\n"
).encode()

MARKDOWN = (
    "# Sourcing target\n\n"
    "Find early engineers at seed-stage robotics startups.\n\n"
    "- Prefer founders with a research background\n"
    "- Skip anyone already in conversation\n"
).encode()

TEXT = b"Recruiting brief\nTarget: staff engineers in Berkeley\nBudget: two intros per week\n"
