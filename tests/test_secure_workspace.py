"""Real encrypted-image lifecycle with generated data, only on macOS."""
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import tempfile
import unittest
import urllib.request


@unittest.skipUnless(sys.platform=='darwin','macOS encrypted disk images')
class EncryptedWorkspaceTests(unittest.TestCase):
    def test_archive_server_locks_and_backup_reopens_without_plaintext_export(self):
        from chronicle.macos.secure_workspace import create,EncryptedWorkspace,backup,mounted,open_workspace
        with tempfile.TemporaryDirectory(prefix='chronicle-secure-fixture-') as folder:
            image=Path(folder)/'Fixture.sparsebundle';password=secrets.token_urlsafe(24)
            create(image,password,'256m')
            code='import sys;from chronicle.macos import secure_workspace as s;s.getpass.getpass=lambda _:sys.stdin.readline().rstrip("\\n");s.open_workspace(sys.argv[1],port=0,open_browser=False)'
            process=subprocess.Popen([sys.executable,'-c',code,str(image)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                process.stdin.write(password+'\n');process.stdin.flush();process.stdin.close()
                first=process.stdout.readline();url=json.loads(first)['url']
                client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
                with client.open(url,timeout=10) as response:self.assertEqual(response.status,200)
                base=url.split('/?ticket=')[0]
                with client.open(base+'/api/status',timeout=10) as response:self.assertTrue(json.load(response)['archive_encrypted'])
                request=urllib.request.Request(base+'/api/projects',data=json.dumps({'name':'Synthetic encrypted archive'}).encode(),headers={'Origin':base,'Content-Type':'application/json'})
                with client.open(request,timeout=10) as response:project=json.load(response)['project_id']
                request=urllib.request.Request(base+'/api/notes',data=json.dumps({'project':project,'text':'Synthetic secret note'}).encode(),headers={'Origin':base,'Content-Type':'application/json'})
                with client.open(request,timeout=10) as response:self.assertEqual(response.status,201)
                with self.assertRaises(RuntimeError):backup(image,Path(folder)/'Busy.sparsebundle')
                # Reopening must keep the running protected volume mounted, not detach it.
                open_workspace(image,port=0,open_browser=False)
                self.assertIsNotNone(mounted(image))
            finally:
                process.send_signal(signal.SIGINT)
                try:process.wait(timeout=40)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=10);raise
                process.stdout.close();errors=process.stderr.read();process.stderr.close()
            self.assertEqual(process.returncode,0,errors);self.assertNotIn(password,errors);self.assertIsNone(mounted(image))
            with self.assertRaises(RuntimeError):
                with EncryptedWorkspace(image,'wrong-password-fixture'):pass
            destination=Path(folder)/'Backup.sparsebundle';backup(image,destination)
            self.assertFalse(any(p.suffix in ('.sqlite3','.wav','.silk') for p in destination.rglob('*')))
            with EncryptedWorkspace(destination,password) as workspace:
                from chronicle.archive import Archive
                with Archive(workspace.home/'archive') as archive:
                    self.assertEqual(archive.timeline(project)[0]['body'],'Synthetic secret note')
            self.assertIsNone(mounted(destination))


if __name__=='__main__':unittest.main()
