from __future__ import annotations

import json
import os
import plistlib
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from src.database import (
    create_gale_submission,
    create_note_in_directory,
    get_note,
    initialize_database,
    list_ai_receipt_inbox_items,
    reject_ai_receipt_inbox_item,
    save_editor_state,
)
from src.gale_production_background import (
    AUTO_PULL_SETTING,
    HELPER_FILENAME,
    HELPER_VERSION,
    HELPER_VERSION_TIMEOUT_SECONDS,
    LAUNCH_AGENT_INTERVAL_SECONDS,
    LAUNCH_AGENT_LABEL,
    AutoPullOutcome,
    BackgroundPullError,
    ProductionPullLock,
    auto_pull_enabled,
    background_paths,
    desired_launch_agent,
    ensure_background_installation,
    install_helper_atomic,
    install_launch_agent,
    load_background_state,
    read_helper_version,
    run_production_pull_once,
)
from src.gale_submission_pull import (
    PullResult,
    _create_gale_submission_with_retry,
)


class FakeSettings:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def value(self, key, default, *, type):
        if key != AUTO_PULL_SETTING:
            return default
        return type(self.enabled)


class FakeLaunchctl:
    def __init__(self, *, loaded: bool = False, fail_bootstrap: bool = False) -> None:
        self.loaded = loaded
        self.fail_bootstrap = fail_bootstrap
        self.calls: list[list[str]] = []

    def __call__(self, arguments):
        args = list(arguments)
        self.calls.append(args)
        if args[0] == "print":
            return subprocess.CompletedProcess(args, 0 if self.loaded else 113)
        if args[0] == "bootout":
            self.loaded = False
            return subprocess.CompletedProcess(args, 0)
        if args[0] == "bootstrap":
            if self.fail_bootstrap:
                return subprocess.CompletedProcess(args, 5)
            self.loaded = True
            return subprocess.CompletedProcess(args, 0)
        return subprocess.CompletedProcess(args, 1)


class PackageDAutoPullTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snotes-d1-auto-pull-", dir="/private/tmp"
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.paths = background_paths(self.home)

    @staticmethod
    def success_result(*, imported: int = 0) -> PullResult:
        return PullResult(
            exported_count=imported,
            imported_count=imported,
            acknowledged_count=imported,
            duplicate_count=0,
        )

    def test_auto_receive_default_key_and_disabled_mode_never_pulls(self) -> None:
        self.assertEqual(AUTO_PULL_SETTING, "gale/auto_pull_enabled")
        self.assertTrue(auto_pull_enabled(FakeSettings(True)))
        self.assertFalse(auto_pull_enabled(FakeSettings(False)))
        pull = Mock(return_value=self.success_result())
        outcome = run_production_pull_once(
            paths=self.paths,
            setting_reader=lambda: False,
            transport=object(),
            pull=pull,
        )
        self.assertEqual(outcome, AutoPullOutcome(status="disabled"))
        pull.assert_not_called()
        self.assertFalse(self.paths.state.exists())
        self.assertFalse(self.paths.log.exists())

    def test_reenabled_mode_runs_once_and_records_only_safe_state(self) -> None:
        pull = Mock(return_value=self.success_result(imported=2))
        outcome = run_production_pull_once(
            paths=self.paths,
            setting_reader=lambda: True,
            transport=object(),
            pull=pull,
        )
        self.assertEqual(outcome.status, "success")
        pull.assert_called_once()
        state = json.loads(self.paths.state.read_text(encoding="utf-8"))
        self.assertEqual(state["last_received_count"], 2)
        self.assertEqual(state["last_error_code"], "")
        self.assertEqual(set(state), {
            "background_version",
            "helper_version",
            "last_check_at",
            "last_error_code",
            "last_received_count",
            "last_success_at",
            "schema_version",
        })
        log_text = self.paths.log.read_text(encoding="utf-8")
        self.assertNotIn("title", log_text.casefold())
        self.assertNotIn("content", log_text.casefold())

    def test_empty_outbox_updates_state_without_log_noise(self) -> None:
        outcome = run_production_pull_once(
            paths=self.paths,
            setting_reader=lambda: True,
            transport=object(),
            pull=lambda _transport: self.success_result(),
        )
        self.assertEqual(outcome.status, "success")
        self.assertTrue(self.paths.state.is_file())
        self.assertFalse(self.paths.log.exists())

    def test_network_failure_records_only_error_code_and_does_not_raise(self) -> None:
        def fail(_transport):
            raise BackgroundPullError("should_be_wrapped")

        outcome = run_production_pull_once(
            paths=self.paths,
            setting_reader=lambda: True,
            transport=object(),
            pull=fail,
        )
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.error_code, "unexpected_failure")
        state = load_background_state(self.paths.state)
        self.assertEqual(state["last_error_code"], "unexpected_failure")

    def test_lock_contention_allows_exactly_one_pull(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def slow_pull(_transport):
            started.set()
            release.wait(5)
            return self.success_result()

        first_outcome: list[AutoPullOutcome] = []
        first = threading.Thread(
            target=lambda: first_outcome.append(
                run_production_pull_once(
                    paths=self.paths,
                    setting_reader=lambda: True,
                    transport=object(),
                    pull=slow_pull,
                )
            )
        )
        first.start()
        self.assertTrue(started.wait(2))
        second = run_production_pull_once(
            paths=self.paths,
            setting_reader=lambda: True,
            transport=object(),
            pull=lambda _transport: self.success_result(imported=1),
        )
        release.set()
        first.join(5)
        self.assertEqual(second.status, "locked")
        self.assertEqual(first_outcome[0].status, "success")

    def test_abnormal_process_exit_does_not_leave_stale_lock(self) -> None:
        script = (
            "import os; from pathlib import Path; "
            "from src.gale_production_background import ProductionPullLock; "
            f"lock=Path({str(self.paths.lock)!r}); "
            "guard=ProductionPullLock(lock); guard.__enter__(); os._exit(7)"
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            check=False,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        )
        self.assertEqual(completed.returncode, 7)
        with ProductionPullLock(self.paths.lock):
            pass

    def test_background_installation_has_its_own_cross_process_lock(self) -> None:
        with ProductionPullLock(self.paths.install_lock):
            with self.assertRaises(BackgroundPullError) as caught:
                ensure_background_installation(
                    source=self.root / "not-reached",
                    paths=self.paths,
                    manage_launch_agent=False,
                )
        self.assertEqual(caught.exception.code, "background_install_running")

    def test_sqlite_busy_is_retried_but_other_errors_are_not(self) -> None:
        calls = 0

        def flaky(**kwargs):
            nonlocal calls
            calls += 1
            if calls < 3:
                raise sqlite3.OperationalError("database is locked")
            return {"id": 1}

        submission = {
            "external_submission_id": "external",
            "idempotency_key": "stable",
            "title": "fictional",
            "content": "fictional body",
            "main_category": "临时",
            "sub_category": "Gale 投稿",
            "sub_sub_category": "待确认",
            "suggested_tags": [],
        }
        with patch("src.gale_submission_pull.time.sleep") as sleep:
            receipt = _create_gale_submission_with_retry(
                flaky,
                submission=submission,
                database_path=self.root / "fictional.db",
            )
        self.assertEqual(receipt, {"id": 1})
        self.assertEqual(calls, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_launch_agent_has_stable_absolute_target_and_required_schedule(self) -> None:
        value = desired_launch_agent(self.paths)
        self.assertEqual(value["Label"], LAUNCH_AGENT_LABEL)
        self.assertEqual(value["ProgramArguments"], [str(self.paths.helper)])
        self.assertTrue(value["RunAtLoad"])
        self.assertEqual(value["StartInterval"], LAUNCH_AGENT_INTERVAL_SECONDS)
        self.assertFalse(value["KeepAlive"])
        serialized = plistlib.dumps(value).decode("utf-8")
        for forbidden in (".venv", "/dist/", "/Applications/", "Documents/"):
            self.assertNotIn(forbidden, serialized)

    def test_launch_agent_is_not_reloaded_when_current(self) -> None:
        self.paths.launch_agents_dir.mkdir(parents=True)
        self.paths.plist.write_bytes(
            plistlib.dumps(desired_launch_agent(self.paths), sort_keys=True)
        )
        runner = FakeLaunchctl(loaded=True)
        updated, loaded = install_launch_agent(self.paths, runner=runner)
        self.assertFalse(updated)
        self.assertTrue(loaded)
        self.assertEqual([call[0] for call in runner.calls], ["print"])

    def test_launch_agent_bootstrap_failure_restores_previous_plist(self) -> None:
        self.paths.launch_agents_dir.mkdir(parents=True)
        prior = plistlib.dumps({"Label": LAUNCH_AGENT_LABEL, "Old": True})
        self.paths.plist.write_bytes(prior)
        runner = FakeLaunchctl(loaded=True, fail_bootstrap=True)
        with self.assertRaisesRegex(
            BackgroundPullError, "launch_agent_bootstrap_failed"
        ):
            install_launch_agent(self.paths, runner=runner)
        self.assertEqual(self.paths.plist.read_bytes(), prior)

    def test_atomic_helper_update_verifies_hash_and_preserves_old_on_failure(self) -> None:
        source = self.root / "source-helper"
        destination = self.paths.helper
        source.write_bytes(b"new fictional executable")
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"old working executable")

        def version_reader(_path):
            return HELPER_VERSION

        def architecture_validator(_path):
            return None

        def fail_replace(_source, _destination):
            raise OSError("fictional atomic replace failure")

        with self.assertRaisesRegex(OSError, "fictional atomic replace failure"):
            install_helper_atomic(
                source,
                destination,
                replace=fail_replace,
                version_reader=version_reader,
                architecture_validator=architecture_validator,
            )
        self.assertEqual(destination.read_bytes(), b"old working executable")
        leftovers = list(destination.parent.glob(f".{HELPER_FILENAME}.*"))
        self.assertEqual(leftovers, [])

    def test_atomic_helper_update_skips_identical_version_and_hash(self) -> None:
        source = self.root / "source-helper"
        destination = self.paths.helper
        source.write_bytes(b"same fictional executable")
        destination.parent.mkdir(parents=True)
        destination.write_bytes(source.read_bytes())
        result = install_helper_atomic(
            source,
            destination,
            version_reader=lambda _path: HELPER_VERSION,
            architecture_validator=lambda _path: None,
        )
        self.assertFalse(result[0])
        self.assertEqual(result[2], HELPER_VERSION)

    def test_helper_version_check_allows_slow_first_onefile_start(self) -> None:
        completed = subprocess.CompletedProcess(
            ["helper", "--version"],
            0,
            stdout=f"{HELPER_FILENAME} {HELPER_VERSION}\n".encode(),
        )
        with patch(
            "src.gale_production_background.subprocess.run",
            return_value=completed,
        ) as run:
            self.assertEqual(read_helper_version(self.root / "helper"), HELPER_VERSION)
        self.assertEqual(
            run.call_args.kwargs["timeout"],
            HELPER_VERSION_TIMEOUT_SECONDS,
        )
        self.assertGreaterEqual(HELPER_VERSION_TIMEOUT_SECONDS, 30)

    def test_gui_save_and_background_pending_import_are_concurrent_safe(self) -> None:
        database = self.root / "concurrent.db"
        initialize_database(database)
        note = create_note_in_directory("并发虚构笔记", database_path=database)
        before_updated_at = note["updated_at"]
        barrier = threading.Barrier(2)

        def save_note():
            barrier.wait()
            return save_editor_state(
                note["id"],
                title="并发虚构笔记（已保存）",
                content_html="<p>fictional editor body</p>",
                main_category=note["main_category"],
                sub_category=note["sub_category"],
                sub_sub_category=note["sub_sub_category"],
                tags=[],
                ai_visibility="禁止 AI 读取",
                ai_targets=[],
                database_path=database,
            )

        def import_pending():
            barrier.wait()
            return create_gale_submission(
                external_submission_id="00000000-0000-4000-8000-000000000001",
                idempotency_key="fictional-concurrent-import",
                title="虚构并发投稿",
                body="fictional pending body",
                database_path=database,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            saved_future = executor.submit(save_note)
            pending_future = executor.submit(import_pending)
            saved = saved_future.result(timeout=10)
            pending = pending_future.result(timeout=10)
        self.assertGreaterEqual(saved["updated_at"], before_updated_at)
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(
            get_note(note["id"], database)["title"],
            "并发虚构笔记（已保存）",
        )

    def test_accept_or_discard_and_next_import_remain_independent(self) -> None:
        database = self.root / "decisions.db"
        initialize_database(database)
        first = create_gale_submission(
            external_submission_id="00000000-0000-4000-8000-000000000010",
            idempotency_key="fictional-discard",
            title="虚构待丢弃投稿",
            body="fictional body",
            database_path=database,
        )
        reject_ai_receipt_inbox_item(first["id"], database)
        second = create_gale_submission(
            external_submission_id="00000000-0000-4000-8000-000000000011",
            idempotency_key="fictional-next-import",
            title="虚构下一条投稿",
            body="fictional body",
            database_path=database,
        )
        statuses = {
            item["external_submission_id"]: item["status"]
            for item in list_ai_receipt_inbox_items(status=None, database_path=database)
        }
        self.assertEqual(statuses[first["external_submission_id"]], "discarded")
        self.assertEqual(statuses[second["external_submission_id"]], "pending")


if __name__ == "__main__":
    unittest.main()
