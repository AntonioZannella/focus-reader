# Focus Reader

[![CI](https://github.com/AntonioZannella/focus-reader/actions/workflows/ci.yml/badge.svg)](https://github.com/AntonioZannella/focus-reader/actions/workflows/ci.yml)

**Try it: <https://focus-reader.onrender.com>** (free hosting: the first visit after a quiet spell can take up to a minute to wake the server).

Focus Reader reads a document out loud and shows only the word or few words being spoken, in large caption-style text on an otherwise empty screen. It's for people who lose their place or their focus when a page is full of text, even while listening along to an audiobook: instead of following the voice across a page, you only ever see what is being said right now. You can paste text, upload a `.txt` file or upload a PDF (including two-column research papers), and every document is saved in a library in your browser with its own reading position. [WHAT-IT-DOES.md](WHAT-IT-DOES.md) describes the features in plain language.

**Privacy:** there are no accounts. Your saved library, reading positions and cached audio live in your browser. Uploaded PDFs are sent to the app server for text extraction; text sections are sent through that server to Microsoft's online speech service via edge-tts. The hosting provider keeps request logs, so this is not a no-logging service. Generated audio and word-timing files are stored temporarily on the server. Cleanup removes files older than one hour when a new synthesis request arrives; this is not a guaranteed one-hour deletion deadline. Browser copies remain until you delete them or clear site data.

## Screenshots and demo

A paused sample document in Education mode, with the library on the left. These screenshots predate the Language tab. The page follows the system's light or dark setting, or the Theme button.

| Light | Dark |
| --- | --- |
| ![Focus Reader in light theme: library sidebar, dark caption stage with a highlighted phrase, playback controls](docs/screenshots/light.jpg) | ![Focus Reader in dark theme: the same view](docs/screenshots/dark.jpg) |

![Focus Reader demo: captions that follow each spoken word](docs/demo/focus-reader-demo.gif)

## How it works

One small server (`backend/server.py`, Python and Flask) delivers both the page and its API from the same address, `http://127.0.0.1:5001/` when run locally, so the whole app can also be hosted at a single public URL:

- **The page** (`frontend/index.html`, plain HTML, CSS and JavaScript, no build step) shows the captions, the controls and the library. It plays the audio and keeps the caption in step with it using each word's start and end time. It talks to the server through relative paths (`/synthesize`, `/extract-pdf`), so it works at whatever address the server has.
- **The server** also does what a web page can't do on its own:
  - **Speech with word timestamps:** it turns text into speech with [edge-tts](https://github.com/rany2/edge-tts), which uses Microsoft's online speech service, and records when every word starts and ends. The browser matches those words to the words of your text.
  - **The PDF pipeline:** it extracts a PDF's text in reading order (columns are read one after the other), tags each word with its page, removes headers, footers and page numbers, rejoins words hyphenated across lines, optionally removes citations, and leaves equations out of the spoken text while recording where they are.

**Audio in sections.** A long document is spoken in sections of about 450 words, ending at a paragraph or sentence end where possible. The first two sections target about 60, then 200 words. Reading starts as soon as the first section is ready, and the next one is prepared while you listen, one request at a time. Jumping to a distant page prepares only the section you jump to.

**Historical startup measurements.** Earlier tests using a 450-word first section recorded about 7–10 seconds to prepare the first audio in several runs. A 765-page book took 5.6 seconds for PDF extraction, followed by 9.7 seconds for the first audio section. Only that first section was generated before playback; later sections were prepared in the background. These measurements date from September–October 2026 and predate the shorter opening sections now in the code. They are not a current speed guarantee: another run took 15.6 seconds for its first section, and hosting cold starts add delay.

**The library.** Each document, its reading position and every audio section already made are saved in the browser's IndexedDB, so reopening a document resumes at the same word and saved sections play without the server or a connection. The browser keeps its own copy independently of server cleanup.

**Reading modes.** Three tabs change how captions are shown:

- **Normal:** plain highlighting.
- **Education:** phonics markup.
- **Language:** shows how to sound out each word, with the stressed syllable marked.

**Education mode.** Education mode marks up the caption for reading practice: short vowels in pink, welded sounds (-ang, -ing, -ank...) in green, and a thin yellow box around prefixes and suffixes. These come from phonics rules and an English word list (`frontend/words.txt`), not a pronunciation dictionary, so unusual words can be marked wrongly.

**Language mode.** Each word's sound-it-out spelling appears underneath it, with the stressed syllable in capitals and cyan ("broccoli" shows BRAH-kuh-lee). The pronunciation guide uses American English. The spellings are made from the CMU Pronouncing Dictionary (`frontend/sounds.txt`, built by `tools/make_sounds.py`). Words it lacks (names, numbers) show nothing, and a word with two pronunciations always gets the first ("read" is always "reed").

**Equations.** When playback reaches an equation in a PDF, it pauses, shows the equation's text for five seconds, and continues.

## Architecture and trade-offs

The browser handles captions, playback and the IndexedDB library. A Python/Flask server serves the page, extracts PDF text and calls edge-tts for audio and word timings. The container runs one Gunicorn worker with four threads.

- **One worker:** the hosted app runs as one server process that handles up to four requests at once. Most of each speech request is waiting on Microsoft's speech service, so four at once is plenty for this app. One process uses little memory on Render's free tier. Speech requests are rate-limited per IP. Keeping one worker keeps the in-memory request counters together; multiple workers or instances would need a shared counter store.
- **edge-tts:** free, natural voices, and it reports when every word starts and ends, which the word highlighting depends on.
- **If edge-tts changes or breaks:** it is unofficial and has already needed one update. The server tries up to three times, then returns a clear error: "The speech service didn't respond." There is no alternate speech provider built in. Audio already saved in the library still plays offline.

## Setup

You need:

- **Python 3.10 or newer** (developed and tested with Python 3.14; the code uses 3.10 syntax)
- **A web browser.** Built for desktop browsers, developed and tested in Chrome. It also works on phones, which are tested less: tap the words to pause, and the text box appears first until a document loads.
- **An internet connection** to generate new audio

Copy this repository's clone URL from GitHub's **Code** button and replace `<repository-url>` below. Install the server's requirements in a virtual environment:

```bash
git clone <repository-url> focus-reader
cd focus-reader/backend
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
python check_env.py               # confirms the packages are installed
```

Start the server (leave it running). It serves both the page and the API:

```bash
python server.py                  # from focus-reader/backend, with the venv active
```

Open <http://127.0.0.1:5001/> in your browser, paste some text and press **Start**. Open the page through the server like this, not by opening the HTML file directly: from a file (`file://`) the page can't reach the server.

The server uses port 5001 (port 5000 is taken by AirPlay on macOS).

### Keys

- **Space:** play / pause (pausing brings the controls back). On a phone or tablet, tap the words to pause.
- **Left / Right arrow:** back / forward 10 seconds

## Running with Docker

The same app runs in a container, the way it's meant to be hosted: one [gunicorn](https://gunicorn.org) server on port 7860 serves the page and the API.

```bash
cd focus-reader
docker build -t focus-reader .
docker run --rm -p 7860:7860 focus-reader
```

Then open <http://localhost:7860/>. Generated audio lives inside the container and is eligible for cleanup after one hour, as on a local run; cleanup runs on new synthesis requests. Each reader's library stays in their own browser.

To keep a public copy from being misused, the server refuses text over 10,000 characters per request (the page sends sections of a few thousand at most) and PDFs over 25 MB, with a message saying why.

## Running the tests

```bash
cd focus-reader
backend/venv/bin/python -m pip install -r backend/requirements-dev.txt   # adds pytest
./run-tests.sh
```

This runs three parts and exits non-zero if anything fails:

- **Python** (`tests/python/`, pytest): word timings from the speech engine, PDF extraction on the small sample PDFs in `tests/fixtures/` (reading order, page numbers, header and footer removal, rejoined hyphenated words, equations), the server's cleanup of old audio, and the server delivering the page (only files from `frontend/`, never anything outside it) with relative audio links. One test calls the real speech service and is skipped when it can't be reached or when `SKIP_LIVE_TTS=1` is set.
- **Node** (`tests/node/`, Node 22 or newer): matching the speech engine's words to the text on deliberately messy input.
- **Browser** (`tests/browser/`, needs Google Chrome): runs the real page in headless Chrome with a fake speech service and silent audio, and checks the library, section playback, ranges, migration of older libraries, and that pauses, seeks and document switches always win over background work. Set `CHROME_PATH` to use another Chrome or Chromium.

GitHub Actions (`.github/workflows/ci.yml`) runs `ruff check`, the Python tests and the Node tests on every push and pull request, with `SKIP_LIVE_TTS=1`; the browser tests run locally.

`run-tests.sh` is a POSIX shell script; on Windows, run it from Git Bash or WSL, or run the three commands inside it one by one.

## Known limitations

- **Equations aren't read aloud.** Playback pauses and shows them as text instead, and that text is exactly what the PDF contains, which is garbled for some PDFs (doubled letters, for example). Math inside a sentence is left out of the audio without a pause.
- **English voices only.** Seven American English voices (Ava by default, plus Aria, Jenny, Andrew, Brian, Guy and Ana, a child's voice), picked with the Voice buttons. Changing the voice makes a document's audio again in the new voice.
- **The speech service is unofficial.** The hosted version, like a local one, uses Microsoft's online speech service through edge-tts. It's free but unofficial: Microsoft hasn't published it for this use, so it may stop working or be rate-limited at any time (it has already needed an edge-tts update once).
- **New audio needs the internet.** The speech is generated by Microsoft's online speech service through edge-tts, so the text you're reading is sent to that service. Audio already saved in your library plays offline.
- **No accounts and no sync.** The library lives in one browser on one device; clearing the browser's site data deletes it.
- **PDFs:** scanned PDFs without a text layer can't be read (run them through OCR first). Figure and table captions and footnotes are read where they sit on the page, so they can interrupt a sentence, and tables are read row by row.

## Credits

The word list in `frontend/words.txt` comes from the en_US Hunspell dictionary in [SCOWL](http://wordlist.aspell.net); its copyright and permission notices are in `frontend/words-LICENSE.txt`.

The sound-it-out spellings in `frontend/sounds.txt` are converted from the [CMU Pronouncing Dictionary](https://github.com/cmusphinx/cmudict), copyright Carnegie Mellon University; its notice is in `frontend/sounds-LICENSE.txt`.

## License

The code is released under the MIT License ([LICENSE](LICENSE)). The word list, the pronunciation data and the fonts keep their own licenses, listed under Credits and in `frontend/fonts/`.

## How it was built

I directed the design and tested it. The code, tests and documentation were written with Claude Code, following a step-by-step build plan. Each step was reviewed and tested in a real browser and against real PDFs as it was built. The automated test suite was added at the end and checked by deliberately breaking the code to confirm each test fails when it should.
