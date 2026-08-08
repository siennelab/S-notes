from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FROZEN_R1_ROOT = Path("/Users/s/Documents/项目/S-notes")
PROBE = r'''
import importlib
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QMimeData
from PySide6.QtWidgets import QApplication

editor_type = importlib.import_module(sys.argv[1]).NotesEditor
app = QApplication.instance() or QApplication([])
editor = editor_type()
mime = QMimeData()
mime.setText("BOLD ITALIC STRIKE PLAIN")
mime.setHtml(
    '<p><span style="font-size:30px"><b>BOLD</b></span> '
    '<span style="font-size:9px"><i>ITALIC</i></span> '
    '<span style="font-size:42px"><s>STRIKE</s></span> '
    '<span style="font-size:18px">PLAIN</span></p>'
)
editor.insertFromMimeData(mime)
records = []
block = editor.document().begin()
while block.isValid():
    iterator = block.begin()
    while not iterator.atEnd():
        fragment = iterator.fragment()
        text = fragment.text().strip()
        if fragment.isValid() and text:
            char_format = fragment.charFormat()
            records.append(
                {
                    "text": text,
                    "pixel_size": char_format.font().pixelSize(),
                    "bold": char_format.fontWeight() > 400,
                    "italic": char_format.fontItalic(),
                    "strike": char_format.fontStrikeOut(),
                }
            )
        iterator += 1
    block = block.next()
print(json.dumps({"plain": editor.toPlainText(), "records": records}, sort_keys=True))
editor.close()
'''


class RichPasteR1ParityTests(unittest.TestCase):
    def run_probe(self, root: Path, module_name: str) -> dict:
        with tempfile.TemporaryDirectory(prefix="snotes-rich-paste-parity-") as temp:
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": temp,
                    "S_NOTES_DATA_ROOT": str(Path(temp) / "data"),
                    "QT_QPA_PLATFORM": "offscreen",
                    "PYTHONPATH": str(root),
                    "PYTHONPYCACHEPREFIX": str(Path(temp) / "pycache"),
                }
            )
            result = subprocess.run(
                [sys.executable, "-c", PROBE, module_name],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_external_font_normalization_matches_frozen_r1(self) -> None:
        r1 = self.run_probe(FROZEN_R1_ROOT, "src.editor")
        r2 = self.run_probe(PROJECT_ROOT, "src.snotes.ui.editor")
        self.assertEqual(r2, r1)
        self.assertEqual(r2["plain"], "BOLD ITALIC STRIKE PLAIN")
        records = {record["text"]: record for record in r2["records"]}
        self.assertTrue(records["BOLD"]["bold"])
        self.assertTrue(records["ITALIC"]["italic"])
        self.assertTrue(records["STRIKE"]["strike"])
        self.assertEqual({record["pixel_size"] for record in records.values()}, {15})


if __name__ == "__main__":
    unittest.main()
