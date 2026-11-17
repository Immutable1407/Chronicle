from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from chronicle.archive import Archive, identity


def message(number, conversation='chat', account='a' * 64, timestamp=None, kind=1, body=None):
    return {'archive_identity': identity(account, conversation, str(number)),
            'conversation_id': conversation, 'server_id': str(number), 'local_id': number,
            'source_database': 'message/message_0.db', 'sender_id': 'person',
            'timestamp': timestamp if timestamp is not None else 1794830000 + number,
            'sort_sequence': number, 'type_code': kind, 'body': body or 'message %d' % number,
            'decode_status': 'decoded'}


def bundle(*messages, account='a' * 64):
    return {'account_fingerprint': account, 'messages': list(messages)}


def asset(directory, data, message_id, name='paper.pdf', **metadata):
    directory.mkdir(parents=True, exist_ok=True)
    filename = hashlib.sha256(data).hexdigest() + '.pdf'
    (directory / filename).write_bytes(data)
    return dict(archive_identity=message_id, status='copied', local_asset=filename, name=name, **metadata)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='chronicle-archive-')
        self.root = Path(self.temporary.name)
        self.archive = Archive(self.root / 'archive')
        self.project = self.archive.create_project('test project')
        self.assets = self.root / 'export/assets'

    def tearDown(self):
        self.archive.close()
        self.temporary.cleanup()

    def test_overlap_order_selection_and_local_date(self):
        start = int(datetime.fromisoformat('2026-11-16T00:00:00+08:00').timestamp())
        first = message(1, timestamp=start-1)
        second = message(2, timestamp=start)
        third = message(3, timestamp=start+10)
        self.archive.import_messages(self.project, bundle(second, first))
        result = self.archive.import_messages(self.project, bundle(third, second))
        self.assertEqual((result['new_messages'], result['linked_to_project']), (1, 1))
        self.assertEqual([r['id'] for r in self.archive.timeline(self.project)], [first['archive_identity'], second['archive_identity'], third['archive_identity']])
        self.assertEqual(len(self.archive.timeline(self.project, '2026-11-16')), 2)
        other = self.archive.create_project('selected')
        self.archive.import_messages(other, bundle(first, second), selected=[second['archive_identity']])
        self.assertEqual(len(self.archive.timeline(other)), 1)

    def test_same_filename_different_bytes_and_same_bytes_multiple_events(self):
        rows = [message(i, kind=49) for i in range(1, 4)]
        records = [asset(self.assets, data, row['archive_identity']) for data, row in zip([b'first', b'second', b'first'], rows)]
        self.archive.import_messages(self.project, bundle(*rows), records, self.assets)
        self.assertEqual(len(self.archive.timeline(self.project)), 3)
        self.assertEqual(self.archive.verify()['objects'], 2)
        self.assertEqual(self.archive.verify()['corrupt'], [])

    def test_archive_survives_source_removal_and_directory_move(self):
        row = message(1, kind=49)
        record = asset(self.assets, b'independent bytes', row['archive_identity'])
        self.archive.import_messages(self.project, bundle(row), [record], self.assets)
        self.archive.close()
        shutil.rmtree(self.root / 'export')
        (self.root / 'archive').rename(self.root / 'moved')
        self.archive = Archive(self.root / 'moved')
        archived = self.archive.timeline(self.project)[0]['attachments'][0]
        self.assertEqual(self.archive.object_path(archived['object_id']).read_bytes(), b'independent bytes')
        self.assertTrue(self.archive.verify()['database_ok'])

    def test_import_rolls_back_database_and_new_objects(self):
        first, second = message(1, kind=49), message(2, kind=49)
        records = [asset(self.assets, b'one', first['archive_identity']),
                   dict(archive_identity=second['archive_identity'], status='copied', local_asset='../outside.pdf')]
        with self.assertRaisesRegex(ValueError, 'asset_reference'):
            self.archive.import_messages(self.project, bundle(first, second), records, self.assets)
        self.assertEqual(self.archive.timeline(self.project), [])
        self.assertEqual(self.archive.verify()['objects'], 0)
        self.assertFalse(any(path.is_file() for path in (self.archive.root / 'objects').rglob('*')))

    def test_missing_can_be_filled_and_later_missing_cannot_downgrade(self):
        row = message(1, kind=49)
        missing = {'archive_identity': row['archive_identity'], 'status': 'file_missing_or_unmatched'}
        self.archive.import_messages(self.project, bundle(row), [missing])
        self.assertIsNone(self.archive.timeline(self.project)[0]['attachments'][0]['object_id'])
        available = asset(self.assets, b'filled', row['archive_identity'])
        result = self.archive.import_messages(self.project, bundle(row), [available], self.assets)
        self.assertEqual(result['new_messages'], 0)
        self.archive.import_messages(self.project, bundle(row), [missing])
        self.assertEqual(self.archive.timeline(self.project)[0]['attachments'][0]['status'], 'available')

    def test_source_and_attachment_conflicts_do_not_overwrite(self):
        row = message(1, kind=49)
        first = asset(self.assets, b'original', row['archive_identity'])
        self.archive.import_messages(self.project, bundle(row), [first], self.assets)
        changed = dict(row, body='changed source')
        second = asset(self.assets, b'different', row['archive_identity'])
        report = self.archive.import_messages(self.project, bundle(changed), [second], self.assets)
        self.assertEqual(report['source_conflicts'], 1)
        self.assertEqual(report['resource_conflicts'], 1)
        archived = self.archive.timeline(self.project)[0]
        self.assertEqual(archived['body'], row['body'])
        self.assertTrue(archived['source_conflict'])
        self.assertEqual(self.archive.object_path(archived['attachments'][0]['object_id']).read_bytes(), b'original')

    def test_cross_account_and_cross_conversation_ids_are_separate(self):
        first, second = message(1), message(1, conversation='other')
        third = message(1, account='b' * 64)
        self.archive.import_messages(self.project, bundle(first, second))
        self.archive.import_messages(self.project, bundle(third, account='b' * 64))
        self.assertEqual(len(self.archive.timeline(self.project)), 3)
        with self.assertRaisesRegex(ValueError, 'identity_mismatch'):
            self.archive.import_messages(self.project, bundle(dict(first, conversation_id='wrong')))

    def test_manual_records_do_not_collapse_and_object_corruption_is_detected(self):
        note1 = self.archive.add_manual(self.project, 'same note')
        note2 = self.archive.add_manual(self.project, 'same note')
        self.assertNotEqual(note1, note2)
        row = message(1, kind=49)
        record = asset(self.assets, b'to protect', row['archive_identity'])
        self.archive.import_messages(self.project, bundle(row), [record], self.assets)
        digest = self.archive.timeline(self.project)[0]['attachments'][0]['object_id']
        self.archive.object_path(digest).write_bytes(b'corruption')
        self.assertEqual(self.archive.verify()['corrupt'], [digest])
        with self.assertRaisesRegex(ValueError, 'corrupt'):
            self.archive.object_path(digest)

    def test_thumbnail_can_upgrade_but_full_image_is_retained(self):
        row = message(1, kind=3)
        thumbnail = asset(self.assets, b'thumb', row['archive_identity'], thumbnail_only=True)
        full = asset(self.assets, b'full', row['archive_identity'], thumbnail_only=False)
        self.archive.import_messages(self.project, bundle(row), [thumbnail], self.assets)
        self.archive.import_messages(self.project, bundle(row), [full], self.assets)
        report = self.archive.import_messages(self.project, bundle(row), [thumbnail], self.assets)
        self.assertEqual(report['resource_conflicts'], 1)
        digest = self.archive.timeline(self.project)[0]['attachments'][0]['object_id']
        self.assertEqual(self.archive.object_path(digest).read_bytes(), b'full')

    def test_v1_archive_migrates_and_keeps_person_names_offline(self):
        row = message(1)
        self.archive.import_messages(self.project, bundle(row))
        with self.archive.db:
            self.archive.db.execute('DROP TABLE people')
            self.archive.db.execute('PRAGMA user_version=1')
        self.archive.close()
        self.archive = Archive(self.root / 'archive')
        self.assertEqual(self.archive.db.execute('PRAGMA user_version').fetchone()[0], 3)
        self.archive.set_people('a' * 64, {'person': '老师'})
        self.archive.set_people('b' * 64, {'person': '另一个账号的人'})
        self.archive.close()
        self.archive = Archive(self.root / 'archive')
        self.assertEqual(self.archive.timeline(self.project)[0]['sender_name'], '老师')
        # Recover a v1 stamp with the additive table already present, without losing names.
        with self.archive.db:
            self.archive.db.execute('PRAGMA user_version=1')
        self.archive.close()
        self.archive = Archive(self.root / 'archive')
        self.assertEqual(self.archive.timeline(self.project)[0]['sender_name'], '老师')


if __name__ == '__main__':
    unittest.main()
