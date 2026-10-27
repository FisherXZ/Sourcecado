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


# Whole 8x8 images that Chrome wrote (canvas.toBlob), so each is a file a
# decoder reads. The WebP is the extended (VP8X) layout. WEBP_LOSSLESS is
# the smallest lossless (VP8L) file.
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/4gHYSUNDX1BST0ZJTEUAAQEAAAHIAAAAAAQwAABtbnRy"
    "UkdCIFhZWiAH4AABAAEAAAAAAABhY3NwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAA"
    "9tYAAQAAAADTLQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAlkZXNjAAAA8AAAACRyWFlaAAABFAAAABRnWFlaAAABKAAAABRiWFlaAAABPAAA"
    "ABR3dHB0AAABUAAAABRyVFJDAAABZAAAAChnVFJDAAABZAAAAChiVFJDAAABZAAAAChjcHJ0"
    "AAABjAAAADxtbHVjAAAAAAAAAAEAAAAMZW5VUwAAAAgAAAAcAHMAUgBHAEJYWVogAAAAAAAA"
    "b6IAADj1AAADkFhZWiAAAAAAAABimQAAt4UAABjaWFlaIAAAAAAAACSgAAAPhAAAts9YWVog"
    "AAAAAAAA9tYAAQAAAADTLXBhcmEAAAAAAAQAAAACZmYAAPKnAAANWQAAE9AAAApbAAAAAAAA"
    "AABtbHVjAAAAAAAAAAEAAAAMZW5VUwAAACAAAAAcAEcAbwBvAGcAbABlACAASQBuAGMALgAg"
    "ADIAMAAxADb/2wBDAAYEBQYFBAYGBQYHBwYIChAKCgkJChQODwwQFxQYGBcUFhYaHSUfGhsj"
    "HBYWICwgIyYnKSopGR8tMC0oMCUoKSj/2wBDAQcHBwoIChMKChMoGhYaKCgoKCgoKCgoKCgo"
    "KCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCj/wAARCAAIAAgDASIAAhEB"
    "AxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAT/xAAbEAACAQUAAAAAAAAAAAAAAAAAEQMEEhMi"
    "Yf/EABUBAQEAAAAAAAAAAAAAAAAAAAQG/8QAGBEAAwEBAAAAAAAAAAAAAAAAAAEDAhH/2gAM"
    "AwEAAhEDEQA/AIqibNbql0AEgAtbdtulH1s//9k="
)
WEBP = base64.b64decode(
    "UklGRiQCAABXRUJQVlA4WAoAAAAgAAAABwAABwAASUNDUMgBAAAAAAHIAAAAAAQwAABtbnRy"
    "UkdCIFhZWiAH4AABAAEAAAAAAABhY3NwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAA"
    "9tYAAQAAAADTLQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAlkZXNjAAAA8AAAACRyWFlaAAABFAAAABRnWFlaAAABKAAAABRiWFlaAAABPAAA"
    "ABR3dHB0AAABUAAAABRyVFJDAAABZAAAAChnVFJDAAABZAAAAChiVFJDAAABZAAAAChjcHJ0"
    "AAABjAAAADxtbHVjAAAAAAAAAAEAAAAMZW5VUwAAAAgAAAAcAHMAUgBHAEJYWVogAAAAAAAA"
    "b6IAADj1AAADkFhZWiAAAAAAAABimQAAt4UAABjaWFlaIAAAAAAAACSgAAAPhAAAts9YWVog"
    "AAAAAAAA9tYAAQAAAADTLXBhcmEAAAAAAAQAAAACZmYAAPKnAAANWQAAE9AAAApbAAAAAAAA"
    "AABtbHVjAAAAAAAAAAEAAAAMZW5VUwAAACAAAAAcAEcAbwBvAGcAbABlACAASQBuAGMALgAg"
    "ADIAMAAxADZWUDggNgAAAPABAJ0BKggACAABQCYlqAJ0ugACsw/IAADIkXHt4R3/lK/nz8g+"
    "i7+7Z//iUftiEFRDvYwoAA=="
)
WEBP_LOSSLESS = base64.b64decode("UklGRhoAAABXRUJQVlA4TA0AAAAvAAAAEAcQERGIiP4HAA==")

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


_OPS = b"BT /F1 12 Tf 72 720 Td (hello world) Tj ET\n"


def bomb_pdf(expanded_bytes: int, *, pages: int = 1, compress: bool = True) -> bytes:
    """A valid PDF whose one content stream expands to ``expanded_bytes``.

    Every page shares that stream. Compressed, a few kilobytes of file hold
    megabytes of text operators, which is what makes a PDF costly to read.
    """
    import zlib

    raw = _OPS * max(1, expanded_bytes // len(_OPS))
    stream = zlib.compress(raw, 6) if compress else raw
    flate = b" /Filter /FlateDecode" if compress else b""
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d%s >>\nstream\n" % (len(stream), flate) + stream + b"\nendstream",
    ]
    kids: list[bytes] = []
    for _ in range(pages):
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents 4 0 R >>"
        )
        kids.append(b"%d 0 R" % len(objects))
    objects[1] = b"<< /Type /Pages /Kids [" + b" ".join(kids) + b"] /Count %d >>" % pages
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref_at,
    )
    return bytes(out)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    import struct
    import zlib

    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(
        ">I", zlib.crc32(body) & 0xFFFFFFFF
    )


def png_claiming(width: int, height: int, *, padding: int = 0) -> bytes:
    """A PNG whose header says ``width`` by ``height``, with a valid structure.

    The pixel data is a stub, because the checks read the header and the chunk
    framing and never decode pixels. ``padding`` adds a text chunk of that many
    bytes, to make a file of a chosen size.
    """
    import struct
    import zlib

    parts = [
        b"\x89PNG\r\n\x1a\n",
        _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)),
    ]
    if padding:
        parts.append(_png_chunk(b"tEXt", b"Comment\x00" + b"x" * padding))
    parts += [_png_chunk(b"IDAT", zlib.compress(b"\x00\x00")), _png_chunk(b"IEND", b"")]
    return b"".join(parts)
