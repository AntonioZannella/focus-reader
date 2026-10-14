// Phrase captions (frontend/alignment.js phraseRanges): the default chunk
// mode shows one phrase at a time, so where phrases break decides what the
// reader sees together. Run: node --test tests/node/*.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const { textWords, alignTimings, phraseRanges } = require("../../frontend/alignment.js");

// Timings one per engine word, aligned to the text the way the app does it;
// each phrase as the caption shows it (the engine's words).
function phrasesOf(text, engineWords = null) {
  const words = textWords(text);
  const engine = (engineWords || words.map((w) => w.word)).map((word, i) => ({ word, start_ms: i * 100, end_ms: i * 100 + 90 }));
  const { timings } = alignTimings(words, engine, 0, words.length);
  return phraseRanges(timings, words).map(([a, b]) => timings.slice(a, b).map((t) => t.word).join(" "));
}

test("a new action or purpose starts a new phrase", () => {
  // the defining example: one idea per phrase, without splitting words that
  // belong together ("I went / outside" would be too small)
  assert.deepEqual(phrasesOf("I went outside to get the garbage and it was raining."),
    ["I went outside", "to get the garbage", "and it was raining."]);
  // subordinating words and "but" start one too
  assert.deepEqual(phrasesOf("She stayed home because it was late but he went out."),
    ["She stayed home", "because it was late", "but he went out."]);
});

test("words that need each other are kept together", () => {
  // "bread and butter" is one thing; "wanted to go" is one action; "to the
  // park" is a place, not a new purpose
  assert.deepEqual(phrasesOf("She bought bread and butter. We wanted to go to the park."),
    ["She bought bread and butter.", "We wanted to go to the park."]);
});

test("punctuation ends a phrase, but not a too-short one", () => {
  // full stops, ? and closing brackets always end a phrase; a comma, colon or
  // dash only once the phrase has 3 words, so "His giant, anvil-like head" and
  // "Here: done" stay together
  assert.deepEqual(phrasesOf("When it rains, we stay in. Why not? (Really.) Here: done"),
    ["When it rains,", "we stay in.", "Why not?", "(Really.)", "Here: done"]);
  assert.deepEqual(phrasesOf("His giant, anvil-like head nodded."), ["His giant, anvil-like head nodded."]);
});

test("a long phrase breaks at its most natural point", () => {
  // over 7 words with no new idea: break before "that", a preposition or a
  // pronoun subject rather than in the middle of a word group
  assert.deepEqual(phrasesOf("Under the unwavering belief that it was his favorite tennis ball."),
    ["Under the unwavering belief", "that it was his favorite tennis ball."]);
  assert.deepEqual(phrasesOf("Enthusiasm for a world he fundamentally did not understand."),
    ["Enthusiasm for a world", "he fundamentally did not understand."]);
});

test("quotes after the punctuation still end the phrase", () => {
  assert.deepEqual(phrasesOf('She said "stop." Then left.'), ['She said "stop."', "Then left."]);
});

test("a long phrase is split into even pieces of at most 7 words", () => {
  // 16 words with no punctuation would overflow the screen as one caption
  const text = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen.";
  const phrases = phrasesOf(text);
  assert.equal(phrases.length, 3);
  assert.deepEqual(phrases.map((p) => p.split(" ").length), [5, 6, 5]);
  assert.equal(phrases.join(" "), text);
});

test("a word the engine splits ends a phrase only after its last piece", () => {
  // "Yes—really." comes back from the engine as "Yes", "really": the phrase
  // must not break after "Yes" just because the document word ends in "."
  assert.deepEqual(phrasesOf("Yes—really. Fine then.", ["Yes", "really", "Fine", "then"]),
    ["Yes really", "Fine then"]);
});

test("every timing belongs to exactly one phrase, in order", () => {
  // the caption lookup needs full coverage: no gaps, no overlaps
  const words = textWords("A, b c. d e f g h i j k l m n o p q r s t; u");
  const timings = words.map((w, i) => ({ wordIndex: i, lastWordIndex: i, word: w.word, start_ms: i, end_ms: i + 1 }));
  const ranges = phraseRanges(timings, words);
  assert.equal(ranges[0][0], 0);
  assert.equal(ranges.at(-1)[1], timings.length);
  ranges.forEach(([a, b], i) => {
    assert.ok(b > a);
    if (i) assert.equal(a, ranges[i - 1][1]);
  });
});
