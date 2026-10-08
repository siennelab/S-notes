from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from src.snotes.ui.ai_center_dialog import (
    _default_actor_mirror_state_path,
    _greencloud_publish_status_text,
)


class AiCenterGreenCloudStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snotes-greencloud-status-"
        )
        self.addCleanup(self.temporary.cleanup)
        self.state_path = Path(self.temporary.name) / "actor-state.json"

    def _write_state(self, gale_state: object) -> None:
        self.state_path.write_text(
            json.dumps(
                {"schema_version": 1, "actors": {"gale": gale_state}}
            ),
            encoding="utf-8",
        )

    def test_reads_gale_success_time_and_formats_it_in_local_time(self) -> None:
        timestamp = "2026-08-10T12:34:00+00:00"
        self._write_state({"last_success_time": timestamp})

        expected = datetime.fromisoformat(timestamp).astimezone().strftime(
            "%Y-%m-%d %H:%M"
        )
        self.assertEqual(
            _greencloud_publish_status_text(self.state_path), expected
        )

    def test_default_path_uses_current_home_without_creating_files(self) -> None:
        home = Path(self.temporary.name) / "future-home"

        with patch.dict("os.environ", {"HOME": str(home)}, clear=True):
            selected = _default_actor_mirror_state_path()

        self.assertEqual(
            selected,
            home
            / "Library"
            / "Application Support"
            / "S-notes"
            / "config"
            / "actor_mirror_publish_state.json",
        )
        self.assertFalse(home.exists())

    def test_default_path_uses_current_override_without_write_probe(self) -> None:
        root = Path(self.temporary.name) / "future-data-root"

        with patch.dict(
            "os.environ",
            {
                "HOME": str(Path(self.temporary.name) / "unused-home"),
                "S_NOTES_DATA_ROOT": str(root),
            },
            clear=True,
        ):
            selected = _default_actor_mirror_state_path()

        self.assertEqual(
            selected,
            root / "config" / "actor_mirror_publish_state.json",
        )
        self.assertFalse(root.exists())

    def test_distinguishes_missing_file_from_no_publish_record(self) -> None:
        self.assertEqual(
            _greencloud_publish_status_text(self.state_path),
            "状态文件不存在",
        )

        self._write_state({})
        self.assertEqual(
            _greencloud_publish_status_text(self.state_path),
            "尚无发布记录",
        )

    def test_reports_read_failure_without_creating_state(self) -> None:
        self.state_path.mkdir()

        self.assertEqual(
            _greencloud_publish_status_text(self.state_path),
            "状态读取失败",
        )
        self.assertTrue(self.state_path.is_dir())

    def test_reports_invalid_json_as_read_failure(self) -> None:
        self.state_path.write_text("{broken", encoding="utf-8")

        self.assertEqual(
            _greencloud_publish_status_text(self.state_path),
            "状态读取失败",
        )

    def test_reports_invalid_timestamp(self) -> None:
        self._write_state({"last_success_time": "not-a-timestamp"})

        self.assertEqual(
            _greencloud_publish_status_text(self.state_path),
            "时间记录无效",
        )


if __name__ == "__main__":
    unittest.main()
