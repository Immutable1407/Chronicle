"""Verify account boundaries and message identities with artificial data."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from core import CipherDatabase, save_private
from reader import MacSource


class ReaderBoundaries(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='chronicle-reader-')
        self.root = Path(self.temporary.name).resolve()
        self.key = '12' * 32
        self.manifest = []
        self.keys = self.root / 'private/keys.json'

    def tearDown(self):
        self.temporary.cleanup()

    def database(self, name, statements):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with CipherDatabase(path, self.key, fixture_write=True) as db:
            for sql in statements:
                db.query(sql)
        with path.open('rb') as stream:
            salt = stream.read(16).hex()
        self.manifest.append({'root': str(self.root), 'database': name,
                              'salt': salt, 'raw_key': self.key})
        save_private(self.keys, {'version': 1, 'databases': self.manifest})

    def test_same_source_id_in_different_chats_is_not_merged(self):
        statements = ['CREATE TABLE Name2Id (user_name TEXT)', "INSERT INTO Name2Id VALUES ('sender')"]
        for chat in ('first-chat', 'second-chat'):
            table = 'Msg_' + hashlib.md5(chat.encode()).hexdigest()
            statements += [
                'CREATE TABLE "' + table + '" (local_id INTEGER,server_id INTEGER,local_type INTEGER,'
                'create_time INTEGER,sort_seq INTEGER,real_sender_id INTEGER,message_content TEXT)',
                'INSERT INTO "' + table + '" VALUES (1,456,1,1000,1,1,\'identical content\')']
        self.database('message/message_0.db', statements)
        reader = MacSource(self.keys)
        first = reader.messages('first-chat')[0]
        second = reader.messages('second-chat')[0]
        self.assertEqual(first['body'], 'identical content')
        self.assertNotEqual(first['archive_identity'], second['archive_identity'])
        self.assertEqual(reader.messages('first-chat')[0]['archive_identity'], first['archive_identity'])

    def test_database_replacement_requires_reverification(self):
        self.database('session/session.db', ['CREATE TABLE SessionTable (username TEXT,last_timestamp INTEGER)'])
        reader = MacSource(self.keys)
        path = self.root / 'session/session.db'
        with path.open('r+b') as stream:
            stream.write(bytes(16))
        with self.assertRaisesRegex(RuntimeError, 'database_replaced_or_key_stale'):
            reader.open('session/session.db')

    def test_multiple_accounts_are_rejected(self):
        save_private(self.keys, {'databases': [{'root': str(self.root)}, {'root': str(self.root / 'other')}]})
        with self.assertRaisesRegex(RuntimeError, 'expected_one_verified_account'):
            MacSource(self.keys)

    def test_keyset_paging_across_shards_with_equal_timestamps(self):
        chat = 'paging-chat'
        table = 'Msg_' + hashlib.md5(chat.encode()).hexdigest()
        for shard in range(2):
            statements = ['CREATE TABLE Name2Id (user_name TEXT)', "INSERT INTO Name2Id VALUES ('sender')",
                'CREATE TABLE "' + table + '" (local_id INTEGER,server_id INTEGER,local_type INTEGER,'
                'create_time INTEGER,sort_seq INTEGER,real_sender_id INTEGER,message_content TEXT)']
            for local_id in range(1, 6):
                statements.append('INSERT INTO "' + table + '" VALUES (%d,%d,1,1000,7,1,\'same time\')' %
                                  (local_id, 100 + shard * 10 + local_id))
            self.database('message/message_%d.db' % shard, statements)
        source = MacSource(self.keys)
        cursor, seen = None, []
        while True:
            page = source.message_page(chat, limit=3, before=cursor)
            seen.extend(m['archive_identity'] for m in page['messages'])
            if not page['has_more']:
                break
            cursor = page['before']
        self.assertEqual(len(seen), 10)
        self.assertEqual(len(set(seen)), 10)


if __name__ == '__main__':
    unittest.main(verbosity=2)
