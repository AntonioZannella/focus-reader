// Pure text helpers shared by the page and the Node tests (tests/node).
// Loaded as a classic script before the page's own script, so these are
// globals there; under Node they're exported. Moved out of index.html
// unchanged.

// The text's whitespace-separated words with their character ranges.
function textWords(text) {
  return Array.from(text.matchAll(/\S+/g), (match) => ({
    word: match[0],
    start: match.index,
    end: match.index + match[0].length,
  }));
}

// --- Aligning the speech engine's words with the document's -------------
// The engine's words don't map one to one onto the text's words: it drops
// punctuation-only words ("—"), splits some ("$4.99/month;" -> "$4", ".",
// "99/month"; "Yes—really." -> "Yes", "really") and merges others ("12:30
// p.m." is one engine word). So both are compared as letters and digits
// only, in order: each engine word must continue exactly where the last
// one stopped (the rest of a split word) or start at one of the next few
// document words, never in the middle of one. A merged engine word covers
// every document word it spans. An engine word that can't be placed that
// way is recorded as unmatched (wordIndex null), never guessed.
const ALIGN_LOOKAHEAD_WORDS = 4;

function comparableText(text) {
  return text.normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
}

// Returns {timings: [{wordIndex, lastWordIndex, word, start_ms, end_ms}],
// alignment: {unmatchedTimings: [{index, word}] (engine words not
// placed), wordsWithoutTiming: [global indices] (words with letters or
// digits no engine word covered), unspokenWords: [global indices] (words
// with neither, e.g. "—")}}.
function alignTimings(documentWords, timings, wordStart, wordEnd) {
  const unspokenWords = [];
  for (let w = wordStart; w < wordEnd; w++) {
    if (!comparableText(documentWords[w].word)) {
      unspokenWords.push(w);
    }
  }
  const identical =
    timings.length === wordEnd - wordStart &&
    timings.every((timing, i) => timing.word === documentWords[wordStart + i].word);
  if (identical) {
    return {
      timings: timings.map((timing, i) => ({
        wordIndex: wordStart + i, lastWordIndex: wordStart + i,
        word: timing.word, start_ms: timing.start_ms, end_ms: timing.end_ms,
      })),
      alignment: { unmatchedTimings: [], wordsWithoutTiming: [], unspokenWords },
    };
  }

  // the section's words as one run of letters and digits, remembering
  // which word each character belongs to and where each word starts
  let stream = "";
  const wordAt = [];
  const wordStartsAt = new Map();
  for (let w = wordStart; w < wordEnd; w++) {
    const comparable = comparableText(documentWords[w].word);
    wordStartsAt.set(w, stream.length);
    for (let k = 0; k < comparable.length; k++) {
      wordAt.push(w);
    }
    stream += comparable;
  }

  let cursor = 0;
  const covered = new Set();
  const unmatchedTimings = [];
  const aligned = timings.map((timing, index) => {
    const token = comparableText(timing.word);
    let at = -1;
    if (token && stream.startsWith(token, cursor)) {
      at = cursor;
    } else if (token) {
      const fromWord = cursor < stream.length ? wordAt[cursor] : wordEnd;
      const toWord = Math.min(wordEnd, fromWord + ALIGN_LOOKAHEAD_WORDS + 1);
      for (let w = fromWord; w < toWord; w++) {
        const position = wordStartsAt.get(w);
        if (position >= cursor && stream.startsWith(token, position)) {
          at = position;
          break;
        }
      }
    }
    if (at < 0) {
      unmatchedTimings.push({ index, word: timing.word });
      return { wordIndex: null, lastWordIndex: null, word: timing.word, start_ms: timing.start_ms, end_ms: timing.end_ms };
    }
    const first = wordAt[at];
    const last = wordAt[at + token.length - 1];
    for (let w = first; w <= last; w++) {
      covered.add(w);
    }
    cursor = at + token.length;
    return { wordIndex: first, lastWordIndex: last, word: timing.word, start_ms: timing.start_ms, end_ms: timing.end_ms };
  });
  const wordsWithoutTiming = [];
  for (let w = wordStart; w < wordEnd; w++) {
    if (!covered.has(w) && comparableText(documentWords[w].word)) {
      wordsWithoutTiming.push(w);
    }
  }
  // The engine sometimes gives several words one timing ("3 has ten
  // plain", "12:30 p.m."), which would show them as one caption whatever
  // the chunk size. Split it into one timing per word, sharing its time by
  // letter count.
  // ponytail: the split points are estimated inside the engine's interval;
  // per-word audio analysis would be needed to place them exactly.
  const split = aligned.flatMap((timing) => {
    if (timing.wordIndex === null || timing.lastWordIndex === timing.wordIndex) {
      return [timing];
    }
    const pieces = [];
    for (let w = timing.wordIndex; w <= timing.lastWordIndex; w++) {
      pieces.push({ w, letters: Math.max(1, comparableText(documentWords[w].word).length) });
    }
    const total = pieces.reduce((sum, piece) => sum + piece.letters, 0);
    const span = timing.end_ms - timing.start_ms;
    let start = timing.start_ms;
    return pieces.map((piece, k) => {
      const end = k === pieces.length - 1 ? timing.end_ms : start + (span * piece.letters) / total;
      const part = { wordIndex: piece.w, lastWordIndex: piece.w, word: documentWords[piece.w].word, start_ms: start, end_ms: end };
      start = end;
      return part;
    });
  });
  return { timings: split, alignment: { unmatchedTimings, wordsWithoutTiming, unspokenWords } };
}

// Phrases for the "Phrase" chunk size: short groups of consecutive words that
// each carry one idea, action, purpose or state ("I went outside / to get the
// garbage / and it was raining"), read as natural chunks. A full stop, ?, !
// or closing bracket always ends a phrase; a comma, semicolon, colon or dash
// does once the phrase has MIN_CLAUSE_WORDS words (so "His giant, anvil-like
// head" stays together). Quotes after them are allowed. Inside a clause, a new
// phrase starts where a new idea does, judged from the word that opens it:
//  - always (BREAK_ALWAYS): a subordinating word ("because", "while", "if",
//    "which"...), "but"/"so"/"yet", "and"/"or" followed by a new subject
//    ("and it", "and the"), and "to" + verb meaning "in order to" (not after
//    verbs that take it, like "wanted to", "learned to");
//  - only to shorten a phrase longer than maxWords (BREAK_IF_LONG): other
//    "and"/"or", "that", and prepositions that start a new piece of
//    information ("with", "for", "like", "during"...); failing those, before
//    "in"/"on"/"at" or a pronoun subject ("a world / he did not understand").
//    With none of those, a long phrase is split into even pieces.
// A break never leaves fewer than MIN_PHRASE_WORDS words on either side
// (so "I went / outside" never happens) and never falls inside a word the
// engine split. Returns [start, end) ranges of timing indices, covering all
// timings in order. documentWords are the document's words (with their
// punctuation); timings point at them through wordIndex / lastWordIndex.
// ponytail: word-list heuristics, not a parser; a grammar parse (e.g. spaCy
// in the backend) would place breaks more reliably if these fall short.
const PHRASE_END = /[,.;:!?—–)\]]["'”’)\]]*$/;
const SENTENCE_END = /[.!?)\]]["'\u201d\u2019)\]]*$/;
const MIN_PHRASE_WORDS = 2;
const MIN_CLAUSE_WORDS = 3;
const BREAK_ALWAYS = 3;
const BREAK_IF_LONG = 2;
const BREAK_WEAK = 1;
const SUBORDINATORS = new Set(["because", "although", "though", "while", "whilst", "when", "whenever", "if", "unless",
  "since", "whereas", "until", "where", "wherever", "which", "who", "whom", "whose"]);
const CONTRASTS = new Set(["but", "so", "yet"]);
const NEW_SUBJECT = new Set(["i", "you", "he", "she", "it", "we", "they", "there", "this", "these", "those", "the", "a", "an",
  "his", "her", "my", "your", "our", "their", "its", "then", "nobody", "everyone", "someone", "nothing", "everything"]);
// "to" after these is part of the verb ("wanted to go"), not a new purpose
const TAKES_TO = new Set(["want", "wants", "wanted", "need", "needs", "needed", "try", "tries", "tried", "learn", "learns",
  "learned", "learnt", "decide", "decides", "decided", "going", "begin", "begins", "began", "start", "starts", "started",
  "like", "likes", "liked", "love", "loves", "loved", "hope", "hoped", "plan", "planned", "have", "has", "had", "used",
  "able", "seem", "seems", "seemed", "refuse", "refused", "ought", "agree", "agreed", "chose", "choose", "managed",
  "failed", "continue", "continued", "tend", "tended", "happen", "happened", "how", "what", "where", "whether"]);
// a word after "to" that makes it a preposition ("to the store"), not a verb
const NOT_A_VERB = new Set(["the", "a", "an", "my", "your", "his", "her", "its", "our", "their", "this", "that", "these",
  "those", "me", "him", "them", "us", "it", "some", "any", "every", "each", "no", "all", "both"]);
const PREPOSITIONS = new Set(["with", "without", "for", "from", "into", "like", "during", "through", "across", "toward",
  "towards", "against", "among", "about", "around", "under", "over", "between", "after", "before", "behind", "beyond",
  "despite", "near", "onto", "upon", "within"]);
const WEAK_PREPOSITIONS = new Set(["in", "on", "at"]);
const SUBJECT_PRONOUNS = new Set(["i", "you", "he", "she", "it", "we", "they"]);

function bareWord(word) {
  return word.toLowerCase().replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, "");
}

// How strongly a new phrase starts at `word` (0 = never here).
function phraseBreakBefore(previous, word, next) {
  if (SUBORDINATORS.has(word) || CONTRASTS.has(word)) {
    return BREAK_ALWAYS;
  }
  if (word === "and" || word === "or") {
    return NEW_SUBJECT.has(next) ? BREAK_ALWAYS : BREAK_IF_LONG;
  }
  if (word === "to") {
    return TAKES_TO.has(previous) || NOT_A_VERB.has(next) || /^\d/.test(next) ? BREAK_WEAK : BREAK_ALWAYS;
  }
  if (PREPOSITIONS.has(word) || word === "that") {
    return BREAK_IF_LONG;
  }
  return WEAK_PREPOSITIONS.has(word) || SUBJECT_PRONOUNS.has(word) ? BREAK_WEAK : 0;
}

function phraseRanges(timings, documentWords, maxWords = 7) {
  const shown = (t) => (t.lastWordIndex != null && documentWords[t.lastWordIndex] ? documentWords[t.lastWordIndex].word : t.word);
  // a word the engine split into pieces ("Yes—really.") is never broken
  const insideWord = (i) => i > 0 && timings[i].wordIndex != null && timings[i].wordIndex === timings[i - 1].lastWordIndex;
  const strength = timings.map((t, i) => (i === 0 || insideWord(i)
    ? -1
    : phraseBreakBefore(bareWord(shown(timings[i - 1])), bareWord(shown(t)), timings[i + 1] ? bareWord(shown(timings[i + 1])) : "")));
  const ranges = [];

  // Splits timings [start, end) (one clause) into phrases.
  function split(start, end) {
    const length = end - start;
    const allowed = (i) => strength[i] >= 0 && i - start >= MIN_PHRASE_WORDS && end - i >= MIN_PHRASE_WORDS;
    // new ideas always start a phrase
    for (let i = start + 1; i < end; i++) {
      if (strength[i] >= BREAK_ALWAYS && allowed(i)) {
        split(start, i);
        split(i, end);
        return;
      }
    }
    if (length <= maxWords) {
      ranges.push([start, end]);
      return;
    }
    // too long: break at the strongest remaining cue, nearest the middle
    let best = -1;
    for (let i = start + 1; i < end; i++) {
      if (allowed(i) && strength[i] > 0 && (best < 0 || strength[i] > strength[best]
        || (strength[i] === strength[best] && Math.abs(i - start - length / 2) < Math.abs(best - start - length / 2)))) {
        best = i;
      }
    }
    if (best >= 0) {
      split(start, best);
      split(best, end);
      return;
    }
    // no cue at all: even pieces
    const pieces = Math.ceil(length / maxWords);
    for (let k = 0; k < pieces; k++) {
      ranges.push([start + Math.round((k * length) / pieces), start + Math.round(((k + 1) * length) / pieces)]);
    }
  }

  let clauseStart = 0;
  timings.forEach((t, i) => {
    const next = timings[i + 1];
    const ends = SENTENCE_END.test(shown(t)) || (PHRASE_END.test(shown(t)) && i + 1 - clauseStart >= MIN_CLAUSE_WORDS);
    if (!next || (!insideWord(i + 1) && ends)) {
      split(clauseStart, i + 1);
      clauseStart = i + 1;
    }
  });
  return ranges;
}

// Short vowels for Education mode: the positions (indices into `word`) of
// vowels that make their short sound, as in cat, bed, sit, hot, cup. Phonics
// rule: a single vowel in a closed syllable, i.e. followed by a consonant
// that ends the syllable (two or more consonants, or consonants to the end of
// the word). Not short: vowel teams (rain, boat, day), a vowel ending the
// word (he, go), silent-e words (cake, hope, makes), r-controlled vowels
// (car, her, bird), "igh" (night), a before ll/lk/lt (ball, talk, salt),
// i before final nd/ld (find, child), o before final ld/lt (old, bolt), the
// "-ed" ending (jumped), a vowel before one consonant and another vowel
// (open, lady: usually long, so left out), and a few common words
// (NOT_SHORT_WORDS). One-letter words ("a", "I") are never marked.
// ponytail: phonics rules, not a pronunciation dictionary; unusual spellings
// can be wrong. A dictionary (e.g. CMUdict) would be exact if needed.
const NOT_SHORT_WORDS = new Set(["both", "most", "post", "host", "ghost", "only", "don't", "won't", "pint", "climb", "sign", "gross"]);

function shortVowelIndexes(word) {
  const lower = word.toLowerCase();
  if (NOT_SHORT_WORDS.has(lower.replace(/[^a-z']/g, ""))) {
    return [];
  }
  const result = [];
  // each run of letters on its own ("it's" -> "it", "s")
  for (const match of lower.matchAll(/[a-z]+/g)) {
    const s = match[0];
    const n = s.length;
    const vowel = (c) => "aeiou".includes(c);
    const vowelOrY = (c) => "aeiouy".includes(c);
    if (n === 1) {
      continue;
    }
    for (let i = 0; i < n; i++) {
      const c = s[i];
      if (!vowel(c)) continue;
      if (i > 0 && vowel(s[i - 1])) continue; // second letter of a team
      if (i + 1 < n && (vowel(s[i + 1]) || "wy".includes(s[i + 1]))) continue; // first letter of a team
      if (i === n - 1) continue; // ends the word: open
      const rest = s.slice(i + 1);
      if (rest[0] === "r" && rest[1] !== "r" && !(rest.length > 1 && vowelOrY(rest[1]))) continue; // r-controlled
      if (c === "i" && rest.startsWith("gh")) continue;
      if (c === "a" && /^l[lkt]/.test(rest)) continue;
      if (c === "i" && /^(nd|ld)$/.test(rest)) continue;
      if (c === "o" && /^(ld|lt)$/.test(rest)) continue;
      if (c === "e" && rest === "d" && n >= 5) continue; // the "-ed" ending
      if (c === "e" && (rest === "s" || rest === "d") && i >= 2 && !vowelOrY(s[i - 1]) && vowel(s[i - 2])) continue; // silent e + s/d: makes, hoped
      // consonants up to the next vowel (y counts as one here: happy, lady)
      let j = i + 1;
      while (j < n && !vowelOrY(s[j])) j++;
      const consonants = j - i - 1;
      if (j < n && consonants === 1) continue; // V-C-V: open (or silent e)
      if (j < n && s[j] === "e" && (j === n - 1 || (j === n - 2 && "sd".includes(s[n - 1]))) && consonants === 1) continue;
      result.push(match.index + i);
    }
  }
  return result;
}

// Welded sounds for Education mode: letter groups read as one sound unit
// (-all, -am, -an, -ang, -ing, -ong, -ung, -ank, -ink, -onk, -unk). Returns
// [start, end) ranges of indices into `word`. A unit only counts where it
// ends the syllable: at the end of the word, or before an ending (-s, -er,
// -ing, -ed...); the ng/nk units also before any consonant (hungry, thanks).
// So "and", "want", "same", "animal" and "angel" have none. Its vowel can't
// belong to a vowel team ("mean"), except in "-ing" (going, seeing).
const WELDED_UNITS = ["ang", "ing", "ong", "ung", "ank", "ink", "onk", "unk", "all", "am", "an"];
const WELDED_ENDINGS = new Set(["", "s", "es", "er", "ers", "est", "ed", "ing", "ings"]);
// spelled like a welded "ang" but said with a long a
const NOT_WELDED_WORDS = new Set(["danger", "dangers", "stranger", "strangers", "ranger", "rangers", "manger", "mangers"]);

function weldedRanges(word) {
  const lower = word.toLowerCase();
  const ranges = [];
  for (const match of lower.matchAll(/[a-z]+/g)) {
    const s = match[0];
    if (NOT_WELDED_WORDS.has(s)) continue;
    for (let i = 0; i < s.length; i++) {
      const unit = WELDED_UNITS.find((u) => {
        if (!s.startsWith(u, i)) return false;
        if (i > 0 && "aeiou".includes(s[i - 1]) && u !== "ing") return false;
        const after = s.slice(i + u.length);
        if (WELDED_ENDINGS.has(after)) return true;
        const ngOrNk = u.endsWith("ng") || u.endsWith("nk");
        return ngOrNk && (after === "y" || !"aeiouy".includes(after[0]));
      });
      if (unit) {
        ranges.push([match.index + i, match.index + i + unit.length]);
        i += unit.length - 1;
      }
    }
  }
  return ranges;
}

// Prefixes and suffixes for Education mode: [start, end) ranges of indices
// into `word`, each boxed in the caption (un|happy, help|ful|ness). A part
// only counts if what's left is a real word of 3+ letters (isWord, backed by
// a dictionary), allowing the usual spelling changes: a doubled consonant
// (run-n-ing), a dropped e (mak-ing -> make), y changed to i (happi-ness ->
// happy). Up to two suffixes are found (help-ful-ness). Words that only look
// affixed (mother, water, present, remember, thing...) are listed in
// NOT_AFFIXED; words under 4 letters are never split.
// ponytail: a dictionary check plus an exception list, not real morphology;
// a word whose leftover happens to be an obscure dictionary word can still
// be boxed wrongly. Add it to NOT_AFFIXED when that happens.
const PREFIXES = ["under", "inter", "trans", "super", "over", "semi", "anti", "non", "mis", "dis", "pre", "sub", "mid", "un", "re", "il", "ir"];
const SUFFIXES = ["ness", "less", "ment", "able", "ible", "ful", "est", "ing", "ly", "ed", "er", "es", "s"];
// suffixes that can have another suffix after them (help-ful-ness, care-less-ly);
// "-er", "-ing" and the like can't, so "wavering" is waver + ing
const INNER_SUFFIXES = new Set(["ness", "less", "ment", "able", "ible", "ful", "ly"]);
const NOT_AFFIXED = new Set(`
  mother father brother sister water winter summer number letter better butter matter dinner corner flower power tower
  paper hammer master center offer order cover never ever over under after other either whether rather together weather
  feather leather ladder finger silver monster member spider tiger copper pepper manner banner chapter wonder thunder
  answer enter super upper proper temper slipper liver river shower cancer soccer timber lumber rubber ginger danger
  stranger ranger manger hunger anger eager sweater poster carpenter character quarter oyster lobster blister whisper
  clever deliver discover consider remember gather laser chatter flatter litter glitter bitter platter scatter shatter
  cucumber slumber burger linger hamper pamper prosper later sober cater
  early reply apply supply belly jelly silly holy ugly lily family only july rely ally bully dolly folly jolly rally
  tally assembly butterfly anomaly monopoly
  hundred naked sacred wicked kindred beloved
  news always perhaps series physics species lens pants jeans measles
  evening ceiling during nothing something everything anything pudding sibling darling sterling herring earring
  honest forest interest modest contest protest request digest suggest invest arrest earnest priest manifest tempest
  comment segment pigment torment garment fragment ferment cement lament harness capable
  repair report record result relate release remain remove repeat recent refuse remark resort reveal reward regard rebel
  present prevent pretend prepare preserve display discuss until union unless uniform missile interest
`.trim().split(/\s+/));

function affixRanges(word, isWord) {
  const lower = word.toLowerCase();
  const letters = lower.match(/^([^a-z]*)([a-z]+)[^a-z]*$/); // one run of letters, maybe with punctuation around
  if (!letters || letters[2].length < 4 || NOT_AFFIXED.has(letters[2])) {
    return [];
  }
  const offset = letters[1].length;
  const w = letters[2];
  // The suffix ranges (relative to w) of `part` ending at `end`, or null
  // if part isn't a word plus suffixes.
  const stemCandidates = (base) => {
    const candidates = [base, base + "e"];
    if (base.length > 2 && base.at(-1) === base.at(-2)) candidates.push(base.slice(0, -1));
    if (base.endsWith("i")) candidates.push(base.slice(0, -1) + "y");
    return candidates;
  };
  const suffixes = (part, end, depth) => {
    for (const suffix of SUFFIXES) {
      const base = part.slice(0, part.length - suffix.length);
      if (!part.endsWith(suffix) || base.length < 3) continue;
      if (depth > 1 && !INNER_SUFFIXES.has(suffix)) continue;
      if (suffix === "s" && /(s|u|i)$/.test(base)) continue; // class, bus, this
      if (suffix === "es" && !/(s|x|z|ch|sh|o|i)$/.test(base)) continue; // boxes, wishes, babies
      const range = [end - suffix.length, end];
      // a second suffix inside first (help-ful-ness), then a plain stem
      const inner = depth < 2 ? suffixes(base, end - suffix.length, depth + 1) : null;
      if (inner) return [...inner, range];
      if (stemCandidates(base).some((stem) => stem.length >= 3 && isWord(stem))) return [range];
    }
    return null;
  };
  const isStem = (part) => isWord(part) || suffixes(part, part.length, 1) !== null;

  const ranges = [];
  let rest = w;
  let restStart = 0;
  const prefix = PREFIXES.find((p) => w.startsWith(p) && w.length - p.length >= 3 && isStem(w.slice(p.length)));
  if (prefix) {
    ranges.push([0, prefix.length]);
    rest = w.slice(prefix.length);
    restStart = prefix.length;
  }
  const found = suffixes(rest, rest.length, 1);
  if (found) {
    ranges.push(...found.map(([a, b]) => [a + restStart, b + restStart]));
  }
  return ranges.map(([a, b]) => [a + offset, b + offset]);
}

// Language mode: the sound-it-out spelling (from frontend/sounds.txt) of a
// caption word, or null if the dictionary doesn't have it (names, numbers...).
// Punctuation around the word is ignored; a hyphenated word is looked up
// part by part ("well-known" -> "wel-nohn").
function soundsFor(word, lookup) {
  const w = word.toLowerCase().replace(/[\u2018\u2019]/g, "'").replace(/^[^a-z]+|[^a-z]+$/g, "");
  if (!w) {
    return null;
  }
  if (lookup.has(w)) {
    return lookup.get(w);
  }
  const parts = w.split("-");
  if (parts.length > 1 && parts.every((part) => lookup.has(part))) {
    return parts.map((part) => lookup.get(part)).join("-");
  }
  return null;
}

if (typeof module !== "undefined") {
  module.exports = { textWords, comparableText, alignTimings, ALIGN_LOOKAHEAD_WORDS, phraseRanges, shortVowelIndexes, weldedRanges, affixRanges, soundsFor };
}
