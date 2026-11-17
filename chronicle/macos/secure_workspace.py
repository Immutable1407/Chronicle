"""Optional AES-256 macOS workspace. Secrets are passed to hdiutil via stdin."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import webbrowser

MARKER='.chronicle-encrypted-workspace.json'


def run(args,password=None,timeout=180):
    if sys.platform!='darwin':raise RuntimeError('encrypted_workspace_requires_macos')
    result=subprocess.run(['/usr/bin/hdiutil',*args],input=password.encode()+b'\0' if password is not None else None,capture_output=True,timeout=timeout)
    if result.returncode:raise RuntimeError('encrypted_workspace_operation_failed')
    return result.stdout


def image_info(image):
    image=Path(image).expanduser().resolve()
    value=plistlib.loads(run(['info','-plist']))
    return next((item for item in value.get('images',[]) if Path(item.get('image-path','/not-an-image')).resolve()==image),None)


def is_encrypted(image):
    current=image_info(image)
    # hdiutil isencrypted cannot open a backing image already held by the OS.
    if current is not None:return current.get('image-encrypted') is True
    return plistlib.loads(run(['isencrypted','-plist',str(image)])).get('encrypted') is True


def mounted(image):
    item=image_info(image)
    if item is not None:
        entities=item.get('system-entities',[])
        mount=next((Path(e['mount-point']) for e in entities if e.get('mount-point')),None)
        device=next((e['dev-entry'] for e in entities if e.get('dev-entry')),None)
        if mount and device:return mount,device
    return None


def image_lock(image):
    if sys.platform!='darwin':raise RuntimeError('encrypted_workspace_requires_macos')
    import fcntl
    base=Path.home()/'Library/Application Support/Chronicle/secure-runtime'
    base.mkdir(parents=True,exist_ok=True,mode=0o700)
    key=hashlib.sha256(str(image).encode()).hexdigest()
    descriptor=os.open(base/(key+'.lock'),os.O_RDWR|os.O_CREAT,0o600)
    try:fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor);raise RuntimeError('encrypted_workspace_in_use')
    return descriptor,base/key


class EncryptedWorkspace:
    def __init__(self,image,password=None,initialize=False):
        self.image=Path(image).expanduser().resolve();self.password=password;self.initialize=initialize
        self.mount=None;self.device=None;self.lock=None;self.detach_on_close=False
    def __enter__(self):
        if not self.image.is_dir() or self.image.suffix!='.sparsebundle':raise ValueError('encrypted_workspace_image_required')
        if not is_encrypted(self.image):raise ValueError('workspace_image_not_encrypted')
        self.lock,target=image_lock(self.image)
        try:
            existing=mounted(self.image)
            if existing:self.mount,self.device=existing
            else:
                target.mkdir(exist_ok=True,mode=0o700)
                if any(target.iterdir()):raise RuntimeError('mount_directory_not_empty')
                password=self.password if self.password is not None else getpass.getpass('Encrypted workspace password: ')
                output=run(['attach','-plist','-nobrowse','-noautoopen','-mountpoint',str(target),'-stdinpass',str(self.image)],password)
                self.detach_on_close=True
                entities=plistlib.loads(output).get('system-entities',[])
                self.device=next((e['dev-entry'] for e in entities if e.get('dev-entry')),None)
                self.mount=next((Path(e['mount-point']) for e in entities if e.get('mount-point')),None)
                if self.mount!=target or not self.device:raise RuntimeError('encrypted_mount_not_observed')
            marker=self.mount/MARKER
            if self.initialize:marker.write_text(json.dumps({'format':'chronicle-macos-encrypted-home','version':1}))
            if not marker.is_file() or json.loads(marker.read_text()).get('format')!='chronicle-macos-encrypted-home':
                raise ValueError('not_a_chronicle_encrypted_workspace')
            self.detach_on_close=True
            self.home=self.mount/'Chronicle';self.home.mkdir(exist_ok=True,mode=0o700)
            return self
        except BaseException:
            self.close();raise
        finally:self.password=None
    def close(self):
        try:
            if self.device and self.detach_on_close:
                try:run(['detach',self.device],timeout=30)
                except RuntimeError:raise RuntimeError('encrypted_workspace_still_mounted_close_open_files') from None
                self.device=None
                if self.mount and self.mount.parent.name=='secure-runtime':
                    try:self.mount.rmdir()
                    except OSError:pass
        finally:
            if self.lock is not None:os.close(self.lock);self.lock=None
    def __exit__(self,kind,error,_trace):
        # An unfinished worker may still use the volume. Keep its mount and lock.
        if error is not None and str(error)=='connection_cleanup_incomplete':return False
        self.close()


def create(image,password,size='20g'):
    image=Path(image).expanduser().resolve()
    if image.suffix!='.sparsebundle' or image.exists():raise ValueError('new_sparsebundle_path_required')
    if not isinstance(password,str) or len(password)<12 or '\0' in password:raise ValueError('password_requires_12_characters')
    if not re.fullmatch(r'[1-9][0-9]*(?:m|g|t)',size):raise ValueError('invalid_image_capacity')
    image.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    staging=Path(tempfile.mkdtemp(prefix='.chronicle-encrypted-',dir=image.parent))
    temporary=staging/'Workspace.sparsebundle'
    try:
        run(['create','-size',size,'-type','SPARSEBUNDLE','-fs','APFS','-volname','Chronicle Secure','-encryption','AES-256','-stdinpass','-nospotlight',str(temporary)],password)
        with EncryptedWorkspace(temporary,password,initialize=True):pass
        if image.exists():raise ValueError('workspace_destination_exists')
        temporary.rename(image)
    finally:
        if not temporary.exists() or not mounted(temporary):shutil.rmtree(staging,ignore_errors=True)
    return image


def open_workspace(image,port=0,open_browser=True):
    image=Path(image).expanduser().resolve()
    from ..launch import main as launch,reopen
    if not image.is_dir() or image.suffix!='.sparsebundle':raise ValueError('encrypted_workspace_image_required')
    if not is_encrypted(image):raise ValueError('workspace_image_not_encrypted')
    current=mounted(image)
    if current:
        marker=current[0]/MARKER
        if not marker.is_file() or json.loads(marker.read_text()).get('format')!='chronicle-macos-encrypted-home':raise ValueError('not_a_chronicle_encrypted_workspace')
        home=current[0]/'Chronicle';url=reopen(home)
        if url:
            import urllib.request
            from ..archive import identity
            runtime=json.loads((home/'browser-runtime.json').read_text())
            base='http://127.0.0.1:'+str(runtime['port'])
            def get(endpoint):
                request=urllib.request.Request(base+endpoint,headers={'X-Chronicle-Native':runtime['secret']})
                with urllib.request.urlopen(request,timeout=5) as response:return json.load(response)
            if not get('/api/status').get('archive_encrypted') or get('/api/health').get('archive')!=identity(str((home/'archive').resolve())):
                raise ValueError('running_workspace_does_not_match_encrypted_home')

            if open_browser:webbrowser.open(url)
            print('Opened the running encrypted workspace.');return
    with EncryptedWorkspace(image) as workspace:
        previous=sys.argv
        sys.argv=['chronicle','--home',str(workspace.home),'--port',str(port)]+(['--open'] if open_browser else [])
        try:launch(encrypted_home=True)
        finally:sys.argv=previous
    print('Encrypted workspace locked.')


def backup(image,output):
    image=Path(image).expanduser().resolve();output=Path(output).expanduser().resolve()
    if output.exists() or image==output or image in output.parents:raise ValueError('new_backup_destination_required')
    descriptor,_=image_lock(image)
    staging=None
    try:
        if mounted(image):raise ValueError('close_and_lock_workspace_before_backup')
        if not plistlib.loads(run(['isencrypted','-plist',str(image)])).get('encrypted'):raise ValueError('workspace_image_not_encrypted')
        for path in image.rglob('*'):
            if path.is_symlink():raise ValueError('backup_source_symlink')
        output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        staging=Path(tempfile.mkdtemp(prefix='.chronicle-backup-',dir=output.parent))
        copied=staging/'Backup.sparsebundle';shutil.copytree(image,copied)
        from ..archive import file_digest
        originals={p.relative_to(image) for p in image.rglob('*') if p.is_file()}
        targets={p.relative_to(copied) for p in copied.rglob('*') if p.is_file()}
        if originals!=targets:raise ValueError('encrypted_backup_integrity_failed')
        for relative in originals:
            if file_digest(image/relative)!=file_digest(copied/relative):raise ValueError('encrypted_backup_integrity_failed')
        if mounted(image):raise ValueError('workspace_opened_during_backup')
        if output.exists():raise ValueError('backup_destination_exists')
        copied.rename(output)
    finally:
        if staging:shutil.rmtree(staging,ignore_errors=True)
        os.close(descriptor)


def lock_workspace(image):
    image=Path(image).expanduser().resolve()
    if not is_encrypted(image):raise ValueError('workspace_image_not_encrypted')
    descriptor,_=image_lock(image)
    try:
        current=mounted(image)
        if current:
            marker=current[0]/MARKER
            if not marker.is_file() or json.loads(marker.read_text()).get('format')!='chronicle-macos-encrypted-home':raise ValueError('not_a_chronicle_encrypted_workspace')
            try:run(['detach',current[1]],timeout=30)
            except RuntimeError:raise RuntimeError('encrypted_workspace_still_mounted_close_open_files') from None
    finally:os.close(descriptor)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    init=commands.add_parser('create');init.add_argument('image',type=Path);init.add_argument('--capacity',default='20g')
    start=commands.add_parser('open');start.add_argument('image',type=Path);start.add_argument('--port',type=int,default=0);start.add_argument('--no-browser',action='store_true')
    importer=commands.add_parser('import');importer.add_argument('image',type=Path);importer.add_argument('--archive',type=Path,required=True)
    exporter=commands.add_parser('backup');exporter.add_argument('image',type=Path);exporter.add_argument('output',type=Path)
    locker=commands.add_parser('lock');locker.add_argument('image',type=Path)
    args=parser.parse_args()
    if args.command=='create':
        secret=getpass.getpass('New password (12+ characters; no recovery if lost): ')
        if secret!=getpass.getpass('Repeat password: '):raise SystemExit('Passwords differ.')
        create(args.image,secret,args.capacity);print('Created an AES-256 encrypted workspace.')
    elif args.command=='open':open_workspace(args.image,args.port,not args.no_browser)
    elif args.command=='import':
        from ..desktop_storage import DesktopStore
        with EncryptedWorkspace(args.image) as workspace:
            result=DesktopStore(workspace.home).import_archive(args.archive)
            print(json.dumps(result))
        print('Imported archive verified; encrypted workspace locked. Original archive retained.')
    elif args.command=='lock':
        lock_workspace(args.image);print('Encrypted workspace locked.')
    elif args.command=='backup':
        backup(args.image,args.output);print('Encrypted backup copied and verified.')


if __name__=='__main__':main()
