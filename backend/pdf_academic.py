"""Two trouble spots in academic PDFs: equations and citations.

Equations (find_equations): a block of the extracted PDF that is an equation
is left out of the text sent to text-to-speech entirely (no spoken
placeholder). Its original text is kept as an "equation marker" with the page
it's on and the word it follows, so the reader can show it at that point.

A block counts as an equation when most of its characters are set in a math
font, or when it's mostly non-letters and contains a real operator ("=", "+",
"≤", "∑", ...). A lone minus sign isn't enough (chart tick labels like
"−2 −1 0"), and neither is a row of numbers (tables). An equation number such
as "(3)" beside an equation belongs to it, and consecutive equation blocks
form one equation.

Inline math (remove_inline_math): math typeset inside a sentence ("a cost of
𝑡𝑥²") is removed from the text too, since text-to-speech reads it as garbled
letters plus "equals"/"plus"/"minus", and recorded as an inline equation
marker. It's recognised by Unicode's mathematical alphabets (𝑃, 𝑡, 𝜋), which
only typeset math produces; ordinary Greek in prose ("α = .05") is left alone.

Citations (strip_citations): removes in-text citation markers from cleaned
text: bracketed numbers ("[12]", "[3, 5–7]"), parenthetical author-year
citations ("(Smith et al., 2019)", "(e.g., Lee & Park, 2020; see Kim,
2018)", "(Park and Chen 2019)"), citations closing an explanatory
parenthesis ("(e.g., weather information; Lee, 2019)" -> "(e.g., weather
information)") and a narrative citation's year ("Smith (2019) found" ->
"Smith found"). Parenthesised numbers ("(15–17)")
are only treated as citations when the document's reference list is
numbered; elsewhere they're usually list items or equation numbers.
Reference-list entries themselves are never touched.
"""

import bisect
import re
import unicodedata

# At least this share of a block's characters in a math font makes it an
# equation (unless it's mostly real words: a sentence full of inline math).
EQUATION_MIN_MATH_FONT_SHARE = 0.5
# Without math fonts: fewer than this share of letters (Latin letters in
# multi-letter words; single letters are variables, and Greek and math-
# alphabet letters count as symbols) plus a real operator.
EQUATION_MAX_LETTER_SHARE = 0.5
# An equation has fewer than this share of real words (3+ letters) among its
# tokens: fewer still without a math font, where a table row of numbers with
# a "<=" in its label would otherwise pass. And at least this many characters.
EQUATION_MAX_WORD_SHARE = 0.34
EQUATION_MAX_WORD_SHARE_NO_MATH_FONT = 0.15
EQUATION_MIN_CHARS = 3
# Operators that make a line of symbols an equation. A minus sign alone
# doesn't (negative numbers on a chart axis).
EQUATION_OPERATORS = set("=<>≤≥≈≠≡∼≃∝+×÷·∑∏∫∮√∂∇∈∉⊂⊆∪∩→⇒⇔↦∀∃^")
EQUATION_NUMBER_PATTERN = re.compile(r"^\(\s*[A-Za-z]?\d+(?:\.\d+)*[a-z]?\s*\)$")

# --- citations -------------------------------------------------------------

# a word of an author list: a capitalised name, "et al.", "&", "and", a name
# particle ("van", "de"), or a small word inside an organisation's name
# ("City of New York", "Bureau of Labor Statistics")
_AUTHOR_WORD = r"(?:[A-ZÀ-Ý][\w’'\-.]*|&|and|et|al\.?|van|von|de|der|den|la|le|du|del|da|dos|of|the|for|on)"
_AUTHORS = rf"{_AUTHOR_WORD}(?:,?\s+{_AUTHOR_WORD})*"
_YEAR = r"(?:1[5-9]\d\d|20\d\d)[a-z]?|n\.\s?d\.|in\s+press|forthcoming"
_YEARS = rf"(?:{_YEAR})(?:\s*,\s*(?:{_YEAR}))*"
_PAGES = r"(?:,\s*(?:pp?\.|p)\s*\d+(?:\s*[-–]\s*\d+)?)?"
_PREFIX = r"(?:(?:e\.g\.|i\.e\.|cf\.|see(?:\s+also)?|for\s+(?:a\s+)?reviews?,?\s+see)\s*,?\s+)?"
_AUTHOR_YEAR = rf"{_PREFIX}{_AUTHORS},?\s+{_YEARS}{_PAGES}"
# "(Smith, 2019)", "(Smith et al., 2019; see Lee, 2020)", "(Park and Chen 2019)"
PARENTHETICAL_CITATION = re.compile(rf"\(\s*{_AUTHOR_YEAR}(?:\s*;\s*{_AUTHOR_YEAR})*\s*\)")
# citations closing an explanatory parenthesis: the "; Lee, 2019" of
# "(e.g., weather information; Lee, 2019)"
TRAILING_CITATION = re.compile(rf"\s*;\s*{_AUTHOR_YEAR}(?:\s*;\s*{_AUTHOR_YEAR})*(?=\s*\))")
# the year of a narrative citation: "Smith et al. (2019)", "(2019a, 2020)"
YEAR_CITATION = re.compile(rf"\(\s*{_YEARS}{_PAGES}\s*\)")
# "[12]", "[3, 5–7]", "[12, p. 4]"
BRACKET_CITATION = re.compile(r"\[\s*\d+(?:\s*[-–,]\s*\d+)*(?:\s*,\s*pp?\.\s*\d+(?:\s*[-–]\s*\d+)?)?\s*\]")
# "(5)", "(15–17)", "(19, 22, 23)": only in numbered-reference documents
PARENTHESIZED_NUMBER_CITATION = re.compile(r"\(\s*\d+(?:\s*[-–,]\s*\d+)*\s*\)")
# first word of a numbered reference-list entry: "1.", "[1]"
NUMBERED_REFERENCE = re.compile(r"^\[?\d+[.\]]$")

_SPOKEN_PUNCTUATION = set(".,;:!?)]\"”’'")
# Punctuation stripped from a word before judging whether it's math.
EDGE_PUNCTUATION = ".,;:!?\"“”‘’'"
# Unicode's Mathematical Alphanumeric Symbols block (𝐀 ... 𝟿).
MATH_ALPHABET_START, MATH_ALPHABET_END = 0x1D400, 0x1D7FF


def _is_letter(ch):
    """A Latin-script letter (Greek and math-alphabet letters count as symbols)."""
    if not ch.isalpha():
        return False
    name = unicodedata.name(ch, "")
    return not name.startswith(("GREEK", "MATHEMATICAL"))


def _is_operator(ch):
    return ch in EQUATION_OPERATORS or unicodedata.category(ch) == "Sm" and ch not in "−-~|"


def _is_equation(text, math_font_share):
    chars = [c for c in text if not c.isspace()]
    if len(chars) < EQUATION_MIN_CHARS:
        return False
    tokens = text.split()
    words = [t for t in tokens if sum(_is_letter(c) for c in t.strip(".,;:()[]")) >= 3 and t.strip(".,;:()[]").isalpha()]
    word_share = len(words) / len(tokens)
    if math_font_share >= EQUATION_MIN_MATH_FONT_SHARE:
        return word_share < EQUATION_MAX_WORD_SHARE
    # letters of multi-letter words: a single letter ("a + b = c") is a
    # variable, not prose
    letter_share = sum(
        sum(_is_letter(c) for c in token) for token in tokens if sum(_is_letter(c) for c in token) >= 2
    ) / len(chars)
    return (
        word_share < EQUATION_MAX_WORD_SHARE_NO_MATH_FONT
        and letter_share < EQUATION_MAX_LETTER_SHARE
        and any(_is_operator(c) for c in chars)
    )


def find_equations(raw):
    """Equations in an extract_pdf() result, in reading order:
    [{blocks: [block indices], page, bbox, text}]. text is the equation's
    lines joined by newlines, exactly as the PDF encodes them (which for
    some PDFs, e.g. ones made in Word, is garbled); bbox is its position on
    the page in points, so the equation can be shown as an image instead.
    """
    words = raw["words"]
    lines_by_block = {}
    for line in raw["lines"]:
        lines_by_block.setdefault(line["block"], []).append(line)

    def block_text(index, separator="\n"):
        return separator.join(
            " ".join(w["word"] for w in words[line["word_start"]:line["word_end"]])
            for line in lines_by_block.get(index, [])
        )

    blocks = raw["blocks"]
    is_equation = [
        _is_equation(block_text(i, " "), block["math_font_share"]) for i, block in enumerate(blocks)
    ]

    # an equation number beside an equation (same page, overlapping height)
    for i, block in enumerate(blocks):
        if is_equation[i] or not EQUATION_NUMBER_PATTERN.match(block_text(i, " ")):
            continue
        for j in (i - 1, i + 1):
            if 0 <= j < len(blocks) and is_equation[j] and blocks[j]["page"] == block["page"]:
                _, a0, _, a1 = block["bbox"]
                _, b0, _, b1 = blocks[j]["bbox"]
                if min(a1, b1) - max(a0, b0) > 0:
                    is_equation[i] = True
                    break

    equations = []
    for i, block in enumerate(blocks):
        if not is_equation[i]:
            continue
        previous = equations[-1] if equations else None
        if previous and previous["blocks"][-1] == i - 1 and previous["page"] == block["page"]:
            previous["blocks"].append(i)
            previous["text"] += "\n" + block_text(i)
            previous["bbox"] = [
                min(previous["bbox"][0], block["bbox"][0]), min(previous["bbox"][1], block["bbox"][1]),
                max(previous["bbox"][2], block["bbox"][2]), max(previous["bbox"][3], block["bbox"][3]),
            ]
        else:
            equations.append(
                {"blocks": [i], "page": block["page"], "bbox": list(block["bbox"]), "text": block_text(i)}
            )
    return equations


def strip_citations(clean):
    """Remove in-text citations from a clean_extraction() result.

    Returns a new result of the same shape (see _remove_spans) plus a
    `citations` list of what was removed: [{text, page, after_word}], where
    after_word is the index (in the new words) of the word the citation
    followed, -1 if none. Punctuation after a citation stays, attached to the
    word before it: "the internet (Kim, 2017)." -> "the internet."
    """
    words, text = clean["words"], clean["text"]
    numbered_references = any(
        p.get("list") and NUMBERED_REFERENCE.match(words[p["word_start"]]["word"])
        for p in clean["paragraphs"]
    )
    patterns = [PARENTHETICAL_CITATION, TRAILING_CITATION, YEAR_CITATION, BRACKET_CITATION]
    if numbered_references:
        patterns.append(PARENTHESIZED_NUMBER_CITATION)

    spans = []  # (start, end) character ranges in text, from non-list paragraphs
    for paragraph in clean["paragraphs"]:
        if paragraph.get("list") or paragraph["word_end"] == paragraph["word_start"]:
            continue
        start = words[paragraph["word_start"]]["start"]
        end = words[paragraph["word_end"] - 1]["end"]
        for pattern in patterns:
            spans.extend(m.span() for m in pattern.finditer(text, start, end))
    result, removed = _remove_spans(clean, spans)
    return {**result, "citations": [{key: r[key] for key in ("text", "page", "after_word")} for r in removed]}


def _is_math_word(core):
    """A word typeset as math: it has letters from Unicode's mathematical
    alphabets (𝑃, 𝑡, 𝜋), which only typeset math produces."""
    return any(MATH_ALPHABET_START <= ord(c) <= MATH_ALPHABET_END for c in core)


def _is_operator_word(core):
    return bool(core) and all(_is_operator(c) or c in "−-" for c in core)


def remove_inline_math(clean):
    """Remove math typeset inside sentences from a clean_extraction() result.

    Text-to-speech reads it as garbled letters and "equals"/"plus"/"minus".
    A run of math words (see _is_math_word), with any operator words ("=",
    "+", "−") next to them, becomes an equation marker like a display
    equation's: {text, page, bbox: None, after_word, inline: True}, added to
    `equations` in text order. Sentence punctuation after it stays: "a
    transportation cost of 𝑡𝑡𝑥𝑥2." -> "a transportation cost of."
    """
    words = clean["words"]
    cores = [w["word"].strip(EDGE_PUNCTUATION) for w in words]
    is_math = [_is_math_word(core) for core in cores]
    # operators join a run when they sit next to a math word
    for i, core in enumerate(cores):
        if not is_math[i] and _is_operator_word(core) and (
            (i > 0 and is_math[i - 1]) or (i + 1 < len(words) and _is_math_word(cores[i + 1]))
        ):
            is_math[i] = True

    def text_of(i, j):
        return clean["text"][words[i]["start"]:words[j]["end"]]

    spans = []
    paragraph_of = {}
    for number, paragraph in enumerate(clean["paragraphs"]):
        for i in range(paragraph["word_start"], paragraph["word_end"]):
            paragraph_of[i] = number
    i = 0
    while i < len(words):
        if not is_math[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(words) and is_math[j + 1] and paragraph_of[j + 1] == paragraph_of[i]:
            j += 1
        # keep sentence punctuation after the run ("𝑡𝑡𝑥𝑥2." keeps "."), and
        # a closing bracket the run didn't open ("(as represented by 𝛼𝛼),")
        run = text_of(i, j)
        end = len(run)
        while end and (run[end - 1] in ".,;:!?" or run[end - 1] in ")]" and run[:end].count(run[end - 1]) > run[:end].count("(" if run[end - 1] == ")" else "[")):
            end -= 1
        spans.append((words[i]["start"], words[i]["start"] + end))
        i = j + 1

    result, removed = _remove_spans(clean, spans)
    inline = [
        {"text": r["text"], "page": r["page"], "bbox": None, "after_word": r["after_word"], "inline": True}
        for r in removed
    ]
    display = [{**e, "inline": e.get("inline", False)} for e in result.get("equations", [])]
    equations = sorted(display + inline, key=lambda e: e["after_word"])
    return {**result, "equations": equations}


def _remove_spans(clean, spans):
    """Remove character spans of clean["text"] from a clean_extraction()
    result: words are re-indexed (pages unchanged), emptied paragraphs
    dropped, `equations` after_word indices remapped.

    Returns (new result, removed) where removed is [{start, end, text, page,
    after_word}] per merged span; after_word is the new index of the word
    the span followed (-1 if none). Punctuation left after a span attaches
    to the word before it.
    """
    words, text = clean["words"], clean["text"]
    spans = _merge_spans(spans)
    removed = [False] * len(text)
    for start, end in spans:
        removed[start:end] = [True] * (end - start)

    # Rebuild word by word. old_to_new[i] is the new index of the last kept
    # word at or before old word i (-1 if none).
    new_words, old_to_new, paragraphs = [], [], []
    for paragraph in clean["paragraphs"]:
        first = len(new_words)
        after_removal = False  # the previous word lost characters to a span
        for index in range(paragraph["word_start"], paragraph["word_end"]):
            word = words[index]
            word_removed = removed[word["start"]:word["end"]]
            kept = "".join(c for c, r in zip(word["word"], word_removed) if not r)
            if kept and word_removed[:1] == [True] and len(new_words) > first:
                # a span at the start of a word that runs on without a space
                # ("(24).The" in PDFs with missing spaces): its leading
                # punctuation goes to the word before, "subtitles. The..."
                rest = kept.lstrip("".join(_SPOKEN_PUNCTUATION))
                if rest and rest != kept:
                    new_words[-1]["word"] += kept[: len(kept) - len(rest)]
                    kept = rest
            punctuation_only = kept and all(c in _SPOKEN_PUNCTUATION for c in kept)
            if punctuation_only and len(new_words) > first and (kept != word["word"] or after_removal):
                # punctuation that followed a span joins the word before it:
                # "(Kim, 2017)." and "(Novak 2000) ." both leave "."
                new_words[-1]["word"] += kept
            elif kept:
                new_words.append({**word, "word": kept})
            after_removal = kept != word["word"]
            old_to_new.append(len(new_words) - 1)
        paragraphs.append({**paragraph, "word_start": first, "word_end": len(new_words)})
    paragraphs = [p for p in paragraphs if p["word_end"] > p["word_start"]]

    # offsets into the rebuilt text
    parts, length = [], 0
    for number, paragraph in enumerate(paragraphs):
        if number:
            parts.append("\n\n")
            length += 2
        for position in range(paragraph["word_start"], paragraph["word_end"]):
            word = new_words[position]
            if position > paragraph["word_start"]:
                parts.append(" ")
                length += 1
            word["start"], word["end"] = length, length + len(word["word"])
            parts.append(word["word"])
            length += len(word["word"])

    starts = [w["start"] for w in words]
    removed_spans = []
    for start, end in spans:
        # the old word the span starts in, and the last word before it
        first = bisect.bisect_right(starts, start) - 1
        before = bisect.bisect_left(starts, start) - 1
        removed_spans.append({
            "start": start, "end": end, "text": text[start:end],
            "page": words[max(first, 0)]["page"],
            "after_word": old_to_new[before] if before >= 0 else -1,
        })

    equations = [
        {**equation, "after_word": old_to_new[equation["after_word"]] if equation["after_word"] >= 0 else -1}
        for equation in clean.get("equations", [])
    ]
    result = {**clean, "text": "".join(parts), "words": new_words, "paragraphs": paragraphs, "equations": equations}
    return result, removed_spans


def _merge_spans(spans):
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [tuple(span) for span in merged]
