from pathlib import Path

import server
import tts_script


def test_unknown_voice_is_refused():
    client = server.app.test_client()
    response = client.post("/synthesize", json={"text": "Hello.", "voice": "en-US-NotARealVoice"})
    assert response.status_code == 400
    assert "Unknown voice" in response.get_json()["error"]


def test_chosen_voice_reaches_the_speech_service(tmp_path, monkeypatch):
    seen = {}

    async def fake_synthesize(text, mp3_path, words_path, voice):
        seen["voice"] = voice
        Path(mp3_path).touch()
        return []

    monkeypatch.setattr(server, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(server, "synthesize_to_files", fake_synthesize)
    client = server.app.test_client()
    response = client.post("/synthesize", json={"text": "Hello.", "voice": "en-US-AvaNeural"})
    assert response.status_code == 200 and seen["voice"] == "en-US-AvaNeural"
    client.post("/synthesize", json={"text": "Hello."})
    assert seen["voice"] == tts_script.VOICE  # no voice given: the default


def test_every_voice_button_is_an_allowed_voice():
    import re
    page = Path(server.__file__).parent.parent.joinpath("frontend", "index.html").read_text(encoding="utf-8")
    buttons = set(re.findall(r'class="voice-button[^"]*" data-voice="([^"]+)"', page))
    assert buttons and buttons <= set(tts_script.VOICES)


def test_voice_sample_is_made_once_then_served_from_disk(tmp_path, monkeypatch):
    calls = []

    async def fake_synthesize(text, mp3_path, words_path, voice):
        calls.append((text, voice))
        Path(mp3_path).write_bytes(b"ID3fake")
        Path(words_path).touch()
        return []

    monkeypatch.setattr(server, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(server, "synthesize_to_files", fake_synthesize)
    client = server.app.test_client()
    first = client.get("/voice-sample/en-US-AvaNeural")
    second = client.get("/voice-sample/en-US-AvaNeural")
    assert first.status_code == 200 and second.status_code == 200
    assert second.data == b"ID3fake"
    assert calls == [("Hi, I'm Ava.", "en-US-AvaNeural")]  # made only once
    assert sorted(p.name for p in (tmp_path / "voice-samples").iterdir()) == ["en-US-AvaNeural.mp3"]
    assert client.get("/voice-sample/../server.py").status_code == 404
    assert client.get("/voice-sample/en-US-Nope").status_code == 404
