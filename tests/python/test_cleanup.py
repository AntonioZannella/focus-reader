"""The server's output folder is temporary: generated audio is deleted after
an hour, but the canonical test files and anything it didn't generate stay."""

import os
import time

import server


def touch(folder, name, age_seconds):
    path = os.path.join(folder, name)
    with open(path, "w") as f:
        f.write("x")
    then = time.time() - age_seconds
    os.utime(path, (then, then))
    return path


def test_cleanup_deletes_only_old_generated_files(tmp_path, monkeypatch):
    # Old uuid-named mp3/json pairs go; new ones (still being downloaded),
    # test.mp3 / test_words.json and unrelated files stay, whatever their age.
    monkeypatch.setattr(server, "OUTPUT_DIR", str(tmp_path))
    old_id, new_id = "a" * 32, "b" * 32
    two_hours, one_minute = 2 * 60 * 60, 60
    touch(tmp_path, f"{old_id}.mp3", two_hours)
    touch(tmp_path, f"{old_id}_words.json", two_hours)
    touch(tmp_path, f"{new_id}.mp3", one_minute)
    touch(tmp_path, f"{new_id}_words.json", one_minute)
    for kept in ("test.mp3", "test_words.json", "fixture_pages.pdf", "notes.txt"):
        touch(tmp_path, kept, two_hours)

    assert server.cleanup_old_outputs() == 2
    assert sorted(os.listdir(tmp_path)) == sorted([
        f"{new_id}.mp3", f"{new_id}_words.json",
        "test.mp3", "test_words.json", "fixture_pages.pdf", "notes.txt",
    ])
