from __future__ import annotations

import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from scripts.build_icns import ICON_ELEMENTS, PNG_SIGNATURE, build_icns


def _chunk(chunk_type: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type)
    checksum = zlib.crc32(payload, checksum) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(payload))
        + chunk_type
        + payload
        + struct.pack(">I", checksum)
    )


def _rgba_png(pixels: int) -> bytes:
    header = struct.pack(">IIBBBBB", pixels, pixels, 8, 6, 0, 0, 0)
    scanlines = (b"\x00" + b"\x00" * (pixels * 4)) * pixels
    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(scanlines))
        + _chunk(b"IEND", b"")
    )


class BuildIcnsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(
            prefix="snotes-build-icns-", dir="/private/tmp"
        )
        self.root = Path(self.temporary_directory.name)
        self.iconset = self.root / "S-notes.iconset"
        self.iconset.mkdir()
        for _element_type, filename, pixels in ICON_ELEMENTS:
            (self.iconset / filename).write_bytes(_rgba_png(pixels))

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_standard_container_is_deterministic(self) -> None:
        first = self.root / "first.icns"
        second = self.root / "second.icns"
        build_icns(self.iconset, first)
        build_icns(self.iconset, second)
        data = first.read_bytes()
        self.assertEqual(data, second.read_bytes())
        self.assertEqual(data[:4], b"icns")
        self.assertEqual(struct.unpack(">I", data[4:8])[0], len(data))

        offset = 8
        element_types = []
        while offset < len(data):
            element_type = data[offset : offset + 4].decode("ascii")
            element_length = struct.unpack(">I", data[offset + 4 : offset + 8])[0]
            self.assertGreaterEqual(element_length, 8)
            self.assertLessEqual(offset + element_length, len(data))
            element_types.append(element_type)
            offset += element_length
        self.assertEqual(offset, len(data))
        self.assertEqual(element_types, [item[0] for item in ICON_ELEMENTS])

    def test_missing_or_extra_icon_fails_closed(self) -> None:
        missing = self.iconset / ICON_ELEMENTS[0][1]
        missing.unlink()
        with self.assertRaisesRegex(ValueError, "invalid iconset files"):
            build_icns(self.iconset, self.root / "missing.icns")
        missing.write_bytes(_rgba_png(ICON_ELEMENTS[0][2]))
        (self.iconset / ".DS_Store").write_bytes(b"unexpected")
        with self.assertRaisesRegex(ValueError, "invalid iconset files"):
            build_icns(self.iconset, self.root / "extra.icns")

    def test_wrong_dimensions_or_symlink_fails_closed(self) -> None:
        target = self.iconset / ICON_ELEMENTS[0][1]
        target.write_bytes(_rgba_png(17))
        with self.assertRaisesRegex(ValueError, "invalid PNG dimensions"):
            build_icns(self.iconset, self.root / "dimensions.icns")
        target.unlink()
        external = self.root / "external.png"
        external.write_bytes(_rgba_png(16))
        target.symlink_to(external)
        with self.assertRaisesRegex(ValueError, "invalid PNG file"):
            build_icns(self.iconset, self.root / "symlink.icns")

    def test_corrupt_checksum_or_truncation_fails_closed(self) -> None:
        target = self.iconset / ICON_ELEMENTS[0][1]
        corrupt = bytearray(target.read_bytes())
        corrupt[-1] ^= 0xFF
        target.write_bytes(corrupt)
        with self.assertRaisesRegex(ValueError, "invalid PNG checksum"):
            build_icns(self.iconset, self.root / "checksum.icns")
        target.write_bytes(_rgba_png(16)[:-3])
        with self.assertRaisesRegex(ValueError, "truncated PNG chunk"):
            build_icns(self.iconset, self.root / "truncated.icns")

    def test_invalid_compressed_image_data_fails_closed(self) -> None:
        target = self.iconset / ICON_ELEMENTS[0][1]
        header = struct.pack(">IIBBBBB", 16, 16, 8, 6, 0, 0, 0)
        target.write_bytes(
            PNG_SIGNATURE
            + _chunk(b"IHDR", header)
            + _chunk(b"IDAT", b"not-zlib-data")
            + _chunk(b"IEND", b"")
        )
        with self.assertRaisesRegex(ValueError, "invalid PNG image data"):
            build_icns(self.iconset, self.root / "compressed.icns")


if __name__ == "__main__":
    unittest.main()
