"""Durable project records, immutable source observations and owned attachments."""
from datetime import datetime, time as day_time, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import uuid
from zoneinfo import ZoneInfo
from .message_format import is_quoted_reply


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def identity(*parts):
    return hashlib.sha256('\x00'.join(str(part) for part in parts).encode()).hexdigest()


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


SCHEMA = '''
CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,created_at INTEGER NOT NULL);
CREATE TABLE sources(id TEXT PRIMARY KEY,account TEXT NOT NULL,conversation TEXT NOT NULL,name TEXT NOT NULL,
                     UNIQUE(account,conversation));
CREATE TABLE people(account TEXT NOT NULL,person TEXT NOT NULL,name TEXT NOT NULL,PRIMARY KEY(account,person));
CREATE TABLE messages(id TEXT PRIMARY KEY,source_id TEXT NOT NULL REFERENCES sources(id),sender TEXT,
    timestamp INTEGER NOT NULL,sequence INTEGER NOT NULL,type_code INTEGER NOT NULL,
    body TEXT NOT NULL,precision TEXT NOT NULL,raw_json TEXT NOT NULL);
CREATE INDEX message_order ON messages(timestamp,source_id,sequence,id);
CREATE TABLE project_messages(project_id TEXT REFERENCES projects(id),message_id TEXT REFERENCES messages(id),
    added_at INTEGER NOT NULL,PRIMARY KEY(project_id,message_id));
CREATE TABLE observations(message_id TEXT REFERENCES messages(id),digest TEXT NOT NULL,payload TEXT NOT NULL,
    observed_at INTEGER NOT NULL,semantic_digest TEXT NOT NULL,PRIMARY KEY(message_id,digest));
CREATE TABLE objects(digest TEXT PRIMARY KEY,bytes INTEGER NOT NULL,path TEXT NOT NULL UNIQUE);
CREATE TABLE attachments(message_id TEXT REFERENCES messages(id),role TEXT NOT NULL,
    object_id TEXT REFERENCES objects(digest),status TEXT NOT NULL,metadata TEXT NOT NULL,
    PRIMARY KEY(message_id,role));
CREATE TABLE attachment_observations(message_id TEXT REFERENCES messages(id),role TEXT NOT NULL,
    object_id TEXT REFERENCES objects(digest),metadata TEXT NOT NULL,
    PRIMARY KEY(message_id,role,object_id));
CREATE TABLE batches(id TEXT PRIMARY KEY,project_id TEXT REFERENCES projects(id),created_at INTEGER NOT NULL,
    payload_digest TEXT NOT NULL,selected_count INTEGER NOT NULL);
CREATE TABLE batch_messages(batch_id TEXT REFERENCES batches(id),message_id TEXT REFERENCES messages(id),
    PRIMARY KEY(batch_id,message_id));
CREATE TABLE transcription_jobs(id TEXT PRIMARY KEY,audio_id TEXT NOT NULL REFERENCES objects(digest),
    profile TEXT NOT NULL,status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,
    text TEXT,error_code TEXT,retryable INTEGER NOT NULL DEFAULT 0,updated_at INTEGER NOT NULL);
CREATE TABLE message_jobs(message_id TEXT REFERENCES messages(id),job_id TEXT REFERENCES transcription_jobs(id),
    PRIMARY KEY(message_id,job_id));
CREATE TABLE transcript_edits(id INTEGER PRIMARY KEY,message_id TEXT REFERENCES messages(id),
    text TEXT NOT NULL,created_at INTEGER NOT NULL);
CREATE TABLE account_aliases(alias TEXT PRIMARY KEY,account TEXT NOT NULL);
CREATE TABLE message_aliases(alias TEXT PRIMARY KEY,message_id TEXT NOT NULL REFERENCES messages(id));
CREATE INDEX observations_semantic ON observations(message_id,semantic_digest);
CREATE INDEX project_messages_message ON project_messages(message_id,project_id);
PRAGMA user_version=3;
'''


class Archive:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        path = self.root / 'archive.sqlite3'
        new = not path.exists()
        if new:
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
        self.db = sqlite3.connect(path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA temp_store=MEMORY')
        if new:
            self.db.executescript(SCHEMA)
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        if version == 1:
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                self.db.execute('CREATE TABLE IF NOT EXISTS people(account TEXT NOT NULL,person TEXT NOT NULL,name TEXT NOT NULL,PRIMARY KEY(account,person))')
                self.db.execute('PRAGMA user_version=2')
            version = 2
        if version == 2:
            from .account_identity import semantic_payload
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                columns={row[1] for row in self.db.execute('PRAGMA table_info(observations)')}
                if 'semantic_digest' not in columns:self.db.execute('ALTER TABLE observations ADD COLUMN semantic_digest TEXT')
                self.db.create_function('chronicle_semantic',1,lambda value:identity(semantic_payload(value)))
                self.db.execute('UPDATE observations SET semantic_digest=chronicle_semantic(payload) WHERE semantic_digest IS NULL')
                self.db.execute('CREATE TABLE IF NOT EXISTS account_aliases(alias TEXT PRIMARY KEY,account TEXT NOT NULL)')
                self.db.execute('CREATE TABLE IF NOT EXISTS message_aliases(alias TEXT PRIMARY KEY,message_id TEXT NOT NULL REFERENCES messages(id))')
                self.db.execute('CREATE INDEX IF NOT EXISTS observations_semantic ON observations(message_id,semantic_digest)')
                self.db.execute('CREATE INDEX IF NOT EXISTS project_messages_message ON project_messages(message_id,project_id)')
                self.db.execute('PRAGMA user_version=3')
            version=3
        if version != 3:
            self.db.close()
            raise RuntimeError('unsupported_archive_version')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def create_project(self, name):
        if not name.strip():
            raise ValueError('project_name_empty')
        project = uuid.uuid4().hex
        with self.db:
            self.db.execute('INSERT INTO projects VALUES (?,?,?)', (project, name.strip(), int(time.time())))
        return project

    def projects(self):
        return [dict(row) for row in self.db.execute('SELECT * FROM projects ORDER BY created_at,id')]

    def _require_project(self, project):
        if not self.db.execute('SELECT 1 FROM projects WHERE id=?', (project,)).fetchone():
            raise ValueError('project_not_found')

    def resolve_message(self, message):
        from .account_identity import resolve_message
        return resolve_message(self,message)

    def bind_account(self, alias, account):
        from .account_identity import bind_account
        return bind_account(self,alias,account)

    def set_people(self, account, names):
        from .account_identity import resolve_account
        account=resolve_account(self,account)
        with self.db:
            for person, name in names.items():
                if person and name:
                    self.db.execute('INSERT INTO people VALUES (?,?,?) ON CONFLICT(account,person) DO UPDATE SET name=excluded.name',
                                    (account, person, name))

    def _put_object(self, path, created):
        path = Path(path)
        if not path.is_file() or path.is_symlink():
            raise ValueError('asset_file_unavailable')
        directory = self.root / 'objects'
        directory.mkdir(exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix='.incoming-', dir=directory)
        try:
            digest = hashlib.sha256()
            size = 0
            before = path.stat()
            with path.open('rb') as source, os.fdopen(fd, 'wb') as target:
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    digest.update(block)
                    size += len(block)
                    target.write(block)
                target.flush()
                os.fsync(target.fileno())
            after = path.stat()
            if (before.st_mtime_ns,before.st_ctime_ns,before.st_size,before.st_ino,before.st_dev) != (after.st_mtime_ns,after.st_ctime_ns,after.st_size,after.st_ino,after.st_dev):
                raise ValueError('asset_changed_during_copy')
            digest = digest.hexdigest()
            parent = directory / digest[:2]
            parent.mkdir(exist_ok=True, mode=0o700)
            destination = parent / digest
            if destination.exists():
                if file_digest(destination) != digest:
                    raise ValueError('archive_object_corrupt')
            else:
                os.replace(temporary, destination)
                created.append((digest, destination))
            self.db.execute('INSERT OR IGNORE INTO objects VALUES (?,?,?)',
                            (digest, size, destination.relative_to(self.root).as_posix()))
            return digest
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def object_path(self, digest):
        row = self.db.execute('SELECT path FROM objects WHERE digest=?', (digest,)).fetchone()
        if not row:
            raise ValueError('object_not_found')
        path = (self.root / row['path']).resolve(strict=True)
        if self.root not in path.parents or file_digest(path) != digest:
            raise ValueError('archive_object_corrupt')
        return path

    def _cleanup_rolled_back(self, created):
        for digest, path in created:
            if not self.db.execute('SELECT 1 FROM objects WHERE digest=?', (digest,)).fetchone():
                path.unlink(missing_ok=True)

    def _link_asset(self, message, role, object_id, status, metadata):
        previous = self.db.execute('SELECT * FROM attachments WHERE message_id=? AND role=?', (message, role)).fetchone()
        if object_id:
            self.db.execute('INSERT OR IGNORE INTO attachment_observations VALUES (?,?,?,?)',
                            (message, role, object_id, canonical(metadata)))
        if previous and previous['object_id']:
            if not object_id:
                return 0  # A later missing export must not remove an owned attachment.
            if previous['object_id'] != object_id:
                old = json.loads(previous['metadata'])
                if not (old.get('thumbnail_only') is True and metadata.get('thumbnail_only') is False):
                    return 1  # Preserve both observations; never silently replace equal-quality bytes.
        self.db.execute('INSERT INTO attachments VALUES (?,?,?,?,?) ON CONFLICT(message_id,role) '
                        'DO UPDATE SET object_id=excluded.object_id,status=excluded.status,metadata=excluded.metadata',
                        (message, role, object_id, status, canonical(metadata)))
        return 0

    def import_messages(self, project, bundle, resources=None, assets=None, selected=None, names=None):
        self._require_project(project)
        account = bundle.get('account_fingerprint')
        if not isinstance(account, str) or not re.fullmatch('[0-9a-f]{64}', account):
            raise ValueError('account_fingerprint_required')
        incoming_account=account
        from .account_identity import resolve_account,semantic_payload
        account=resolve_account(self,account)
        messages = bundle.get('messages')
        if not isinstance(messages, list):
            raise ValueError('messages_must_be_list')
        available = {m.get('archive_identity') for m in messages}
        selected = set(selected) if selected is not None else available
        if not selected <= available:
            raise ValueError('selected_message_not_in_bundle')
        resources = resources or []
        resource_map = {}
        for record in resources:
            key = record['archive_identity']
            if key in resource_map:
                raise ValueError('ambiguous_resource_record')
            resource_map[key] = record
        names = names or {}
        assets = Path(assets).resolve(strict=True) if assets is not None else None
        batch = uuid.uuid4().hex
        report = {'batch_id': batch, 'selected': 0, 'new_messages': 0, 'linked_to_project': 0,
                  'source_conflicts': 0, 'resource_conflicts': 0}
        created = []
        try:
            with self.db:
                self.db.execute('INSERT INTO batches VALUES (?,?,?,?,?)',
                    (batch, project, int(time.time()), identity(canonical(bundle)), len(selected)))
                for message in messages:
                    key = message.get('archive_identity')
                    if key not in selected:
                        continue
                    conversation = message['conversation_id']
                    server = str(message.get('server_id') or '0')
                    source_key = server if server != '0' else message['source_database'] + ':' + str(message['local_id'])
                    incoming_key=key
                    if key != identity(incoming_account, conversation, source_key):
                        raise ValueError('message_identity_mismatch')
                    key=self.resolve_message(identity(account,conversation,source_key))
                    report['selected'] += 1
                    source = identity(account, conversation)
                    self.db.execute('INSERT OR IGNORE INTO sources VALUES (?,?,?,?)',
                        (source, account, conversation, names.get(conversation, conversation)))
                    source=self.db.execute('SELECT id FROM sources WHERE account=? AND conversation=?',(account,conversation)).fetchone()['id']
                    if conversation in names:
                        self.db.execute('UPDATE sources SET name=? WHERE id=?', (names[conversation], source))
                    raw = canonical(message)
                    existing = self.db.execute('SELECT raw_json FROM messages WHERE id=?', (key,)).fetchone()
                    if existing is None:
                        self.db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)',
                            (key, source, message.get('sender_id'), int(message['timestamp']),
                             int(message.get('sort_sequence', message['local_id'])), int(message['type_code']),
                             message.get('body', ''), 'second', raw))
                        report['new_messages'] += 1
                    elif semantic_payload(existing['raw_json']) != semantic_payload(raw):
                        report['source_conflicts'] += 1
                    self.db.execute('INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?)',
                                    (key, identity(raw), raw, int(time.time()),identity(semantic_payload(raw))))
                    report['linked_to_project'] += self.db.execute('INSERT OR IGNORE INTO project_messages VALUES (?,?,?)',
                            (project, key, int(time.time()))).rowcount
                    self.db.execute('INSERT OR IGNORE INTO batch_messages VALUES (?,?)', (batch, key))
                    if is_quoted_reply(message):
                        continue  # A quote snapshot never imports the referenced message or file.
                    resource = resource_map.get(incoming_key)
                    if resource is None:
                        if int(message['type_code']) & 0xffffffff in (3, 34, 43, 49):
                            self._link_asset(key, 'primary', None, 'not_collected', {})
                        continue
                    roles = {'local_asset': 'primary', 'preview_asset': 'preview', 'voice_playback_asset': 'voice_playback'}
                    metadata = {k: v for k, v in resource.items() if k not in roles and k != 'archive_identity'}
                    status = resource.get('status', 'unknown')
                    has_asset = False
                    for field, role in roles.items():
                        if not resource.get(field):
                            continue
                        name = resource[field]
                        if not isinstance(name, str) or not re.fullmatch('[0-9a-f]{64}\\.[A-Za-z0-9]+', name) or assets is None:
                            raise ValueError('invalid_resource_asset_reference')
                        path = assets / name
                        if path.is_symlink() or assets not in path.resolve().parents:
                            raise ValueError('resource_path_outside_export')
                        digest = self._put_object(path, created)
                        if digest != name.split('.')[0]:
                            raise ValueError('export_asset_hash_mismatch')
                        metadata_with_name = dict(metadata, extension=Path(name).suffix)
                        report['resource_conflicts'] += self._link_asset(key, role, digest, 'available', metadata_with_name)
                        has_asset = True
                    if not has_asset:
                        self._link_asset(key, 'primary', None, status, metadata)
        except BaseException:
            self._cleanup_rolled_back(created)
            raise
        return report

    def attach_derivative(self, message, path, role, metadata=None):
        if role not in ('voice_playback', 'preview'):
            raise ValueError('invalid_derivative_role')
        if not self.db.execute('SELECT 1 FROM messages WHERE id=?', (message,)).fetchone():
            raise ValueError('message_not_found')
        created = []
        try:
            with self.db:
                digest = self._put_object(path, created)
                metadata = dict(metadata or {}, extension=Path(path).suffix)
                if self._link_asset(message, role, digest, 'available', metadata):
                    raise ValueError('derivative_conflict')
            return digest
        except BaseException:
            self._cleanup_rolled_back(created)
            raise

    def add_manual(self, project, body='', path=None, sender='我', timestamp=None):
        self._require_project(project)
        if not body.strip() and path is None:
            raise ValueError('manual_record_empty')
        message = uuid.uuid4().hex
        timestamp = int(time.time()) if timestamp is None else int(timestamp)
        source = identity('manual', project)
        raw = canonical({'origin': 'manual', 'body': body, 'timestamp': timestamp, 'sender': sender})
        created = []
        try:
            with self.db:
                self.db.execute('INSERT OR IGNORE INTO sources VALUES (?,?,?,?)', (source, 'manual', project, '手动添加'))
                self.db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)',
                    (message, source, sender, timestamp, timestamp, 49 if path else 1, body, 'second', raw))
                self.db.execute('INSERT INTO project_messages VALUES (?,?,?)', (project, message, int(time.time())))
                if path:
                    digest = self._put_object(path, created)
                    self._link_asset(message, 'primary', digest, 'available', {'name': Path(path).name})
        except BaseException:
            self._cleanup_rolled_back(created)
            raise
        return message

    def timeline(self, project, date=None, timezone='Asia/Shanghai'):
        from .timeline import select_rows
        return select_rows(self,project,date,timezone)[0]

    def timeline_page(self, project, date=None, timezone='Asia/Shanghai', **options):
        from .timeline import select_rows,summary
        options.setdefault('limit',100)
        rows,cursor,matched=select_rows(self,project,date,timezone,**options)
        return dict(summary(self,project,timezone),messages=rows,next=cursor,matched=matched)

    def transcript(self, message):
        message=self.resolve_message(message)
        correction = self.db.execute('SELECT text FROM transcript_edits WHERE message_id=? ORDER BY id DESC LIMIT 1', (message,)).fetchone()
        if correction:
            return {'state': 'corrected', 'text': correction['text'], 'origin': 'manual'}
        job = self.db.execute('SELECT j.* FROM transcription_jobs j JOIN message_jobs m ON m.job_id=j.id '
            "WHERE m.message_id=? ORDER BY (j.status='succeeded') DESC,j.updated_at DESC,j.rowid DESC LIMIT 1", (message,)).fetchone()
        return {'state': job['status'], 'text': job['text'], 'error_code': job['error_code']} if job else {'state': 'not_requested'}

    def correct_transcript(self, message, text):
        message=self.resolve_message(message)
        if not text.strip():
            raise ValueError('transcript_empty')
        row = self.db.execute('SELECT type_code FROM messages WHERE id=?', (message,)).fetchone()
        if row is None or row['type_code'] & 0xffffffff != 34:
            raise ValueError('voice_message_required')
        with self.db:
            self.db.execute('INSERT INTO transcript_edits(message_id,text,created_at) VALUES (?,?,?)',
                            (message, text, int(time.time())))

    def verify(self):
        missing, corrupt = [], []
        for row in self.db.execute('SELECT * FROM objects'):
            path = self.root / row['path']
            if not path.is_file():
                missing.append(row['digest'])
            elif file_digest(path) != row['digest']:
                corrupt.append(row['digest'])
        return {'database_ok': self.db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok',
                'objects': self.db.execute('SELECT count(*) FROM objects').fetchone()[0],
                'missing': missing, 'corrupt': corrupt}
