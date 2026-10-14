// Welded sounds for Education mode (frontend/alignment.js weldedRanges):
// these letter groups are colored green in the caption as one sound unit.
// Run: node --test tests/node/*.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const { weldedRanges } = require("../../frontend/alignment.js");

// The word with its welded units in brackets, e.g. "th[ank]s".
const mark = (word) => {
  let out = word;
  for (const [a, b] of weldedRanges(word).reverse()) out = `${out.slice(0, a)}[${out.slice(a, b)}]${out.slice(b)}`;
  return out;
};

test("each welded unit is found at the end of a word", () => {
  // -all, -am, -an, -ang, -ing, -ong, -ung, -ank, -ink, -onk, -unk
  const words = ["ball", "ham", "can", "sang", "king", "long", "hung", "bank", "think", "honk", "junk"];
  assert.deepEqual(words.map(mark),
    ["b[all]", "h[am]", "c[an]", "s[ang]", "k[ing]", "l[ong]", "h[ung]", "b[ank]", "th[ink]", "h[onk]", "j[unk]"]);
});

test("a unit still counts before an ending, and ng/nk before a consonant", () => {
  assert.deepEqual(["fans", "smaller", "thanks", "hungry", "singing", "longer"].map(mark),
    ["f[an]s", "sm[all]er", "th[ank]s", "h[ung]ry", "s[ing][ing]", "l[ong]er"]);
  // "-ing" is welded even after a vowel
  assert.equal(mark("going"), "go[ing]");
});

test("letters that only look welded are left alone", () => {
  // a/n before another consonant, before a vowel, inside a vowel team, and
  // the long-a "-anger" words
  for (const word of ["and", "want", "hand", "camp", "same", "animal", "angel", "mean", "really", "finally", "danger", "stranger"]) {
    assert.deepEqual(weldedRanges(word), [], word);
  }
});

test("positions index the original word, including capitals and apostrophes", () => {
  assert.equal(mark("Thing,"), "Th[ing],");
  assert.equal(mark("can't"), "c[an]'t");
});
