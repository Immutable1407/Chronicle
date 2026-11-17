"""Migration, bounded queries, filtered paging and persistent file indexes."""
import json
import hashlib
from pathlib import Path
import tempfile
import unittest

from chronicle.archive import Archive,identity
from chronicle.macos.resource_index import FileIndex
from test_archive import message,bundle,asset


class AccountMigration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.archive=Archive(self.root/'archive');self.project=self.archive.create_project('fixture')
    def tearDown(self):self.archive.close();self.tmp.cleanup()

    def test_legacy_ids_survive_binding_and_reimport_without_false_conflicts(self):
        old='a'*64;stable='b'*64
        row=message(17,account=old,kind=34)
        record=asset(self.root/'assets',b'voice',row['archive_identity'])
        self.archive.import_messages(self.project,bundle(row,account=old),[record],self.root/'assets')
        self.archive.correct_transcript(row['archive_identity'],'manual correction')
        self.archive.set_people(old,{'person':'老师'})
        self.archive.bind_account(old,stable)
        new=message(17,account=stable,kind=34)
        result=self.archive.import_messages(self.project,bundle(new,account=stable))
        self.assertEqual((result['new_messages'],result['linked_to_project'],result['source_conflicts']),(0,0,0))
        saved=self.archive.timeline(self.project)[0]
        self.assertEqual(saved['id'],row['archive_identity']);self.assertEqual(saved['account'],stable)
        self.assertFalse(saved['source_conflict']);self.assertEqual(saved['sender_name'],'老师')
        self.assertEqual(self.archive.transcript(new['archive_identity'])['text'],'manual correction')
        self.assertEqual(self.archive.resolve_message(new['archive_identity']),row['archive_identity'])
        self.assertEqual(self.archive.verify()['objects'],1)
        self.assertEqual(self.archive.bind_account(old,stable)['messages'],0)
        with self.assertRaisesRegex(ValueError,'already_bound'):self.archive.bind_account(old,'c'*64)

    def test_real_v2_layout_upgrades_without_losing_observation_payloads(self):
        row=message(19);self.archive.import_messages(self.project,bundle(row))
        with self.archive.db:
            self.archive.db.execute('DROP INDEX observations_semantic')
            self.archive.db.execute('CREATE TABLE old_observations(message_id TEXT,digest TEXT,payload TEXT,observed_at INTEGER,PRIMARY KEY(message_id,digest))')
            self.archive.db.execute('INSERT INTO old_observations SELECT message_id,digest,payload,observed_at FROM observations')
            self.archive.db.execute('DROP TABLE observations');self.archive.db.execute('ALTER TABLE old_observations RENAME TO observations')
            self.archive.db.execute('DROP TABLE account_aliases');self.archive.db.execute('DROP TABLE message_aliases')
            self.archive.db.execute('PRAGMA user_version=2')
        self.archive.close();self.archive=Archive(self.root/'archive')
        self.assertEqual(self.archive.db.execute('PRAGMA user_version').fetchone()[0],3)
        observed=self.archive.db.execute('SELECT * FROM observations').fetchone()
        self.assertEqual(json.loads(observed['payload'])['archive_identity'],row['archive_identity'])
        self.assertTrue(observed['semantic_digest']);self.assertEqual(self.archive.timeline(self.project)[0]['id'],row['archive_identity'])

    def test_preexisting_duplicates_merge_projects_assets_and_observations(self):
        old,stable='a'*64,'b'*64
        first,second=message(9,account=old,kind=49),message(9,account=stable,kind=49,body='updated')
        other=self.archive.create_project('other')
        records=[asset(self.root/'assets',data,row['archive_identity']) for row,data in ((first,b'one'),(second,b'two'))]
        self.archive.import_messages(self.project,bundle(first,account=old),records[:1],self.root/'assets')
        self.archive.import_messages(other,bundle(second,account=stable),records[1:],self.root/'assets')
        result=self.archive.bind_account(old,stable)
        self.assertEqual(result['merged'],1)
        self.assertEqual(self.archive.db.execute('SELECT count(*) FROM messages').fetchone()[0],1)
        self.assertEqual(len(self.archive.timeline(self.project)),1);self.assertEqual(len(self.archive.timeline(other)),1)
        self.assertTrue(self.archive.timeline(other)[0]['source_conflict'])
        self.assertEqual(self.archive.db.execute('SELECT count(*) FROM attachment_observations').fetchone()[0],2)
        self.assertEqual(self.archive.resolve_message(first['archive_identity']),second['archive_identity'])
        self.assertEqual(self.archive.db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_verified_source_status_binds_legacy_account_before_new_imports(self):
        from types import SimpleNamespace
        from chronicle.workbench import Workbench
        row=message(18);self.archive.import_messages(self.project,bundle(row))
        app=Workbench(self.root/'archive',self.root/'cache')
        app.source=lambda:SimpleNamespace(account='b'*64,identity_aliases=['a'*64],identity_scheme='wechat-internal-user-v1',conversations=lambda:[])
        try:self.assertTrue(app.status()['source_available'])
        finally:app.close()
        self.assertEqual(self.archive.timeline(self.project)[0]['account'],'b'*64)
        self.assertEqual(self.archive.timeline(self.project)[0]['id'],row['archive_identity'])

    def test_bad_legacy_payload_rolls_back_binding(self):
        row=message(8);self.archive.import_messages(self.project,bundle(row))
        with self.archive.db:self.archive.db.execute("UPDATE messages SET raw_json='{}'")
        with self.assertRaises(KeyError):self.archive.bind_account('a'*64,'b'*64)
        self.assertEqual(self.archive.db.execute('SELECT account FROM sources').fetchone()[0],'a'*64)
        self.assertEqual(self.archive.db.execute('SELECT count(*) FROM account_aliases').fetchone()[0],0)


class BoundedTimeline(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.archive=Archive(Path(self.tmp.name)/'archive')
        self.project=self.archive.create_project('large')
        rows=[message(i,timestamp=1794830000+i//7,body='needle' if i==499 else 'ordinary') for i in range(1,501)]
        self.archive.import_messages(self.project,bundle(*rows))
    def tearDown(self):self.archive.close();self.tmp.cleanup()

    def test_cursor_has_no_gaps_or_duplicates_and_queries_are_bounded(self):
        cursor=None;seen=[]
        while True:
            statements=[];self.archive.db.set_trace_callback(statements.append)
            page=self.archive.timeline_page(self.project,limit=40,after=cursor)
            self.archive.db.set_trace_callback(None)
            self.assertLessEqual(sum(s.lstrip().upper().startswith('SELECT') for s in statements),12)
            self.assertLessEqual(len(page['messages']),40);self.assertEqual(page['total'],500)
            seen.extend(row['id'] for row in page['messages']);cursor=page['next']
            if not cursor:break
        self.assertEqual(len(seen),500);self.assertEqual(len(set(seen)),500)
        self.assertEqual(seen,[row['id'] for row in self.archive.timeline(self.project)])

    def test_search_hits_beyond_first_page_and_rejects_changed_cursor_scope(self):
        page=self.archive.timeline_page(self.project,limit=20,query='needle')
        self.assertEqual(page['matched'],1);self.assertEqual(page['messages'][0]['body'],'needle')
        cursor=self.archive.timeline_page(self.project,limit=20)['next']
        with self.assertRaisesRegex(ValueError,'invalid_timeline_cursor'):self.archive.timeline_page(self.project,limit=20,after=cursor,query='ordinary')
        with self.assertRaises(ValueError):self.archive.timeline_page(self.project,limit=500)

    def test_files_and_dates_are_filtered_on_server(self):
        file=Path(self.tmp.name)/'fixture.txt';file.write_text('file')
        self.archive.add_manual(self.project,'file note',file)
        page=self.archive.timeline_page(self.project,files=True,limit=20)
        self.assertEqual(page['matched'],1);self.assertEqual(len(page['messages']),1)
        self.assertEqual(page['resources'],1)
        self.assertEqual(self.archive.timeline_page(self.project,date='2020-01-01',limit=20)['matched'],0)


class FileInventory(unittest.TestCase):
    def test_hashes_and_inventory_are_reused_but_changed_bytes_revalidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'files';root.mkdir();file=root/'a.txt';file.write_bytes(b'first')
            cache=Path(directory)/'index.sqlite3';index=FileIndex(root,cache,interval=3600)
            digest=hashlib.md5(b'first').hexdigest()
            self.assertEqual(len(index.find(5,digest)),1)
            for _ in range(20):self.assertEqual(len(index.find(5,digest)),1)
            self.assertEqual(index.scan_count,1);self.assertEqual(index.hash_count,1)
            file.write_bytes(b'other')
            self.assertEqual(index.find(5,digest),[])
            self.assertEqual(len(index.find(5,hashlib.md5(b'other').hexdigest())),1)
            index.close();index=FileIndex(root,cache)
            self.assertEqual(len(index.find(5,hashlib.md5(b'other').hexdigest())),1)
            self.assertEqual(index.hash_count,0)
            file.unlink();self.assertEqual(index.find(5,digest),[]);index.close()

    def test_symlinks_and_deleted_entries_never_resolve_to_private_outside_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'files';root.mkdir();outside=Path(directory)/'private';outside.write_bytes(b'data')
            (root/'link').symlink_to(outside)
            index=FileIndex(root);self.assertEqual(index.find(4,hashlib.md5(b'data').hexdigest()),[])
            index.close()


class CompatibilityGate(unittest.TestCase):
    def test_only_verified_version_build_is_eligible_for_first_connection(self):
        import plistlib
        from unittest.mock import patch
        from chronicle.macos.compatibility import compatibility
        with tempfile.TemporaryDirectory() as directory:
            app=Path(directory)/'Fixture.app';info=app/'Contents/Info.plist';info.parent.mkdir(parents=True)
            with patch('platform.system',return_value='Darwin'),patch('platform.machine',return_value='arm64'):
                info.write_bytes(plistlib.dumps({'CFBundleShortVersionString':'4.1.15','CFBundleVersion':'270115'}))
                self.assertTrue(compatibility(app)['connection_supported'])
                info.write_bytes(plistlib.dumps({'CFBundleShortVersionString':'4.1.15','CFBundleVersion':'999999'}))
                self.assertFalse(compatibility(app)['connection_supported'])
                info.unlink();self.assertFalse(compatibility(app)['connection_supported'])


if __name__=='__main__':unittest.main()
