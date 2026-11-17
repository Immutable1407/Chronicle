import io
import json
import os
from pathlib import Path
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest.mock import patch
import wave

from chronicle.archive import Archive
from chronicle.asr import Profile, HttpTranscriber, TranscriptionJobs, TranscriptionError
from test_archive import message, bundle


def wav_bytes():
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(24000)
        writer.writeframes(b'\x01\x00' * 2400)
    return stream.getvalue()


class Worker:
    def __init__(self, profile, failure=None):
        self.profile, self.failure, self.calls = profile, failure, 0

    def transcribe(self, path):
        self.calls += 1
        if self.failure:
            raise self.failure
        return '人工测试返回，不是真实识别结果'


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='chronicle-job-')
        self.root = Path(self.temporary.name)
        self.archive = Archive(self.root / 'archive')
        project = self.archive.create_project('private test project')
        self.voice = message(1, kind=34, body='<msg>private source metadata</msg>')
        self.archive.import_messages(project, bundle(self.voice))
        self.audio = self.root / 'audio.wav'
        self.audio.write_bytes(wav_bytes())
        self.archive.attach_derivative(self.voice['archive_identity'], self.audio, 'voice_playback')
        self.profile = Profile('test', 'http://127.0.0.1:1/transcriptions', 'test-model', 'TEST_ASR_KEY')
        self.jobs = TranscriptionJobs(self.archive)

    def tearDown(self):
        self.archive.close()
        self.temporary.cleanup()

    def test_cache_prevents_duplicate_requests_and_correction_is_preserved(self):
        job = self.jobs.queue(self.voice['archive_identity'], self.profile)
        worker = Worker(self.profile)
        self.assertEqual(self.jobs.run(job, worker), 'succeeded')
        self.assertEqual(self.jobs.queue(self.voice['archive_identity'], self.profile), job)
        self.assertEqual(self.jobs.run(job, worker), 'succeeded')
        self.assertEqual(worker.calls, 1)
        self.archive.correct_transcript(self.voice['archive_identity'], '人工纠正后的内容')
        self.assertEqual(self.archive.transcript(self.voice['archive_identity'])['text'], '人工纠正后的内容')
        self.assertEqual(self.jobs.run(job, worker), 'succeeded')
        self.assertEqual(self.archive.transcript(self.voice['archive_identity'])['origin'], 'manual')

    def test_failure_requires_explicit_retry_and_does_not_change_source(self):
        job = self.jobs.queue(self.voice['archive_identity'], self.profile)
        worker = Worker(self.profile, TranscriptionError('http_429', True))
        self.assertEqual(self.jobs.run(job, worker), 'failed')
        self.assertEqual(self.jobs.run(job, worker), 'failed')
        self.assertEqual(worker.calls, 1)
        self.assertEqual(self.archive.db.execute('SELECT body FROM messages').fetchone()['body'], self.voice['body'])
        self.jobs.retry(job)
        worker.failure = None
        self.assertEqual(self.jobs.run(job, worker), 'succeeded')
        self.assertEqual(worker.calls, 2)

    def test_exceptions_are_redacted_and_running_jobs_are_not_implicitly_retried(self):
        job = self.jobs.queue(self.voice['archive_identity'], self.profile)
        self.jobs.run(job, Worker(self.profile, RuntimeError('secret-api-key-value')))
        row = self.archive.db.execute('SELECT * FROM transcription_jobs').fetchone()
        self.assertEqual(row['error_code'], 'transcription_internal_error')
        self.assertNotIn('secret-api-key-value', '\n'.join(self.archive.db.iterdump()))
        with self.archive.db:
            self.archive.db.execute("UPDATE transcription_jobs SET status='running' WHERE id=?", (job,))
        worker = Worker(self.profile)
        self.assertEqual(self.jobs.run(job, worker), 'running')
        self.assertEqual(worker.calls, 0)
        self.jobs.recover_interrupted(job)
        self.assertEqual(self.archive.transcript(self.voice['archive_identity'])['error_code'], 'interrupted_result_unknown')

    def test_unconfigured_client_does_not_make_request(self):
        job = self.jobs.queue(self.voice['archive_identity'], self.profile)
        with patch.dict(os.environ, {}, clear=True), patch('urllib.request.build_opener') as opener:
            self.assertEqual(self.jobs.run(job, HttpTranscriber(self.profile)), 'failed')
        opener.assert_not_called()
        self.assertEqual(self.archive.transcript(self.voice['archive_identity'])['error_code'], 'provider_key_missing')

    def test_only_prepared_voice_audio_can_be_queued(self):
        project = self.archive.projects()[0]['id']
        other = message(2, kind=34)
        self.archive.import_messages(project, bundle(other))
        with self.assertRaisesRegex(ValueError, 'not_prepared'):
            self.jobs.queue(other['archive_identity'], self.profile)


class ProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.requests = []
        cls.response_code = 200
        cls.response_body = b'{"text":"artificial protocol response"}'
        cls.redirect = None
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                data = self.rfile.read(int(self.headers['Content-Length']))
                cls.requests.append((self.path, dict(self.headers), data))
                self.send_response(cls.response_code)
                if cls.redirect:
                    self.send_header('Location', cls.redirect)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(cls.response_body)
            def log_message(self, *_args):
                pass
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='chronicle-http-')
        self.audio = Path(self.temporary.name) / 'private-original-name.wav'
        self.audio.write_bytes(wav_bytes())
        type(self).requests.clear()
        type(self).response_code = 200
        type(self).response_body = b'{"text":"artificial protocol response"}'
        type(self).redirect = None
        self.endpoint = 'http://127.0.0.1:%d/transcriptions' % self.server.server_port
        self.profile = Profile('test', self.endpoint, 'explicit-test-model', 'TEST_ASR_KEY')

    def tearDown(self):
        self.temporary.cleanup()

    def test_multipart_contains_only_configured_fields_and_audio(self):
        with patch.dict(os.environ, TEST_ASR_KEY='synthetic-key'):
            self.assertEqual(HttpTranscriber(self.profile).transcribe(self.audio), 'artificial protocol response')
        self.assertEqual(len(self.requests), 1)
        _path, headers, body = self.requests[0]
        self.assertEqual(headers['Authorization'], 'Bearer synthetic-key')
        self.assertIn(b'name="model"', body)
        self.assertIn(b'explicit-test-model', body)
        self.assertIn(b'filename="voice.wav"', body)
        self.assertIn(wav_bytes(), body)
        self.assertNotIn(str(self.audio).encode(), body)

    def test_redirect_does_not_forward_audio_or_credentials(self):
        type(self).response_code = 307
        type(self).redirect = self.endpoint + '/unexpected'
        with patch.dict(os.environ, TEST_ASR_KEY='synthetic-key'):
            with self.assertRaises(TranscriptionError) as caught:
                HttpTranscriber(self.profile).transcribe(self.audio)
        self.assertEqual(caught.exception.code, 'http_307')
        self.assertEqual(len(self.requests), 1)

    def test_bad_provider_responses_are_not_accepted_as_transcripts(self):
        for payload, code in [(b'not-json', 'invalid_provider_json'), (b'{"message":"secret detail"}', 'transcript_field_missing'),
                              (b'{"text":"  "}', 'empty_transcript')]:
            with self.subTest(code=code):
                type(self).response_body = payload
                with patch.dict(os.environ, TEST_ASR_KEY='synthetic-key'):
                    with self.assertRaises(TranscriptionError) as caught:
                        HttpTranscriber(self.profile).transcribe(self.audio)
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn('secret detail', str(caught.exception))

    def test_endpoint_and_profile_validation(self):
        for endpoint in ('http://not-local.example/transcriptions', 'https://user:key@example.com/transcriptions',
                         'https://example.com/transcriptions?api_key=secret'):
            with self.assertRaises(ValueError):
                Profile('test', endpoint, 'model')
        with self.assertRaises(ValueError):
            Profile('test', self.endpoint, 'model', fields={'api_key': 'secret'})


if __name__ == '__main__':
    unittest.main()
