"""Refactored runtime dependencies must travel with the existing source packages."""
from pathlib import Path
import unittest

from scripts.package_greencloud_read import source_closure


ROOT = Path(__file__).resolve().parents[1]


class RemoteRuntimeClosureTests(unittest.TestCase):
    def test_reader_package_includes_application_boundary(self):
        files = source_closure(["src.remote_reader"])
        self.assertIn(ROOT / "src/snotes/application/reader_service.py", files)
        self.assertIn(ROOT / "src/snotes/storage/cloud_read.py", files)

    def test_auth_shared_support_is_in_source_closure_and_install_copy_lists(self):
        support = "src/snotes/transport/mcp/remote_auth_support.py"
        for entry, installer in (
            ("src.greencloud_remote_mcp", "gale-remote"),
            ("src.claude_web_remote_mcp", "claude-web-remote"),
        ):
            with self.subTest(entry=entry):
                self.assertIn(ROOT / support, source_closure([entry]))
                script = (ROOT / "deploy/greencloud" / installer / "install.sh").read_text()
                copy_list = script.split("for file in src/", 1)[1].split("\ndo", 1)[0]
                self.assertIn(support, copy_list.split())


if __name__ == "__main__":
    unittest.main()
