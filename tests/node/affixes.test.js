// Prefix and suffix boxes for Education mode (frontend/alignment.js
// affixRanges), checked against the real word list the page loads
// (frontend/words.txt). A wrong box teaches a wrong word part.
// Run: node --test tests/node/*.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { affixRanges } = require("../../frontend/alignment.js");

const words = new Set(readFileSync(join(__dirname, "../../frontend/words.txt"), "utf8")
  .split("\n").filter((line) => line && !line.startsWith("#")));
// The word with each prefix/suffix in brackets, e.g. "[un]help[ful]".
const mark = (word) => {
  let out = word;
  for (const [a, b] of affixRanges(word, (w) => words.has(w)).reverse()) out = `${out.slice(0, a)}[${out.slice(a, b)}]${out.slice(b)}`;
  return out;
};

test("prefixes and suffixes on a real word are boxed", () => {
  // the definitions' own examples, plus each common prefix and suffix
  assert.equal(mark("unhappy"), "[un]happy");
  assert.equal(mark("helpful"), "help[ful]");
  assert.deepEqual(["reread", "preview", "dislike", "misread", "careless", "kindness", "movement", "readable", "teacher", "faster", "quickly", "walked", "jumping", "cats", "boxes"].map(mark),
    ["[re]read", "[pre]view", "[dis]like", "[mis]read", "care[less]", "kind[ness]", "move[ment]", "read[able]", "teach[er]", "fast[er]", "quick[ly]", "walk[ed]", "jump[ing]", "cat[s]", "box[es]"]);
});

test("spelling changes before a suffix are allowed", () => {
  // doubled consonant, dropped e, y changed to i
  assert.deepEqual(["running", "stopped", "bigger", "making", "nicer", "happiness", "carried", "babies"].map(mark),
    ["runn[ing]", "stopp[ed]", "bigg[er]", "mak[ing]", "nic[er]", "happi[ness]", "carri[ed]", "babi[es]"]);
});

test("stacked suffixes are each boxed", () => {
  assert.equal(mark("helpfulness"), "help[ful][ness]");
  assert.equal(mark("unhelpful"), "[un]help[ful]");
  assert.equal(mark("carelessly"), "care[less][ly]");
  // "-ing" after "-er" isn't a stack: "wavering" is waver + ing
  assert.equal(mark("unwavering"), "[un]waver[ing]");
});

test("words that only look like they have a prefix or suffix are left alone", () => {
  // the leftover isn't a word, is too short, or the word is a known look-alike
  for (const word of ["mother", "water", "sister", "number", "better", "under", "uncle", "thing", "king", "ring", "bring",
    "present", "remember", "until", "ready", "real", "only", "family", "hundred", "news", "always", "this", "bus", "class",
    "series", "evening", "during", "interest", "honest", "forest", "people", "garbage"]) {
    assert.deepEqual(affixRanges(word, (w) => words.has(w)), [], `${word} -> ${mark(word)}`);
  }
});

test("positions index the original word, including capitals and punctuation", () => {
  assert.equal(mark("Unhappy,"), "[Un]happy,");
  assert.equal(mark('"Thankfully'), '"Thank[ful][ly]');
});
