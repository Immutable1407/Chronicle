"""Source-launch boundaries and demo integration; no real WeChat access."""
import http.cookiejar
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from chronicle.archive import Archive, identity
from chronicle.demo import DemoWorkbench
from chronicle.desktop_storage import DesktopStore
from chronicle.launch import BrowserWorkbench, UnavailableVault, create_server, reopen


class SourceSession(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='chronicle-source-test-')
        self.root=Path(self.tmp.name)
        self.app=BrowserWorkbench(DesktopStore(self.root),vault=UnavailableVault(),workspace=self.root)
        self.server=create_server(self.app,0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:%d'%self.server.server_port
        self.cookies=http.cookiejar.CookieJar()
        self.client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.app.close();self.tmp.cleanup()
    def request(self,path,data=None,headers=None):
        fields={'Origin':self.base,'Content-Type':'application/json'};fields.update(headers or {})
        req=urllib.request.Request(self.base+path,data=json.dumps(data).encode() if data is not None else None,headers=fields)
        try:r=self.client.open(req,timeout=5)
        except urllib.error.HTTPError as e:r=e
        with r:return r.status,r.read()
    def login(self):self.assertEqual(self.request('/?ticket='+self.server.ticket)[0],200)

    def test_first_start_and_preferences_survive_restart(self):
        self.login()
        state=json.loads(self.request('/api/desktop/state')[1]);self.assertFalse(state['welcome_complete'])
        self.assertEqual(state['mode'],'source')
        self.assertEqual(self.request('/api/desktop/welcome',{})[0],200)
        self.assertEqual(self.request('/api/setup/preferences',{'project':'artificial','mode':'files','secret':'discard'})[0],200)
        state=DesktopStore(self.root).config
        self.assertTrue(state['welcome_complete']);self.assertEqual(state['preferences'],{'project':'artificial','mode':'files'})
        self.assertEqual(self.request('/api/setup/preferences',{}, {'Origin':'https://external.example'})[0],403)

    def test_browser_cannot_mint_tickets_or_submit_native_paths(self):
        self.login()
        self.assertEqual(self.request('/api/setup/open',{})[0],403)
        self.assertEqual(self.request('/api/desktop/import-archive',{'path':'/arbitrary'})[0],403)
        self.assertEqual(self.request('/api/setup/reuse',{'candidate':'/arbitrary'})[0],400)

    def test_private_launcher_can_reopen_without_a_second_server(self):
        from chronicle.__main__ import write_json
        write_json(self.root/'browser-runtime.json',{'pid':os.getpid(),'port':self.server.server_port,'secret':self.server.secret})
        url=reopen(self.root);self.assertTrue(url.startswith(self.base+'/?ticket='))
        self.assertEqual(self.request(url[len(self.base):])[0],200)
        self.assertIn('chronicle_session_%d'%self.server.server_port,[c.name for c in self.cookies])
        value=json.loads((self.root/'browser-runtime.json').read_text());value['secret']='wrong'
        write_json(self.root/'browser-runtime.json',value);self.assertIsNone(reopen(self.root))

    def test_normal_quit_cancellation_never_starts_capture(self):
        root=self.root/'artificial/db_storage';root.mkdir(parents=True)
        info={'developer_tools_ready':True,'wechat_running':True,'dependencies_ready':True}
        with patch.object(self.app,'preflight',return_value=info),patch('pathlib.Path.glob',return_value=[root]),patch('subprocess.run',return_value=SimpleNamespace(returncode=1)),patch('subprocess.Popen') as capture:
            self.app.begin_connection(identity(str(root.resolve())),'fixture',quit_running=True)
            self.app.connection_thread.join(3)
            self.assertEqual(self.app.connection['state'],'awaiting_quit');capture.assert_not_called()

    def test_explicit_connect_uses_source_module_and_handles_cancel(self):
        root=self.root/'artificial/db_storage';root.mkdir(parents=True)
        info={'developer_tools_ready':True,'wechat_running':True,'dependencies_ready':True}
        process=SimpleNamespace(stdout=[],wait=lambda:130)
        with patch.object(self.app,'preflight',return_value=info),patch('pathlib.Path.glob',return_value=[root]),patch('subprocess.run',return_value=SimpleNamespace(returncode=0)),patch('chronicle.macos.bootstrap.wechat_process_paths',return_value=[]),patch('subprocess.Popen',return_value=process) as capture:
            self.app.begin_connection(identity(str(root.resolve())),'fixture',quit_running=True)
            self.app.connection_thread.join(3)
            self.assertEqual(self.app.connection['state'],'cancelled')
            self.assertEqual(capture.call_args.args[0][1:3],['-m','chronicle.macos.bootstrap'])

    def test_login_deadline_and_unverified_result_are_reported_without_secrets(self):
        import time
        root=self.root/'artificial/db_storage';root.mkdir(parents=True)
        info={'developer_tools_ready':True,'wechat_running':False,'dependencies_ready':True}
        process=SimpleNamespace(stdout=[json.dumps({'event':'login_required'})],wait=lambda:3)
        before=time.time()
        with patch.object(self.app,'preflight',return_value=info),patch('pathlib.Path.glob',return_value=[root]),patch('subprocess.Popen',return_value=process):
            self.app.begin_connection(identity(str(root.resolve())),'fixture account')
            self.app.connection_thread.join(3)
        state=self.app.desktop_state()['connection']
        self.assertEqual(state['state'],'failed');self.assertEqual(state['error'],'login_not_verified')
        self.assertGreaterEqual(state['login_deadline'],int(before)+300)
        self.assertLessEqual(state['login_deadline'],int(time.time())+300)
        self.assertEqual(state['label'],'fixture account')
        self.assertNotIn(str(root),json.dumps(state))

    def test_auto_discovery_passes_all_candidates_and_waits_for_identity_confirmation(self):
        roots=[self.root/name/'db_storage' for name in ('account-one','account-two')]
        for root in roots:root.mkdir(parents=True)
        info={'developer_tools_ready':True,'wechat_running':False,'dependencies_ready':True}
        process=SimpleNamespace(stdout=[],wait=lambda:0)
        with patch.object(self.app,'preflight',return_value=info),patch('pathlib.Path.glob',return_value=roots),patch('subprocess.Popen',return_value=process) as launch,patch.object(self.app,'queue_account_confirmation') as confirm,patch.object(self.app,'import_connection') as save:
            self.app.begin_connection('auto','我的大号')
            self.app.connection_thread.join(3)
            args=launch.call_args.args[0]
            self.assertEqual(args.count('--database-root'),2)
            for root in roots:self.assertIn(str(root),args)
            confirm.assert_called_once();save.assert_not_called()

    def test_identity_confirmation_and_alias_are_bound_to_verified_account(self):
        attempt=self.root/'connection-attempts/fixture';attempt.mkdir(parents=True)
        path=attempt/'verified-keys.json';path.write_text('{}')
        (attempt/'state.json').write_text(json.dumps({'phase':'verified'}))
        account={'id':'verified-account','nickname':'示例昵称','wechat_alias':'example-user'}
        with patch.object(self.app,'describe_connection',return_value=account):self.app.queue_account_confirmation(path,'我的大号')
        self.assertEqual(self.app.desktop_state()['connection']['account']['nickname'],'示例昵称')
        with patch.object(self.app,'import_connection') as save:
            with self.assertRaisesRegex(ValueError,'account_confirmation_required'):self.app.save_pending_credentials()
            save.assert_not_called()
        self.login()
        with patch.object(self.app,'import_connection',return_value={'connected':True}) as save:
            status,_=self.request('/api/setup/confirm-account',{'label':'测试小号'})
            self.assertEqual(status,200);save.assert_called_once_with(path,'测试小号')
        self.assertFalse(path.exists());self.assertEqual(self.app.connection['state'],'connected')

    def test_restart_preserves_account_confirmation_without_silently_saving(self):
        attempt=self.root/'connection-attempts/fixture';attempt.mkdir(parents=True)
        (attempt/'verified-keys.json').write_text('{}')
        (attempt/'state.json').write_text(json.dumps({'phase':'verified','confirmation_required':True,'connection_label':'我的大号'}))
        with patch.object(BrowserWorkbench,'describe_connection',return_value={'id':'verified','nickname':'示例昵称'}):
            recovered=BrowserWorkbench(DesktopStore(self.root),vault=UnavailableVault(),workspace=self.root)
        try:
            self.assertEqual(recovered.connection['state'],'confirm_account')
            self.assertEqual(recovered.desktop_state()['connection']['label'],'我的大号')
            self.assertIsNone(recovered.store.config['selected_profile'])
            with self.assertRaisesRegex(ValueError,'account_confirmation_required'):recovered.save_pending_credentials()
        finally:recovered.close()

    def test_alias_edit_is_persistent_and_does_not_reinitialize(self):
        self.app.store.config['profiles']=[{'id':'one','label':'原备注','nickname':'真实昵称'},{'id':'two','label':'小号','nickname':'另一昵称'}]
        self.app.store.save();self.login()
        with patch.object(self.app,'begin_connection') as connect:
            self.assertEqual(self.request('/api/setup/profile-label',{'profile':'one','label':'我的大号'})[0],200)
            self.assertEqual(self.request('/api/setup/profile-label',{'profile':'unknown','label':'wrong'})[0],400)
            self.assertEqual(self.request('/api/setup/profile-label',{'profile':'two','label':' '})[0],400)
            connect.assert_not_called()
        profiles=DesktopStore(self.root).config['profiles']
        self.assertEqual(profiles[0],{'id':'one','label':'我的大号','nickname':'真实昵称'})
        self.assertEqual(profiles[1]['label'],'小号')

    def test_declining_account_removes_only_pending_material_without_saving(self):
        attempt=self.root/'connection-attempts/fixture';attempt.mkdir(parents=True)
        path=attempt/'verified-keys.json';path.write_text('{}')
        (attempt/'state.json').write_text(json.dumps({'phase':'verified'}))
        with patch.object(self.app,'describe_connection',return_value={'id':'verified','nickname':'示例昵称'}):self.app.queue_account_confirmation(path,'大号')
        with patch.object(self.app,'import_connection') as save:
            self.app.discard_pending_account();save.assert_not_called()
        self.assertFalse(path.exists());self.assertIsNone(self.app.pending_credentials)
        self.assertEqual(self.app.connection['state'],'cancelled')

    def test_missing_dependencies_are_detected_before_exit(self):
        with patch.object(self.app,'preflight',return_value={'developer_tools_ready':True,'dependencies_ready':False}),patch('subprocess.run') as run:
            with self.assertRaisesRegex(ValueError,'source_dependencies_required'):self.app.begin_connection('fixture','fixture',True)
            run.assert_not_called()


class DemoIsolation(unittest.TestCase):
    def test_demo_never_reads_wechat_or_keychain_and_deduplicates_across_chats(self):
        with tempfile.TemporaryDirectory() as home,patch('chronicle.keychain.MacKeychain.get',side_effect=AssertionError('real vault')),patch('chronicle.macos.reader.MacSource',side_effect=AssertionError('real source')):
            app=DemoWorkbench(home)
            try:
                self.assertTrue(app.status()['demo']);self.assertEqual(len(app.source().conversations()),2)
                with Archive(app.archive_root) as archive:
                    project=archive.projects()[0]['id'];rows=archive.timeline(project)
                    self.assertEqual(len(rows),9)
                    result=archive.import_messages(project,{'account_fingerprint':'d'*64,'messages':app.demo_messages},[],None)
                    self.assertEqual(len(archive.timeline(project)),9)
                    check=archive.verify();self.assertTrue(check['database_ok']);self.assertFalse(check['missing'])
                self.assertEqual(app.known_connections(),[])
            finally:app.close()


if __name__=='__main__':unittest.main()
