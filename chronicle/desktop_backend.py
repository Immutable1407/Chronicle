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
        self.pending_credentials = None
        super().__init__(store.home / 'archive', store.home / 'cache')
        for key_file in sorted((store.home / 'connection-attempts').glob('*/verified-keys.json')):
            state_file = key_file.parent / 'state.json'
            if state_file.is_file() and json.loads(state_file.read_text()).get('phase') == 'verified':
                self.pending_credentials = (key_file, '微信账号')
                self.connection = {'state': 'credentials_pending'}

    def source(self):
        selected = self.store.config.get('selected_profile')
        if not selected:
            raise ValueError('wechat_not_connected')
        if self.manifest is None:
            self.manifest = self.vault.get(selected)
        MacSource, _, _ = mac_modules()
        from .macos.core import refresh_manifest
        if refresh_manifest(self.manifest):
            self.vault.put(selected, self.manifest)
        return MacSource(key_data=self.manifest)

    def status(self):
        result = super().status()
        result['desktop'] = True
        return result

    def desktop_state(self):
        return {'welcome_complete': self.store.config['welcome_complete'],
                'profiles': self.store.config['profiles'], 'selected_profile': self.store.config.get('selected_profile'),
                'connection': self.connection}

    def preflight(self):
        mac_modules()
        from .macos.bootstrap import wechat_process_paths
        base = Path.home() / 'Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files'
        accounts = [{'id': identity(str(path.resolve())), 'label': path.parent.name}
                    for path in sorted(base.glob('*/db_storage'))]
        ready = False
        selected = subprocess.run(['/usr/bin/xcode-select', '-p'], capture_output=True, timeout=10)
        if selected.returncode == 0:
            tool = subprocess.run(['/usr/bin/xcrun', 'lldb', '-P'], capture_output=True, timeout=15)
            if tool.returncode == 0:
                check = subprocess.run(['/usr/bin/python3', '-c', 'import lldb'],
                    env=dict(os.environ, PYTHONPATH=tool.stdout.decode().strip()), capture_output=True, timeout=15)
                ready = check.returncode == 0
        from .doctor import report
        dependencies=report()
        return {'accounts': accounts, 'developer_tools_ready': ready, 'wechat_running': bool(wechat_process_paths()),
                'dependencies_ready':dependencies['sqlcipher'] and dependencies['zstd'],
                'media_ready':dependencies['ffmpeg'] and dependencies['pilk']}

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
        profile = identity(str(source.root))
        self.vault.put(profile, manifest)
        self.store.config['profiles'] = [p for p in self.store.config['profiles'] if p['id'] != profile] + [
            {'id': profile, 'label': (label or source.root.parent.name)[:100]}]
        self.store.config['selected_profile'] = profile
        self.store.save()
        self.manifest = manifest
        return {'connected': True}

    def save_pending_credentials(self):
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
        preflight = self.preflight()
        if not preflight['developer_tools_ready']:
            raise ValueError('developer_tools_required')
        if not preflight.get('dependencies_ready',True):
            raise ValueError('source_dependencies_required')
        if preflight['wechat_running'] and not quit_running:
            raise ValueError('quit_wechat_first')
        base = Path.home() / 'Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files'
        roots = [p for p in base.glob('*/db_storage') if identity(str(p.resolve())) == account_id]
        if len(roots) != 1:
            raise ValueError('select_account_first')
        attempt = self.store.home / 'connection-attempts' / secrets.token_hex(12)
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
                self.connection_process = subprocess.Popen(command + ['--state', str(attempt),
                    '--database-root', str(roots[0]), '--timeout', '300'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
                for line in self.connection_process.stdout:
                    try:
                        value = json.loads(line)
                    except ValueError:
                        continue
                    event = value.get('event')
                    if event == 'manual_restore_required':
                        recovery_required = True
                    self.connection = {'state': {'login_required': 'waiting_for_login', 'child_started': 'waiting_for_login',
                        'keys_verified': 'verifying', 'original_restored': 'restored'}.get(event, self.connection['state'])}
                rc = self.connection_process.wait()
                if rc == 130:
                    self.connection = {'state': 'cancelled'}
                    return
                if rc:
                    raise RuntimeError('initialization_failed')
                self.pending_credentials = (attempt / 'verified-keys.json', label)
                result = self.save_pending_credentials()
                self.connection = {'state': 'connected', **result}
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
