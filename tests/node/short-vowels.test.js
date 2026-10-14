// Short vowels for Education mode (frontend/alignment.js shortVowelIndexes):
// these letters are colored pink in the caption, so a wrong answer teaches a
// wrong sound. Run: node --test tests/node/*.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const { shortVowelIndexes } = require("../../frontend/alignment.js");

// The word with its short vowels in capitals, e.g. "rAbbIt".
const mark = (word) => [...word].map((c, i) => (shortVowelIndexes(word).includes(i) ? c.toUpperCase() : c)).join("");

test("the classic short vowels are marked", () => {
  // a vowel in a closed syllable (a consonant ends it) makes its short sound
  assert.deepEqual(["cat", "bed", "sit", "hot", "cup"].map(mark), ["cAt", "bEd", "sIt", "hOt", "cUp"]);
  assert.deepEqual(["rabbit", "happy", "intellect", "it's"].map(mark), ["rAbbIt", "hAppy", "IntEllEct", "It's"]);
});

test("vowels that say their name or another sound are not marked", () => {
  // silent e, vowel teams, open syllables, r-controlled, igh, al/all
  const notShort = ["cake", "hope", "makes", "rain", "boat", "day", "he", "go", "lady",
    "car", "her", "bird", "night", "ball", "talk", "salt"];
  for (const word of notShort) {
    assert.deepEqual(shortVowelIndexes(word), [], `${word} -> ${mark(word)}`);
  }
  // an open syllable's vowel says its name: the o of "open"
  assert.ok(!shortVowelIndexes("open").includes(0));
});

test("common exceptions to the closed-syllable rule are left alone", () => {
  // -ind, -ild, -old and a few words whose vowel is long despite the rule
  for (const word of ["find", "child", "old", "bolt", "most", "both", "only", "don't"]) {
    assert.deepEqual(shortVowelIndexes(word), [], word);
  }
});

test("one-letter words and the -ed ending are never marked", () => {
  // "a" and "I" don't use the short sound; the e of "-ed" is usually silent
  assert.deepEqual(shortVowelIndexes("a"), []);
  assert.deepEqual(shortVowelIndexes("I"), []);
  assert.equal(mark("jumped"), "jUmped");
});

test("positions index the original word, including capitals and punctuation", () => {
  // the caption colors letters by these positions
  assert.equal(mark("Rottweiler,"), "ROttweiler,");
  assert.equal(mark('"Stop!"'), '"StOp!"');
});
