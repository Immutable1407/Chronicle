"""Archive account aliases. Existing public message identifiers remain valid."""
import json
import re
from .archive import canonical, identity


def wechat_account_fingerprint(username):
    if not isinstance(username,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{2,127}',username):raise ValueError('invalid_wechat_principal')
    return identity('wechat','account-v1',username)


def semantic_payload(raw):
    value=json.loads(raw) if isinstance(raw,str) else dict(raw)
    value.pop('archive_identity',None)
    return canonical(value)


def source_key(message):
    server=str(message.get('server_id') or '0')
    return server if server!='0' else message['source_database']+':'+str(message['local_id'])


def resolve_account(archive, account):
    row=archive.db.execute('SELECT account FROM account_aliases WHERE alias=?',(account,)).fetchone()
    return row['account'] if row else account


def resolve_message(archive, message):
    row=archive.db.execute('SELECT message_id FROM message_aliases WHERE alias=?',(message,)).fetchone()
    return row['message_id'] if row else message


def merge_message(archive, old, target):
    db=archive.db
    for row in db.execute('SELECT role,object_id,status,metadata FROM attachments WHERE message_id=?',(old,)).fetchall():
        archive._link_asset(target,row['role'],row['object_id'],row['status'],json.loads(row['metadata']))
    for table,columns in (
        ('observations','digest,payload,observed_at,semantic_digest'),
        ('attachment_observations','role,object_id,metadata'),
        ('message_jobs','job_id')):
        db.execute(f'INSERT OR IGNORE INTO {table} (message_id,{columns}) SELECT ?,{columns} FROM {table} WHERE message_id=?',(target,old))
        db.execute(f'DELETE FROM {table} WHERE message_id=?',(old,))
    db.execute('INSERT INTO project_messages SELECT project_id,?,added_at FROM project_messages WHERE message_id=? ON CONFLICT(project_id,message_id) DO UPDATE SET added_at=min(added_at,excluded.added_at)',(target,old))
    db.execute('DELETE FROM project_messages WHERE message_id=?',(old,))
    db.execute('INSERT OR IGNORE INTO batch_messages SELECT batch_id,? FROM batch_messages WHERE message_id=?',(target,old))
    db.execute('DELETE FROM batch_messages WHERE message_id=?',(old,))
    db.execute('UPDATE transcript_edits SET message_id=? WHERE message_id=?',(target,old))
    db.execute('DELETE FROM attachments WHERE message_id=?',(old,))
    db.execute('UPDATE message_aliases SET message_id=? WHERE message_id=?',(target,old))
    db.execute('INSERT OR REPLACE INTO message_aliases VALUES (?,?)',(old,target))
    db.execute('DELETE FROM messages WHERE id=?',(old,))


def bind_account(archive, alias, account):
    if not all(isinstance(v,str) and re.fullmatch('[0-9a-f]{64}',v) for v in (alias,account)):
        raise ValueError('invalid_account_binding')
    account=resolve_account(archive,account)
    previous=resolve_account(archive,alias)
    if previous!=alias:
        if previous!=account:raise ValueError('account_alias_already_bound')
        return {'messages':0,'merged':0}
    if alias==account:return {'messages':0,'merged':0}
    migrated=merged=0
    db=archive.db
    with db:
        for source in db.execute('SELECT * FROM sources WHERE account=?',(alias,)).fetchall():
            found=db.execute('SELECT id FROM sources WHERE account=? AND conversation=?',(account,source['conversation'])).fetchone()
            target_source=found['id'] if found else source['id']
            if not found:db.execute('UPDATE sources SET account=? WHERE id=?',(account,source['id']))
            rows=db.execute('SELECT id,raw_json FROM messages WHERE source_id=?',(source['id'],)).fetchall()
            for row in rows:
                payload=json.loads(row['raw_json'])
                key=identity(account,source['conversation'],source_key(payload))
                target=resolve_message(archive,key)
                exists=db.execute('SELECT 1 FROM messages WHERE id=?',(target,)).fetchone()
                if exists and target!=row['id']:
                    merge_message(archive,row['id'],target);merged+=1
                else:
                    target=row['id']
                    db.execute('UPDATE messages SET source_id=? WHERE id=?',(target_source,target))
                db.execute('INSERT OR REPLACE INTO message_aliases VALUES (?,?)',(key,target))
                migrated+=1
            if target_source!=source['id']:db.execute('DELETE FROM sources WHERE id=?',(source['id'],))
        db.execute('INSERT OR IGNORE INTO people SELECT ?,person,name FROM people WHERE account=?',(account,alias))
        db.execute('DELETE FROM people WHERE account=?',(alias,))
        db.execute('UPDATE account_aliases SET account=? WHERE account=?',(account,alias))
        db.execute('INSERT INTO account_aliases VALUES (?,?)',(alias,account))
    return {'messages':migrated,'merged':merged}
