"""One Flask server delivers the page and the API from the same address, so
the app can be hosted at a single public URL."""

import json
import os
from pathlib import Path

import pytest
import server


@pytest.fixture
def client(tmp_path, monkeypatch):
    # started from an unrelated folder: paths must not depend on the cwd
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(server, "OUTPUT_DIR", str(tmp_path / "output"))
    return server.app.test_client()


def test_page_and_its_files_are_served(client):
    # "/" is the app itself; the files it loads by relative path come from
    # frontend/ too (alignment.js as a script, words.txt by fetch)
    page = client.get("/")
    assert page.status_code == 200 and b"<title>" in page.data and b'src="alignment.js"' in page.data
    assert client.get("/alignment.js").status_code == 200
    words = client.get("/words.txt")
    assert words.status_code == 200 and b"\nhappy\n" in words.data


@pytest.mark.parametrize("path", [
    "/../backend/server.py",
    "/%2e%2e/backend/server.py",
    "/..%2fbackend%2fserver.py",
    "/frontend/../backend/server.py",
    "/%2e%2e/%2e%2e/etc/hosts",
])
def test_nothing_outside_frontend_is_served(client, path):
    # a crafted path must never reach the server code or anything else
    response = client.get(path)
    assert response.status_code == 404
    assert b"send_from_directory" not in response.data


def test_audio_link_is_relative(client, monkeypatch):
    # an absolute http:// link would be blocked on an https page behind a
    # proxy, so /synthesize must return a path on the same site. A fake
    # synthesizer keeps this offline.
    async def fake_synthesize(text, mp3_path, words_path, voice=None):
        words = [{"word": w, "start_ms": i * 100.0, "end_ms": i * 100.0 + 90} for i, w in enumerate(text.split())]
        Path(mp3_path).write_bytes(b"\xff\xf3fake")
        Path(words_path).write_text(json.dumps(words))
        return words

    monkeypatch.setattr(server, "synthesize_to_files", fake_synthesize)
    body = client.post("/synthesize", json={"text": "Read this aloud."}).get_json()
    assert body["audio_url"].startswith("/output/") and body["audio_url"].endswith(".mp3")
    # and the link works on the same server
    audio = client.get(body["audio_url"])
    assert audio.status_code == 200 and audio.data == b"\xff\xf3fake"
    assert os.path.isfile(os.path.join(server.OUTPUT_DIR, body["audio_url"].rsplit("/", 1)[-1]))
