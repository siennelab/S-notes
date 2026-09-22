#!/usr/bin/env python3
"""Batch-check the App's bundled Milkdown (no DB access, no Node runtime).

Input JSON: [{"id": "any identifier", "markdown": "..."}, ...].
Output JSON: per-item first/second serialization and stability; nonzero on failure.
Uses an isolated temporary HOME and one offscreen WebEngine editor for the batch.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def check(items):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from src.snotes.ui.markdown_editor import MarkdownEditorWidget
    app = QApplication.instance() or QApplication(['markdown-roundtrip'])
    editor = MarkdownEditorWidget()
    editor.resize(900, 700)
    editor.show()
    results = []

    def wait(predicate):
        deadline = time.monotonic() + 30
        while not predicate():
            if time.monotonic() > deadline:
                raise TimeoutError(editor.error or 'Milkdown timed out')
            QTest.qWait(10)

    def load(body):
        editor.setMarkdown(body, uuid.uuid4().hex)
        wait(lambda: editor.state in {'loaded', 'failed'})
        if not editor.can_save:
            raise RuntimeError(editor.error)
        return editor.latest_markdown

    try:
        for item in items:
            try:
                first = load(item['markdown'])
                second = load(first)
                results.append({'id': item['id'], 'stable': first == second,
                                'first': first, 'second': second})
            except Exception as error:
                results.append({'id': item['id'], 'stable': False, 'error': str(error)})
    finally:
        done = []
        editor.closeReady.connect(lambda: done.append(True))
        editor.prepareClose()
        wait(lambda: bool(done))
        editor.close()
        editor.page.deleteLater()
        QTimer.singleShot(0, app.quit)
        app.exec()
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    items = json.loads(args.input.read_text(encoding='utf-8'))
    if not isinstance(items, list) or any(not isinstance(x, dict) or 'id' not in x or not isinstance(x.get('markdown'), str) for x in items):
        parser.error('Expected a list of {id, markdown} objects')
    with tempfile.TemporaryDirectory(prefix='snotes-roundtrip-') as home:
        os.environ['HOME'] = home
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')
        results = check(items)
    output = json.dumps(results, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(output + '\n', encoding='utf-8')
    else:
        print(output)
    return 0 if all(x['stable'] for x in results) else 1


if __name__ == '__main__':
    sys.exit(main())
