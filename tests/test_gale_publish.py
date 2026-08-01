from __future__ import annotations

import contextlib
import fcntl
import inspect
import io
import json
import logging
import os
import shutil
import sqlite3
import stat
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from src.services.gale_mirror_service import (
    GaleMirrorError,
    export_gale_mirror,
    search_gale_mirror,
    verify_gale_mirror,
)
from src.services.gale_publish_service import (
    GaleAutoPublishScheduler,
    GalePublishConfig,
    GalePublishError,
    RemoteActivationConfig,
    RemoteActivationResult,
    activate_remote_mirror,
    generate_validate_and_publish_gale_mirror,
    load_publish_state,
    operator_rollback_remote_mirror,
    publish_gale_mirror,
    sha256_file,
    source_inputs_sha256,
    validate_activated_mirror,
)
from tests_mcp.prepare_gale_remote_fixture import (
    _create_note,
    main as prepare_fixture,
)


class RecordingTransport:
    def __init__(
        self,
        *,
        activation: RemoteActivationResult | None = None,
        upload_error: str = "",
    ) -> None:
        self.activation = activation
        self.upload_error = upload_error
        self.upload_calls = 0
        self.activation_calls = 0

    def upload(self, mirror_path: Path, sha256: str) -> None:
        self.upload_calls += 1
        if self.upload_error:
            raise GalePublishError(self.upload_error)

    def activate(self, sha256: str, note_count: int) -> RemoteActivationResult:
        self.activation_calls += 1
        return self.activation or RemoteActivationResult(
            "success",
            "none",
            sha256,
            note_count,
        )


class BlockingTransport(RecordingTransport):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def upload(self, mirror_path: Path, sha256: str) -> None:
        self.upload_calls += 1
        self.entered.set()
        self.release.wait(timeout=5)


class GalePublishTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        fixture_root = self.root / "fixture"
        fixture_root.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                prepare_fixture(["--output-dir", str(fixture_root)]),
                0,
            )
        self.source = fixture_root / "fictional-source.db"
        self.rules = fixture_root / "fictional-rules.json"
        self.mirror_v1 = fixture_root / "gale-mirror.db"
        self.mirror_v2 = self.root / "gale-mirror-v2.db"
        _create_note(
            self.source,
            title="FICTIONAL_PACKAGE_C_NEW",
            body="FICTIONAL_PACKAGE_C_SEARCH",
            directory=("FICTIONAL_PACKAGE_C_DIRECTORY", "无", "无"),
            tags=["FICTIONAL_PACKAGE_C_TAG"],
            visibility="full",
            targets=["gale"],
        )
        self.export_v2 = export_gale_mirror(
            source_database=self.source,
            output_path=self.mirror_v2,
            redaction_rules_path=self.rules,
            generator_app_version="Function Package C fictional v2",
        )
        self.publish_config = GalePublishConfig(
            mirror_path=self.mirror_v2,
            state_path=self.root / "state" / "publish.json",
            lock_path=self.root / "state" / "publish.lock",
        )

    def remote_layout(
        self,
        *,
        current_source: Path | None = None,
    ) -> RemoteActivationConfig:
        remote_root = self.root / "remote"
        incoming = remote_root / "incoming"
        current = remote_root / "current"
        previous = remote_root / "previous"
        for directory in (incoming, current, previous):
            directory.mkdir(parents=True, exist_ok=True)
        current_mirror = current / "gale-mirror.db"
        shutil.copy2(current_source or self.mirror_v1, current_mirror)
        os.chmod(current_mirror, 0o600)
        return RemoteActivationConfig(
            incoming_dir=incoming,
            current_dir=current,
            previous_dir=previous,
            current_mirror=current_mirror,
            previous_mirror=previous / "gale-mirror.db",
            lock_path=remote_root / "publish.lock",
            service_name="snotes-gale-mcp-oidc-staging.service",
            owner_uid=os.geteuid(),
            owner_gid=os.getegid(),
        )

    def stage_candidate(
        self,
        config: RemoteActivationConfig,
        source: Path | None = None,
    ) -> tuple[str, int, Path]:
        mirror = source or self.mirror_v2
        digest = sha256_file(mirror)
        candidate = config.incoming_dir / f"{digest}.db"
        shutil.copy2(mirror, candidate)
        os.chmod(candidate, 0o600)
        note_count = (
            self.export_v2.note_count
            if mirror == self.mirror_v2
            else 3
        )
        return digest, note_count, candidate

    def rollback_layout(
        self,
    ) -> tuple[RemoteActivationConfig, str, str]:
        config = self.remote_layout(current_source=self.mirror_v2)
        shutil.copy2(self.mirror_v1, config.previous_mirror)
        os.chmod(config.previous_mirror, 0o600)
        return (
            config,
            sha256_file(config.current_mirror),
            sha256_file(config.previous_mirror),
        )

    def run_operator_rollback(
        self,
        config: RemoteActivationConfig,
        current_hash: str,
        previous_hash: str,
        **overrides,
    ):
        options = {
            "service_is_active": lambda _name: True,
            "stop_service": lambda _name: True,
            "start_service": lambda _name: True,
            "service_probe": self.probe,
            "event_logger": lambda *_args: None,
        }
        options.update(overrides)
        return operator_rollback_remote_mirror(
            config,
            expected_current_sha256=current_hash,
            expected_previous_sha256=previous_hash,
            **options,
        )

    @staticmethod
    def probe(
        mirror: Path,
        root: Path,
        digest: str,
        note_count: int,
    ) -> bool:
        try:
            validate_activated_mirror(
                mirror,
                root,
                expected_sha256=digest,
                expected_note_count=note_count,
            )
        except Exception:
            return False
        return True

    def test_fictional_generation_local_validation_upload_and_state(self) -> None:
        transport = RecordingTransport()
        result = publish_gale_mirror(
            self.publish_config,
            transport=transport,
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(result.note_count, self.export_v2.note_count)
        self.assertEqual(transport.upload_calls, 1)
        self.assertEqual(transport.activation_calls, 1)
        state = load_publish_state(self.publish_config.state_path)
        self.assertFalse(state.is_publishing)
        self.assertEqual(state.last_success_hash, result.sha256)
        self.assertFalse(state.needs_retry)
        self.assertEqual(
            stat.S_IMODE(self.publish_config.state_path.stat().st_mode),
            0o600,
        )

    def test_local_validation_failure_forbids_upload(self) -> None:
        invalid = self.root / "invalid.db"
        invalid.write_bytes(b"not sqlite")
        os.chmod(invalid, 0o600)
        transport = RecordingTransport()
        config = GalePublishConfig(
            invalid,
            self.root / "invalid-state.json",
            self.root / "invalid.lock",
        )

        with self.assertRaisesRegex(
            GalePublishError,
            "local_validation_failed",
        ):
            publish_gale_mirror(config, transport=transport)

        self.assertEqual(transport.upload_calls, 0)
        state = load_publish_state(config.state_path)
        self.assertTrue(state.needs_retry)
        self.assertEqual(state.last_failure_reason, "local_validation_failed")
        self.assertEqual(state.last_failure_check, "mirror_validation_failed")

    def test_missing_local_mirror_is_generated_validated_then_published(self) -> None:
        runtime = self.root / "runtime" / "exports" / "gale"
        config = GalePublishConfig(
            runtime / "gale-mirror.db",
            self.root / "runtime" / "config" / "publish-state.json",
            self.root / "runtime" / "config" / "publish.lock",
        )
        transport = RecordingTransport()

        result = generate_validate_and_publish_gale_mirror(
            config,
            source_database=self.source,
            redaction_rules_path=self.rules,
            transport=transport,
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(result.source_sha256, source_inputs_sha256(self.source, self.rules))
        self.assertEqual(transport.upload_calls, 1)
        self.assertEqual(transport.activation_calls, 1)
        self.assertEqual(stat.S_IMODE(runtime.stat().st_mode), 0o700)
        self.assertTrue(config.mirror_path.is_file())
        self.assertEqual(stat.S_IMODE(config.mirror_path.stat().st_mode), 0o600)
        verification = verify_gale_mirror(config.mirror_path)
        self.assertEqual(verification.note_count, result.note_count)
        self.assertTrue(
            search_gale_mirror(
                config.mirror_path,
                "FICTIONAL_PACKAGE_C_SEARCH",
            )
        )
        self.assertEqual(list(runtime.glob(".gale-source-snapshot.*")), [])
        self.assertEqual(list(runtime.glob(".gale-publish-candidate.*")), [])

    def test_generation_handles_unicode_rich_text_revision_and_deep_directory(
        self,
    ) -> None:
        note_id = _create_note(
            self.source,
            title="FICTIONAL_VMATRIX × 2026-08-01",
            body="FICTIONAL_RICH_PLACEHOLDER",
            directory=(
                "FICTIONAL_一级",
                "FICTIONAL_二级",
                "FICTIONAL_三级",
            ),
            tags=[],
            visibility="full",
            targets=["gale"],
        )
        long_text = "FICTIONAL_LONG_RICH_段落" * 300
        rich_html = (
            "<h2>FICTIONAL_RICH_HEADING</h2>"
            f"<p><strong>{long_text}</strong></p>"
        )
        updated_at = "2026-08-01T23:59:58+08:00"
        with sqlite3.connect(self.source) as connection:
            connection.execute(
                """
                UPDATE notes
                SET body_rich_text=?, content_html=?, revision=?, updated_at=?
                WHERE id=?
                """,
                (rich_html, rich_html, 17, updated_at, note_id),
            )

        runtime = self.root / "runtime-complex" / "exports" / "gale"
        config = GalePublishConfig(
            runtime / "gale-mirror.db",
            self.root / "runtime-complex" / "config" / "publish-state.json",
            self.root / "runtime-complex" / "config" / "publish.lock",
        )
        result = generate_validate_and_publish_gale_mirror(
            config,
            source_database=self.source,
            redaction_rules_path=self.rules,
            transport=RecordingTransport(),
        )

        verification = verify_gale_mirror(config.mirror_path)
        self.assertEqual(result.status, "success")
        self.assertEqual(verification.note_count, result.note_count)
        self.assertEqual(verification.fts_count, result.note_count)
        with sqlite3.connect(
            f"{config.mirror_path.resolve().as_uri()}?mode=ro",
            uri=True,
        ) as connection:
            row = connection.execute(
                """
                SELECT title, body, directory, tags_json, source_updated_at
                FROM mirror_notes
                WHERE source_note_id=?
                """,
                (note_id,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "FICTIONAL_VMATRIX × 2026-08-01")
            self.assertIn("FICTIONAL_RICH_HEADING", row[1])
            self.assertIn("FICTIONAL_LONG_RICH_段落", row[1])
            self.assertNotIn("<strong>", row[1])
            self.assertEqual(
                row[2],
                "FICTIONAL_一级 / FICTIONAL_二级 / FICTIONAL_三级",
            )
            self.assertEqual(json.loads(row[3]), [])
            self.assertEqual(row[4], updated_at)
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertIsNone(connection.execute("PRAGMA foreign_key_check").fetchone())
        self.assertEqual(list(runtime.glob(".gale-source-snapshot.*")), [])
        self.assertEqual(list(runtime.glob(".gale-publish-candidate.*")), [])

    def test_generation_validation_failure_preserves_old_local_mirror(self) -> None:
        runtime = self.root / "runtime-failure" / "exports" / "gale"
        runtime.mkdir(parents=True)
        mirror = runtime / "gale-mirror.db"
        shutil.copy2(self.mirror_v1, mirror)
        os.chmod(mirror, 0o600)
        old_hash = sha256_file(mirror)
        config = GalePublishConfig(
            mirror,
            self.root / "runtime-failure" / "config" / "publish-state.json",
            self.root / "runtime-failure" / "config" / "publish.lock",
        )
        transport = RecordingTransport()

        with patch(
            "src.services.gale_publish_service.export_gale_mirror",
            side_effect=GaleMirrorError("fts_content_mismatch"),
        ), self.assertRaises(GalePublishError) as raised:
            generate_validate_and_publish_gale_mirror(
                config,
                source_database=self.source,
                redaction_rules_path=self.rules,
                transport=transport,
            )

        self.assertEqual(raised.exception.code, "local_validation_failed")
        self.assertEqual(raised.exception.check, "fts_content_mismatch")
        self.assertEqual(sha256_file(mirror), old_hash)
        self.assertEqual(transport.upload_calls, 0)
        self.assertEqual(transport.activation_calls, 0)
        state = load_publish_state(config.state_path)
        self.assertEqual(state.last_failure_reason, "local_validation_failed")
        self.assertEqual(state.last_failure_check, "fts_content_mismatch")
        self.assertTrue(state.needs_retry)
        self.assertEqual(list(runtime.glob(".gale-source-snapshot.*")), [])
        self.assertEqual(list(runtime.glob(".gale-publish-candidate.*")), [])

    def test_remote_hash_mismatch_removes_incoming_and_preserves_current(self) -> None:
        config = self.remote_layout()
        old_hash = sha256_file(config.current_mirror)
        digest, count, candidate = self.stage_candidate(
            config,
            self.mirror_v1,
        )
        wrong_digest = "0" * 64
        wrong_candidate = config.incoming_dir / f"{wrong_digest}.db"
        candidate.replace(wrong_candidate)

        result = activate_remote_mirror(
            config,
            expected_sha256=wrong_digest,
            expected_note_count=count,
            restart_service=lambda _name: self.fail("restart called"),
            service_probe=self.probe,
            event_logger=lambda *_args: None,
        )

        self.assertEqual(result.reason, "remote_validation_failed")
        self.assertFalse(wrong_candidate.exists())
        self.assertEqual(sha256_file(config.current_mirror), old_hash)

    def test_corrupt_incoming_does_not_change_current(self) -> None:
        config = self.remote_layout()
        old_hash = sha256_file(config.current_mirror)
        digest = sha256_file(self.mirror_v2)
        candidate = config.incoming_dir / f"{digest}.db"
        candidate.write_bytes(b"corrupt fictional database")
        os.chmod(candidate, 0o600)

        result = activate_remote_mirror(
            config,
            expected_sha256=digest,
            expected_note_count=self.export_v2.note_count,
            restart_service=lambda _name: self.fail("restart called"),
            service_probe=self.probe,
            event_logger=lambda *_args: None,
        )

        self.assertEqual(result.reason, "remote_validation_failed")
        self.assertFalse(candidate.exists())
        self.assertEqual(sha256_file(config.current_mirror), old_hash)

    def test_success_updates_current_and_preserves_previous(self) -> None:
        config = self.remote_layout()
        old_hash = sha256_file(config.current_mirror)
        digest, count, _candidate = self.stage_candidate(config)

        result = activate_remote_mirror(
            config,
            expected_sha256=digest,
            expected_note_count=count,
            restart_service=lambda _name: True,
            service_probe=self.probe,
            event_logger=lambda *_args: None,
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(sha256_file(config.current_mirror), digest)
        self.assertEqual(sha256_file(config.previous_mirror), old_hash)
        self.assertEqual(
            search_gale_mirror(
                config.current_mirror,
                "FICTIONAL_PACKAGE_C_SEARCH",
            )[0]["title"],
            "FICTIONAL_PACKAGE_C_NEW",
        )

    def test_start_failure_restores_previous_and_old_search(self) -> None:
        config = self.remote_layout()
        old_hash = sha256_file(config.current_mirror)
        digest, count, _candidate = self.stage_candidate(config)
        restart_results = iter((False, True))

        result = activate_remote_mirror(
            config,
            expected_sha256=digest,
            expected_note_count=count,
            restart_service=lambda _name: next(restart_results),
            service_probe=self.probe,
            event_logger=lambda *_args: None,
        )

        self.assertEqual(result.reason, "activation_failed_rolled_back")
        self.assertTrue(result.rolled_back)
        self.assertEqual(sha256_file(config.current_mirror), old_hash)
        self.assertTrue(
            search_gale_mirror(
                config.current_mirror,
                "FICTIONAL_COBALT_SEARCH",
            )
        )
        self.assertFalse(
            search_gale_mirror(
                config.current_mirror,
                "FICTIONAL_PACKAGE_C_SEARCH",
            )
        )

    def test_probe_exception_still_restores_previous(self) -> None:
        config = self.remote_layout()
        old_hash = sha256_file(config.current_mirror)
        digest, count, _candidate = self.stage_candidate(config)
        probe_calls = 0

        def probe_with_first_failure(
            mirror: Path,
            root: Path,
            expected_hash: str,
            expected_count: int,
        ) -> bool:
            nonlocal probe_calls
            probe_calls += 1
            if probe_calls == 1:
                raise RuntimeError("fictional probe failure")
            return self.probe(
                mirror,
                root,
                expected_hash,
                expected_count,
            )

        result = activate_remote_mirror(
            config,
            expected_sha256=digest,
            expected_note_count=count,
            restart_service=lambda _name: True,
            service_probe=probe_with_first_failure,
            event_logger=lambda *_args: None,
        )

        self.assertEqual(result.reason, "activation_failed_rolled_back")
        self.assertEqual(sha256_file(config.current_mirror), old_hash)

    def test_rollback_replace_failure_is_reported_without_deleting_current(
        self,
    ) -> None:
        config = self.remote_layout()
        digest, count, _candidate = self.stage_candidate(config)
        real_replace = os.replace
        replace_calls = 0

        def fail_only_rollback(source: Path, destination: Path) -> None:
            nonlocal replace_calls
            replace_calls += 1
            if replace_calls == 3:
                raise OSError("fictional rollback replace failure")
            real_replace(source, destination)

        with patch(
            "src.services.gale_publish_service.os.replace",
            side_effect=fail_only_rollback,
        ):
            result = activate_remote_mirror(
                config,
                expected_sha256=digest,
                expected_note_count=count,
                restart_service=lambda _name: False,
                service_probe=self.probe,
                event_logger=lambda *_args: None,
            )

        self.assertEqual(result.reason, "rollback_failed")
        self.assertFalse(result.previous_preserved)
        self.assertEqual(sha256_file(config.current_mirror), digest)

    def test_activated_probe_searches_and_reads_new_fictional_note(self) -> None:
        config = self.remote_layout(current_source=self.mirror_v2)
        validate_activated_mirror(
            config.current_mirror,
            config.current_dir,
            expected_sha256=sha256_file(config.current_mirror),
            expected_note_count=self.export_v2.note_count,
        )
        results = search_gale_mirror(
            config.current_mirror,
            "FICTIONAL_PACKAGE_C_SEARCH",
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source_note_id"], 7)

    def test_same_hash_is_not_uploaded_twice(self) -> None:
        first_transport = RecordingTransport()
        first = publish_gale_mirror(
            self.publish_config,
            transport=first_transport,
        )
        second_transport = RecordingTransport()
        second = publish_gale_mirror(
            self.publish_config,
            transport=second_transport,
        )

        self.assertEqual(first.status, "success")
        self.assertEqual(second.status, "duplicate")
        self.assertEqual(second_transport.upload_calls, 0)
        self.assertEqual(second_transport.activation_calls, 0)

    def test_concurrent_local_publish_runs_only_once(self) -> None:
        transport = BlockingTransport()
        outcomes: list[str] = []

        def run_publish() -> None:
            try:
                result = publish_gale_mirror(
                    self.publish_config,
                    transport=transport,
                )
                outcomes.append(result.status)
            except GalePublishError as error:
                outcomes.append(error.code)

        first = threading.Thread(target=run_publish)
        first.start()
        self.assertTrue(transport.entered.wait(timeout=5))
        second = threading.Thread(target=run_publish)
        second.start()
        second.join(timeout=5)
        transport.release.set()
        first.join(timeout=5)

        self.assertCountEqual(outcomes, ["success", "publisher_busy"])
        self.assertEqual(transport.upload_calls, 1)

    def test_remote_lock_allows_only_one_activation(self) -> None:
        config = self.remote_layout()
        digest, count, _candidate = self.stage_candidate(config)
        entered = threading.Event()
        release = threading.Event()

        def blocking_restart(_name: str) -> bool:
            entered.set()
            release.wait(timeout=5)
            return True

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                activate_remote_mirror,
                config,
                expected_sha256=digest,
                expected_note_count=count,
                restart_service=blocking_restart,
                service_probe=self.probe,
                event_logger=lambda *_args: None,
            )
            self.assertTrue(entered.wait(timeout=5))
            second = executor.submit(
                activate_remote_mirror,
                config,
                expected_sha256=digest,
                expected_note_count=count,
                restart_service=lambda _name: True,
                service_probe=self.probe,
                event_logger=lambda *_args: None,
            )
            second_result = second.result(timeout=5)
            release.set()
            first_result = first.result(timeout=5)

        self.assertEqual(first_result.status, "success")
        self.assertEqual(second_result.reason, "publisher_busy")

    def test_operator_rollback_swaps_and_second_run_restores_layout(self) -> None:
        config, real_hash, fictional_hash = self.rollback_layout()
        transitions: list[str] = []

        first = self.run_operator_rollback(
            config,
            real_hash,
            fictional_hash,
            stop_service=lambda _name: transitions.append("stop") or True,
            start_service=lambda _name: transitions.append("start") or True,
            service_probe=lambda *args: (
                transitions.append("probe") or self.probe(*args)
            ),
        )

        self.assertEqual(first.status, "success")
        self.assertEqual(sha256_file(config.current_mirror), fictional_hash)
        self.assertEqual(sha256_file(config.previous_mirror), real_hash)
        self.assertEqual(transitions, ["stop", "start", "probe"])
        self.assertEqual(
            stat.S_IMODE(config.current_mirror.stat().st_mode),
            0o600,
        )
        self.assertEqual(
            stat.S_IMODE(config.previous_mirror.stat().st_mode),
            0o600,
        )
        self.assertFalse(
            list(config.current_dir.glob(".gale-mirror.operator-swap.*"))
        )

        second = self.run_operator_rollback(
            config,
            fictional_hash,
            real_hash,
        )

        self.assertEqual(second.status, "success")
        self.assertEqual(sha256_file(config.current_mirror), real_hash)
        self.assertEqual(sha256_file(config.previous_mirror), fictional_hash)
        self.assertFalse(
            list(config.current_dir.glob(".gale-mirror.operator-swap.*"))
        )

    def test_operator_rollback_rejects_each_expected_hash_mismatch(self) -> None:
        for mismatch in ("current", "previous"):
            with self.subTest(mismatch=mismatch):
                config, current_hash, previous_hash = self.rollback_layout()
                result = self.run_operator_rollback(
                    config,
                    "0" * 64 if mismatch == "current" else current_hash,
                    "f" * 64 if mismatch == "previous" else previous_hash,
                    stop_service=lambda _name: self.fail("stop called"),
                )

                self.assertEqual(
                    result.reason,
                    f"{mismatch}_hash_mismatch",
                )
                self.assertEqual(
                    sha256_file(config.current_mirror),
                    current_hash,
                )
                self.assertEqual(
                    sha256_file(config.previous_mirror),
                    previous_hash,
                )

    def test_operator_rollback_rejects_missing_previous(self) -> None:
        config, current_hash, previous_hash = self.rollback_layout()
        config.previous_mirror.unlink()

        result = self.run_operator_rollback(
            config,
            current_hash,
            previous_hash,
            stop_service=lambda _name: self.fail("stop called"),
        )

        self.assertEqual(result.reason, "previous_missing")
        self.assertEqual(sha256_file(config.current_mirror), current_hash)

    def test_operator_rollback_rejects_invalid_mirror_before_stop(self) -> None:
        for target in ("current", "previous"):
            with self.subTest(target=target):
                config, current_hash, previous_hash = self.rollback_layout()
                path = (
                    config.current_mirror
                    if target == "current"
                    else config.previous_mirror
                )
                path.write_bytes(b"fictional corrupt mirror")
                os.chmod(path, 0o600)
                corrupt_hash = sha256_file(path)

                result = self.run_operator_rollback(
                    config,
                    corrupt_hash if target == "current" else current_hash,
                    (
                        corrupt_hash
                        if target == "previous"
                        else previous_hash
                    ),
                    stop_service=lambda _name: self.fail("stop called"),
                )

                self.assertEqual(result.reason, f"{target}_verify_failed")

    def test_operator_rollback_requires_empty_incoming_without_cleanup(self) -> None:
        config, current_hash, previous_hash = self.rollback_layout()
        unexpected = config.incoming_dir / "do-not-delete.db"
        unexpected.write_bytes(b"fictional incoming blocker")

        result = self.run_operator_rollback(
            config,
            current_hash,
            previous_hash,
            stop_service=lambda _name: self.fail("stop called"),
        )

        self.assertEqual(result.reason, "incoming_not_empty")
        self.assertTrue(unexpected.is_file())
        self.assertEqual(sha256_file(config.current_mirror), current_hash)
        self.assertEqual(sha256_file(config.previous_mirror), previous_hash)

    def test_operator_rollback_rejects_unsafe_mode_or_sqlite_sidecar(
        self,
    ) -> None:
        for condition in ("mode", "sidecar"):
            with self.subTest(condition=condition):
                shutil.rmtree(self.root / "remote", ignore_errors=True)
                config, current_hash, previous_hash = self.rollback_layout()
                if condition == "mode":
                    os.chmod(config.previous_mirror, 0o640)
                    expected_reason = "previous_unsafe"
                else:
                    Path(f"{config.current_mirror}-wal").touch()
                    expected_reason = "current_sidecar_present"

                result = self.run_operator_rollback(
                    config,
                    current_hash,
                    previous_hash,
                    stop_service=lambda _name: self.fail("stop called"),
                )

                self.assertEqual(result.reason, expected_reason)
                self.assertEqual(
                    sha256_file(config.current_mirror),
                    current_hash,
                )
                self.assertEqual(
                    sha256_file(config.previous_mirror),
                    previous_hash,
                )

    def test_operator_rollback_rejects_current_or_previous_symlink(self) -> None:
        for target in ("current", "previous"):
            with self.subTest(target=target):
                shutil.rmtree(self.root / "remote", ignore_errors=True)
                config, current_hash, previous_hash = self.rollback_layout()
                path = (
                    config.current_mirror
                    if target == "current"
                    else config.previous_mirror
                )
                replacement = path.with_name(f"{target}-target.db")
                path.replace(replacement)
                path.symlink_to(replacement)

                result = self.run_operator_rollback(
                    config,
                    current_hash,
                    previous_hash,
                    stop_service=lambda _name: self.fail("stop called"),
                )

                self.assertEqual(result.reason, f"{target}_unsafe")
                self.assertTrue(path.is_symlink())

    def test_operator_rollback_health_failure_restores_original_layout(
        self,
    ) -> None:
        config, current_hash, previous_hash = self.rollback_layout()
        probe_results = iter((False, True))
        stop_calls = 0

        def stop_service(_name: str) -> bool:
            nonlocal stop_calls
            stop_calls += 1
            return True

        result = self.run_operator_rollback(
            config,
            current_hash,
            previous_hash,
            stop_service=stop_service,
            service_probe=lambda *_args: next(probe_results),
        )

        self.assertEqual(result.reason, "health_check_failed_restored")
        self.assertTrue(result.auto_restored)
        self.assertEqual(stop_calls, 2)
        self.assertEqual(sha256_file(config.current_mirror), current_hash)
        self.assertEqual(sha256_file(config.previous_mirror), previous_hash)
        self.assertFalse(
            list(config.current_dir.glob(".gale-mirror.operator-swap.*"))
        )

    def test_operator_rollback_swap_exception_restores_original_layout(
        self,
    ) -> None:
        config, current_hash, previous_hash = self.rollback_layout()
        real_replace = os.replace
        replace_calls = 0

        def fail_second_replace_once(source: Path, destination: Path) -> None:
            nonlocal replace_calls
            replace_calls += 1
            if replace_calls == 2:
                raise OSError("fictional swap failure")
            real_replace(source, destination)

        with patch(
            "src.services.gale_publish_service.os.replace",
            side_effect=fail_second_replace_once,
        ):
            result = self.run_operator_rollback(
                config,
                current_hash,
                previous_hash,
            )

        self.assertEqual(result.reason, "swap_failed_restored")
        self.assertTrue(result.auto_restored)
        self.assertEqual(sha256_file(config.current_mirror), current_hash)
        self.assertEqual(sha256_file(config.previous_mirror), previous_hash)
        self.assertFalse(
            list(config.current_dir.glob(".gale-mirror.operator-swap.*"))
        )

    def test_operator_rollback_rejects_shared_lock_conflict(self) -> None:
        config, current_hash, previous_hash = self.rollback_layout()
        with config.lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_operator_rollback(
                config,
                current_hash,
                previous_hash,
                service_is_active=lambda _name: self.fail(
                    "service state checked"
                ),
            )

        self.assertEqual(result.reason, "publisher_busy")
        self.assertEqual(sha256_file(config.current_mirror), current_hash)
        self.assertEqual(sha256_file(config.previous_mirror), previous_hash)

    def test_operator_rollback_requires_active_service_before_stop(self) -> None:
        config, current_hash, previous_hash = self.rollback_layout()

        result = self.run_operator_rollback(
            config,
            current_hash,
            previous_hash,
            service_is_active=lambda _name: False,
            stop_service=lambda _name: self.fail("stop called"),
        )

        self.assertEqual(result.reason, "service_not_active")
        self.assertEqual(sha256_file(config.current_mirror), current_hash)
        self.assertEqual(sha256_file(config.previous_mirror), previous_hash)

    def test_operator_rollback_stop_failure_keeps_original_layout(self) -> None:
        config, current_hash, previous_hash = self.rollback_layout()
        recovery_events: list[str] = []

        result = self.run_operator_rollback(
            config,
            current_hash,
            previous_hash,
            stop_service=lambda _name: False,
            start_service=lambda _name: recovery_events.append("start") or True,
            service_probe=lambda *args: (
                recovery_events.append("probe") or self.probe(*args)
            ),
        )

        self.assertEqual(result.reason, "service_stop_failed")
        self.assertEqual(recovery_events, ["start", "probe"])
        self.assertEqual(sha256_file(config.current_mirror), current_hash)
        self.assertEqual(sha256_file(config.previous_mirror), previous_hash)

    def test_operator_rollback_log_fields_exclude_note_content(self) -> None:
        config, current_hash, previous_hash = self.rollback_layout()
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("snotes_gale_publish")
        previous_level = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        self.addCleanup(logger.setLevel, previous_level)

        with patch(
            "src.services.gale_publish_service.subprocess.run"
        ) as system_logger:
            result = operator_rollback_remote_mirror(
                config,
                expected_current_sha256=current_hash,
                expected_previous_sha256=previous_hash,
                service_is_active=lambda _name: True,
                stop_service=lambda _name: True,
                start_service=lambda _name: True,
                service_probe=self.probe,
            )

        self.assertEqual(result.status, "success")
        self.assertEqual(system_logger.call_count, 1)
        combined = stream.getvalue() + repr(system_logger.call_args)
        for private_text in (
            "FICTIONAL_PACKAGE_C_NEW",
            "FICTIONAL_PACKAGE_C_SEARCH",
            "FICTIONAL_PACKAGE_C_TAG",
            "FICTIONAL_PACKAGE_C_DIRECTORY",
            "token",
            "client_secret",
            "password",
        ):
            self.assertNotIn(private_text, combined)

    def test_auto_publish_quiet_fifteen_minutes(self) -> None:
        digest = sha256_file(self.mirror_v2)
        scheduler = GaleAutoPublishScheduler()
        scheduler.observe(digest, 100)

        self.assertFalse(scheduler.should_publish(999))
        self.assertTrue(scheduler.should_publish(1000))

    def test_auto_publish_continuous_changes_at_thirty_minutes(self) -> None:
        scheduler = GaleAutoPublishScheduler()
        scheduler.observe("1" * 64, 100)
        scheduler.observe("2" * 64, 800)
        scheduler.observe("3" * 64, 1500)

        self.assertFalse(scheduler.should_publish(1899))
        self.assertTrue(scheduler.should_publish(1900))

    def test_retry_delay_has_no_fast_loop_and_resume_is_one_attempt(self) -> None:
        scheduler = GaleAutoPublishScheduler(retry_delay_seconds=300)
        scheduler.observe("1" * 64, 0)
        scheduler.mark_started()
        scheduler.mark_failure(900)

        self.assertFalse(scheduler.should_publish(901))
        self.assertFalse(scheduler.should_publish(1199))
        self.assertTrue(scheduler.should_publish(1200))
        scheduler.mark_started()
        self.assertFalse(scheduler.should_publish(1201, resumed=True))
        scheduler.mark_failure(1201)
        self.assertTrue(scheduler.should_publish(1202, network_restored=True))

    def test_network_upload_failure_does_not_touch_remote_current(self) -> None:
        remote = self.remote_layout()
        old_hash = sha256_file(remote.current_mirror)
        transport = RecordingTransport(upload_error="upload_failed")

        with self.assertRaisesRegex(GalePublishError, "upload_failed"):
            publish_gale_mirror(
                self.publish_config,
                transport=transport,
            )

        self.assertEqual(sha256_file(remote.current_mirror), old_hash)
        self.assertTrue(
            load_publish_state(self.publish_config.state_path).needs_retry
        )

    def test_logs_and_state_exclude_note_content_and_secret_values(self) -> None:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("snotes_gale_publish")
        previous_level = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        self.addCleanup(logger.setLevel, previous_level)

        publish_gale_mirror(
            self.publish_config,
            transport=RecordingTransport(),
        )
        combined = (
            stream.getvalue()
            + self.publish_config.state_path.read_text(encoding="utf-8")
        )
        for private_text in (
            "FICTIONAL_PACKAGE_C_NEW",
            "FICTIONAL_PACKAGE_C_SEARCH",
            "FICTIONAL_PACKAGE_C_TAG",
            "FICTIONAL_PACKAGE_C_DIRECTORY",
            "token",
            "client_secret",
            "password",
        ):
            self.assertNotIn(private_text, combined)
        self.assertEqual(
            set(json.loads(
                self.publish_config.state_path.read_text(encoding="utf-8")
            )),
            {
                "is_publishing",
                "last_success_time",
                "last_success_hash",
                "last_success_source_hash",
                "last_failure_time",
                "last_failure_reason",
                "last_failure_check",
                "needs_retry",
            },
        )

    def test_publisher_has_no_real_database_or_materialization_dependency(self) -> None:
        source = inspect.getsource(publish_gale_mirror)
        module_source = inspect.getsource(
            __import__(
                "src.services.gale_publish_service",
                fromlist=["unused"],
            )
        )
        self.assertNotIn("DATABASE_PATH", source)
        self.assertNotIn("materialize_ai_safe", module_source)
        self.assertNotIn("src.database", module_source)


if __name__ == "__main__":
    unittest.main()
