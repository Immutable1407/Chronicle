"""Explicit transcription jobs and a configurable multipart HTTP adapter.

No provider, model, or key is selected implicitly. There are no automatic retries.
"""
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave

from .archive import canonical, identity


class TranscriptionError(Exception):
    def __init__(self, code, retryable=False):
        self.code, self.retryable = code, retryable
        super().__init__(code)


class Profile:
    def __init__(self, name, endpoint, model, key_env='CHRONICLE_ASR_API_KEY', fields=None):
        parsed = urllib.parse.urlsplit(endpoint)
        loopback = parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost', '::1')
        if (parsed.scheme != 'https' and not loopback) or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('invalid_provider_endpoint')
        if not name.strip() or not model.strip() or '\r' in model or '\n' in model:
            raise ValueError('provider_name_and_model_required')
        if not re.fullmatch('[A-Z][A-Z0-9_]*', key_env):
            raise ValueError('invalid_key_environment_name')
        fields = fields or {}
        allowed = {'language', 'languages[]', 'response_format', 'prompt', 'keywords[]'}
        if not isinstance(fields, dict) or set(fields) - allowed or not all(isinstance(v, str) for v in fields.values()):
            raise ValueError('unsupported_provider_fields')
        if 'language' in fields and 'languages[]' in fields:
            raise ValueError('conflicting_language_fields')
        self.data = dict(name=name, endpoint=endpoint, model=model, key_env=key_env, fields=fields)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class HttpTranscriber:
    def __init__(self, profile, timeout=60, max_bytes=25 * 1024 * 1024):
        self.profile, self.timeout, self.max_bytes = profile, timeout, max_bytes

    def transcribe(self, path):
        profile = self.profile.data
        key = os.environ.get(profile['key_env'])
        if not key:
            raise TranscriptionError('provider_key_missing')
        path = Path(path)
        if path.stat().st_size > self.max_bytes:
            raise TranscriptionError('audio_too_large')
        try:
            with wave.open(str(path), 'rb') as audio:
                if audio.getnframes() <= 0:
                    raise TranscriptionError('audio_empty')
        except (wave.Error, EOFError):
            raise TranscriptionError('wav_audio_required') from None
        boundary = 'chronicle-' + uuid.uuid4().hex
        chunks = []
        fields = dict(profile['fields'], model=profile['model'])
        for name, value in fields.items():
            chunks.append(('--' + boundary + '\r\nContent-Disposition: form-data; name="' + name +
                           '"\r\n\r\n' + value + '\r\n').encode())
        chunks.extend([('--' + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="voice.wav"\r\n'
                        'Content-Type: audio/wav\r\n\r\n').encode(), path.read_bytes(),
                       ('\r\n--' + boundary + '--\r\n').encode()])
        request = urllib.request.Request(profile['endpoint'], data=b''.join(chunks), method='POST', headers={
            'Authorization': 'Bearer ' + key, 'Content-Type': 'multipart/form-data; boundary=' + boundary})
        try:
            opener = urllib.request.build_opener(NoRedirect())
            with opener.open(request, timeout=self.timeout) as response:
                payload = response.read(1024 * 1024 + 1)
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            raise TranscriptionError('http_%d' % code, code == 429 or code >= 500) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise TranscriptionError('network_error', True) from None
        if len(payload) > 1024 * 1024:
            raise TranscriptionError('response_too_large')
        try:
            decoded = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            raise TranscriptionError('invalid_provider_json') from None
        if not isinstance(decoded, dict) or not isinstance(decoded.get('text'), str):
            raise TranscriptionError('transcript_field_missing')
        text = decoded['text'].strip()
        if not text:
            raise TranscriptionError('empty_transcript')
        return text


class TranscriptionJobs:
    def __init__(self, archive):
        self.archive = archive

    def queue(self, message, profile):
        message=self.archive.resolve_message(message)
        db = self.archive.db
        row = db.execute('SELECT type_code FROM messages WHERE id=?', (message,)).fetchone()
        if row is None or row['type_code'] & 0xffffffff != 34:
            raise ValueError('voice_message_required')
        audio = db.execute("SELECT object_id FROM attachments WHERE message_id=? AND role='voice_playback' AND status='available'", (message,)).fetchone()
        if not audio:
            raise ValueError('voice_audio_not_prepared')
        spec = canonical(profile.data)
        job = identity(audio['object_id'], spec)
        with db:
            db.execute('INSERT OR IGNORE INTO transcription_jobs(id,audio_id,profile,status,updated_at) VALUES (?,?,?,?,?)',
                       (job, audio['object_id'], spec, 'pending', int(time.time())))
            db.execute('INSERT OR IGNORE INTO message_jobs VALUES (?,?)', (message, job))
        return job

    def run(self, job, transcriber):
        db = self.archive.db
        row = db.execute('SELECT * FROM transcription_jobs WHERE id=?', (job,)).fetchone()
        if row is None:
            raise ValueError('job_not_found')
        if row['profile'] != canonical(transcriber.profile.data):
            raise ValueError('provider_profile_mismatch')
        if row['status'] != 'pending':
            return row['status']
        path = self.archive.object_path(row['audio_id'])
        with db:
            claimed = db.execute("UPDATE transcription_jobs SET status='running',attempts=attempts+1,updated_at=? WHERE id=? AND status='pending'",
                                 (int(time.time()), job)).rowcount
        if not claimed:
            return db.execute('SELECT status FROM transcription_jobs WHERE id=?', (job,)).fetchone()['status']
        try:
            text = transcriber.transcribe(path)
            if not isinstance(text, str) or not text.strip():
                raise TranscriptionError('empty_transcript')
        except Exception as error:
            known = isinstance(error, TranscriptionError)
            code = error.code if known else 'transcription_internal_error'
            retryable = bool(error.retryable) if known else False
            with db:
                db.execute("UPDATE transcription_jobs SET status='failed',error_code=?,retryable=?,updated_at=? WHERE id=?",
                           (code, retryable, int(time.time()), job))
            return 'failed'
        with db:
            db.execute("UPDATE transcription_jobs SET status='succeeded',text=?,error_code=NULL,retryable=0,updated_at=? WHERE id=?",
                       (text.strip(), int(time.time()), job))
        return 'succeeded'

    def retry(self, job):
        with self.archive.db:
            updated = self.archive.db.execute("UPDATE transcription_jobs SET status='pending',error_code=NULL,updated_at=? WHERE id=? AND status='failed'",
                                              (int(time.time()), job)).rowcount
        if not updated:
            raise ValueError('failed_job_required')

    def recover_interrupted(self, job):
        # An interrupted network call may already have been billed; recovery is explicit.
        with self.archive.db:
            count = self.archive.db.execute("UPDATE transcription_jobs SET status='failed',error_code='interrupted_result_unknown',retryable=1,updated_at=? WHERE id=? AND status='running'",
                                            (int(time.time()), job)).rowcount
        if not count:
            raise ValueError('running_job_required')
