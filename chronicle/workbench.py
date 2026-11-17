"""Local Chronicle workbench: private loopback server and application services."""
import argparse
import base64
from collections import Counter
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

from .archive import Archive, identity
from .message_format import quoted_reply, is_quoted_reply


STATIC = Path(__file__).with_name('web')


def mac_modules():
    from .macos.reader import MacSource
    from .macos.resources import collect, store_bytes
    return MacSource, collect, store_bytes


def present_message(message, conversation_name='', people=None):
    people = people or {}
    kind = int(message.get('type_code', 1)) & 0xffffffff
    body = message.get('body', '')
    result = {'id': message.get('id', message.get('archive_identity')), 'timestamp': message['timestamp'],
              'datetime': datetime.fromtimestamp(message['timestamp'], ZoneInfo('Asia/Shanghai')).isoformat(),
              'sender': message.get('sender_name') or people.get(message.get('sender_id', message.get('sender')), '参与者'),
              'conversation': conversation_name or message.get('conversation_name', ''),
              'kind': {1: 'text', 3: 'image', 34: 'voice', 43: 'video', 49: 'file'}.get(kind, 'other'),
              'text': body if kind == 1 else '', 'title': '', 'attachments': [],
              'transcript': message.get('transcript', {'state': 'not_requested'}),
              'source_conflict': message.get('source_conflict', False)}
    if kind != 1:
        try:
            xml = ET.fromstring(body)
            result['title'] = xml.findtext('.//appmsg/title') or ''
            if kind == 49 and xml.findtext('.//appmsg/type') not in ('6', None):
                result['kind'] = 'other'
            if kind == 49 and message.get('account') != 'manual':
                quoted = quoted_reply(xml, people)
                if quoted:
                    result.update(kind='quote', title='', **quoted)
            voice = xml.find('.//voicemsg')
            if voice is not None:
                result['duration'] = round(int(voice.get('voicelength', '0')) / 1000, 1)
        except (ET.ParseError, ValueError):
            pass  # Keep opaque source metadata in the archive, not in the product view.
    if message.get('account') == 'manual':
        result['sender'] = message.get('sender') or '我'
        result['text'] = body
    return result


class Workbench:
    def __init__(self, archive, cache, keys=None, codec_python=None):
        self.archive_root = Path(archive).resolve()
        self.cache = Path(cache).resolve()
        self.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.cache, 0o700)
        self.keys = Path(keys).resolve() if keys else None
        # Preserve a venv's executable symlink: resolving it bypasses its site-packages.
        self.codec_python = Path(codec_python).absolute() if codec_python else None
        self.sessions = {}
        self.lock = threading.RLock()
        with Archive(self.archive_root):
            pass

    def source(self):
        if not self.keys or not self.keys.is_file():
            raise ValueError('wechat_not_connected')
        if sys.platform != 'darwin':
            raise ValueError('wechat_platform_not_supported')
        return mac_modules()[0](self.keys)

    def close(self):
        for session in self.sessions.values():
            shutil.rmtree(session['directory'], ignore_errors=True)
        self.sessions.clear()

    def people(self, source=None):
        source = source or self.source()
        with source.open('contact/contact.db') as db:
            rows = db.query('SELECT username,nick_name,remark FROM contact')
        names = {r['username']: r['remark'] or r['nick_name'] or '参与者' for r in rows}
        account = source.root.parent.name
        own = re.sub(r'_[A-Za-z0-9]{4}$', '', account)
        if account.startswith('wxid_'):
            own = re.match(r'wxid_[^_]+', account).group()
        names[own] = '我'
        return names

    def projects(self):
        with Archive(self.archive_root) as archive:
            rows = archive.projects()
            for row in rows:
                row['message_count'] = archive.db.execute('SELECT count(*) FROM project_messages WHERE project_id=?', (row['id'],)).fetchone()[0]
            return rows

    def status(self):
        try:
            source = self.source()
            conversations = source.conversations()
            return {'source_available': True, 'conversation_count': len(conversations)}
        except Exception:
            return {'source_available': False}

    def conversations(self):
        return self.source().conversations()

    def browse(self, conversation, before=None):
        for token, value in list(self.sessions.items()):
            if time.monotonic() - value['created'] > 3600:
                shutil.rmtree(value['directory'], ignore_errors=True)
                del self.sessions[token]
        source = self.source()
        conversations = {r['conversation_id']: r for r in source.conversations()}
        if conversation not in conversations:
            raise ValueError('conversation_not_found')
        page = source.message_page(conversation, before=before)
        token = secrets.token_urlsafe(24)
        directory = self.cache / token
        directory.mkdir(mode=0o700)
        self.sessions[token] = {'created': time.monotonic(), 'source': source,
            'messages': {m['archive_identity']: m for m in page['messages']}, 'directory': directory,
            'conversation': conversations[conversation], 'resources': {}}
        people = self.people(source)
        return {'token': token, 'messages': [present_message(m, conversations[conversation]['display_name'], people)
                for m in page['messages']], 'has_more': page['has_more'], 'before': page['before']}

    def session(self, token):
        session = self.sessions.get(token)
        if not session or time.monotonic() - session['created'] > 3600:
            raise ValueError('selection_expired')
        return session

    def prepare(self, token, message_id):
        session = self.session(token)
        if message_id not in session['messages']:
            raise ValueError('message_not_in_selection')
        if message_id in session['resources']:
            return session['resources'][message_id]
        message = session['messages'][message_id]
        _, collect, store_bytes = mac_modules()
        rows = collect(session['source'], [message], session['directory'])
        if not rows:
            return None
        record = rows[0]
        frozen = bool(getattr(sys, 'frozen', False))
        if message['type_code'] & 0xffffffff == 34 and record.get('status') == 'copied' and (frozen or self.codec_python and self.codec_python.is_file()):
            output = session['directory'] / (message_id + '.wav')
            tool = Path(__file__).resolve().parent / 'macos/decode_voice.py'
            command = [sys.executable, '--decode-voice'] if frozen else [str(self.codec_python), str(tool)]
            result = subprocess.run(command + [
                str(session['directory'] / 'assets' / record['local_asset']), str(output)],
                capture_output=True, timeout=30)
            if result.returncode == 0 and output.is_file():
                record['voice_playback_asset'] = store_bytes(session['directory'] / 'assets', output.read_bytes(), '.wav')
                output.unlink()
        session['resources'][message_id] = record
        return record

    def source_media(self, token, message):
        record = self.prepare(token, message)
        if not record:
            raise ValueError('message_has_no_media')
        result = {'status': record.get('status'), 'assets': []}
        for field, role in [('preview_asset', 'preview'), ('voice_playback_asset', 'voice_playback'), ('local_asset', 'primary')]:
            name = record.get(field)
            if name:
                result['assets'].append({'role': role, 'extension': Path(name).suffix,
                    'url': '/source-assets/' + token + '/' + name, 'name': record.get('name', ''),
                    'preview_first_frame_only': record.get('preview_first_frame_only', False),
                    'thumbnail_only': record.get('thumbnail_only', False)})
        return result

    def ingest(self, project, selections):
        if not isinstance(selections, list) or not selections:
            raise ValueError('select_messages_first')
        # Resolve all requested messages before writing a batch. No arbitrary source paths from the browser.
        grouped = {}
        for selection in selections:
            session = self.session(selection['token'])
            message_id = selection['id']
            if message_id not in session['messages']:
                raise ValueError('message_not_in_selection')
            grouped.setdefault(selection['token'], set()).add(message_id)
        directory = self.cache / ('ingest-' + secrets.token_hex(12))
        (directory / 'assets').mkdir(parents=True, mode=0o700)
        messages, records, names, accounts = {}, {}, {}, set()
        try:
            for token, chosen in grouped.items():
                session = self.session(token)
                accounts.add(session['source'].account)
                names[session['conversation']['conversation_id']] = session['conversation']['display_name']
                for key in chosen:
                    messages[key] = session['messages'][key]
                    record = self.prepare(token, key)
                    if record:
                        records[key] = record
                        for field in ('local_asset', 'preview_asset', 'voice_playback_asset'):
                            if record.get(field):
                                name = record[field]
                                shutil.copyfile(session['directory'] / 'assets' / name, directory / 'assets' / name)
            if len(accounts) != 1:
                raise ValueError('selection_accounts_mismatch')
            with Archive(self.archive_root) as archive:
                account = accounts.pop()
                report = archive.import_messages(project, {'account_fingerprint': account, 'messages': list(messages.values())},
                                                  list(records.values()), directory / 'assets', names=names)
                people = self.people()
                actors = {m.get('sender_id') for m in messages.values()}
                archive.set_people(account, {person: name for person, name in people.items() if person in actors})
            return {'selected': report['selected'], 'new_messages': report['new_messages'],
                    'linked_to_project': report['linked_to_project'],
                    'conflicts': report['source_conflicts'] + report['resource_conflicts']}
        finally:
            shutil.rmtree(directory)

    def timeline(self, project, date=None):
        try:
            source = self.source()
            people = self.people(source)
        except Exception:
            source, people = None, {}
        with Archive(self.archive_root) as archive:
            rows = archive.timeline(project, date)
            all_rows = rows if not date else archive.timeline(project)
            if source:
                actors = {r['sender'] for r in all_rows if r['account'] == source.account}
                archive.set_people(source.account, {person: name for person, name in people.items() if person in actors})
                for row in rows:
                    if row['account'] == source.account and row['sender'] in people:
                        row['sender_name'] = people[row['sender']]
        result = []
        for row in rows:
            item = present_message(row, people=people)
            for asset in row['attachments']:
                if item['kind'] == 'quote':
                    continue  # Legacy placeholders are not missing quoted attachments.
                metadata = asset['metadata']
                item['attachments'].append({'role': asset['role'], 'status': asset['status'],
                    'url': '/assets/' + row['id'] + '/' + asset['role'] if asset['object_id'] else None,
                    'extension': metadata.get('extension', Path(metadata.get('name', '')).suffix),
                    'bytes': metadata.get('bytes'),
                    'name': metadata.get('name', ''), 'thumbnail_only': metadata.get('thumbnail_only', False),
                    'preview_first_frame_only': metadata.get('preview_first_frame_only', False)})
                if metadata.get('name'):
                    item['title'] = metadata['name']
            result.append(item)
        return {'messages': result, 'days': dict(Counter(row['datetime'][:10] for row in all_rows)),
                'total': len(all_rows), 'sources': len({r['source_id'] for r in all_rows if r['account'] != 'manual'}),
                'resources': sum(not is_quoted_reply(r) and any(a['role'] == 'primary' for a in r['attachments']) for r in all_rows)}


class Handler(BaseHTTPRequestHandler):
    server_version = 'Chronicle'

    @property
    def app(self):
        return self.server.app

    def log_message(self, *_args):
        pass

    def allowed(self, write=False):
        try:
            host = urllib.parse.urlsplit('http://' + self.headers.get('Host', ''))
            if host.hostname not in ('127.0.0.1', 'localhost') or host.port != self.server.server_port:
                return False
            origin = self.headers.get('Origin')
            if write and not origin:
                return False
            if origin:
                value = urllib.parse.urlsplit(origin)
                if value.scheme != 'http' or value.hostname not in ('127.0.0.1', 'localhost') or value.port != self.server.server_port:
                    return False
            return self.headers.get('Sec-Fetch-Site') != 'cross-site'
        except ValueError:
            return False

    def send_common_headers(self, code, mime, length=None):
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; object-src 'none'; frame-ancestors 'none'")
        if length is not None:
            self.send_header('Content-Length', str(length))

    def json(self, value, code=200):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_common_headers(code, 'application/json; charset=utf-8', len(data))
        self.end_headers()
        self.wfile.write(data)

    def file(self, path, name, mime=None):
        path = Path(path)
        size = path.stat().st_size
        start, end, code = 0, size-1, 200
        range_header = self.headers.get('Range')
        if range_header:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header)
            if not match or not any(match.groups()):
                return self.json({'error': 'invalid_range'}, 416)
            if not match[1]:
                start = max(0, size-int(match[2]))
            else:
                start = int(match[1])
                end = min(size-1, int(match[2])) if match[2] else size-1
            if start > end or start >= size:
                self.send_common_headers(416, 'application/octet-stream', 0)
                self.send_header('Content-Range', 'bytes */' + str(size))
                self.end_headers()
                return
            code = 206
        mime = mime or mimetypes.guess_type(name)[0] or 'application/octet-stream'
        if mime in ('text/html', 'application/javascript', 'image/svg+xml'):
            mime = 'application/octet-stream'
        self.send_common_headers(code, mime, max(0, end-start+1))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Disposition', ('attachment' if mime == 'application/octet-stream' else 'inline') +
                         "; filename*=UTF-8''" + urllib.parse.quote(name, safe=''))
        if code == 206:
            self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, end, size))
        self.end_headers()
        with path.open('rb') as stream:
            stream.seek(start)
            remaining = max(0, end-start+1)
            while remaining:
                data = stream.read(min(65536, remaining))
                if not data:
                    break
                self.wfile.write(data)
                remaining -= len(data)

    def do_GET(self):
        if not self.allowed():
            return self.json({'error': 'origin_not_allowed'}, 403)
        try:
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)
            if path in ('/', '/app.js', '/style.css'):
                filename = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}[path]
                data = (STATIC / filename).read_bytes()
                self.send_common_headers(200, {'index.html': 'text/html; charset=utf-8', 'app.js': 'application/javascript; charset=utf-8',
                                  'style.css': 'text/css; charset=utf-8'}[filename], len(data))
                self.end_headers()
                return self.wfile.write(data)
            if path == '/api/status':
                return self.json(self.app.status())
            if path == '/api/health':
                return self.json({'application': 'Chronicle', 'protocol': 1, 'archive': identity(str(self.app.archive_root))})
            if path == '/api/projects':
                return self.json({'projects': self.app.projects()})
            with self.app.lock:
                if path == '/api/conversations':
                    return self.json({'conversations': self.app.conversations()})
                if path == '/api/browse':
                    cursor = json.loads(query['before'][0]) if query.get('before') else None
                    return self.json(self.app.browse(query['conversation'][0], cursor))
                if path == '/api/timeline':
                    return self.json(self.app.timeline(query['project'][0], query.get('date', [None])[0]))
                if path == '/api/source-media':
                    return self.json(self.app.source_media(query['token'][0], query['message'][0]))
                if path.startswith('/source-assets/'):
                    parts = path.split('/')
                    if len(parts) != 4 or not re.fullmatch('[0-9a-f]{64}\\.[A-Za-z0-9]+', parts[3]):
                        raise ValueError('asset_not_found')
                    session = self.app.session(parts[2])
                    candidate = session['directory'] / 'assets' / parts[3]
                    return self.file(candidate, parts[3])
                if path.startswith('/assets/'):
                    parts = path.split('/')
                    if len(parts) != 4:
                        raise ValueError('asset_not_found')
                    with Archive(self.app.archive_root) as archive:
                        row = archive.db.execute('SELECT * FROM attachments WHERE message_id=? AND role=?', (parts[2], parts[3])).fetchone()
                        if not row or not row['object_id']:
                            raise ValueError('asset_not_found')
                        metadata = json.loads(row['metadata'])
                        name = metadata.get('name') if parts[3] == 'primary' else None
                        name = name or 'chronicle' + metadata.get('extension', '.bin')
                        return self.file(archive.object_path(row['object_id']), name)
            self.json({'error': 'not_found'}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (ValueError, KeyError, FileNotFoundError) as error:
            known = {'selection_expired', 'wechat_not_connected', 'select_messages_first'}
            self.json({'error': str(error) if str(error) in known else 'request_unavailable'}, 400)
        except Exception:
            self.json({'error': 'operation_failed'}, 500)

    def do_POST(self):
        if not self.allowed(write=True):
            return self.json({'error': 'origin_not_allowed'}, 403)
        if self.headers.get_content_type() != 'application/json':
            return self.json({'error': 'json_required'}, 415)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 17 * 1024 * 1024:
                return self.json({'error': 'request_size_invalid'}, 413)
            data = json.loads(self.rfile.read(length))
            with self.app.lock:
                if self.path == '/api/projects':
                    with Archive(self.app.archive_root) as archive:
                        return self.json({'project_id': archive.create_project(data['name'])}, 201)
                if self.path == '/api/archive':
                    return self.json(self.app.ingest(data['project'], data['selections']))
                if self.path == '/api/notes':
                    directory = self.app.cache / ('note-' + secrets.token_hex(12))
                    path = None
                    try:
                        if data.get('file'):
                            name = Path(data['file']['name'].replace('\\', '/')).name
                            if name in ('', '.', '..') or '\x00' in name:
                                raise ValueError('invalid_filename')
                            content = base64.b64decode(data['file']['data'], validate=True)
                            if len(content) > 12 * 1024 * 1024:
                                raise ValueError('file_too_large')
                            directory.mkdir(mode=0o700)
                            path = directory / name
                            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                            with os.fdopen(descriptor, 'wb') as stream:
                                stream.write(content)
                        with Archive(self.app.archive_root) as archive:
                            return self.json({'message_id': archive.add_manual(data['project'], data.get('text', ''), path=path,
                                sender=data.get('sender', '我'), timestamp=data.get('timestamp'))}, 201)
                    finally:
                        shutil.rmtree(directory, ignore_errors=True)
                if self.path == '/api/transcript':
                    with Archive(self.app.archive_root) as archive:
                        archive.correct_transcript(data['message'], data['text'])
                    return self.json({'state': 'corrected'})
            self.json({'error': 'not_found'}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (ValueError, KeyError, TypeError) as error:
            known = {'selection_expired', 'wechat_not_connected', 'select_messages_first'}
            self.json({'error': str(error) if str(error) in known else 'request_unavailable'}, 400)
        except Exception:
            self.json({'error': 'operation_failed'}, 500)


def serve(app, port=8765):
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.app = app
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('data/workbench.json'))
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--open', action='store_true')
    args = parser.parse_args()
    config = json.loads(args.config.read_text()) if args.config.is_file() else {}
    base = args.config.resolve().parent
    def configured(key, default=None):
        value = config.get(key, default)
        return (base / value).absolute() if value else None
    app = Workbench(configured('archive', 'archive'), configured('cache', 'workbench-cache'),
                    configured('wechat_keys'), configured('codec_python'))
    try:
        server = serve(app, args.port)
    except OSError:
        if args.open:
            try:
                existing_url = 'http://127.0.0.1:%d' % args.port
                with urllib.request.urlopen(existing_url + '/api/health', timeout=2) as response:
                    health = json.load(response)
                if health.get('application') == 'Chronicle' and health.get('archive') == identity(str(app.archive_root)):
                    webbrowser.open(existing_url)
                    app.close()
                    return
            except Exception:
                pass
        app.close()
        print(json.dumps({'error': 'port_unavailable'}), flush=True)
        raise SystemExit(2)
    url = 'http://127.0.0.1:%d' % server.server_port
    print(json.dumps({'url': url}), flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.close()


if __name__ == '__main__':
    main()
