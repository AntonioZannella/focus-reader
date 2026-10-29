// Sound-it-out spellings for Language mode (frontend/alignment.js soundsFor),
// checked against the real list the page loads (frontend/sounds.txt, made by
// tools/make_sounds.py). A wrong spelling teaches a wrong pronunciation.
// Run: node --test tests/node/*.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { soundsFor } = require("../../frontend/alignment.js");

const lookup = new Map(readFileSync(join(__dirname, "../../frontend/sounds.txt"), "utf8")
  .split("\n").filter((line) => line && !line.startsWith("#")).map((line) => line.split("\t")));

test("words are spelled by sound, stressed syllable in capitals", () => {
  assert.equal(soundsFor("broccoli", lookup), "BRAH-kuh-lee");
  assert.equal(soundsFor("computer", lookup), "kuhm-PYOO-tur");
  assert.equal(soundsFor("teacher", lookup), "TEE-chur");
  assert.equal(soundsFor("banana", lookup), "buh-NAN-uh");
  assert.equal(soundsFor("cat", lookup), "kat");
});

test("punctuation, capitals and curly apostrophes are ignored", () => {
  assert.equal(soundsFor("“Broccoli,", lookup), "BRAH-kuh-lee");
  assert.equal(soundsFor("don’t.", lookup), soundsFor("don't", lookup));
});

test("hyphenated words are spelled part by part", () => {
  assert.equal(soundsFor("well-known", lookup), `${soundsFor("well", lookup)}-${soundsFor("known", lookup)}`);
});

test("words the dictionary lacks get nothing", () => {
  assert.equal(soundsFor("42", lookup), null);
  assert.equal(soundsFor("—", lookup), null);
  assert.equal(soundsFor("xqzvb", lookup), null);
});
