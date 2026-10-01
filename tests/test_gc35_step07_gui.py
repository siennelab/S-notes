from __future__ import annotations
import copy
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from src.snotes.application.reminder_service import ReminderService
from src.snotes.storage.repositories.reminders import SQLiteReminderRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.transport.reminder_http import ReminderHttpApplication, ReminderCredential, OWNER_ROLE
from src.snotes.transport.remote_reminder_client import RemoteReminderError
from src.snotes.ui.remote_reminder_controller import RemoteReminderController
from src.snotes.ui.reminder_center import ReminderCenter, ReminderLabelManagerDialog
from src.snotes.ui.reminder_owner import OwnerSnapshotCache
from src.snotes.domain.reminders import ReminderDraft


class Client:
    def __init__(self, api):
        self.api=api; self.error=None; self.future=False; self.delay=0.12; self.calls=[]
    def snapshot(self):
        self.calls.append(('GET',threading.get_ident()))
        time.sleep(self.delay)
        if self.error: raise RemoteReminderError(self.error)
        if self.future: return {'schema_version':999,'items':'must not parse'}
        return self.api.handle('GET','/v1/reminder/snapshot',headers={'Authorization':'Bearer fictional'}).body
    def command(self,name,payload):
        self.calls.append((name,threading.get_ident()))
        if self.error: raise RemoteReminderError(self.error)
        result=self.api.handle('POST','/v1/reminder/commands/'+name,headers={'Authorization':'Bearer fictional'},body=json.dumps(payload).encode())
        if result.status!=200: raise RemoteReminderError('server')
        return result.body


class RemoteGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='snotes-step07-gui-')
        self.root=Path(self.tmp.name)
        self.service=ReminderService(SQLiteReminderRepository(initialize_database(self.root/'cloud-fixture.db')))
        self.item=self.service.create_reminder(ReminderDraft('FICTIONAL UI','active',{'type':'one_time','date':'2099-01-01'}))
        self.api=ReminderHttpApplication(self.service,{'fictional':ReminderCredential('owner',OWNER_ROLE)})
        self.client=Client(self.api)
        self.cache=OwnerSnapshotCache(self.root/'gui-only'/'snapshot.json')
        self.controller=RemoteReminderController(self.client,cache=self.cache)
        self.center=ReminderCenter(self.controller)
    def wait(self,condition,timeout=5):
        start=time.monotonic()
        while not condition() and time.monotonic()-start<timeout:
            self.app.processEvents();time.sleep(.003)
        self.assertTrue(condition())
    def online(self): self.wait(lambda:self.controller.state=='online' and not self.controller.pending)
    def tearDown(self):
        self.controller.begin_shutdown()
        self.wait(lambda:not self.controller._threads)
        self.center.deleteLater(); self.app.processEvents(); self.tmp.cleanup()
    def test_delay_keeps_gui_responsive_and_disables_writes(self):
        ticks=[];timer=QTimer();timer.setInterval(5);timer.timeout.connect(lambda:ticks.append(1));timer.start()
        self.assertFalse(self.center.new_button.isEnabled())
        self.online();timer.stop()
        self.assertGreater(len(ticks),5)
        self.assertTrue(all(tid!=threading.get_ident() for _,tid in self.client.calls))
        self.assertTrue(self.center.new_button.isEnabled())
        self.assertTrue(self.controller.submit('pause_reminder',{'item_id':self.item.id}))
        self.assertFalse(self.center.new_button.isEnabled())
        self.online()
        self.assertEqual(self.controller.get_reminder(self.item.id).reminder.lifecycle,'paused')
    def test_shutdown_releases_pooled_connections(self):
        self.online();closed=[]
        self.client.close=lambda:closed.append(True)
        self.controller.begin_shutdown()
        self.wait(lambda:closed==[True])
    def test_offline_snapshot_time_and_all_write_controls_disabled(self):
        self.online();saved=self.controller.snapshot_time
        self.client.error='offline';self.controller.refresh()
        self.wait(lambda:self.controller.state=='offline')
        self.assertEqual(self.controller.snapshot_time,saved)
        self.assertIn(saved,self.center.remote_status.text());self.assertIn('离线',self.center.remote_status.text())
        for name in ('new_button','edit_button','delete_button','pause_button','resume_button','complete_button','table_label_combo','table_ai_combo','enabled_check','time_edit'):
            self.assertFalse(getattr(self.center,name).isEnabled(),name)
        self.assertTrue(self.center.table_title_input.isReadOnly())
        calls=len(self.client.calls)
        self.assertFalse(self.controller.submit('delete_reminder',{'item_id':self.item.id}))
        self.assertEqual(len(self.client.calls),calls)
        self.assertEqual(len(self.controller.list_reminders()),1)
    def test_future_schema_does_not_parse_or_write_and_keeps_cache(self):
        self.online();cached=self.cache.path.read_bytes();calls=len(self.client.calls)
        self.client.future=True;self.controller.refresh()
        self.wait(lambda:self.controller.state=='schema_mismatch')
        self.assertIn('版本不匹配',self.center.remote_status.text())
        self.assertEqual(self.cache.path.read_bytes(),cached)
        self.assertFalse(self.controller.submit('ensure_default_table',{}))
        self.assertEqual([name for name,_ in self.client.calls[calls:]],['GET'])
    def test_empty_snapshot_ensures_on_worker_without_local_fallback(self):
        self.online()
        for table in self.service.list_tables(): self.service.delete_table(table.table.id)
        sentinel=self.root/'s_notes.db';sentinel.write_bytes(b'fictional local sentinel')
        self.controller.refresh();self.online()
        self.assertTrue(any(name=='ensure_default_table' for name,_ in self.client.calls))
        self.assertEqual(sentinel.read_bytes(),b'fictional local sentinel')
        self.assertTrue(self.controller.list_tables())
    def test_stale_response_never_replaces_newer_snapshot(self):
        self.online();original=copy.deepcopy(self.controller._snapshot);calls=[]
        def snapshot():
            index=len(calls);calls.append(index)
            time.sleep(.18 if index==0 else .02)
            value=copy.deepcopy(original);value['snapshot_time']='old' if index==0 else 'new';return value
        self.client.snapshot=snapshot
        self.controller._start(None,{},refresh=True,callback=None)
        time.sleep(.01)
        self.controller._start(None,{},refresh=True,callback=None)
        self.wait(lambda:not self.controller.pending)
        self.assertEqual(self.controller.snapshot_time,'new')
        self.assertEqual(self.cache.load()['snapshot_time'],'new')
    def test_auth_failure_never_ensures_default(self):
        self.online();self.client.error='auth';calls=len(self.client.calls)
        self.controller.refresh();self.wait(lambda:self.controller.state=='auth')
        self.assertEqual([name for name,_ in self.client.calls[calls:]],['GET'])
        self.assertTrue(self.controller.read_only)
