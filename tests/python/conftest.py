import os
import sys

# The backend modules (server, tts_script, pdf_cleanup...) import each other
# by plain name, as when server.py runs from backend/.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))

FIXTURES = os.path.join(ROOT, "tests", "fixtures")


# Every test gets a fresh process-local quota, just as it gets fresh outputs.
import pytest


@pytest.fixture(autouse=True)
def isolated_speech_quota(monkeypatch):
    import server
    monkeypatch.setattr(server, "speech_limiter", server.SpeechLimiter())
