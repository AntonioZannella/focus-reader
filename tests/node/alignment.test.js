// Aligning the speech engine's words with the displayed text
// (frontend/alignment.js). Captions, click-to-jump, saved positions and range
// stops all use the document word each timing is matched to, so a wrong match
// shows or seeks to the wrong word. Run: node --test tests/node
const test = require("node:test");
const assert = require("node:assert/strict");
const { textWords, alignTimings } = require("../../frontend/alignment.js");

// Deliberately messy text: contractions, hyphens, decimals, thousands,
// quotes, em-dashes (attached and standalone), parentheses, an abbreviation,
// a time, a price, and paragraph/line breaks.
const TEXT = `"Don't panic," she said. The well-known value is 3.14 (roughly).
The U.S. sent 1,000 boxes — fast.

Yes—really. We met at 12:30 p.m. and paid $4.99/month; that's it.`;

// What the engine reports for it, modelled on its measured behaviour (see
// HANDOFF.md, "Sections: partitioning and alignment"): quotes and brackets
// stripped, a standalone "—" not spoken, "Yes—really." split in two,
// "12:30 p.m." merged into one word, "$4.99/month;" split into pieces with a
// punctuation-only "." among them. Times are 300 ms per engine word.
const ENGINE = ["Don't", "panic", "she", "said", "The", "well-known", "value", "is", "3.14", "roughly",
  "The", "U.S.", "sent", "1,000", "boxes", "fast", "Yes", "really", "We", "met", "at", "12:30 p.m.",
  "and", "paid", "$4", ".", "99/month", "that's", "it"]
  .map((word, i) => ({ word, start_ms: i * 300, end_ms: i * 300 + 250 }));

const words = textWords(TEXT);
const at = (w) => words.findIndex((x) => x.word === w);

test("textWords keeps exact character ranges across line breaks", () => {
  // positions index the original text, so the page can highlight in place
  for (const w of words) {
    assert.equal(TEXT.slice(w.start, w.end), w.word);
  }
  assert.equal(words[at("Yes—really.")].start, TEXT.indexOf("Yes—really."));
});

test("every spoken word gets a timing, in order, and nothing is guessed", () => {
  const { timings, alignment } = alignTimings(words, ENGINE, 0, words.length);
  // only the standalone dash has nothing to speak
  assert.deepEqual(alignment.unspokenWords, [at("—")]);
  assert.deepEqual(alignment.wordsWithoutTiming, []);
  // the punctuation-only engine word "." can't be placed: recorded, not guessed
  assert.deepEqual(alignment.unmatchedTimings.map((t) => t.word), ["."]);
  const placed = timings.filter((t) => t.wordIndex !== null);
  for (let i = 1; i < placed.length; i++) {
    assert.ok(placed[i].wordIndex >= placed[i - 1].wordIndex, "word order never goes backwards");
  }
});

test("each awkward case lands on the right document word", () => {
  const { timings } = alignTimings(words, ENGINE, 0, words.length);
  const wordFor = (engineWord) => words[timings.find((t) => t.word === engineWord || t.word.includes(engineWord)).wordIndex].word;
  assert.equal(wordFor("Don't"), '"Don\'t');      // quote stripped by the engine
  assert.equal(wordFor("well-known"), "well-known");
  assert.equal(wordFor("3.14"), "3.14");
  assert.equal(wordFor("roughly"), "(roughly).");  // parentheses stripped
  assert.equal(wordFor("U.S."), "U.S.");
  assert.equal(wordFor("1,000"), "1,000");
  assert.equal(wordFor("fast"), "fast.");          // after the skipped "—"
  // a split word: both engine pieces point at the one document word
  const yes = timings.filter((t) => t.wordIndex === at("Yes—really."));
  assert.equal(yes.length, 2);
});

test("a merged engine word is split back into one timing per word", () => {
  // "12:30 p.m." arrives as one engine word; captions need one per word, so
  // its time is shared between "12:30" and "p.m." without gaps or overlap
  const { timings } = alignTimings(words, ENGINE, 0, words.length);
  const time = timings.find((t) => t.wordIndex === at("12:30"));
  const pm = timings.find((t) => t.wordIndex === at("p.m."));
  assert.equal(time.lastWordIndex, at("12:30"));
  assert.equal(time.start_ms, 21 * 300);
  assert.equal(time.end_ms, pm.start_ms);
  assert.equal(pm.end_ms, 21 * 300 + 250);
});

test("an engine word that isn't in the text is skipped without derailing the rest", () => {
  const withExtra = [...ENGINE.slice(0, 5), { word: "banana", start_ms: 1, end_ms: 2 }, ...ENGINE.slice(5)];
  const { timings, alignment } = alignTimings(words, withExtra, 0, words.length);
  assert.ok(alignment.unmatchedTimings.some((t) => t.word === "banana"));
  assert.equal(words[timings.find((t) => t.word === "it").wordIndex].word, "it.");
});

test("a section aligns only within its own word range", () => {
  // sections are aligned separately; indices stay document-wide
  const start = at("Yes—really.");
  const { timings, alignment } = alignTimings(words, ENGINE.slice(16), start, words.length);
  assert.equal(timings[0].wordIndex, start);
  assert.deepEqual(alignment.wordsWithoutTiming, []);
});
