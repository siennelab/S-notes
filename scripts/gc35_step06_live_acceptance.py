"""Run fictional-only Step 6 HTTP checks locally on GreenCloud as admin.

Credentials are read privately on the server and never returned or logged.
"""
from datetime import datetime, timedelta
from http.client import HTTPConnection
import json
from pathlib import Path
import uuid
from zoneinfo import ZoneInfo


def main():
    credentials = json.loads(Path('/root/greencloud-secrets/reminder-api.json').read_text())
    results = []

    def check(name, value):
        if not value:
            raise AssertionError(name)
        results.append(name)

    def request(actor='owner', command=None, payload=None):
        headers = {'Content-Type': 'application/json'}
        if actor is not None:
            headers['Authorization'] = 'Bearer ' + credentials.get(actor, 'invalid-fictional-token')
        connection = HTTPConnection('127.0.0.1', 8766, timeout=10)
        try:
            path = '/v1/reminder/snapshot' if command is None else '/v1/reminder/commands/' + command
            connection.request('GET' if command is None else 'POST', path,
                               body=None if command is None else json.dumps(payload or {}), headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    check('missing_credential', request(None)[0] == 401)
    check('wrong_credential', request('invalid')[0] in (401, 403))
    status, snapshot = request()
    check('snapshot_contract', status == 200 and {
        'schema_version', 'server_time', 'snapshot_time', 'labels', 'tables', 'items', 'settings', 'badge_count'
    } <= snapshot.keys())
    check('snapshot_no_endpoint_or_credential', 'ntfy_endpoint' not in snapshot['settings'] and
          all(token not in json.dumps(snapshot) for token in credentials.values()))
    suffix = uuid.uuid4().hex[:10]
    tables = []
    items = []
    date_value = (datetime.now(ZoneInfo('Asia/Shanghai')) + timedelta(days=30)).date().isoformat()
    for visible in (True, False):
        status, table = request(command='create_table', payload={
            'title': 'FICTIONAL acceptance ' + suffix, 'ai_visible': visible,
        })
        check('create_table_' + str(visible), status == 200 and bool(table.get('table_id')))
        tables.append(table['table_id'])
        draft = {'table_id': table['table_id'], 'title': 'FICTIONAL item ' + suffix,
                 'action_spec': {'type': 'one_time', 'date': date_value}, 'multi_notify': True}
        status, item = request(command='create_reminder', payload=draft)
        check('create_item_' + str(visible), status == 200 and bool(item.get('item_id')))
        items.append(item['item_id'])
        status, updated = request(command='update_reminder', payload={**draft, 'item_id': item['item_id'],
                                                                                    'note': 'FICTIONAL updated'})
        check('update_item_' + str(visible), status == 200)
    for actor in ('yan', 'claude-code', 'clawbot'):
        check(actor + '_visible_get', request(actor, 'get_reminder_item', {'item_id': items[0]})[1].get('status') != 'not_found')
        check(actor + '_hidden_item', request(actor, 'get_reminder_item', {'item_id': items[1]})[1].get('status') == 'not_found')
        check(actor + '_hidden_table', request(actor, 'get_reminder_table', {'table_id': tables[1]})[1].get('status') == 'not_found')
        for command in ('pause_reminder_item', 'resume_reminder_item', 'complete_reminder_item', 'update_reminder_item'):
            payload = {'item_id': items[1], 'request_id': 'step06-' + uuid.uuid4().hex}
            if command == 'update_reminder_item':
                payload['title'] = 'FICTIONAL forbidden'
            status, result = request(actor, command, payload)
            check(actor + '_hidden_' + command, status == 200 and result.get('status') == 'not_found')
        check(actor + '_delete_rejected', request(actor, 'delete_reminder', {'item_id': items[0]})[0] == 403)
        check(actor + '_visibility_rejected', request(actor, 'set_table_ai_visible', {'table_id': tables[0], 'ai_visible': False})[0] == 403)
        check(actor + '_self_identity_rejected', request(actor, 'get_reminder_item', {'item_id': items[1], 'actor': 'owner'})[0] == 400)
        status, view = request(actor)
        check(actor + '_hidden_snapshot', status == 200 and str(items[1]) not in
              [str(item.get('item_id', item.get('id'))) for item in view['items']])
        actor_draft = {'request_id': 'step06-' + uuid.uuid4().hex, 'table_id': tables[0],
                       'title': 'FICTIONAL actor ' + actor,
                       'action_spec': {'type': 'one_time', 'date': date_value}}
        status, created = request(actor, 'create_reminder_item', actor_draft)
        check(actor + '_create_visible', status == 200 and created.get('status') == 'created')
        actor_item_id = created['item_id']
        check(actor + '_idempotent_create', request(actor, 'create_reminder_item', actor_draft)[1].get('status') == 'already_processed')
        for command in ('update_reminder_item', 'pause_reminder_item', 'resume_reminder_item', 'complete_reminder_item'):
            payload = {'item_id': actor_item_id, 'request_id': 'step06-' + uuid.uuid4().hex}
            if command == 'update_reminder_item':
                payload['note'] = 'FICTIONAL actor changed'
            status, result = request(actor, command, payload)
            check(actor + '_visible_' + command, status == 200 and result.get('status') in ('updated', 'completed', 'paused', 'resumed'))
        check(actor + '_owner_cleanup', request(command='delete_reminder', payload={'item_id': actor_item_id})[0] == 200)
    for item_id in items:
        check('delete_' + str(item_id), request(command='delete_reminder', payload={'item_id': item_id})[0] == 200)
        check('deleted_not_found_' + str(item_id), request(command='get_reminder_item', payload={'item_id': item_id})[1].get('status') == 'not_found')
    for table_id in tables:
        check('trash_table_' + str(table_id), request(command='delete_table', payload={'table_id': table_id})[0] == 200)
        check('purge_table_' + str(table_id), request(command='permanently_delete_table', payload={'table_id': table_id})[0] == 200)
    print(json.dumps({'passed': len(results), 'checks': results}))


if __name__ == '__main__':
    main()
