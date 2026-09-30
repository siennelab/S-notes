"""Read-only cutover preparation: detect, never unload, the local scheduler."""
from __future__ import annotations
import os
import subprocess
from pathlib import Path
LEGACY_WARNING = 'Legacy local Reminder scheduler detected'


def legacy_scheduler_detected(home: Path | None = None) -> bool:
    home = Path(os.environ.get('S_NOTES_STEP07_HOST_HOME', str(Path.home()))) if home is None else Path(home)
    if (home / 'Library/LaunchAgents/com.snotes.reminder-helper.plist').exists():
        return True
    try:
        result = subprocess.run(
            ['/bin/launchctl', 'print', f'gui/{os.getuid()}/com.snotes.reminder-helper'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def start_legacy_check(window) -> None:
    from PySide6.QtCore import QThread, Signal
    from PySide6.QtWidgets import QApplication, QLabel

    class Check(QThread):
        detected = Signal(bool)

        def run(self):
            self.detected.emit(legacy_scheduler_detected())

    def show_warning(detected):
        if detected:
            label = QLabel(LEGACY_WARNING + ' · Step 7 保留现有 helper')
            label.setStyleSheet('color: #b45309; font-weight: bold; padding: 6px;')
            window.statusBar().addPermanentWidget(label, 1)
            window.legacy_reminder_warning = label

    worker = Check(window)
    worker.detected.connect(show_warning)
    window.legacy_reminder_check = worker
    QApplication.instance().aboutToQuit.connect(lambda: worker.wait(2500))
    worker.start()
