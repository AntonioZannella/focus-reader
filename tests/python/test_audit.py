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
