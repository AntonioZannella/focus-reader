"""Limits for a public deployment: oversized requests are refused with a
readable JSON error, and /synthesize works when several requests run at once
in gunicorn's worker threads (each with its own asyncio event loop)."""

import asyncio
import io
import json
import re
import threading
from pathlib import Path

import pytest
import server
from conftest import ROOT


@pytest.fixture
def fake_synthesis(tmp_path, monkeypatch):
    # offline stand-in for the speech service; awaits like the real one does
    calls = []

    async def fake(text, mp3_path, words_path, voice=None):
        calls.append(text)
        await asyncio.sleep(0.2)
        words = [{"word": w, "start_ms": i * 100.0, "end_ms": i * 100.0 + 90} for i, w in enumerate(text.split())]
        Path(mp3_path).write_bytes(b"\xff\xf3fake")
        Path(words_path).write_text(json.dumps(words))
        return words

    monkeypatch.setattr(server, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(server, "synthesize_to_files", fake)
    return calls


def test_text_over_the_limit_is_refused(fake_synthesis):
    # anyone can reach the public URL: a huge text gets a 413 with a reason,
    # and the speech service is never called for it
    client = server.app.test_client()
    response = client.post("/synthesize", json={"text": "a" * (server.MAX_SYNTHESIS_CHARS + 1)})
    assert response.status_code == 413
    assert "too long" in response.get_json()["error"]
    assert fake_synthesis == []
    # exactly at the limit is still fine
    assert client.post("/synthesize", json={"text": "a " * (server.MAX_SYNTHESIS_CHARS // 2)}).status_code == 200


def test_upload_over_the_limit_gets_a_json_413():
    # a PDF over the limit is refused before it's read, with a reason the page can show
    client = server.app.test_client()
    big = b"%PDF-" + b"0" * server.MAX_UPLOAD_BYTES
    response = client.post("/extract-pdf", data={"file": (io.BytesIO(big), "big.pdf")},
                           content_type="multipart/form-data")
    assert response.status_code == 413
    assert "25 MB" in response.get_json()["error"]


def test_page_and_server_share_the_same_pdf_limit():
    # the page checks the size before uploading; both limits must stay equal
    page = Path(ROOT, "frontend", "index.html").read_text(encoding="utf-8")
    match = re.search(r"const MAX_PDF_BYTES = (\d+) \* 1024 \* 1024;", page)
    assert match and int(match.group(1)) * 1024 * 1024 == server.MAX_UPLOAD_BYTES == server.app.config["MAX_CONTENT_LENGTH"]


def test_concurrent_synthesis_in_threads(fake_synthesis):
    # gunicorn --threads 4 runs requests in parallel threads; asyncio.run in
    # each must get its own event loop ("event loop already running" would
    # surface here as a 500 or an exception)
    results, errors = [], []

    def call(n):
        try:
            response = server.app.test_client().post("/synthesize", json={"text": f"Request number {n} reads aloud."})
            results.append(response.status_code)
        except Exception as error:  # noqa: BLE001 - any failure in the thread is reported below
            errors.append(repr(error))

    threads = [threading.Thread(target=call, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert errors == []
    assert results == [200] * 8
    assert len(fake_synthesis) == 8


def test_stalled_speech_service_times_out_with_a_502(tmp_path, monkeypatch):
    # a service that accepts the connection and then never answers: the
    # request gives up after SYNTHESIS_TIMEOUT_SECONDS instead of hanging
    async def stalled(text, mp3_path, words_path, voice=None):
        await asyncio.sleep(30)

    monkeypatch.setattr(server, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(server, "synthesize_to_files", stalled)
    monkeypatch.setattr(server, "SYNTHESIS_TIMEOUT_SECONDS", 0.1)
    client = server.app.test_client()
    response = client.post("/synthesize", json={"text": "Hello."})
    assert response.status_code == 502
    assert "speech service" in response.get_json()["error"]
    response = client.get(f"/voice-sample/{server.VOICE}")
    assert response.status_code == 502
    assert list(tmp_path.rglob("*.partial*")) == []


def test_limiter_evicts_the_least_recently_seen_client(monkeypatch):
    limiter = server.SpeechLimiter(limit=1, max_clients=2)
    monkeypatch.setattr(server.time, "monotonic", lambda: 100.0)
    assert limiter.retry_after("a") == 0
    assert limiter.retry_after("b") == 0
    assert limiter.retry_after("a") > 0  # "a" is now the most recently seen
    assert limiter.retry_after("c") == 0  # table full: "b" is forgotten
    assert list(limiter.clients) == ["a", "c"]
    assert limiter.retry_after("a") > 0  # an active limit survives eviction of others


def test_limiter_prunes_idle_clients_once_per_window(monkeypatch):
    limiter = server.SpeechLimiter(limit=5, window=60)
    now = [100.0]
    monkeypatch.setattr(server.time, "monotonic", lambda: now[0])
    limiter.retry_after("a")  # first call prunes and sets the next prune to 160
    now[0] = 159.0
    limiter.retry_after("b")
    now[0] = 160.5  # "a" is idle now, but the table is only scanned when due
    assert limiter.next_prune == 160.0
    limiter.retry_after("b")
    assert set(limiter.clients) == {"b"} and limiter.next_prune == 220.5
    now[0] = 200.0  # between prunes, idle-or-not entries stay put
    limiter.retry_after("c")
    assert set(limiter.clients) == {"b", "c"} and limiter.next_prune == 220.5
