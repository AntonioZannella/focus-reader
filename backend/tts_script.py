import asyncio
import json
import os

import edge_tts

TEXT = "This is a test of the focus reader text to speech system."
VOICE = "en-US-AriaNeural"
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "test.mp3")
WORDS_PATH = os.path.join(OUTPUT_DIR, "test_words.json")

# edge-tts reports offset/duration in 100-nanosecond ticks; 10000 ticks = 1 ms.
TICKS_PER_MS = 10000


# edge-tts talks to an unofficial Microsoft endpoint that now and then drops a
# connection or sends no audio; a short retry hides most of those blips.
SYNTHESIS_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1.0


async def synthesize_to_files(text, mp3_path, words_path, voice=VOICE):
    """Run edge-tts on `text`, writing the mp3 and word-boundary timestamps.
    Retries a failed attempt (each attempt starts the files over); the last
    failure is raised."""
    for attempt in range(1, SYNTHESIS_ATTEMPTS + 1):
        try:
            return await _synthesize_once(text, mp3_path, words_path, voice)
        except Exception as error:  # network errors, NoAudioReceived, ...
            if attempt == SYNTHESIS_ATTEMPTS:
                raise
            print(f"edge-tts attempt {attempt} failed ({type(error).__name__}: {error}); retrying", flush=True)
            await asyncio.sleep(RETRY_DELAY_SECONDS * attempt)


async def _synthesize_once(text, mp3_path, words_path, voice):
    communicate = edge_tts.Communicate(text, voice, boundary="WordBoundary")

    words = []
    with open(mp3_path, "wb") as audio_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start_ms = chunk["offset"] / TICKS_PER_MS
                end_ms = (chunk["offset"] + chunk["duration"]) / TICKS_PER_MS
                words.append(
                    {
                        "word": chunk["text"],
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                    }
                )

    with open(words_path, "w", encoding="utf-8") as words_file:
        json.dump(words, words_file, indent=2)

    return words


async def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    await synthesize_to_files(TEXT, OUTPUT_PATH, WORDS_PATH)
    print(f"Saved: {OUTPUT_PATH}")
    print(f"Saved: {WORDS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
