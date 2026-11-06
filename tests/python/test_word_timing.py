"""Word timings from the speech engine: the captions depend on them being the
text's words, in order, with sensible times that fit inside the audio."""

import itertools
import json
import os
import re

import pytest
import server
import tts_script

# MPEG audio bitrates (kbps) for Layer III, by version and bitrate index.
BITRATES = {
    "mpeg1": [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
    "mpeg2": [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
}


def mp3_duration_ms(path):
    """Duration of a constant-bitrate MP3 (what edge-tts produces), from the
    first frame header's bitrate and the file size."""
    with open(path, "rb") as f:
        data = f.read()
    start = data.find(b"\xff")
    while start >= 0 and (data[start + 1] & 0xE0) != 0xE0:
        start = data.find(b"\xff", start + 1)
    header = data[start:start + 4]
    version = "mpeg1" if (header[1] >> 3) & 0x3 == 0x3 else "mpeg2"
    kbps = BITRATES[version][header[2] >> 4]
    return (len(data) - start) * 8 / kbps  # bits / (kbit/s) = ms


def letters(word):
    return re.sub(r"[^\w]", "", word.lower())


def check_timings(words, text, duration_ms):
    # 1. the engine's words are the text's words, in the same order
    assert [letters(w["word"]) for w in words] == [letters(t) for t in text.split() if letters(t)]
    # 2. each word has a positive length, and starts and ends strictly increase
    for w in words:
        assert w["end_ms"] > w["start_ms"]
    for previous, current in itertools.pairwise(words):
        assert current["start_ms"] > previous["start_ms"]
        assert current["end_ms"] > previous["end_ms"]
    # 3. the last word ends inside the audio, followed only by the trailing
    # silence (~0.9 s measured); a bigger gap means timings and audio disagree
    assert words[-1]["end_ms"] <= duration_ms
    assert duration_ms - words[-1]["end_ms"] < 1500


def test_saved_synthesis_output_has_consistent_timings():
    # Checks the committed real output of the synthesis code (test.mp3 /
    # test_words.json from tts_script.py), so it runs offline: catches a
    # change in how timings are converted from the engine's ticks.
    output = os.path.join(os.path.dirname(tts_script.__file__), "output")
    with open(os.path.join(output, "test_words.json"), encoding="utf-8") as f:
        words = json.load(f)
    check_timings(words, tts_script.TEXT, mp3_duration_ms(os.path.join(output, "test.mp3")))


def test_live_synthesize_endpoint(tmp_path, monkeypatch):
    # The same checks on a fresh /synthesize response, through Flask's test
    # client. Needs the online speech service, so it's skipped when that
    # can't be reached (offline, or a sandbox without network).
    monkeypatch.setattr(server, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setitem(server.app.config, "TESTING", True)  # errors raise, not 500
    client = server.app.test_client()
    text = "Captions follow each word as it is spoken aloud."
    monkeypatch.setattr(tts_script, "RETRY_DELAY_SECONDS", 0)
    response = client.post("/synthesize", json={"text": text})
    if response.status_code == 502:  # every attempt failed: service unreachable
        pytest.skip(f"speech service unreachable: {response.get_json()['error']}")
    assert response.status_code == 200
    body = response.get_json()
    mp3 = os.path.join(tmp_path, body["audio_url"].rsplit("/", 1)[-1])
    check_timings(body["words"], text, mp3_duration_ms(mp3))
