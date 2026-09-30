"""Step 7 candidate-only, temporary-data GUI smoke entry."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def main() -> int:
    from PySide6.QtCore import QTimer, QThread, Signal
    from PySide6.QtWidgets import QApplication
    from src.snotes.ui.reminder_owner import owner_credential
    host_home=os.environ.get('S_NOTES_STEP07_HOST_HOME', str(Path.home()))
    with tempfile.TemporaryDirectory(prefix='snotes-step07-gui-smoke-') as directory:
        os.environ.update(HOME=directory,S_NOTES_DATA_ROOT=directory,
                          S_NOTES_STEP07_HOST_HOME=host_home,
                          S_NOTES_REMOTE_REMINDER_ENDPOINT='https://reminder.ke-den.de')
        from src.snotes.bootstrap import build_application
        app,window=build_application(sys.argv, data_root=Path(directory))
        controller=window.remote_reminder_controller
        result={'online':False,'heartbeat_ticks':0,'credential_not_in_db_env':False,'legacy_warning':False}
        started=time.monotonic()
        scanning=False
        class Scan(QThread):
            done=Signal(bool)
            def run(self):
                try:
                    value=owner_credential()
                    leaked=any(value in text for text in os.environ.values())
                    for path in Path(directory).rglob('*'):
                        if path.is_file() and path.suffix in {'.db','.json','.log','.ini'}:
                            leaked=leaked or value.encode() in path.read_bytes()
                    del value
                    self.done.emit(not leaked)
                except Exception:
                    self.done.emit(False)
        scanner=Scan(window)
        def scan_done(ok):
            result['credential_not_in_db_env']=ok
            result['legacy_warning']=hasattr(window,'legacy_reminder_warning')
            screenshot=os.environ.get('S_NOTES_STEP07_SCREENSHOT')
            if screenshot:
                window.grab().save(screenshot)
            window.close()
        scanner.done.connect(scan_done)
        def tick():
            nonlocal scanning
            result['heartbeat_ticks']+=1
            if controller.state=='online' and not controller.pending and not scanning:
                scanning=True; result['online']=True
                window.show_reminder_default()
                result['snapshot_items']=len(controller.list_reminders())
                QTimer.singleShot(300, scanner.start)
            if time.monotonic()-started > 25 and not scanning:
                scanning=True
                result['state']=controller.state
                window.close()
        timer=QTimer(window); timer.setInterval(40); timer.timeout.connect(tick)
        timer.start(); window.show(); app.exec()
        scanner.wait(4000)
        print('STEP07_GUI_SMOKE='+json.dumps(result,sort_keys=True))
        return 0 if result['online'] and result['credential_not_in_db_env'] and result['legacy_warning'] and result['heartbeat_ticks']>1 else 1
