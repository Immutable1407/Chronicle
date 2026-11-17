"""Frozen desktop backend entry point and private native-window integration."""
import hmac
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.parse

from .archive import Archive, identity
from .desktop_storage import DesktopStore, application_home
from .__main__ import write_json
from .workbench import Workbench, Handler, mac_modules


def prepare_runtime():
    if getattr(sys, 'frozen', False):
        root = Path(sys._MEIPASS)
        for name in ('ffmpeg', 'ffprobe', 'libsqlcipher.dylib', 'libzstd.dylib'):
            if not (root / name).is_file():
                raise RuntimeError('bundled_dependency_missing:' + name)
        os.environ['PATH'] = str(root) + os.pathsep + '/usr/bin:/bin:/usr/sbin:/sbin'
        os.environ['SQLCIPHER_LIBRARY'] = str(root / 'libsqlcipher.dylib')
        os.environ['ZSTD_LIBRARY'] = str(root / 'libzstd.dylib')


class NativeVault:
    """Keychain requests use the owning native window's private anonymous pipes."""
    def __init__(self):
        self.lock = threading.Lock()

    def command(self, operation, profile, value=None):
        with self.lock:
            request = secrets.token_hex(16)
            print(json.dumps({'event': 'vault', 'request': request, 'operation': operation,
                              'profile': profile, 'value': value}), flush=True)
            response = json.loads(sys.stdin.readline(2 * 1024 * 1024))
            if response.get('request') != request or not response.get('ok'):
                raise RuntimeError('keychain_operation_failed')
            return response.get('value')

    def get(self, profile):
        return self.command('get', profile)

    def put(self, profile, value):
        self.command('put', profile, value)

    def delete(self, profile):
        self.command('delete', profile)


class DesktopWorkbench(Workbench):
    def __init__(self, store, vault):
        self.store, self.vault = store, vault
        self.manifest = None
        self.connection = {'state': 'idle'}
        self.connection_process = None
        self.connection_thread = None
        self.connection_context = {}
        self.pending_credentials = None
        super().__init__(store.home / 'archive', store.home / 'cache')
        for key_file in sorted((store.home / 'connection-attempts').glob('*/verified-keys.json')):
            state_file = key_file.parent / 'state.json'
            if state_file.is_file() and json.loads(state_file.read_text()).get('phase') == 'verified':
                record=json.loads(state_file.read_text())
                self.pending_credentials = (key_file, record.get('connection_label') or '我的微信')
                self.connection = {'state': 'credentials_pending'}
                if record.get('confirmation_required'):
                    try:
                        account=self.describe_connection(key_file)
                        self.connection={'state':'confirm_account','account':account}
                        self.connection_context={'label':record.get('connection_label') or ''}
                    except Exception:
                        self.pending_credentials=None
                        self.connection={'state':'failed','error':'account_identity_uncertain'}

    def source(self):
        selected = self.store.config.get('selected_profile')
        if not selected:
            raise ValueError('wechat_not_connected')
        if self.manifest is None:
            self.manifest = self.vault.get(selected)
        MacSource, _, _ = mac_modules()
        from .macos.core import refresh_manifest
        refreshed=refresh_manifest(self.manifest)
        source=MacSource(key_data=self.manifest)
        if source.principal and selected!=source.account:
            profile=next((p for p in self.store.config['profiles'] if p['id']==selected),{})
            self._save_profile(source,profile.get('label'),source.account_info())
        elif refreshed or source.identity_dirty:self.vault.put(selected,self.manifest)
        return source

    def status(self):
        result = super().status()
        result['desktop'] = True
        return result

    def desktop_state(self):
        return {'welcome_complete': self.store.config['welcome_complete'],
                'profiles': self.store.config['profiles'], 'selected_profile': self.store.config.get('selected_profile'),
                'connection': {**self.connection, **self.connection_context}}

    def preflight(self):
        mac_modules()
        from .macos.bootstrap import wechat_process_paths
        base = Path.home() / 'Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files'
        saved={profile.get('location_fingerprint',profile['id']):profile for profile in self.store.config['profiles']}
        accounts=[]
        for index,path in enumerate(sorted(base.glob('*/db_storage')),1):
            key=identity(str(path.resolve()));profile=saved.get(key,{})
            accounts.append({'id':key,'label':profile.get('label') or '尚未识别的本机账号 '+str(index),
                             'nickname':profile.get('nickname') or '', 'directory_hint':path.parent.name[-4:]})
        ready = False
        selected = subprocess.run(['/usr/bin/xcode-select', '-p'], capture_output=True, timeout=10)
        if selected.returncode == 0:
            tool = subprocess.run(['/usr/bin/xcrun', 'lldb', '-P'], capture_output=True, timeout=15)
            if tool.returncode == 0:
                check = subprocess.run(['/usr/bin/python3', '-c', 'import lldb'],
                    env=dict(os.environ, PYTHONPATH=tool.stdout.decode().strip()), capture_output=True, timeout=15)
                ready = check.returncode == 0
        from .doctor import report
        from .macos.compatibility import compatibility
        dependencies=report()
        return {'accounts': accounts, 'developer_tools_ready': ready, 'wechat_running': bool(wechat_process_paths()),
                'dependencies_ready':dependencies['sqlcipher'] and dependencies['zstd'],
                'media_ready':dependencies['ffmpeg'] and dependencies['pilk'],**compatibility()}

    def import_connection(self, path, label=None):
        path = Path(path)
        if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError('connection_file_invalid')
        manifest = json.loads(path.read_text())
        MacSource, _, _ = mac_modules()
        source = MacSource(key_data=manifest)
        if source.root.name != 'db_storage':
            raise ValueError('unexpected_account_directory')
        source.conversations()  # Validate both contact and session schemas before reporting connected.
        if not source.principal:raise ValueError('verified_account_identity_required')
        profile = source.account
        account=source.account_info()
        self._save_profile(source,label,account)
        self.manifest = manifest
        return {'connected': True}

    def _save_profile(self, source, label, account):
        profile=source.account
        aliases=set(source.identity_aliases)|{profile}
        previous=next((p for p in self.store.config['profiles'] if p['id'] in aliases),{})
        self.vault.put(profile,source.manifest)
        self.store.config['profiles']=[p for p in self.store.config['profiles'] if p['id'] not in aliases]+[
            {'id':profile,'label':(label or previous.get('label') or account['nickname'] or '我的微信')[:100],
             'nickname':account['nickname'],'wechat_alias':account['wechat_alias'],'location_fingerprint':source.legacy_account}]
        self.store.config['selected_profile']=profile;self.store.save()

    def describe_connection(self, path):
        MacSource, _, _ = mac_modules()
        source=MacSource(key_data=json.loads(Path(path).read_text()))
        source.conversations()
        if not source.principal:raise ValueError('verified_account_identity_required')
        return source.account_info()

    def queue_account_confirmation(self, path, label):
        account=self.describe_connection(path)
        journal=Path(path).parent/'state.json'
        record=json.loads(journal.read_text())
        record.update(confirmation_required=True,connection_label=label)
        write_json(journal,record)
        self.pending_credentials=(Path(path),label)
        self.connection={'state':'confirm_account','account':account}

    def confirm_account(self, label):
        if self.connection['state']!='confirm_account' or not self.pending_credentials:
            raise ValueError('account_confirmation_required')
        path,previous=self.pending_credentials
        label=label.strip() if isinstance(label,str) else ''
        self.pending_credentials=(path,(label or previous)[:100])
        journal=path.parent/'state.json';record=json.loads(journal.read_text())
        record.update(confirmation_required=False,connection_label=self.pending_credentials[1])
        write_json(journal,record)
        self.connection={'state':'credentials_pending'}
        return self.save_pending_credentials()

    def discard_pending_account(self):
        if self.connection['state']!='confirm_account' or not self.pending_credentials:
            raise ValueError('account_confirmation_required')
        path,_=self.pending_credentials
        path.unlink(missing_ok=True)
        import shutil
        for name in ('Chronicle-WeChat.disabled','Chronicle-WeChat.app'):
            owned=path.parent/name
            if owned.exists():shutil.rmtree(owned)
        journal=path.parent/'state.json';record=json.loads(journal.read_text())
        record.update(phase='account_declined',confirmation_required=False);write_json(journal,record)
        self.pending_credentials=None;self.connection_context={};self.connection={'state':'cancelled'}
        return {'cancelled':True}

    def rename_profile(self, profile, label):
        if not isinstance(label,str) or not label.strip() or len(label)>100:
            raise ValueError('invalid_profile_label')
        selected=next((p for p in self.store.config['profiles'] if p['id']==profile),None)
        if selected is None:raise ValueError('profile_not_found')
        selected['label']=label.strip();self.store.save()
        return {'saved':True}

    def save_pending_credentials(self):
        if self.connection['state']=='confirm_account':
            raise ValueError('account_confirmation_required')
        if not self.pending_credentials:
            raise ValueError('no_pending_credentials')
        path, label = self.pending_credentials
        result = self.import_connection(path, label)
        path.unlink()
        import shutil
        for name in ('Chronicle-WeChat.disabled', 'Chronicle-WeChat.app'):
            target = path.parent / name
            if target.exists():
                shutil.rmtree(target)
        self.pending_credentials = None
        self.connection = {'state': 'connected'}
        return result

    def begin_connection(self, account_id, label, quit_running=False):
        if self.connection_thread and self.connection_thread.is_alive():
            raise ValueError('connection_already_running')
        if self.pending_credentials:
            raise ValueError('account_confirmation_required')
        preflight = self.preflight()
        if not preflight.get('connection_supported',True):raise ValueError('wechat_version_not_validated')
        if not preflight['developer_tools_ready']:
            raise ValueError('developer_tools_required')
        if not preflight.get('dependencies_ready',True):
            raise ValueError('source_dependencies_required')
        if preflight['wechat_running'] and not quit_running:
            raise ValueError('quit_wechat_first')
        base = Path.home() / 'Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files'
        roots = [p for p in base.glob('*/db_storage') if account_id=='auto' or identity(str(p.resolve())) == account_id]
        if not roots or account_id!='auto' and len(roots) != 1:
            raise ValueError('select_account_first')
        attempt = self.store.home / 'connection-attempts' / secrets.token_hex(12)
        import time
        self.connection_context = {'started_at':int(time.time()),'label':label[:100]}
        self.connection = {'state': 'preparing'}
        def worker():
            recovery_required = False
            try:
                if preflight['wechat_running']:
                    self.connection = {'state': 'closing_wechat'}
                    requested = subprocess.run(['/usr/bin/osascript', '-e', 'tell application id "com.tencent.xinWeChat" to quit'],
                                               capture_output=True, timeout=60)
                    from .macos.bootstrap import wechat_process_paths
                    if requested.returncode or wechat_process_paths():
                        self.connection = {'state': 'awaiting_quit'}
                        return
                if getattr(sys, 'frozen', False):
                    command = [sys.executable, '--bootstrap']
                else:
                    command = [sys.executable, '-m', 'chronicle.macos.bootstrap']
                arguments=command + ['--state', str(attempt), '--timeout', '300']
                for root in roots:arguments.extend(['--database-root',str(root)])
                self.connection_process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
                for line in self.connection_process.stdout:
                    try:
                        value = json.loads(line)
                    except ValueError:
                        continue
                    event = value.get('event')
                    if event == 'login_required':
                        self.connection_context['login_deadline'] = int(time.time()) + 300
                    if event == 'manual_restore_required':
                        recovery_required = True
                    self.connection = {'state': {'login_required': 'waiting_for_login', 'child_started': 'waiting_for_login',
                        'keys_verified': 'verifying', 'original_restored': 'restored'}.get(event, self.connection['state'])}
                rc = self.connection_process.wait()
                if rc == 130:
                    self.connection = {'state': 'cancelled'}
                    return
                if rc == 3 and not recovery_required:
                    self.connection = {'state':'failed','error':'login_not_verified'}
                    return
                if rc:
                    raise RuntimeError('initialization_failed')
                self.queue_account_confirmation(attempt / 'verified-keys.json',label)
            except Exception:
                self.connection = {'state': 'recovery_required' if recovery_required else
                                   'credentials_pending' if self.pending_credentials else 'failed', 'error': 'connection_failed'}
            finally:
                self.connection_process = None
        self.connection_thread = threading.Thread(target=worker, daemon=False)
        self.connection_thread.start()
        return {'state': 'preparing'}

    def close(self):
        if self.connection_process and self.connection_process.poll() is None:
            self.connection_process.terminate()
        if self.connection_thread and self.connection_thread.is_alive():
            self.connection_thread.join(timeout=30)
        super().close()


class DesktopHandler(Handler):
    def native(self):
        value = self.headers.get('X-Chronicle-Native', '')
        return value.isascii() and hmac.compare_digest(value, self.server.secret)

    def allowed(self, write=False):
        if not super().allowed(write):
            return False
        if self.native():
            return True
        cookie = self.headers.get('Cookie', '')
        cookie_name=getattr(self.server,'cookie_name','chronicle_session')
        return any(part.isascii() and hmac.compare_digest(part.strip(), cookie_name+'=' + self.server.secret) for part in cookie.split(';'))

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        ticket = urllib.parse.parse_qs(parsed.query).get('ticket', [''])[0]
        if parsed.path == '/' and self.server.ticket and hmac.compare_digest(ticket, self.server.ticket):
            if not Handler.allowed(self):
                return self.json({'error': 'origin_not_allowed'}, 403)
            self.server.ticket = None
            self.send_common_headers(303, 'text/plain', 0)
            self.send_header('Location', '/')
            self.send_header('Set-Cookie', getattr(self.server,'cookie_name','chronicle_session')+'=' + self.server.secret + '; HttpOnly; SameSite=Strict; Path=/')
            self.end_headers()
            return
        if parsed.path.startswith('/api/desktop/'):
            if not self.allowed():
                return self.json({'error': 'session_required'}, 403)
            try:
                if parsed.path == '/api/desktop/state':
                    return self.json(self.app.desktop_state())
                if parsed.path == '/api/desktop/preflight':
                    return self.json(self.app.preflight())
            except Exception:
                return self.json({'error': 'connection_status_unavailable'}, 400)
        super().do_GET()

    def do_POST(self):
        if not self.path.startswith('/api/desktop/'):
            return super().do_POST()
        if not self.allowed(write=True) or self.headers.get_content_type() != 'application/json':
            return self.json({'error': 'session_required'}, 403)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length < 65536:
                raise ValueError('invalid_request')
            data = json.loads(self.rfile.read(length))
            with self.app.lock:
                if self.path == '/api/desktop/welcome':
                    self.app.store.config['welcome_complete'] = True
                    self.app.store.save()
                    return self.json({'ready': True})
                if self.path == '/api/desktop/profile':
                    selected = data['profile']
                    if selected not in [p['id'] for p in self.app.store.config['profiles']]:
                        raise ValueError('profile_not_found')
                    self.app.store.config['selected_profile'] = selected
                    self.app.store.save()
                    self.app.manifest = None
                    return self.json({'selected': True})
                if self.path == '/api/desktop/connect':
                    return self.json(self.app.begin_connection(data['account'], data.get('label', '微信账号')))
                if self.path == '/api/desktop/retry-credentials':
                    return self.json(self.app.save_pending_credentials())
                if self.path in ('/api/desktop/import-archive', '/api/desktop/import-connection'):
                    if not self.native():
                        return self.json({'error': 'native_file_selection_required'}, 403)
                    if self.path.endswith('import-archive'):
                        return self.json(self.app.store.import_archive(data['path']))
                    return self.json(self.app.import_connection(data['path']))
            return self.json({'error': 'not_found'}, 404)
        except Exception:
            return self.json({'error': 'desktop_operation_failed'}, 400)


def main():
    prepare_runtime()
    tools = Path(__file__).parent / 'macos'
    sys.path.insert(0, str(tools))
    if len(sys.argv) > 1 and sys.argv[1] == '--decode-voice':
        from .macos.decode_voice import decode
        print(json.dumps(decode(sys.argv[2], sys.argv[3])))
        return
    if len(sys.argv) > 1 and sys.argv[1] == '--bootstrap':
        from .macos import bootstrap
        sys.argv = ['bootstrap.py'] + sys.argv[2:]
        raise SystemExit(bootstrap.main())
    if len(sys.argv) > 1 and sys.argv[1] == '--self-test':
        from .runtime_selftest import run
        print(json.dumps(run()))
        return
    if len(sys.argv) > 1 and sys.argv[1] == '--vault-self-test':
        vault = NativeVault()
        profile = identity('synthetic-vault-test', secrets.token_hex(16))
        try:
            vault.put(profile, {'synthetic': 'private-pipe-test'})
            assert vault.get(profile) == {'synthetic': 'private-pipe-test'}
        finally:
            vault.delete(profile)
        print(json.dumps({'ipc_keychain_roundtrip': True}), flush=True)
        return
    from http.server import ThreadingHTTPServer
    home = application_home()
    executable = os.environ.get('CHRONICLE_NATIVE_EXECUTABLE')
    if not executable:
        raise RuntimeError('native_application_required')
    secret = os.environ.pop('CHRONICLE_NATIVE_TOKEN')
    store = DesktopStore(home)
    app = DesktopWorkbench(store, NativeVault())
    server = ThreadingHTTPServer(('127.0.0.1', 0), DesktopHandler)
    server.app, server.secret, server.ticket = app, secret, secrets.token_urlsafe(32)
    print(json.dumps({'url': 'http://127.0.0.1:%d/?ticket=%s' % (server.server_port, server.ticket)}), flush=True)
    def stop(_signum, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    parent = int(os.environ.get('CHRONICLE_PARENT_PID', '0'))
    finished = threading.Event()
    def watch_parent():
        while not finished.wait(1):
            if parent and os.getppid() != parent:
                server.shutdown()
                return
    if parent:
        threading.Thread(target=watch_parent, daemon=True).start()
    try:
        server.serve_forever()
    finally:
        finished.set()
        server.server_close()
        app.close()


if __name__ == '__main__':
    main()
