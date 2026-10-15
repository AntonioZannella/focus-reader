"""Limits for a public deployment: oversized requests are refused with a
readable JSON error, and /synthesize works when several requests run at once
in gunicorn's worker threads (each with its own asyncio event loop)."""

import asyncio
import io
import json
import os
import re
import threading

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
        with open(mp3_path, "wb") as f:
            f.write(b"\xff\xf3fake")
        with open(words_path, "w") as f:
            json.dump(words, f)
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
    page = open(os.path.join(ROOT, "frontend", "index.html"), encoding="utf-8").read()
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
        except Exception as error:  # pragma: no cover - reported below
            errors.append(repr(error))

    threads = [threading.Thread(target=call, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert errors == []
    assert results == [200] * 8
    assert len(fake_synthesis) == 8
