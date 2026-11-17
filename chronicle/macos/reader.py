#!/usr/bin/env python3
"""Read sessions and selected conversation messages into private local files."""
import argparse
import ctypes
import ctypes.util
import hashlib
import json
from pathlib import Path
import re

from .core import CipherDatabase, save_private, refresh_keys


def text_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def decode_body(value):
    raw = bytes.fromhex(value or '')
    if raw.startswith(b'\x28\xb5\x2f\xfd'):
        import os
        library = os.environ.get('ZSTD_LIBRARY') or ctypes.util.find_library('zstd')
        if not library:
            raise RuntimeError('zstd_decoder_missing')
        native = ctypes.CDLL(library)
        native.ZSTD_getFrameContentSize.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        native.ZSTD_getFrameContentSize.restype = ctypes.c_ulonglong
        native.ZSTD_decompress.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
        native.ZSTD_decompress.restype = ctypes.c_size_t
        native.ZSTD_isError.argtypes = [ctypes.c_size_t]
        native.ZSTD_isError.restype = ctypes.c_uint
        size = native.ZSTD_getFrameContentSize(raw, len(raw))
        if size > 16 * 1024 * 1024:
            raise RuntimeError('zstd_frame_size_unknown_or_excessive')
        buffer = ctypes.create_string_buffer(max(size, 1))
        length = native.ZSTD_decompress(buffer, size, raw, len(raw))
        if native.ZSTD_isError(length):
            raise RuntimeError('zstd_decode_failed')
        raw = buffer.raw[:length]
    return raw.decode('utf-8', errors='strict')


class MacSource:
    def __init__(self, key_file=None, key_data=None):
        if key_data is None:
            key_file = Path(key_file)
            if key_file.stat().st_mode & 0o077:
                raise RuntimeError('key_file_not_private')
            key_data = json.loads(key_file.read_text())
        entries = key_data['databases']
        roots = {entry['root'] for entry in entries}
        if len(roots) != 1:
            raise RuntimeError('expected_one_verified_account')
        self.root = Path(roots.pop()).resolve(strict=True)
        self.entries = {entry['database']: entry for entry in entries}
        self.account = hashlib.sha256(str(self.root).encode()).hexdigest()

    def account_info(self):
        """Read the verified account's own contact; never guess from other contacts."""
        folder=self.root.parent.name
        username=re.match(r'wxid_[^_]+',folder).group() if folder.startswith('wxid_') else re.sub(r'_[A-Za-z0-9]{4}$','',folder)
        with self.open('contact/contact.db') as database:
            rows=database.query('SELECT nick_name,alias FROM contact WHERE username='+text_literal(username)+' LIMIT 1')
        own=rows[0] if rows else {}
        return {'id':self.account,'nickname':own.get('nick_name') or '', 'wechat_alias':own.get('alias') or ''}

    def open(self, name):
        entry = self.entries.get(name)
        if entry is None:
            raise RuntimeError('database_key_not_available')
        path = (self.root / name).resolve(strict=True)
        if self.root not in path.parents:
            raise RuntimeError('database_outside_account')
        with path.open('rb') as stream:
            salt = stream.read(16).hex()
        if salt != entry['salt']:
            raise RuntimeError('database_replaced_or_key_stale')
        return CipherDatabase(path, entry['raw_key'])

    def conversations(self):
        with self.open('contact/contact.db') as db:
            contacts = {row['username']: row for row in db.query(
                'SELECT username,nick_name,remark,alias FROM contact')}
        with self.open('session/session.db') as db:
            sessions = db.query('SELECT username,last_timestamp FROM SessionTable ORDER BY last_timestamp DESC')
        result = []
        for session in sessions:
            identity = session['username']
            person = contacts.get(identity, {})
            result.append({'conversation_id': identity,
                           'display_name': person.get('remark') or person.get('nick_name') or identity,
                           'nickname': person.get('nick_name'), 'remark': person.get('remark'),
                           'kind': 'group' if identity.endswith('@chatroom') else 'private',
                           'last_timestamp': int(session['last_timestamp'] or 0)})
        return result

    def messages(self, conversation, limit=100, before=None):
        table = 'Msg_' + hashlib.md5(conversation.encode()).hexdigest()
        result = []
        for name in sorted(self.entries):
            if not re.fullmatch(r'message/message_\d+\.db', name):
                continue
            with self.open(name) as db:
                if not db.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=" + text_literal(table)):
                    continue
                condition = ''
                if before is not None:
                    if not isinstance(before, (list, tuple)) or len(before) != 4:
                        raise ValueError('invalid_message_cursor')
                    timestamp, sequence, database, local_id = before
                    condition = 'WHERE (m.create_time,COALESCE(m.sort_seq,0),' + text_literal(name) + ',m.local_id) < (' + \
                        str(int(timestamp)) + ',' + str(int(sequence)) + ',' + text_literal(str(database)) + ',' + str(int(local_id)) + ') '
                rows = db.query('SELECT m.local_id,m.server_id,m.local_type,m.create_time,m.sort_seq,'
                    'n.user_name AS sender_id,hex(m.message_content) AS body_hex '
                    'FROM "' + table + '" m LEFT JOIN Name2Id n ON n.rowid=m.real_sender_id '
                    + condition + 'ORDER BY m.create_time DESC,COALESCE(m.sort_seq,0) DESC,m.local_id DESC LIMIT ' + str(int(limit)))
            for row in rows:
                message = {'source_database': name, 'conversation_id': conversation,
                           'sender_id': row['sender_id'], 'local_id': int(row['local_id']),
                           'server_id': row['server_id'], 'type_code': int(row['local_type']),
                           'timestamp': int(row['create_time']), 'sort_sequence': int(row['sort_seq'] or 0)}
                source_id = row['server_id'] if row['server_id'] not in (None, '', '0') else name + ':' + row['local_id']
                message['archive_identity'] = hashlib.sha256(
                    (self.account + '\x00' + conversation + '\x00' + source_id).encode()).hexdigest()
                try:
                    body = decode_body(row['body_hex'])
                    prefix = (row['sender_id'] or '') + ':\n'
                    if conversation.endswith('@chatroom') and body.startswith(prefix):
                        body = body[len(prefix):]
                    message['body'] = body
                    message['decode_status'] = 'decoded'
                except (UnicodeDecodeError, RuntimeError) as error:
                    message['decode_status'] = type(error).__name__
                result.append(message)
        result.sort(key=lambda row: (row['timestamp'], row['sort_sequence'], row['source_database'], row['local_id']))
        return result[-limit:]

    def message_page(self, conversation, limit=50, before=None):
        if not 1 <= limit <= 200:
            raise ValueError('invalid_page_size')
        rows = self.messages(conversation, limit + 1, before)
        has_more = len(rows) > limit
        items = rows[-limit:]
        oldest = items[0] if items else None
        cursor = ([oldest['timestamp'], oldest['sort_sequence'], oldest['source_database'], oldest['local_id']]
                  if has_more else None)
        return {'messages': items, 'has_more': has_more, 'before': cursor}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--keys', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--conversation')
    parser.add_argument('--limit', type=int, default=100)
    parser.add_argument('--refresh-keys', action='store_true', help='Verify keys for newly created shards using private account material')
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error('limit must be between 1 and 1000')
    if args.refresh_keys:
        print(json.dumps({'new_verified_keys': refresh_keys(args.keys)}))
    source = MacSource(args.keys)
    if args.conversation:
        messages = source.messages(args.conversation, args.limit)
        save_private(args.output, {'account_fingerprint': source.account, 'messages': messages})
        counts = {}
        for row in messages:
            kind = str(row['type_code'])
            counts[kind] = counts.get(kind, 0) + 1
        print(json.dumps({'read_messages': len(messages), 'types': counts,
                          'decoded': sum(row['decode_status'] == 'decoded' for row in messages)}))
    else:
        rows = source.conversations()
        save_private(args.output, {'account_fingerprint': source.account, 'conversations': rows})
        print(json.dumps({'conversations': len(rows), 'groups': sum(row['kind'] == 'group' for row in rows)}))


if __name__ == '__main__':
    main()
