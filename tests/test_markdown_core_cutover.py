"""Package 2 boundaries: all databases and rules are synthetic and temporary."""
from __future__ import annotations
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

from src.snotes.application.ai_safe_read_service import AiSafeReadService, AiSafeReadSource
from src.snotes.application.ai_actor_registry import AI_TARGET_COMMON_ID
from src.snotes.application.derived_text_cache import DerivedTextCache
from src.snotes.application.note_service import NoteService
from src.snotes.domain.markdown import markdown_to_plain_text
from src.snotes.exporting.ordinary import note_to_json, note_to_markdown
from src.snotes.migration.legacy_qt import legacy_qt_html_to_markdown
from src.snotes.storage.connection import SQLiteConnectionFactory
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.schema import initialize_database
from scripts.migrate_markdown_body import migrate

ROOT = Path(__file__).resolve().parents[1]
CASES = ['se**cr**et', '*sec*ret', ':size[secret]{size="20"}', '[secret](https://example.com)', '`secret`']


class MarkdownCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='snotes-markdown-core-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'synthetic.db'
        self.connections = initialize_database(self.db)
        self.notes = NoteService(SQLiteNotesRepository(self.connections, SQLiteDirectoriesRepository(self.connections)))

    def save(self, body, note=None, title='fixture'):
        note = note or self.notes.create_note()
        return self.notes.save_note(note.id, title=title, content_markdown=body, location=note.location, expected_revision=note.revision)

    def test_fresh_schema_single_authority(self):
        with self.connections.read() as connection:
            columns = {r['name']: r for r in connection.execute('PRAGMA table_info(notes)')}
            self.assertEqual(columns['content_markdown']['notnull'], 1)
            self.assertEqual(columns['content_markdown']['dflt_value'], "''")
            self.assertFalse({'content_html', 'body_rich_text', 'content_text'} & columns.keys())
            self.assertEqual(connection.execute("SELECT meta_value FROM schema_meta WHERE meta_key='body_format'").fetchone()[0], 'markdown')
        note = self.save('# Heading\n\n**body**')
        self.assertFalse(hasattr(note, 'content_html'))
        self.assertEqual(self.notes.get_note(note.id).content_markdown, '# Heading\n\n**body**')

    def test_marker_fail_closed_without_writes(self):
        for marker in (None, 'html'):
            with sqlite3.connect(self.db) as connection:
                connection.execute("DELETE FROM schema_meta WHERE meta_key='body_format'")
                if marker:
                    connection.execute("INSERT INTO schema_meta VALUES ('body_format', ?)", (marker,))
            before = self.db.read_bytes()
            with self.assertRaisesRegex(RuntimeError, 'Markdown migration'):
                initialize_database(self.db)
            with self.assertRaisesRegex(RuntimeError, 'Markdown migration'):
                with SQLiteConnectionFactory(self.db).read():
                    pass
            self.assertEqual(before, self.db.read_bytes())

    def test_plain_text_and_redaction_all_formats(self):
        rules = self.root / 'rules.json'
        rules.write_text(json.dumps({'secret': '[MASKED]'}))
        reader = AiSafeReadService(rules, body_renderer=markdown_to_plain_text)
        for body in CASES:
            with self.subTest(body=body):
                self.assertEqual(markdown_to_plain_text(body), 'secret')
                safe = reader.read_for_export_target(AI_TARGET_COMMON_ID, AiSafeReadSource(1, 'test', body, 'redacted', (AI_TARGET_COMMON_ID,)))
                self.assertEqual(safe.content, '[MASKED]')
                self.assertEqual(safe.redaction_count, 1)
        self.assertEqual(markdown_to_plain_text(':size[se**cr**et]{size="20"}'), 'secret')
        self.assertEqual(markdown_to_plain_text('`**secret**`'), '**secret**')
        self.assertEqual(markdown_to_plain_text('```\nsecret\n```'), 'secret')
        self.assertEqual(markdown_to_plain_text(':size[bad]{size="99"}'), ':size[bad]{size="99"}')
        self.assertEqual(markdown_to_plain_text('# Title\n\n- one\n- two'), 'Title\none\n\ntwo')

    def test_exports_and_cache_revision(self):
        note = self.save('# Heading\n\n:size[secret]{size="20"}')
        exported = note_to_json(note)
        self.assertEqual(exported['content_markdown'], note.content_markdown)
        self.assertEqual(exported['content_text'], 'Heading\nsecret')
        self.assertNotIn('content_html', exported)
        self.assertTrue(note_to_markdown(note).endswith(note.content_markdown))
        self.assertEqual([x.id for x in self.notes.search_notes('secret')], [note.id])
        self.notes.related_notes(note.id)
        self.assertEqual(self.notes.derived_text.parse_count, 1)
        changed = self.save('replacement', note)
        self.assertFalse(self.notes.search_notes('secret'))
        self.assertEqual(self.notes.derived_text.parse_count, 2)
        self.assertNotIn((note.id, note.revision), self.notes.derived_text._values)
        self.assertIn((changed.id, changed.revision), self.notes.derived_text._values)

    def test_mcp_markdown_writes_plain_reads_and_snapshot(self):
        from src.snotes.bootstrap import build_local_mcp_service
        service = build_local_mcp_service(actor_target_id='codex', resolved_data_root=self.root)
        with sqlite3.connect(self.root / 's_notes.db') as connection:
            connection.execute("INSERT INTO app_settings VALUES ('ai_modify_requires_confirmation', '0', '2026-01-01')")
        body = '# 标题\n\n这是 **重点**\n\n- item'
        result = service.create_note(request_id='md-create', title='markdown fixture', content=body, ai_visibility='full', ai_targets=['common'])
        self.assertEqual(result['status'], 'created')
        note_id = result['note_id']
        self.assertEqual(service._notes.get_note(note_id).content_markdown, body)
        read = service.get_note(note_id)
        self.assertEqual(read['content'], markdown_to_plain_text(body))
        addition = '## appended\n\n**second**'
        result = service.append_note(request_id='md-append', note_id=note_id, content=addition, expected_version=read['version'])
        self.assertEqual(result['status'], 'updated')
        self.assertEqual(service._notes.get_note(note_id).content_markdown, body + '\n\n' + addition)
        read = service.get_note(note_id)
        service.prepend_note(request_id='md-prepend', note_id=note_id, content='# first', expected_version=read['version'])
        self.assertTrue(service._notes.get_note(note_id).content_markdown.startswith('# first\n\n'))
        with sqlite3.connect(self.root / 's_notes.db') as connection:
            snapshot = json.loads(connection.execute('SELECT before_snapshot FROM ai_note_history ORDER BY id LIMIT 1').fetchone()[0])
        self.assertEqual(snapshot['content_markdown'], body)
        self.assertNotIn('content_html', snapshot)
        self.assertNotIn('body_rich_text', snapshot)

    def test_helper_bootstraps_reject_missing_marker(self):
        from src.snotes.bootstrap import compose_reminder_service, build_local_mcp_service
        from src.snotes.gale_bootstrap import build_gale_control_service
        path = self.root / 's_notes.db'
        initialize_database(path)
        with sqlite3.connect(path) as connection:
            connection.execute("DELETE FROM schema_meta WHERE meta_key='body_format'")
        before = path.read_bytes()
        calls = [lambda: compose_reminder_service(self.root),
                 lambda: build_local_mcp_service(actor_target_id='codex', resolved_data_root=self.root),
                 lambda: build_gale_control_service(data_root=self.root)]
        for call in calls:
            with self.assertRaisesRegex(RuntimeError, 'Markdown migration'):
                call()
        self.assertEqual(path.read_bytes(), before)

    def test_scale_cache_reuse(self):
        # 83 × ~30 KB; no production data or persisted derived text.
        body = ('alpha plain synthetic note body 中文\n' * 780)
        notes = [self.save(body + f'\n[[fixture {i % 3}]]', title=f'fixture {i}') for i in range(83)]
        print('SYNTHETIC_BODY_BYTES=' + str(sum(len(n.content_markdown.encode()) for n in notes)))
        def timed(label, action):
            start = time.perf_counter(); action()
            print(f'{label}={time.perf_counter() - start:.6f}s')
        self.notes.derived_text = DerivedTextCache()
        timed('COLD_SEARCH_TIME', lambda: self.notes.search_notes('alpha'))
        timed('WARM_SEARCH_TIME', lambda: self.notes.search_notes('alpha'))
        self.assertEqual(self.notes.derived_text.parse_count, 83)
        self.notes.derived_text = DerivedTextCache()
        timed('COLD_RELATIONS_TIME', lambda: self.notes.related_notes(notes[0].id))
        timed('WARM_RELATIONS_TIME', lambda: self.notes.related_notes(notes[0].id))
        self.assertEqual(self.notes.derived_text.parse_count, 83)


class LegacyMigrationTests(unittest.TestCase):
    def test_formats_and_known_losses(self):
        fixtures = {
            '<p><b> one </b>two</p>': '**one** two',
            '<p><b>one</b><strong>two</strong></p>': '**onetwo**',
            '<p><b><span style="font-weight:700">one</span></b></p>': '**one**',
            '<h1>one</h1><h2>two</h2><h3>three</h3>': '# one\n\n## two\n\n### three',
            '<p><b>bold</b> <i>italic</i> <s>strike</s></p>': '**bold** *italic* ~~strike~~',
            '<p><strong>b</strong> <em>i</em> <strike>s</strike></p>': '**b** *i* ~~s~~',
            '<p><span style="text-decoration: line-through">s</span></p>': '~~s~~',
            '<ul><li>one</li><li>two</li></ul>': '- one\n- two',
            '<ol><li>one</li><li>two</li></ol>': '1. one\n2. two',
            '<p><a href="https://example.com">link</a></p>': '[link](<https://example.com>)',
            '<p style="margin-left:24px;margin-top:4px;margin-bottom:4px">quote</p>': '> quote',
            '<p style="margin-left:24px;margin-top:8px;margin-bottom:8px">code</p>': '```\ncode\n```',
            '<p style="margin-left:16px;background-color:#eee">old code</p>': '```\nold code\n```',
            '<p><span style="font-size:15px">a</span><span style="font-size:17px">b</span><span style="font-size:20px">c</span></p>': 'ab:size[c]{size="20"}',
        }
        for source, expected in fixtures.items():
            with self.subTest(source=source):
                self.assertEqual(legacy_qt_html_to_markdown(source).markdown, expected)
        plain = ' first\nsecond\n\n\nlast\n'
        self.assertEqual(legacy_qt_html_to_markdown(plain).markdown, plain)
        result = legacy_qt_html_to_markdown('<p><u>under</u><span style="color:red">red</span></p>')
        self.assertEqual(result.known_losses, ('color: red', 'underline'))

    def test_cli_atomic_schema_marker_repeat_and_metadata(self):
        with tempfile.TemporaryDirectory(prefix='snotes-migration-') as home:
            path = Path(home) / 'legacy.db'
            # Build a synthetic full legacy schema, including foreign keys/audit.
            from src import database as frozen
            frozen.initialize_database(path)
            note = frozen.create_note_in_directory('临时', '无', '无', path)
            frozen.update_note(note['id'], title='fixture', content_html='<h1>Title</h1><p>body</p>', database_path=path)
            with sqlite3.connect(path) as connection:
                before = connection.execute('SELECT note_uuid, revision, created_at, updated_at FROM notes').fetchall()
                counts = {table: connection.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in ['notes','note_tags','note_ai_targets','ai_receipt_inbox']}
            with self.assertRaisesRegex(RuntimeError, 'Markdown migration'):
                initialize_database(path)
            run = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/migrate_markdown_body.py'), '--db', str(path)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout)['note_count'], 1)
            with sqlite3.connect(path) as connection:
                self.assertEqual(connection.execute('SELECT note_uuid, revision, created_at, updated_at FROM notes').fetchall(), before)
                self.assertEqual(connection.execute('SELECT content_markdown FROM notes').fetchone()[0], '# Title\n\nbody')
                for table, count in counts.items():
                    self.assertEqual(connection.execute(f'SELECT count(*) FROM {table}').fetchone()[0], count)
                self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            unchanged = path.read_bytes()
            with self.assertRaisesRegex(ValueError, 'repeat'):
                migrate(path)
            self.assertEqual(path.read_bytes(), unchanged)
            initialize_database(path)

    def test_transaction_rolls_back_on_schema_dependency(self):
        with tempfile.TemporaryDirectory() as home:
            path = Path(home) / 'legacy.db'
            with sqlite3.connect(path) as connection:
                connection.execute("CREATE TABLE notes(id INTEGER PRIMARY KEY, content_html TEXT, body_rich_text TEXT)")
                connection.execute("INSERT INTO notes VALUES (1, '<b>text</b>', '')")
                connection.execute('CREATE VIEW dependency AS SELECT content_html FROM notes')
            before = path.read_bytes()
            with self.assertRaises(sqlite3.OperationalError):
                migrate(path)
            self.assertEqual(path.read_bytes(), before)
