"""Source-first browser application. No DMG or native wrapper is required."""
import argparse
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from http.server import ThreadingHTTPServer

from .archive import identity
from .desktop_backend import DesktopWorkbench, DesktopHandler
from .desktop_storage import DesktopStore, application_home
from .keychain import MacKeychain


class UnavailableVault:
    def get(self, *_args):raise RuntimeError('keychain_requires_macos')
    def put(self, *_args):raise RuntimeError('keychain_requires_macos')


class BrowserWorkbench(DesktopWorkbench):
    def __init__(self, store, legacy=None, archive=None, codec_python=None, workspace=None, vault=None):
        self.legacy_keys = Path(legacy).resolve() if legacy else None
        self.workspace = Path(workspace).resolve() if workspace else Path.cwd()
        self.candidates = {}
        super().__init__(store, vault or (MacKeychain() if sys.platform == 'darwin' else UnavailableVault()))
        if archive:
            from .archive import Archive
            self.archive_root = Path(archive).resolve()
            with Archive(self.archive_root):pass
        self.codec_python = Path(codec_python or sys.executable).absolute()
        self.demo_server = None

    def source(self):
        if self.store.config.get('selected_profile'):
            return super().source()
        if self.legacy_keys and self.legacy_keys.is_file():
            from .macos.reader import MacSource
            return MacSource(self.legacy_keys)
        raise ValueError('wechat_not_connected')

    def status(self):
        result = super().status()
        result.update(desktop=False, setup_available=True, mode='source')
        return result

    def desktop_state(self):
        result = super().desktop_state()
        result.update(mode='source', legacy_available=bool(self.legacy_keys and self.legacy_keys.is_file()),
                      native_file_picker=False, preferences=self.store.config.get('preferences',{}))
        return result

    def known_connections(self):
        paths = [self.legacy_keys] if self.legacy_keys else []
        for config in (self.workspace / 'data').glob('workbench*.json'):
            try:
                value=json.loads(config.read_text()).get('wechat_keys')
                if value:paths.append((config.parent/value).resolve())
            except (OSError,ValueError):continue
        self.candidates = {}
        result=[];seen=set()
        from .macos.reader import MacSource,text_literal
        for path in paths:
            try:
                source=MacSource(path)
                if source.account in seen:continue
                count=len(source.conversations())
                account=source.root.parent.name
                import re
                own=re.match(r'wxid_[^_]+',account).group() if account.startswith('wxid_') else re.sub(r'_[A-Za-z0-9]{4}$','',account)
                with source.open('contact/contact.db') as db:
                    rows=db.query('SELECT nick_name,remark FROM contact WHERE username='+text_literal(own)+' LIMIT 1')
                label=(rows[0]['nick_name'] or rows[0]['remark']) if rows else None
                label=label or '已验证的本机账号'
                key=identity(str(path))
                self.candidates[key]=path
                result.append({'id':key,'label':label,'conversation_count':count,'current':path==self.legacy_keys})
                seen.add(source.account)
            except Exception:continue
        return result

    def start_demo(self):
        if self.demo_server is None:
            from .demo import DemoWorkbench
            demo=DemoWorkbench(self.store.home/'demo')
            self.demo_server=create_server(demo,0)
            threading.Thread(target=self.demo_server.serve_forever,daemon=True).start()
        if not self.demo_server.ticket:self.demo_server.ticket=secrets.token_urlsafe(32)
        return 'http://127.0.0.1:%d/?ticket=%s'%(self.demo_server.server_port,self.demo_server.ticket)

    def close(self):
        if getattr(self,'demo_server',None):
            self.demo_server.shutdown();self.demo_server.server_close();self.demo_server.app.close()
        super().close()


class BrowserHandler(DesktopHandler):
    def do_GET(self):
        path=urllib.parse.urlsplit(self.path).path
        if path=='/demo':
            if not self.allowed():return self.json({'error':'session_required'},403)
            if getattr(self.app,'is_demo',False):return self.json({'error':'already_in_demo'},400)
            self.send_common_headers(303,'text/plain',0);self.send_header('Location',self.app.start_demo());self.end_headers();return
        if path=='/api/setup/connections':
            if not self.allowed():return self.json({'error':'session_required'},403)
            with self.app.lock:return self.json({'connections':self.app.known_connections()})
        return super().do_GET()

    def do_POST(self):
        if self.path == '/api/setup/open':
            if not self.native() or not self.allowed(write=True):
                return self.json({'error':'session_required'},403)
            self.server.ticket=secrets.token_urlsafe(32)
            return self.json({'url':'http://127.0.0.1:%d/?ticket=%s'%(self.server.server_port,self.server.ticket)})
        if not self.path.startswith('/api/setup/'):
            return super().do_POST()
        if not self.allowed(write=True) or self.headers.get_content_type()!='application/json':
            return self.json({'error':'session_required'},403)
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=2*1024*1024:raise ValueError('invalid_size')
            value=json.loads(self.rfile.read(length))
            with self.app.lock:
                if getattr(self.app,'is_demo',False):raise ValueError('demo_read_only_setup')
                if self.path=='/api/setup/reuse':
                    path=self.app.candidates.get(value['candidate'])
                    if path is None:raise ValueError('connection_candidate_expired')
                    return self.json(self.app.import_connection(path,value.get('label')))
                if self.path=='/api/setup/legacy':
                    if not self.app.legacy_keys or not self.app.legacy_keys.is_file():raise ValueError('legacy_connection_unavailable')
                    self.app.store.config['selected_profile']=None;self.app.store.save();self.app.manifest=None
                    return self.json({'selected':True})
                if self.path=='/api/setup/import-connection':
                    from .macos.core import save_private
                    temporary=self.app.cache/('connection-'+secrets.token_hex(12))/'selected.json'
                    try:
                        save_private(temporary,value['manifest'])
                        return self.json(self.app.import_connection(temporary,value.get('label')))
                    finally:
                        import shutil
                        shutil.rmtree(temporary.parent,ignore_errors=True)
                if self.path=='/api/setup/connect':
                    return self.json(self.app.begin_connection(value['account'],value.get('label') or '微信账号',quit_running=value.get('quit_running') is True))
                if self.path=='/api/setup/cancel':
                    process=self.app.connection_process
                    if process and process.poll() is None:
                        self.app.connection={'state':'canceling'};process.terminate()
                    return self.json({'state':self.app.connection['state']})
                if self.path=='/api/setup/tools':
                    subprocess.Popen(['/usr/bin/xcode-select','--install'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                    return self.json({'installer_requested':True})
                if self.path=='/api/setup/preferences':
                    preferences={k:v for k,v in value.items() if k in ('project','date','mode') and isinstance(v,str)}
                    self.app.store.config['preferences']=preferences;self.app.store.save()
                    return self.json({'saved':True})
            return self.json({'error':'not_found'},404)
        except Exception as error:
            known={'developer_tools_required','source_dependencies_required','quit_wechat_first','select_account_first','connection_already_running','connection_candidate_expired','demo_read_only_setup'}
            code=str(error) if str(error) in known else 'keychain_permission_required' if str(error).startswith('keychain_') else 'setup_operation_failed'
            return self.json({'error':code},400)


def create_server(app,port):
    server=ThreadingHTTPServer(('127.0.0.1',port),BrowserHandler)
    server.app=app;server.secret=secrets.token_hex(32);server.ticket=secrets.token_urlsafe(32)
    server.cookie_name='chronicle_session_%d'%server.server_port
    return server


def reopen(home):
    """Only the local launcher holding the private runtime secret can mint a ticket."""
    try:
        value=json.loads((Path(home)/'browser-runtime.json').read_text())
        os.kill(value['pid'],0)
        port=int(value['port'])
        if not 1<=port<=65535:return None
        base='http://127.0.0.1:%d'%port
        request=urllib.request.Request(base+'/api/setup/open',data=b'{}',headers={
            'X-Chronicle-Native':value['secret'],'Origin':base,'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=2) as response:
            url=json.load(response)['url']
        if urllib.parse.urlsplit(url).netloc!='127.0.0.1:%d'%port:return None
        return url
    except (OSError,ValueError,KeyError,TypeError):return None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home',type=Path)
    parser.add_argument('--config',type=Path,help='Existing workspace configuration; contents stay private')
    parser.add_argument('--port',type=int,default=8766)
    parser.add_argument('--open',action='store_true')
    parser.add_argument('--demo',action='store_true')
    args=parser.parse_args()
    home=args.home or application_home()
    runtime_home=Path(home)/'demo' if args.demo else Path(home)
    if args.open and (url:=reopen(runtime_home)):
        print(json.dumps({'url':url,'reused':True}),flush=True);webbrowser.open(url);return
    config=json.loads(args.config.read_text()) if args.config else {}
    base=args.config.resolve().parent if args.config else Path.cwd()
    if args.demo:
        from .demo import DemoWorkbench
        app=DemoWorkbench(Path(home)/'demo')
    else:
        app=BrowserWorkbench(DesktopStore(home),legacy=(base/config['wechat_keys']) if config.get('wechat_keys') else None,
            archive=(base/config['archive']) if config.get('archive') else None,
            codec_python=(base/config['codec_python']) if config.get('codec_python') else None)
    try:server=create_server(app,args.port)
    except OSError:
        app.close();raise SystemExit('Port is in use. Choose a different --port.')
    url='http://127.0.0.1:%d/?ticket=%s'%(server.server_port,server.ticket)
    from .__main__ import write_json
    runtime_path=runtime_home/'browser-runtime.json'
    write_json(runtime_path,{'pid':os.getpid(),'port':server.server_port,'secret':server.secret})
    print(json.dumps({'url':url}),flush=True)
    if args.open:webbrowser.open(url)
    def stop(_number,_frame):threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
    try:server.serve_forever()
    finally:
        server.server_close();app.close()
        try:
            if json.loads(runtime_path.read_text()).get('pid')==os.getpid():runtime_path.unlink()
        except (OSError,ValueError):pass


if __name__=='__main__':main()
