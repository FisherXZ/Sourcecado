"""Bounded checks of an image's own structure, with no image library.

A file that starts with the right magic bytes is not yet an image. A model
provider answers HTTP 400 to a broken one, and a thread that keeps sending it
stops working. Each reader below walks the container the way a decoder would
reach the pixel data, and stops at the first thing that is wrong:

- PNG: every chunk length and CRC, an IHDR first, at least one IDAT, an IEND.
- JPEG: every segment up to the frame header (SOF), a scan (SOS), and an EOI.
- WebP: the RIFF size against the file, then the VP8, VP8L or VP8X header.

None of them decodes pixels. The cost is one pass over at most the 5 MB the
store allows, so a file cannot hold the CPU. They return the width and height
the file declares. The caller compares those with the provider limit.
"""

from __future__ import annotations

import struct
import zlib

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# SOF0-SOF15 without DHT (C4), JPG (C8) and DAC (CC).
_JPEG_FRAME_MARKERS = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)
_PNG_COLOR_DEPTHS = {
    0: {1, 2, 4, 8, 16},
    2: {8, 16},
    3: {1, 2, 4, 8},
    4: {8, 16},
    6: {8, 16},
}


class BrokenImage(ValueError):
    """The bytes carry the magic number of an image and are not a whole one."""


def _png(data: bytes) -> tuple[int, int]:
    if not data.startswith(_PNG_SIGNATURE):
        raise BrokenImage("not a PNG")
    position = len(_PNG_SIGNATURE)
    size: tuple[int, int] | None = None
    saw_data = False
    while position + 12 <= len(data):
        length = int.from_bytes(data[position : position + 4], "big")
        kind = data[position + 4 : position + 8]
        end = position + 12 + length
        if length > 0x7FFFFFFF or end > len(data) or not kind.isalpha():
            raise BrokenImage("PNG chunk is cut short")
        stored = int.from_bytes(data[position + 8 + length : end], "big")
        if zlib.crc32(data[position + 4 : position + 8 + length]) & 0xFFFFFFFF != stored:
            raise BrokenImage("PNG chunk fails its check")
        if size is None:
            if kind != b"IHDR" or length != 13:
                raise BrokenImage("PNG has no header")
            width, height, depth, color = struct.unpack(
                ">IIBB", data[position + 8 : position + 8 + 10]
            )
            if depth not in _PNG_COLOR_DEPTHS.get(color, set()):
                raise BrokenImage("PNG header is not valid")
            size = (width, height)
        elif kind == b"IDAT":
            saw_data = True
        elif kind == b"IEND":
            if not saw_data:
                raise BrokenImage("PNG has no pixel data")
            return size
        position = end
    raise BrokenImage("PNG has no end")


def _jpeg(data: bytes) -> tuple[int, int]:
    if not data.startswith(b"\xff\xd8\xff"):
        raise BrokenImage("not a JPEG")
    position = 2
    size: tuple[int, int] | None = None
    scan_at: int | None = None
    while position + 4 <= len(data):
        if data[position] != 0xFF:
            raise BrokenImage("JPEG segment is out of place")
        marker = data[position + 1]
        if marker == 0xFF:  # fill byte
            position += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:
            position += 2
            continue
        if marker == 0xD9:
            break
        length = int.from_bytes(data[position + 2 : position + 4], "big")
        if length < 2 or position + 2 + length > len(data):
            raise BrokenImage("JPEG segment is cut short")
        if marker in _JPEG_FRAME_MARKERS:
            if length < 8:
                raise BrokenImage("JPEG frame header is cut short")
            height, width = struct.unpack(">HH", data[position + 5 : position + 9])
            size = (width, height)
        if marker == 0xDA:
            scan_at = position
            break
        position += 2 + length
    if size is None or scan_at is None:
        raise BrokenImage("JPEG has no frame or scan")
    if data.rfind(b"\xff\xd9") < scan_at:
        raise BrokenImage("JPEG has no end")
    return size


def _webp(data: bytes) -> tuple[int, int]:
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise BrokenImage("not a WebP")
    if int.from_bytes(data[4:8], "little") + 8 > len(data):
        raise BrokenImage("WebP is cut short")
    kind = data[12:16]
    length = int.from_bytes(data[16:20], "little")
    body = data[20 : 20 + length]
    if len(body) != length:
        raise BrokenImage("WebP chunk is cut short")
    if kind == b"VP8 " and length >= 10 and body[3:6] == b"\x9d\x01\x2a":
        return (
            int.from_bytes(body[6:8], "little") & 0x3FFF,
            int.from_bytes(body[8:10], "little") & 0x3FFF,
        )
    if kind == b"VP8L" and length >= 5 and body[0] == 0x2F:
        bits = int.from_bytes(body[1:5], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if kind == b"VP8X" and length >= 10:
        return (
            int.from_bytes(body[4:7], "little") + 1,
            int.from_bytes(body[7:10], "little") + 1,
        )
    raise BrokenImage("WebP header is not valid")


_READERS = {"image/png": _png, "image/jpeg": _jpeg, "image/webp": _webp}


def image_size(data: bytes, mime: str) -> tuple[int, int]:
    """The (width, height) the image declares. Raises ``BrokenImage``."""
    reader = _READERS.get(mime)
    if reader is None:
        raise BrokenImage("not an image type")
    width, height = reader(data)
    if width < 1 or height < 1:
        raise BrokenImage("image has no pixels")
    return width, height
