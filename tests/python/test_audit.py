"""Audit regressions use stdlib unittest and offline speech stubs."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import server


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output_patch = patch.object(server, 'OUTPUT_DIR', self.tmp.name)
        self.output_patch.start()
        self.addCleanup(self.output_patch.stop)
        self.quota_patch = patch.object(server, 'speech_limiter', server.SpeechLimiter())
        self.quota_patch.start()
        self.addCleanup(self.quota_patch.stop)
        self.client = server.app.test_client()

    def test_speech_rate_limit_tracks_real_clients_and_both_speech_endpoints(self):
        async def fake(text, audio, words, voice=None):
            Path(audio).write_bytes(b'fake audio')
            Path(words).write_text('[]')
            return []
        with patch.object(server, 'synthesize_to_files', side_effect=fake) as speech, \
             patch.object(server, 'speech_limiter', server.SpeechLimiter(limit=2)), \
             patch.dict(os.environ, {'RENDER': 'true'}):
            one = {'CF-Connecting-IP': '203.0.113.1', 'X-Forwarded-For': 'spoof, 10.0.0.1'}
            two = {'CF-Connecting-IP': '203.0.113.2'}
            self.assertEqual(self.client.post('/synthesize', json={'text': 'Hello'}, headers=one).status_code, 200)
            sample = '/voice-sample/' + server.VOICE
            self.assertEqual(self.client.get(sample, headers=one).status_code, 200)
            one['X-Forwarded-For'] = 'another spoof'
            response = self.client.post('/synthesize', json={'text': 'Hello'}, headers=one)
            self.assertEqual(response.status_code, 429)
            self.assertTrue(response.is_json)
            self.assertGreater(int(response.headers['Retry-After']), 0)
            other_voice = next(v for v in server.VOICES if v != server.VOICE)
            self.assertEqual(self.client.get('/voice-sample/' + other_voice, headers=one).status_code, 429)
            self.assertEqual(self.client.get(sample, headers=one).status_code, 200)  # cached, no service call
            self.assertEqual(self.client.post('/synthesize', json={'text': 'Hello'}, headers=two).status_code, 200)
            self.assertEqual(speech.call_count, 3)
        with patch.dict(os.environ, {'RENDER': ''}):
            with server.app.test_request_context(headers={'CF-Connecting-IP': '1.2.3.4', 'X-Forwarded-For': '5.6.7.8'},
                                                 environ_base={'REMOTE_ADDR': '127.0.0.1'}):
                self.assertEqual(server.speech_client_ip(), '127.0.0.1')
        limiter = server.SpeechLimiter(limit=2, max_clients=1)
        with patch('server.time.monotonic', return_value=100):
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: limiter.retry_after('one'), range(8)))
            self.assertEqual(results.count(0), 2)
            self.assertGreater(limiter.retry_after('two'), 0)
        with patch('server.time.monotonic', return_value=161):
            self.assertEqual(limiter.retry_after('two'), 0)

    def test_cors_allows_only_configured_origin_and_loopback(self):
        with patch.object(server, 'ALLOWED_ORIGIN', 'https://reader.example'):
            for origin in ('https://reader.example', 'http://localhost:3000', 'http://127.0.0.1:5001'):
                response = self.client.options('/synthesize', headers={'Origin': origin})
                self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), origin)
                self.assertIn('Origin', response.vary)
            for origin in ('https://evil.example', 'http://localhost.evil.example', 'null', '*', ''):
                response = self.client.options('/synthesize', headers={'Origin': origin})
                self.assertNotIn('Access-Control-Allow-Origin', response.headers)
        with patch.object(server, 'ALLOWED_ORIGIN', '*'):
            self.assertNotIn('Access-Control-Allow-Origin', self.client.options('/synthesize', headers={'Origin': '*'}).headers)

    def test_non_object_bodies_and_non_string_voices_return_400(self):
        with patch.object(server, 'synthesize_to_files') as speech:
            for body in ([], [1], None, 'text', 7, True):
                response = self.client.post('/synthesize', data=json.dumps(body), content_type='application/json')
                self.assertEqual(response.status_code, 400)
                self.assertTrue(response.is_json)
            for voice in ([], {}, None, 7, True):
                response = self.client.post('/synthesize', json={'text': 'Hello', 'voice': voice})
                self.assertEqual(response.status_code, 400)
                self.assertTrue(response.is_json)
            speech.assert_not_called()

    def test_all_http_and_pdf_errors_are_json(self):
        for method, path, expected in [('get', '/missing-file', 404), ('put', '/synthesize', 405),
                                       ('get', '/output/missing.mp3', 404)]:
            response = getattr(self.client, method)(path)
            self.assertEqual(response.status_code, expected)
            self.assertIsInstance(response.get_json()['error'], str)
            if expected == 405:
                self.assertIn('POST', response.headers['Allow'])
        for data in ({}, {'file': (io.BytesIO(b'not a pdf'), 'bad.pdf')}):
            response = self.client.post('/extract-pdf', data=data)
            self.assertEqual(response.status_code, 400)
            self.assertTrue(response.is_json)
        for error, expected in [(ValueError('Invalid PDF.'), 400), (RuntimeError('private path'), 500)]:
            with patch.object(server, 'process_pdf', side_effect=error), patch.object(server.app.logger, 'exception'):
                response = self.client.post('/extract-pdf', data={'file': (io.BytesIO(b'%PDF-broken'), 'bad.pdf')})
                self.assertEqual(response.status_code, expected)
                self.assertIsInstance(response.get_json()['error'], str)
                self.assertNotIn(b'private path', response.data)
        with patch.object(server, 'MAX_UPLOAD_BYTES', 16), patch.dict(server.app.config, {'MAX_CONTENT_LENGTH': 16}):
            response = self.client.post('/extract-pdf', data=b'x' * 17)
            self.assertEqual(response.status_code, 413)
            self.assertTrue(response.is_json)

    def test_speech_and_cleanup_failures_log_tracebacks(self):
        with patch.object(server, 'synthesize_to_files', new=AsyncMock(side_effect=RuntimeError('speech failed'))):
            with patch('server.os.remove', side_effect=PermissionError('cannot remove')):
                with self.assertLogs(server.app.logger, level='ERROR') as captured:
                    response = self.client.post('/synthesize', json={'text': 'Hello'})
                self.assertEqual(response.status_code, 502)
                self.assertEqual(len(captured.records), 3)
                self.assertTrue(all(record.exc_info for record in captured.records))
            with self.assertLogs(server.app.logger, level='ERROR') as captured:
                response = self.client.get('/voice-sample/' + server.VOICE)
            self.assertEqual(response.status_code, 502)
            self.assertTrue(captured.records[0].exc_info)
        with patch('server.os.listdir', return_value=['a' * 32 + '.mp3']), \
             patch('server.os.path.isfile', return_value=True), \
             patch('server.os.path.getmtime', return_value=0), \
             patch('server.os.remove', side_effect=PermissionError('cannot remove')):
            with self.assertLogs(server.app.logger, level='ERROR') as captured:
                self.assertEqual(server.cleanup_old_outputs(), 0)
            self.assertTrue(captured.records[0].exc_info)

    def test_sample_cleanup_tolerates_disappearing_files_and_logs_other_os_errors(self):
        async def fake(text, audio, words, voice=None):
            Path(audio).write_bytes(b'fake audio')
            Path(words).write_text('[]')
            return []
        voices = iter(server.VOICES)
        with patch.object(server, 'synthesize_to_files', side_effect=fake):
            with patch('server.os.remove', side_effect=FileNotFoundError), patch.object(server.app.logger, 'exception') as log:
                response = self.client.get('/voice-sample/' + next(voices))
                self.assertEqual(response.status_code, 200)
                log.assert_not_called()
            with patch('server.os.remove', side_effect=PermissionError), self.assertLogs(server.app.logger, level='ERROR') as captured:
                response = self.client.get('/voice-sample/' + next(voices))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(captured.records), 2)
                self.assertTrue(all(record.exc_info for record in captured.records))

    def test_cleanup_requires_a_complete_generated_filename_match(self):
        names = ['a' * 32 + '.mp3', 'b' * 32 + '_words.json',
                 'c' * 32 + '.mp3\n', 'd' * 32 + '_words.json\n', 'test.mp3']
        for name in names:
            path = Path(self.tmp.name) / name
            path.write_bytes(b'fixture')
            os.utime(path, (0, 0))
        self.assertEqual(server.cleanup_old_outputs(), 2)
        self.assertEqual(sorted(p.name for p in Path(self.tmp.name).iterdir()), sorted(names[2:]))
