from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ICON_ELEMENTS = (
    ("icp4", "icon_16x16.png", 16),
    ("ic11", "icon_16x16@2x.png", 32),
    ("icp5", "icon_32x32.png", 32),
    ("ic12", "icon_32x32@2x.png", 64),
    ("ic07", "icon_128x128.png", 128),
    ("ic13", "icon_128x128@2x.png", 256),
    ("ic08", "icon_256x256.png", 256),
    ("ic14", "icon_256x256@2x.png", 512),
    ("ic09", "icon_512x512.png", 512),
    ("ic10", "icon_512x512@2x.png", 1024),
)


def _read_png(path: Path, expected_pixels: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"invalid PNG file: {path.name}")
    data = path.read_bytes()
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError(f"invalid PNG: {path.name}")

    offset = len(PNG_SIGNATURE)
    saw_ihdr = False
    saw_iend = False
    idat_payloads: list[bytes] = []
    while offset < len(data):
        if len(data) - offset < 12:
            raise ValueError(f"truncated PNG chunk: {path.name}")
        chunk_length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk_end = offset + 12 + chunk_length
        if chunk_end > len(data):
            raise ValueError(f"truncated PNG chunk: {path.name}")
        chunk_payload = data[offset + 8 : offset + 8 + chunk_length]
        stored_crc = struct.unpack(
            ">I", data[offset + 8 + chunk_length : chunk_end]
        )[0]
        calculated_crc = zlib.crc32(chunk_type)
        calculated_crc = zlib.crc32(chunk_payload, calculated_crc) & 0xFFFFFFFF
        if stored_crc != calculated_crc:
            raise ValueError(f"invalid PNG checksum: {path.name}")

        if not saw_ihdr:
            if chunk_type != b"IHDR" or chunk_length != 13:
                raise ValueError(f"invalid PNG header: {path.name}")
            width, height, bit_depth, color_type, compression, filtering, interlace = (
                struct.unpack(">IIBBBBB", chunk_payload)
            )
            if (width, height) != (expected_pixels, expected_pixels):
                raise ValueError(
                    f"invalid PNG dimensions for {path.name}: {width}x{height}"
                )
            if (bit_depth, color_type, compression, filtering, interlace) != (
                8,
                6,
                0,
                0,
                0,
            ):
                raise ValueError(f"PNG must be non-interlaced 8-bit RGBA: {path.name}")
            saw_ihdr = True
        elif chunk_type == b"IHDR":
            raise ValueError(f"duplicate PNG header: {path.name}")
        elif chunk_type == b"IDAT":
            idat_payloads.append(chunk_payload)
        elif chunk_type == b"IEND":
            if chunk_length != 0 or chunk_end != len(data):
                raise ValueError(f"invalid PNG end chunk: {path.name}")
            saw_iend = True

        offset = chunk_end

    if not saw_ihdr or not idat_payloads or not saw_iend:
        raise ValueError(f"incomplete PNG: {path.name}")
    try:
        scanlines = zlib.decompress(b"".join(idat_payloads))
    except zlib.error as error:
        raise ValueError(f"invalid PNG image data: {path.name}") from error
    expected_scanline_length = expected_pixels * (1 + expected_pixels * 4)
    if len(scanlines) != expected_scanline_length:
        raise ValueError(f"invalid PNG image data length: {path.name}")
    row_length = 1 + expected_pixels * 4
    if any(scanlines[row * row_length] > 4 for row in range(expected_pixels)):
        raise ValueError(f"invalid PNG row filter: {path.name}")
    return data


def build_icns(iconset: Path, output: Path) -> None:
    if iconset.is_symlink() or not iconset.is_dir():
        raise ValueError("iconset must be a real directory")
    expected_names = {filename for _, filename, _ in ICON_ELEMENTS}
    actual_names = {item.name for item in iconset.iterdir()}
    if actual_names != expected_names:
        missing = sorted(expected_names - actual_names)
        extra = sorted(actual_names - expected_names)
        raise ValueError(f"invalid iconset files: missing={missing}, extra={extra}")

    elements = bytearray()
    for element_type, filename, expected_pixels in ICON_ELEMENTS:
        png = _read_png(iconset / filename, expected_pixels)
        elements.extend(element_type.encode("ascii"))
        elements.extend(struct.pack(">I", 8 + len(png)))
        elements.extend(png)

    payload = b"icns" + struct.pack(">I", 8 + len(elements)) + elements
    output.write_bytes(payload)


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: build_icns.py ICONSET_DIR OUTPUT.icns", file=sys.stderr)
        return 2
    try:
        build_icns(Path(sys.argv[1]), Path(sys.argv[2]))
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
