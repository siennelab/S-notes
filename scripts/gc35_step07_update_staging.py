"""Update only the authorized Step 7 service/HTTP patch on existing staging."""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[1]
FILES = ('src/snotes/application/reminder_service.py', 'src/snotes/transport/reminder_http.py')
REMOTE = r'''
import base64, hashlib, json, os, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
payload=json.load(sys.stdin)
root=Path('/srv/snotes/reminder-runtime')
allowed={'src/snotes/application/reminder_service.py','src/snotes/transport/reminder_http.py'}
if set(payload)!=allowed: raise SystemExit('unexpected_files')
for name, row in payload.items():
    target=root/name
    if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest()!=row['before']:
        raise SystemExit('staging_source_baseline_mismatch:'+name)
    compile(base64.b64decode(row['source']),name,'exec')
backup=Path('/root/greencloud-backups')/('step07-read-purity-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
backup.mkdir(mode=0o700)
for name in allowed:
    saved=backup/name; saved.parent.mkdir(parents=True,exist_ok=True); saved.write_bytes((root/name).read_bytes())
try:
    for name,row in payload.items():
        target=root/name; temporary=target.with_suffix('.step07-new')
        with temporary.open('xb') as stream: stream.write(base64.b64decode(row['source']))
        temporary.chmod(0o644); os.replace(temporary,target)
    subprocess.run(['systemctl','restart','snotes-reminder-api.service'],check=True)
    subprocess.run(['systemctl','is-active','--quiet','snotes-reminder-api.service'],check=True)
except Exception:
    for name in allowed: (root/name).write_bytes((backup/name).read_bytes())
    subprocess.run(['systemctl','restart','snotes-reminder-api.service'],check=True)
    raise SystemExit('patch_failed_source_restored')
print(json.dumps({'backup':str(backup),'files':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in sorted(allowed)}}))
subprocess.run(['systemctl','is-active','snotes-reminder-api.service','snotes-reminder-scheduler.timer','snotes-reminder-backup.timer'],check=True)
'''

def main():
    payload={}
    for name in FILES:
        before=subprocess.check_output(['git','show','fdbb6eb:'+name],cwd=ROOT)
        payload[name]={'before':hashlib.sha256(before).hexdigest(),'source':base64.b64encode((ROOT/name).read_bytes()).decode()}
    subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8','optimal','python3 -c '+shlex.quote(REMOTE)],input=json.dumps(payload).encode(),check=True,timeout=45)

if __name__=='__main__': main()
