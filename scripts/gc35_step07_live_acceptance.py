"""Fictional staging-only Step 7 acceptance; owner credentials stay in GUI worker."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import uuid
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QApplication
from src.snotes.transport.remote_reminder_client import RemoteReminderClient, RemoteReminderError
from src.snotes.ui.reminder_owner import owner_credential
from src.snotes.application.remote_mcp_reminder import ActorReminderCredentialProvider, RemoteMcpReminderAdapter


class Acceptance(QThread):
    done = Signal(object)

    def run(self):
        checks = []
        tables = []
        client = RemoteReminderClient('https://reminder.ke-den.de', owner_credential)
        def check(name, condition):
            if not condition:
                raise AssertionError(name)
            checks.append(name)
        def cloud(command, payload):
            # Server-side credential stays on GreenCloud, never in SSH output.
            code = """import json, urllib.request
from pathlib import Path
credentials=json.loads(Path('/root/greencloud-secrets/reminder-api.json').read_text())
command,payload=json.loads(%r)
request=urllib.request.Request('http://127.0.0.1:8766/v1/reminder/commands/'+command,data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+credentials['owner'],'Content-Type':'application/json'})
with urllib.request.urlopen(request,timeout=4) as response: print(response.read().decode())
""" % json.dumps([command, payload])
            response = subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8','optimal','python3 -c '+shlex.quote(code)],capture_output=True,timeout=15)
            if response.returncode:
                raise RuntimeError('cloud_crosscheck_failed')
            return json.loads(response.stdout)
        error = None
        try:
            first = client.snapshot()
            check('compatible_staging_snapshot', first['schema_version']==4)
            one = client.command('ensure_default_table', {})
            two = client.command('ensure_default_table', {})
            check('ensure_idempotent_identity', one['table']['table_id']==two['table']['table_id'])
            table = client.command('create_table', {'title':'FICTIONAL Step07 '+uuid.uuid4().hex[:8], 'ai_visible':True})['table_id']
            tables.append(table)
            draft = {'table_id':table, 'title':'FICTIONAL Step07 item', 'action_spec':{'type':'one_time','date':'2099-01-01'}, 'note':'from Mac', 'multi_notify':False}
            item = client.command('create_reminder', draft)['item_id']
            check('owner_create_visible_in_snapshot', any(row['item_id']==item for row in client.snapshot()['items']))
            client.command('update_reminder', {**draft,'item_id':item,'note':'Mac updated'})
            observed = cloud('get_reminder_item', {'item_id':item})
            check('mac_write_visible_on_cloud', observed['item']['note']=='Mac updated')
            cloud('update_reminder', {**draft,'item_id':item,'note':'Cloud updated'})
            refreshed = next(row for row in client.snapshot()['items'] if row['item_id']==item)
            check('cloud_write_visible_on_mac_refresh', refreshed['note']=='Cloud updated')
            for command, lifecycle in [('pause_reminder','paused'),('resume_reminder','active'),('complete_reminder','completed')]:
                client.command(command, {'item_id':item})
                row = next(row for row in client.snapshot()['items'] if row['item_id']==item)
                check('owner_'+command, row['lifecycle']==lifecycle)
            for actor in ('yan','claude-code'):
                actor_client = RemoteReminderClient('https://reminder.ke-den.de', ActorReminderCredentialProvider(actor))
                adapter = RemoteMcpReminderAdapter(actor_client)
                check(actor+'_read_with_own_credential', adapter.get_reminder_table(table)['status']=='ok')
                result = adapter.create_reminder_item('step07-'+uuid.uuid4().hex, table, 'FICTIONAL '+actor, draft['action_spec'])
                check(actor+'_write_with_own_credential', result['status']=='created')
                try:
                    actor_client.command('ensure_default_table', {})
                    check(actor+'_ensure_forbidden', False)
                except RemoteReminderError as failure:
                    check(actor+'_ensure_forbidden', failure.status==403)
                for operation, arguments in (
                    ('update_reminder_item', {'note':'FICTIONAL actor updated'}),
                    ('pause_reminder_item', {}), ('resume_reminder_item', {}),
                    ('complete_reminder_item', {})):
                    changed=getattr(adapter, operation)('step07-'+uuid.uuid4().hex, result['item_id'], **arguments)
                    check(actor+'_'+operation, changed['status'] in {'updated','completed','paused','resumed'})
                client.command('delete_reminder', {'item_id':result['item_id']})
            client.command('delete_reminder', {'item_id':item})
            check('owner_delete_visible', not any(row['item_id']==item for row in client.snapshot()['items']))
            token=owner_credential()
            check('owner_not_in_process_environment', all(token not in value for value in os.environ.values()))
            check('owner_not_in_report', token not in json.dumps(checks))
            del token
        except Exception as failure:
            error = failure.kind if isinstance(failure, RemoteReminderError) else type(failure).__name__
        finally:
            for table in tables:
                try:
                    client.command('delete_table', {'table_id':table})
                    client.command('permanently_delete_table', {'table_id':table})
                except Exception:
                    error='fixture_cleanup_failed'
            self.done.emit({'passed':len(checks),'checks':checks,'error':error})


def main():
    host_home=Path.home()
    with tempfile.TemporaryDirectory(prefix='snotes-step07-live-') as home:
        os.environ['S_NOTES_STEP07_HOST_HOME']=str(host_home)
        os.environ['HOME']=home
        os.environ['S_NOTES_DATA_ROOT']=home
        app=QApplication(sys.argv)
        result={}
        worker=Acceptance()
        def finish(value):
            result.update(value); app.quit()
        worker.done.connect(finish)
        worker.start(); app.exec(); worker.wait()
        print(json.dumps(result))
        return 0 if result.get('error') is None else 1


if __name__=='__main__':
    raise SystemExit(main())
