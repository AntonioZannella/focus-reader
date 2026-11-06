# Focus Reader - what it does

Focus Reader reads a document out loud and shows only the few words being spoken, one small group at a time, in large text in the middle of an otherwise empty screen. It is built for people who lose focus when a page is full of words, even while listening along to an audiobook.

---

## The problem it solves

Listening to a recording while following a full page of text is hard if a lot of words on the screen pulls your attention away. Your eyes drift, you lose your place, and the audio and the page stop lining up. Focus Reader removes the page. You see only what is being said right now, so there is nothing to lose your place in.

The look is meant to match captions on short videos: big, bold white text with a black outline, centered on a dark background.

---

## What using it looks like

1. **Load a document.** Paste text, upload a .txt file, or upload a PDF (a research paper or a book).
2. **Name it.** Every document gets a name you choose, like "Chapter 1" or "Chapter 20", and is saved to your library.
3. **Press play.** The app creates a spoken version. Then the whole screen goes blank except the words being spoken, which change in time with the voice.
4. **Press Space (or tap the words on a phone or tablet) to pause.** The controls come back: the skip buttons, speed, group size, the text box, your library, and the full document. Press Space again and they disappear and it keeps reading.
5. **Use the arrow keys.** Left Arrow goes back 10 seconds. Right Arrow goes forward 10 seconds.

---

## Features

### Reading and captions
- The spoken audio and the on-screen words are matched word by word, so the caption is always the word being said.
- **Group size:** by default one phrase at a time: a short group of words carrying one idea, action or purpose ("I went outside / to get the garbage / and it was raining"). Phrases break at sentence ends, at commas once a phrase has a few words, and where a new idea starts (words like "because", "but", "and it", "to get"); long ones are split so they fit on screen. The breaks come from word-based rules, not real grammar analysis, so a few land in odd places. Or a fixed 1 to 5 words at a time; one is the most stripped down.
- **Speed:** 0.25x, 0.5x, 0.75x, 1x, 1.25x, 1.5x, 1.75x, or 2x.
- **Text size:** Small, Medium, Large or Extra large captions.
- **Back 10 / Forward 10 seconds**, as buttons or with the arrow keys.
- **Focus mode:** while it is playing, nothing else is on screen. Pausing brings everything back. When the audio ends, the controls come back on their own so you are never stuck on a blank screen.
- Space and the arrow keys never interfere with typing. In a text box, Space types a space and the arrows move the cursor.

### Education mode
- Tabs at the top of the screen switch between **Normal** and **Education**. Both work the same; Education adds learning aids, and the app remembers which one you chose.
- **Short vowels in pink:** in the caption, vowels that make their short sound (the a in cat, e in bed, i in sit, o in hot, u in cup) are colored pink. It follows the phonics rule for closed syllables, with the common exceptions (silent e, vowel teams like "rain", r-controlled vowels like "car", "night", "ball", "find", "old"). It uses rules rather than a pronunciation dictionary, so an unusual word can be marked wrong, and some short vowels it can't be sure of (like the first e in "seven") are left unmarked.
- **Welded sounds in green:** letter groups read as one sound (-all, -am, -an, -ang, -ing, -ong, -ung, -ank, -ink, -onk, -unk, as in ball, ham, can, sang, king, long, hung, bank, think, honk, junk) are colored green, at the end of a word or before an ending like -s, -er or -ing. Their vowel is part of the unit, so it's green rather than pink. Look-alikes such as "and", "want", "same", "animal" and "danger" are left alone.
- **Prefixes and suffixes in a yellow box:** a thin yellow box goes around each prefix and suffix (un|happy, help|ful, help|ful|ness, run|ning). A part only counts if what's left is a real word, checked against an English word list, allowing the usual spelling changes (running, making, happiness). Words that only look like they have one (mother, water, present, remember, thing) are left alone. It's a word-list check rather than full word analysis, so an occasional word can still be boxed wrongly.

### Jumping around a short document
- **Show Full Document:** shows the whole text on screen with every word clickable. Click a word and it starts reading from there.
- **Select a range:** click a start word and an end word. They get different highlight colors, and Play Range reads only that stretch, then stops at the end word.

### The library
- Every document you load is saved under the name you gave it.
- Each one remembers its own place. Reopen "Chapter 3" and it picks up exactly where you left off in Chapter 3, no matter what you read in between.
- The library shows how far through each document you are, and lets you rename or delete entries.
- The audio for each document is saved on your device with it, so reopening is instant and does not need to be generated again.

### Research papers and PDFs
- **Reading order:** two-column papers are read in the right order, finishing the left column before the right, instead of jumping across the page.
- **Cleanup:** page numbers, repeated headers and footers are removed, and words split across lines by hyphens are put back together.
- **Citations:** a setting lets you skip citation marks like [12] or (Smith et al., 2019), or leave them in.
- **Equations:** math cannot be read aloud sensibly, so equations are left out of the audio. When the reading reaches one, the audio pauses on its own, the equation is shown on screen for 5 seconds, and then it continues. The screen stays in focus mode during those 5 seconds.

### Books and long documents
- A document is saved as one library entry. Audio is prepared in short sections, starting around 450 words and ending at sentence boundaries where possible. Reading starts when the first section is ready, while the next prepares in the background. The pause between sections is about one second, a little longer than a normal sentence pause.
- **Page numbers** mean the position of the page in the file (the 42nd page of the PDF), not the number printed on the page.
- **Three ways to start:**
  - Start from the beginning.
  - Find a start point: type a page number, or search for a phrase you remember, then click the exact word you want to start from on that page.
  - Select a range: pick a start word and an end word, even if they are on different pages, and it reads only that stretch.
- **Page controls:** next and previous page, and a "Page X of Y" counter. Browsing pages does not require generating their audio.
- **Search:** looks through the whole document. Click a word to start there; the app reuses its saved audio or prepares the section containing it, without generating all the intervening pages.
- **Waiting and offline use:** if the next section is not ready, a preparation message appears with Pause and Cancel, and Retry if preparing it fails. Only downloaded sections can play offline. Back/Forward 10 crosses adjacent prepared sections and stops at an unprepared edge.

### Small safeguards
- A loading indicator while audio is being created.
- Clear error messages with a Retry button if something fails, including if the audio itself fails to play.
- A warning before you close the tab while a document is open.
- All colors are stored in one place, so a dark or light theme can be changed easily.

---

## How the pieces fit together

There are two parts that talk to each other.

**The page in your browser.** This is everything you see and touch: the captions, the controls, the library, and the audio player. It also stores your library on your device.

**A small program running on your computer (the server).** It does the two jobs a browser page cannot do on its own:
1. It turns text into speech and records when each word is spoken. Every word gets a start time and an end time. That is how the captions stay exactly in step with the voice.
2. It reads PDFs: it finds the text, puts it in the right order, and cleans it up.

Where things are kept:
- **Your documents, reading positions, and audio** are saved in the browser on your device.
- **The server** keeps the audio it generates only briefly (about an hour) and then deletes it.
- **The speech itself** comes from an online speech service (Microsoft's, through a free tool called edge-tts). The text of whatever you are reading is sent to that service to make the audio, so creating new audio needs an internet connection. Playing audio already saved in your library does not; unprepared sections still need a connection.

A few terms:
- **Group (chunk):** the few words shown at once.
- **Timestamp:** the start and end time of a single word in the audio.
- **Audio section:** a short passage with its own saved audio and word timings, played as part of one document.
- **Library entry:** one saved, named piece of reading with its text, its audio, and your place in it.

---

## What it does not do

- No accounts or logins, and nothing is shared between devices or people. Your library lives on the device you use.
- It does not track or collect usage data.
- It does not read equations aloud, and it does not show or describe figures and images. Tables are not handled specially and may sound odd.
- It shows the text of a PDF, not the PDF's pages as they look.
- One English voice for now.
- New audio needs an internet connection, because the speech is generated online.
