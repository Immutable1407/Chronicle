import http.cookiejar
import json
from pathlib import Path
import secrets
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from chronicle.archive import Archive, file_digest
from chronicle.desktop_storage import DesktopStore
from chronicle.desktop_backend import DesktopWorkbench, DesktopHandler


class ArchiveImport(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='chronicle-desktop-')
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / 'source'
        self.file = self.root / 'test.txt'
        self.file.write_text('artificial file')
        with Archive(self.source) as archive:
            project = archive.create_project('artificial archive')
            archive.add_manual(project, 'artificial note', self.file)
        self.store = DesktopStore(self.root / 'application')

    def tearDown(self):
        self.temporary.cleanup()

    def test_import_copies_and_verifies_without_touching_original(self):
        before = file_digest(self.source / 'archive.sqlite3')
        result = self.store.import_archive(self.source)
        self.assertEqual(result, {'projects': 1, 'messages': 1, 'objects': 1})
        self.assertEqual(file_digest(self.source / 'archive.sqlite3'), before)
        with Archive(self.store.home / 'archive') as archive:
            self.assertTrue(archive.verify()['database_ok'])
            digest = archive.db.execute('SELECT digest FROM objects').fetchone()['digest']
            self.assertEqual(archive.object_path(digest).read_text(), 'artificial file')
        self.assertTrue(self.store.config['welcome_complete'])
        with self.assertRaisesRegex(ValueError, 'must_be_empty'):
            self.store.import_archive(self.source)

    def test_corrupt_object_does_not_replace_destination(self):
        with Archive(self.source) as archive:
            digest = archive.db.execute('SELECT digest FROM objects').fetchone()['digest']
            archive.object_path(digest).write_bytes(b'corruption')
        with self.assertRaisesRegex(ValueError, 'integrity_failed'):
            self.store.import_archive(self.source)
        with Archive(self.store.home / 'archive') as archive:
            self.assertEqual(archive.projects(), [])
        self.assertEqual(list(self.store.home.glob('archive-import-*')), [])

    def test_recovery_completes_a_staged_atomic_swap(self):
        import shutil
        home = self.store.home
        with Archive(home / 'archive'):
            pass
        shutil.copytree(self.source, home / 'stage')
        (home / 'archive').rename(home / 'previous')
        (home / 'archive-import.json').write_text(json.dumps({'stage':'stage','previous':'previous'}))
        DesktopStore(home)
        with Archive(home / 'archive') as archive:
            self.assertEqual(len(archive.projects()), 1)
        self.assertFalse((home / 'archive-import.json').exists())

    def test_import_rejects_outside_object_paths(self):
        database = sqlite3.connect(self.source / 'archive.sqlite3')
        database.execute("UPDATE objects SET path='../test.txt'")
        database.commit(); database.close()
        with self.assertRaisesRegex(ValueError, 'object_layout'):
            self.store.import_archive(self.source)


class FakeVault:
    def get(self, _profile):
        raise RuntimeError('no_profile')
    def put(self, _profile, _value):
        raise RuntimeError('not_used_in_protocol_test')


class DesktopSession(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='chronicle-session-')
        self.app = DesktopWorkbench(DesktopStore(self.temporary.name), FakeVault())
        self.server = ThreadingHTTPServer(('127.0.0.1',0),DesktopHandler)
        self.server.app=self.app
        self.server.secret=secrets.token_hex(32)
        self.server.ticket=secrets.token_urlsafe(24)
        self.base='http://127.0.0.1:%d' % self.server.server_port
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.cookies=http.cookiejar.CookieJar()
        self.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.app.close();self.temporary.cleanup()

    def request(self,path,data=None,opener=None,headers=None):
        fields={'Origin':self.base};fields.update(headers or {})
        if data is not None:fields['Content-Type']='application/json'
        request=urllib.request.Request(self.base+path,data=json.dumps(data).encode() if data is not None else None,headers=fields)
        try:response=(opener or self.opener).open(request,timeout=5)
        except urllib.error.HTTPError as error:response=error
        with response:return response.status,response.read()

    def login(self):
        ticket=self.server.ticket
        status,_=self.request('/?ticket='+ticket)
        self.assertEqual(status,200)
        self.assertIsNone(self.server.ticket)
        return ticket

    def test_native_ticket_creates_an_http_only_session_once(self):
        self.assertEqual(self.request('/api/projects')[0],403)
        ticket=self.login()
        self.assertEqual(self.request('/api/projects')[0],200)
        self.assertEqual(self.request('/?ticket='+ticket,opener=urllib.request.build_opener())[0],403)
        self.assertTrue(any('HttpOnly' in cookie._rest for cookie in self.cookies))

    def test_browser_session_cannot_submit_arbitrary_native_paths(self):
        self.login()
        self.assertEqual(self.request('/api/desktop/import-archive',{'path':'/not/selected/by/native/panel'})[0],403)
        self.assertEqual(self.request('/api/desktop/welcome',{})[0],200)
        self.assertTrue(self.app.store.config['welcome_complete'])

    def test_desktop_session_still_rejects_cross_origin_requests(self):
        self.login()
        self.assertEqual(self.request('/api/desktop/welcome',{},headers={'Origin':'https://external.example'})[0],403)

    def test_connection_requires_developer_tools_and_a_closed_wechat(self):
        with patch.object(self.app,'preflight',return_value={'developer_tools_ready':False}),patch('subprocess.Popen') as process:
            with self.assertRaisesRegex(ValueError,'developer_tools_required'):self.app.begin_connection('unused','test')
            process.assert_not_called()
        with patch.object(self.app,'preflight',return_value={'developer_tools_ready':True,'wechat_running':True}),patch('subprocess.Popen') as process:
            with self.assertRaisesRegex(ValueError,'quit_wechat_first'):self.app.begin_connection('unused','test')
            process.assert_not_called()

    def test_pending_credentials_survive_keychain_failure(self):
        attempt = self.app.store.home / 'connection-attempts/test'
        attempt.mkdir(parents=True)
        key_file = attempt / 'verified-keys.json'
        key_file.write_text('{"artificial":"fixture"}')
        self.app.pending_credentials = (key_file, 'test')
        with patch.object(self.app, 'import_connection', side_effect=RuntimeError('keychain_denied')):
            with self.assertRaises(RuntimeError):self.app.save_pending_credentials()
        self.assertTrue(key_file.exists())
        self.assertIsNotNone(self.app.pending_credentials)
        with patch.object(self.app, 'import_connection', return_value={'connected':True}):
            self.assertTrue(self.app.save_pending_credentials()['connected'])
        self.assertFalse(key_file.exists())
        self.assertEqual(self.app.connection['state'],'connected')

    def test_verified_attempt_is_recoverable_without_restarting_wechat(self):
        home=Path(self.temporary.name)/'recovery'
        attempt=home/'connection-attempts/test'
        attempt.mkdir(parents=True)
        (attempt/'verified-keys.json').write_text('{"artificial":"fixture"}')
        (attempt/'state.json').write_text('{"phase":"verified"}')
        recovered=DesktopWorkbench(DesktopStore(home),FakeVault())
        self.assertEqual(recovered.connection['state'],'credentials_pending')
        self.assertIsNotNone(recovered.pending_credentials)
        self.assertNotIn('fixture',json.dumps(recovered.desktop_state()))
        recovered.close()

    @unittest.skipUnless(__import__('sys').platform == 'darwin','Mac account adapter')
    def test_connection_import_stores_only_profile_reference_in_settings(self):
        from chronicle.workbench import mac_modules
        mac_modules()
        from chronicle.macos.core import CipherDatabase
        root=Path(self.temporary.name)/'wxid_fixtureA_0000/db_storage'
        entries=[]
        key='12'*32
        for name,sql in [('contact/contact.db','CREATE TABLE contact (username TEXT,nick_name TEXT,remark TEXT,alias TEXT)'),
                         ('session/session.db','CREATE TABLE SessionTable (username TEXT,last_timestamp INTEGER)')]:
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
            with CipherDatabase(path,key,fixture_write=True) as database:
                database.query(sql)
                if name.startswith('contact/'):database.query("INSERT INTO contact VALUES ('wxid_fixtureA','人工账号','','')")
            entries.append({'root':str(root.resolve()),'database':name,'raw_key':key,'salt':path.read_bytes()[:16].hex()})
        selected=Path(self.temporary.name)/'selected-connection.json'
        selected.write_text(json.dumps({'databases':entries}))
        captured={}
        self.app.vault.put=lambda profile,value:captured.update({profile:value})
        self.assertTrue(self.app.import_connection(selected,'人工账号')['connected'])
        settings=self.app.store.config_path.read_text()
        self.assertNotIn(key,settings)
        self.assertNotIn('raw_key',settings)
        self.assertEqual(len(captured),1)
        self.assertEqual(next(iter(captured.values()))['databases'][0]['raw_key'],key)


if __name__=='__main__':unittest.main()
