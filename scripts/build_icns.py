from __future__ import annotations

import struct
import sys
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
    data = path.read_bytes()
    if not data.startswith(PNG_SIGNATURE) or len(data) < 24:
        raise ValueError(f"invalid PNG: {path.name}")
    ihdr_length = struct.unpack(">I", data[8:12])[0]
    if data[12:16] != b"IHDR" or ihdr_length != 13:
        raise ValueError(f"invalid PNG header: {path.name}")
    width, height = struct.unpack(">II", data[16:24])
    if (width, height) != (expected_pixels, expected_pixels):
        raise ValueError(
            f"invalid PNG dimensions for {path.name}: {width}x{height}"
        )
    return data


def build_icns(iconset: Path, output: Path) -> None:
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
