import base64
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import urllib.error
import urllib.request

from chronicle.archive import Archive
from chronicle.workbench import Workbench, present_message, serve
from test_archive import message


class WorkbenchHTTP(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='chronicle-http-ui-')
        self.root = Path(self.temporary.name)
        self.app = Workbench(self.root / 'archive', self.root / 'cache')
        self.server = serve(self.app, 0)
        self.url = 'http://127.0.0.1:%d' % self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.app.close()
        self.temporary.cleanup()

    def request(self, path, data=None, headers=None):
        fields = {'Origin': self.url}
        fields.update(headers or {})
        if data is not None:
            fields['Content-Type'] = 'application/json'
        request = urllib.request.Request(self.url + path, data=json.dumps(data).encode() if data is not None else None, headers=fields)
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, dict(response.headers), response.read()

    def test_application_files_and_local_origin_boundary(self):
        status, headers, body = self.request('/')
        self.assertEqual(status, 200)
        self.assertIn(b'Chronicle', body)
        self.assertIn("script-src 'self'", headers['Content-Security-Policy'])
        self.assertEqual(json.loads(self.request('/api/health')[2])['application'], 'Chronicle')
        self.assertEqual(self.request('/api/projects', headers={'Origin': 'https://external.example'})[0], 403)
        self.assertEqual(self.request('/api/projects', headers={'Host': 'external.example'})[0], 403)
        self.assertEqual(self.request('/api/projects', headers={'Host': '127.0.0.1:bad'})[0], 403)

    def test_project_note_upload_and_byte_range(self):
        status, _, data = self.request('/api/projects', {'name': 'test'})
        self.assertEqual(status, 201)
        project = json.loads(data)['project_id']
        content = b'test file content'
        status, _, data = self.request('/api/notes', {'project': project, 'text': 'manual note',
            'sender': '我', 'timestamp': 1794830000, 'file': {'name': '../report.pdf', 'data': base64.b64encode(content).decode()}})
        self.assertEqual(status, 201)
        message = json.loads(data)['message_id']
        status, headers, data = self.request('/assets/' + message + '/primary', headers={'Range': 'bytes=2-5'})
        self.assertEqual((status, data), (206, content[2:6]))
        self.assertEqual(headers['Content-Range'], 'bytes 2-5/17')
        self.assertEqual(self.request('/assets/' + message + '/primary', headers={'Range': 'bytes=900-1000'})[0], 416)
        status, _, data = self.request('/api/timeline?project=' + project + '&date=2026-11-16')
        parsed = json.loads(data)
        self.assertEqual(parsed['sources'], 0)
        record = parsed['messages'][0]
        self.assertEqual(record['sender'], '我')
        self.assertEqual(record['title'], 'report.pdf')
        self.assertNotIn('raw_json', record)
        self.assertEqual(list((self.root / 'cache').iterdir()), [])

    def test_source_failure_does_not_block_existing_archive(self):
        self.assertFalse(json.loads(self.request('/api/status')[2])['source_available'])
        project = json.loads(self.request('/api/projects', {'name': 'offline'})[2])['project_id']
        self.assertEqual(self.request('/api/timeline?project=' + project)[0], 200)
        self.assertEqual(self.request('/api/conversations')[0], 400)

    def test_unselected_source_ids_cannot_be_archived(self):
        project = json.loads(self.request('/api/projects', {'name': 'test'})[2])['project_id']
        self.assertEqual(self.request('/api/archive', {'project': project, 'selections': [{'token': 'unknown', 'id': 'unknown'}]})[0], 400)
        with Archive(self.app.archive_root) as archive:
            self.assertEqual(archive.timeline(project), [])

    def test_active_file_types_are_downloaded_instead_of_executed(self):
        project = json.loads(self.request('/api/projects', {'name': 'test'})[2])['project_id']
        data = {'project': project, 'file': {'name': 'page.html', 'data': base64.b64encode(b'<script>unsafe()</script>').decode()}}
        message = json.loads(self.request('/api/notes', data)[2])['message_id']
        _, headers, _ = self.request('/assets/' + message + '/primary')
        self.assertEqual(headers['Content-Type'], 'application/octet-stream')
        self.assertTrue(headers['Content-Disposition'].startswith('attachment'))

    def test_cdn_urls_and_media_credentials_are_not_sent_as_message_text(self):
        record = present_message({'archive_identity': 'test', 'timestamp': 1794830000, 'type_code': 49,
            'body': '<msg><appmsg><title>document.pdf</title><type>6</type><aeskey>private-secret</aeskey><cdnattachurl>https://example.com/private</cdnattachurl></appmsg></msg>'})
        self.assertEqual(record['title'], 'document.pdf')
        self.assertNotIn('private-secret', json.dumps(record))
        self.assertNotIn('example.com', json.dumps(record))

    def test_codec_python_keeps_virtual_environment_path(self):
        import sys
        executable = self.root / 'venv/bin/python'
        executable.parent.mkdir(parents=True)
        executable.symlink_to(sys.executable)
        app = Workbench(self.root / 'second-archive', self.root / 'second-cache', codec_python=executable)
        self.assertEqual(app.codec_python, executable.absolute())
        self.assertNotEqual(app.codec_python, executable.resolve())
        app.close()

    def staged_session(self, token, row):
        directory = self.app.cache / token
        directory.mkdir()
        self.app.sessions[token] = {'created': time.monotonic(), 'source': SimpleNamespace(account='a' * 64),
            'messages': {row['archive_identity']: row}, 'directory': directory,
            'conversation': {'conversation_id': row['conversation_id'], 'display_name': row['conversation_id']},
            'resources': {row['archive_identity']: None}}

    def test_ingest_only_saves_selected_actor_names_and_reads_them_offline(self):
        row = message(1)
        self.staged_session('selection', row)
        self.app.people = lambda: {'person': '老师', 'unrelated-person': '不相关联系人'}
        with Archive(self.app.archive_root) as archive:
            project = archive.create_project('test')
        self.app.ingest(project, [{'token': 'selection', 'id': row['archive_identity']}])
        with Archive(self.app.archive_root) as archive:
            self.assertEqual(archive.db.execute('SELECT count(*) FROM people').fetchone()[0], 1)
        self.assertEqual(self.app.timeline(project)['messages'][0]['sender'], '老师')

    def test_cross_selection_failure_rolls_back_all_messages(self):
        first = message(1)
        second = dict(message(2, conversation='other'), archive_identity='invalid')
        self.staged_session('first', first)
        self.staged_session('second', second)
        with Archive(self.app.archive_root) as archive:
            project = archive.create_project('test')
        with self.assertRaisesRegex(ValueError, 'message_identity_mismatch'):
            self.app.ingest(project, [{'token': 'first', 'id': first['archive_identity']},
                                      {'token': 'second', 'id': second['archive_identity']}])
        with Archive(self.app.archive_root) as archive:
            self.assertEqual(archive.timeline(project), [])
        self.assertEqual(list(self.app.cache.glob('ingest-*')), [])


if __name__ == '__main__':
    unittest.main()
