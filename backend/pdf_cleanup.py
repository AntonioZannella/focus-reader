"""Clean up extracted PDF text before it's sent to text-to-speech.

Runs on the result of pdf_extract.extract_pdf() and:
  1. strips running headers, footers and page numbers: lines in the top or
     bottom margin whose text repeats at the same height on several pages,
     and standalone page numbers that follow the document's numbering,
  2. rejoins words split by a line-wrap hyphen ("informa-" + "tion" ->
     "information"), across columns and pages too, and URLs broken across
     lines without one,
  3. rebuilds the text as normal paragraphs: words separated by single
     spaces, paragraphs by one blank line, and a paragraph that continues
     into the next column or page kept as one paragraph. Hanging-indent
     lists (reference lists) get one paragraph per entry.

Every word keeps the physical page it came from. A word rejoined across a
page break keeps the page it starts on.

Usage: python pdf_cleanup.py paper.pdf [--words 500] [--removed] [--joins]
       [--strip-citations] [--citations] [--equations] [--json out.json]
"""

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict

from pdf_academic import find_equations, remove_inline_math, strip_citations as remove_citations
from pdf_extract import extract_pdf

# Lines whose center is within this share of the page height from the top or
# bottom edge are candidates for headers, footers and page numbers.
MARGIN_ZONE = 0.10
# Two margin lines are "at the same position" if their distance from the page
# edge differs by less than this share of the page height (~8pt on Letter).
SAME_POSITION_TOLERANCE = 0.01
# A header/footer must repeat on at least this many pages.
MIN_REPEAT_PAGES = 2

# A standalone page number: "7", "- 7 -", "Page 7", "7 of 38", "Page 7 of 38".
PAGE_NUMBER_PATTERN = re.compile(r"^(?:page\s+)?[-–—]?\s*(\d+)\s*[-–—]?(?:\s*(?:of|/)\s*\d+)?$", re.IGNORECASE)
# A word ending in a line-wrap hyphen (hyphen, soft hyphen or Unicode hyphen
# after a letter).
WRAP_HYPHEN_PATTERN = re.compile("^(.*[^\\W\\d_])[-\u00ad\u2010]$")
# Ends a sentence or a heading-like clause, so a following line can start a
# new paragraph.
SENTENCE_END_PATTERN = re.compile(r"[.?!:][\"”’')\]]*$")
# Invisible characters that sometimes come out of PDFs inside words.
INVISIBLE_CHARACTERS = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"))
# Punctuation stripped when comparing word forms.
EDGE_PUNCTUATION = "\"'“”‘’()[]{}.,;:!?"
# Both halves of a line-end split must be known words at least this long to
# be read as a hyphenated compound ("content-based") when the document has
# no other evidence; shorter halves are usually a wrapped word ("in-formation").
COMPOUND_MIN_PART_LENGTH = 3
URL_PATTERN = re.compile(r"://|^\(?www\.", re.IGNORECASE)
# Spelled-out numbers 21-99 are always hyphenated ("eighty-nine").
TENS_WORDS = {"twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"}

# A line is a new paragraph inside a block when it's indented at least this
# many line heights, or when the line before it ends this share of the
# block's width short of the right edge (and ended a sentence).
PARAGRAPH_INDENT_LINES = 0.8
PARAGRAPH_SHORT_LINE = 0.15
# Lines of one column this close (in line heights) are one flow of text.
PARAGRAPH_MAX_LINE_GAP = 0.8

# Hanging-indent lists (reference lists, many numbered lists): each entry's
# first line sits at the column's left edge and its continuation lines are
# indented by this many points. A run of such lines needs at least this many
# indented lines and entry starts, and most entry starts must be full-width
# (so a series of indented two-line paragraphs, whose unindented line is
# their short last line, doesn't look like a list).
HANGING_INDENT_MIN = 2.0
HANGING_INDENT_MAX = 30.0
# Continuation lines of a real list share one indent: at least this share of
# them within HANGING_INDENT_SPREAD points of their median (glyphs such as
# quotes start a little left of letters). Right-aligned chart labels, with
# ragged left edges, don't.
HANGING_INDENT_SPREAD = 2.0
HANGING_SAME_INDENT_SHARE = 0.75
HANGING_MIN_INDENTED = 3
HANGING_MIN_ENTRIES = 2
HANGING_MIN_FULL_WIDTH_ENTRIES = 2 / 3
# A URL broken across lines without a hyphen: the next line's first word has
# only URL characters (no trailing comma) and either starts with one of these,
# or the URL line ends in "/" and the word looks like more URL (a digit, "/",
# "_", "=" or an inner "."), so "https://.../" + "Smith," isn't joined.
URL_CONTINUATION_START = "-._/"
URL_FRAGMENT_PATTERN = re.compile(r"^[\w\-.~%/?#=&:+@!$*'()]+$")
URL_FRAGMENT_HINT = re.compile(r"[\d/_=]|\.\w")


def _normalize(text):
    """Line text with digits masked, so '647' and '648' compare equal."""
    return re.sub(r"\d", "#", " ".join(text.lower().split()))


def _find_margin_lines(raw):
    """Indices into raw["lines"] of headers, footers and page numbers, with reasons."""
    pages = {p["page"]: p for p in raw["pages"]}
    margin = []  # (line index, zone, distance from that edge / page height, text)
    for index, line in enumerate(raw["lines"]):
        page = pages[line["page"]]
        center = (line["bbox"][1] + line["bbox"][3]) / 2 / page["height"]
        if center <= MARGIN_ZONE:
            zone, distance = "top", center
        elif center >= 1 - MARGIN_ZONE:
            zone, distance = "bottom", 1 - center
        else:
            continue
        text = " ".join(w["word"] for w in raw["words"][line["word_start"]:line["word_end"]])
        margin.append((index, zone, distance, text))

    removed = {}

    # Rule A: repeated header/footer text at the same position. Text with
    # letters matches with digits masked ("Page 3 of 38"); text without letters
    # must match exactly ("2025"), so different reference numbers ("7.",
    # "25.") at the top of two pages don't count as a repeat.
    groups = defaultdict(list)
    for entry in margin:
        _, zone, _, text = entry
        key = _normalize(text) if re.search(r"[^\W\d_]", text) else text
        groups[(zone, key)].append(entry)
    for entries in groups.values():
        entries.sort(key=lambda e: e[2])
        cluster = []
        for entry in entries + [None]:
            if entry is not None and (not cluster or entry[2] - cluster[-1][2] < SAME_POSITION_TOLERANCE):
                cluster.append(entry)
                continue
            if len({raw["lines"][e[0]]["page"] for e in cluster}) >= MIN_REPEAT_PAGES:
                for e in cluster:
                    removed[e[0]] = f"repeated {e[1]} line"
            cluster = [entry] if entry is not None else []

    # Rule B: standalone page numbers whose printed number keeps the same
    # offset from the physical page as most of the document's page numbers
    # (catches a first page with its number at the bottom when the others
    # have it at the top, but not a section number like "1").
    # Running headers removed by rule A often carry the page number too
    # ("JOURNAL SHORT TITLE 647"), which is evidence for the offset.
    numbered = []
    offsets = Counter()
    for index, zone, _, text in margin:
        match = PAGE_NUMBER_PATTERN.match(text)
        if match:
            offset = int(match.group(1)) - raw["lines"][index]["page"]
            numbered.append((index, offset))
            offsets[offset] += 1
        elif index in removed:
            edge_number = re.match(r"^(\d+)\s", text) or re.search(r"\s(\d+)$", text)
            if edge_number:
                offsets[int(edge_number.group(1)) - raw["lines"][index]["page"]] += 1
    if offsets:
        offset, count = offsets.most_common(1)[0]
        if count >= MIN_REPEAT_PAGES:
            for index, line_offset in numbered:
                if line_offset == offset:
                    removed.setdefault(index, "page number")

    # Headers and footers are blocks of their own. A matching line in a block
    # with other, kept lines is content: e.g. "Note." opening a table note
    # that sits at the same height on two pages.
    kept_blocks = {line["block"] for i, line in enumerate(raw["lines"]) if i not in removed}
    return {i: reason for i, reason in removed.items() if raw["lines"][i]["block"] not in kept_blocks}


def _core(word):
    return word.strip(EDGE_PUNCTUATION).lower()


def clean_extraction(raw, equations=()):
    """Clean an extract_pdf() result. Returns a dict:
      text       - cleaned text: single spaces between words, one blank line
                   ("\\n\\n") between paragraphs.
      words      - [{word, page, start, end, raw}] in reading order; page is
                   the 1-indexed physical page (the page a rejoined word
                   starts on), start/end are character offsets into `text`,
                   and raw lists the indices into raw["words"] the word came
                   from (two when rejoined; not necessarily adjacent, since a
                   removed header can sit between the halves).
      paragraphs - [{word_start, word_end, list}] ranges of `words`; list
                   is true for an entry of a hanging-indent list (references).
      equations  - [{text, page, bbox, after_word}] equations left out of
                   the text (only when `equations` from
                   pdf_academic.find_equations() is passed): the equation's
                   original text, its page and position on it, and the index
                   in `words` of the word it follows (-1 if none).
      removed    - [{page, text, reason}] header/footer/page-number lines taken out.
      joins      - [{page, kind, parts, word}] words rejoined across a line
                   break; kind is "hyphen" or "url" (a URL broken without one).
      pages      - raw["pages"] (page sizes and column counts).
      page_count - number of pages in the file.
    """
    raw_words = raw["words"]
    removed_lines = _find_margin_lines(raw)
    # equation blocks (pdf_academic.find_equations) are left out of the text;
    # each leaves a marker recording the word it follows
    equation_of_block = {b: n for n, equation in enumerate(equations) for b in equation["blocks"]}
    equation_lines = {i for i, line in enumerate(raw["lines"]) if line["block"] in equation_of_block}

    # Word forms used in the running text, to decide whether a hyphen at a
    # line end is part of the word ("self-report") or just a wrap. Words at
    # either end of a line are left out: they may be half-words ("informa-",
    # "tion").
    line_ends = {
        i
        for line in raw["lines"]
        if line["word_end"] > line["word_start"]
        for i in (line["word_start"], line["word_end"] - 1)
    }
    vocabulary = {_core(w["word"]) for i, w in enumerate(raw_words) if i not in line_ends}
    known_words = vocabulary | {part for form in vocabulary for part in form.split("-")}

    def is_known_word(part):
        return len(part) >= COMPOUND_MIN_PART_LENGTH and part in known_words

    def join_split_word(first, second):
        """(kind, rejoined word) if `first` + `second` is one word split at a
        line break, else None."""
        if (
            URL_PATTERN.search(first)
            and URL_FRAGMENT_PATTERN.match(second)
            and (
                second[:1] in URL_CONTINUATION_START
                or (first.endswith("/") and URL_FRAGMENT_HINT.search(second))
            )
        ):
            # "https://doi.org/" + "10.2307/1128905"; "https://www.latimes" + ".com/..."
            return "url", first + second
        hyphenated = join_hyphenated(first, second)
        return ("hyphen", hyphenated) if hyphenated is not None else None

    def join_hyphenated(first, second):
        """The rejoined word, or None if `first` doesn't end in a wrap hyphen."""
        match = WRAP_HYPHEN_PATTERN.match(first)
        if not match or not second[:1].isalnum():
            return None
        stem = match.group(1)
        if first.endswith("\u00ad"):
            return stem + second
        closed, hyphenated = stem + second, stem + "-" + second
        if URL_PATTERN.search(stem) or not second[0].islower():
            # URLs break after their own hyphens; "Smith-" + "Jones" and
            # "COVID-" + "19" are real hyphens
            return hyphenated
        if _core(hyphenated) in vocabulary and _core(closed) not in vocabulary:
            return hyphenated
        if _core(closed) in vocabulary:
            return closed
        # no evidence either way: two real words ("content" + "based",
        # "statistics" + "based") make a compound, anything else was one
        # word wrapped ("develop" + "ment", "accu" + "rate")
        # A stem that's already a compound ("summary-statistics-") only needs
        # a known word after it, as long as its last part is a whole word
        # rather than a syllable ("low-in-" + "come" is "low-income").
        left = _core(stem).rsplit("-", 1)[-1]
        right = _core(second).split("-", 1)[0]
        if left in TENS_WORDS:
            return hyphenated
        if "-" in stem and len(left) > COMPOUND_MIN_PART_LENGTH:
            left_ok = True
        else:
            left_ok = is_known_word(left)
        return hyphenated if left_ok and is_known_word(right) else closed

    hanging = _hanging_indent_lines(raw, set(removed_lines) | equation_lines)
    column_edges = _column_edges(raw, set(removed_lines) | equation_lines)

    paragraphs = []  # each a list of [word, page, raw indices]
    paragraph_is_list = []  # whether each paragraph is a hanging-indent list entry
    joins = []
    markers = {}  # equation number -> marker
    emitted = 0  # words in `paragraphs` so far
    previous = None  # the last kept line
    for index, line in enumerate(raw["lines"]):
        if index in removed_lines:
            continue
        if index in equation_lines:
            number = equation_of_block[line["block"]]
            if number not in markers:
                equation = equations[number]
                markers[number] = {
                    "text": equation["text"], "page": equation["page"], "bbox": equation["bbox"],
                    "after_word": emitted - 1,
                }
            continue
        line_words = [
            [w["word"].translate(INVISIBLE_CHARACTERS), w["page"], [i]]
            for i, w in enumerate(raw_words[line["word_start"]:line["word_end"]], line["word_start"])
        ]
        line_words = [w for w in line_words if w[0]]
        if not line_words:
            continue

        if previous is not None:
            last = paragraphs[-1][-1]
            # nothing is joined into the first line of a list entry
            entry_start = index in hanging and not hanging[index]
            joined = None if entry_start else join_split_word(last[0], line_words[0][0])
            if joined is not None:
                kind, word = joined
                joins.append({"page": last[1], "kind": kind, "parts": [last[0], line_words[0][0]], "word": word})
                last[0] = word
                last[2] = last[2] + line_words[0][2]
                line_words = line_words[1:]
            elif index in hanging:
                # inside a hanging-indent list: an entry starts at the left edge
                if not hanging[index]:
                    paragraphs.append([])
                    paragraph_is_list.append(True)
            elif _starts_paragraph(raw, previous, line, line_words[0][0], column_edges):
                paragraphs.append([])
                paragraph_is_list.append(False)
        else:
            paragraphs.append([])
            paragraph_is_list.append(index in hanging)
        paragraphs[-1].extend(line_words)
        emitted += len(line_words)
        previous = line

    words, paragraphs_out, parts, length = [], [], [], 0
    for paragraph, is_list in zip(paragraphs, paragraph_is_list):
        if not paragraph:
            continue
        if parts:
            parts.append("\n\n")
            length += 2
        start_index = len(words)
        for position, (word, page, raw_indices) in enumerate(paragraph):
            if position > 0:
                parts.append(" ")
                length += 1
            words.append(
                {"word": word, "page": page, "start": length, "end": length + len(word),
                 "raw": raw_indices}
            )
            parts.append(word)
            length += len(word)
        paragraphs_out.append({"word_start": start_index, "word_end": len(words), "list": is_list})

    removed = [
        {
            "page": raw["lines"][i]["page"],
            "text": " ".join(w["word"] for w in raw_words[raw["lines"][i]["word_start"]:raw["lines"][i]["word_end"]]),
            "reason": reason,
        }
        for i, reason in sorted(removed_lines.items())
    ]
    return {
        "text": "".join(parts),
        "words": words,
        "paragraphs": paragraphs_out,
        "equations": [markers[n] for n in sorted(markers)],
        "removed": removed,
        "joins": joins,
        "pages": raw["pages"],
        "page_count": raw["page_count"],
    }


def _share_near_median(values, spread):
    median = statistics.median(values)
    return sum(abs(v - median) <= spread for v in values) / len(values)


def _hanging_indent_lines(raw, removed_lines):
    """Kept lines that belong to a hanging-indent list, as {line index: indented}.

    Lines are measured against their column's left edge (not their block's:
    PyMuPDF often splits a reference list mid-entry, putting one entry's
    continuation lines and the next entry's first line in one block). A run
    is a stretch of a column with no two unindented lines in a row; a line
    indented further than a list would indent (a heading, a centered link)
    ends a run too.
    """
    columns = defaultdict(list)  # (page, column) -> line indices in reading order
    for index, line in enumerate(raw["lines"]):
        if index in removed_lines or line["word_end"] == line["word_start"]:
            continue
        columns[_column_key(raw, line)].append(index)

    result = {}
    # A list that runs on from the previous column or page may show only a
    # line or two of it here, too few to recognise on their own; it keeps its
    # continuation indent. This is that indent while the last column ended
    # inside a list, else None.
    list_indent = None
    for indices in columns.values():  # in reading order
        lines = [raw["lines"][i] for i in indices]
        left = min(line["bbox"][0] for line in lines)
        right = max(line["bbox"][2] for line in lines)
        indents = [line["bbox"][0] - left for line in lines]
        at_left = [indent < HANGING_INDENT_MIN for indent in indents]
        indented = [HANGING_INDENT_MIN <= indent <= HANGING_INDENT_MAX for indent in indents]
        full_width = [right - line["bbox"][2] < PARAGRAPH_SHORT_LINE * (right - left) for line in lines]

        runs, run = [], []
        for position in range(len(lines)):
            if not (at_left[position] or indented[position]):
                runs.append(run)
                run = []
                continue
            if run and at_left[position] and at_left[run[-1]]:
                runs.append(run)
                run = []
            run.append(position)
        runs.append(run)

        next_list_indent = None
        for run in runs:
            if not run:
                continue
            continuation = [indents[p] for p in run if indented[p]]
            if not continuation:
                continue
            same_indent = _share_near_median(continuation, HANGING_INDENT_SPREAD) >= HANGING_SAME_INDENT_SHARE
            # an entry start followed by its own continuation lines
            starts = [p for p in run if at_left[p] and p + 1 in run and indented[p + 1]]
            # Where the short lines fall: in a list an entry's short last line
            # comes before the next entry (a line at the left edge); in
            # paragraphs with an indented first line (footnotes, body text)
            # it comes before the next indented line.
            short_before_entry = sum(not full_width[p - 1] for p in run[1:] if at_left[p])
            short_before_indent = sum(not full_width[p - 1] for p in run[1:] if indented[p])
            is_list = (
                len(continuation) >= HANGING_MIN_INDENTED
                and same_indent
                and len(starts) >= HANGING_MIN_ENTRIES
                and sum(full_width[p] for p in starts) >= HANGING_MIN_FULL_WIDTH_ENTRIES * len(starts)
                and short_before_entry >= short_before_indent
            ) or (
                # the column's first run, continuing the last column's list
                list_indent is not None
                and run[0] == 0
                and same_indent
                and abs(statistics.median(continuation) - list_indent) <= HANGING_INDENT_SPREAD
            )
            if is_list:
                for p in run:
                    result[indices[p]] = indented[p]
            ends_column = run[-1] == len(lines) - 1
            next_list_indent = statistics.median(continuation) if is_list and ends_column else None
        list_indent = next_list_indent
    return result


def _column_key(raw, line):
    """The column a line is in: (page, column), or its own key for a block
    running across columns."""
    column = raw["blocks"][line["block"]]["column"]
    return (line["page"], column) if column is not None else (line["page"], "span", line["block"])


def _column_edges(raw, skipped_lines):
    """{column key: (left, right)} from the lines that are kept."""
    edges = {}
    for index, line in enumerate(raw["lines"]):
        if index in skipped_lines or line["word_end"] == line["word_start"]:
            continue
        key = _column_key(raw, line)
        left, right = edges.get(key, (line["bbox"][0], line["bbox"][2]))
        edges[key] = (min(left, line["bbox"][0]), max(right, line["bbox"][2]))
    return edges


def _starts_paragraph(raw, previous, line, first_word, column_edges):
    """Whether `line` starts a new paragraph after the kept line `previous`.

    Consecutive lines of one column with ordinary line spacing are one flow
    of text however PyMuPDF grouped them into blocks (in some PDFs, e.g.
    double-spaced Word documents, every line is a block of its own): a new
    paragraph there needs a sentence end and an indent or a short previous
    line, or a short previous line without a sentence end (a heading) before
    a capital or a number. Across a column or page break, a sentence that
    didn't end, or a line ending in a comma or semicolon, runs on.
    """
    last_word = raw["words"][previous["word_end"] - 1]["word"]
    ends_sentence = bool(SENTENCE_END_PATTERN.search(last_word))
    line_height = line["bbox"][3] - line["bbox"][1]
    gap = line["bbox"][1] - previous["bbox"][3]
    same_flow = previous["block"] == line["block"] or (
        _column_key(raw, previous) == _column_key(raw, line)
        and -PARAGRAPH_SHORT_LINE * line_height <= gap <= PARAGRAPH_MAX_LINE_GAP * line_height
    )
    if not same_flow:
        if ends_sentence:
            return True
        return first_word[:1].isupper() and not last_word.endswith((",", ";"))
    left, right = column_edges[_column_key(raw, line)]
    indented = line["bbox"][0] - left >= PARAGRAPH_INDENT_LINES * line_height
    ended_short = right - previous["bbox"][2] >= PARAGRAPH_SHORT_LINE * (right - left)
    if not ends_sentence:
        # a short line that doesn't end a sentence is a heading ("1 Introduction")
        return ended_short and not last_word.endswith((",", ";")) and first_word[:1].isalnum() and not first_word[:1].islower()
    return indented or ended_short


def process_pdf(source, strip_citations=False):
    """Extract and clean a PDF (a file path or bytes): what goes to TTS.

    Equations, display and inline, are always left out of the text (see
    `equations` in the result, each with an `inline` flag); in-text
    citations are removed only with strip_citations (see `citations`, empty
    otherwise).
    """
    raw = extract_pdf(source)
    clean = remove_inline_math(clean_extraction(raw, find_equations(raw)))
    if strip_citations:
        return remove_citations(clean)
    return {**clean, "citations": []}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("pdf", help="path to the PDF file")
    parser.add_argument("--words", type=int, default=500, help="how many cleaned words to print (default 500)")
    parser.add_argument("--removed", action="store_true", help="list every removed header/footer/page-number line")
    parser.add_argument("--joins", action="store_true", help="list every rejoined hyphenated word")
    parser.add_argument("--strip-citations", action="store_true", help="remove in-text citations")
    parser.add_argument("--citations", action="store_true", help="list every removed citation")
    parser.add_argument("--equations", action="store_true", help="list every equation left out of the text")
    parser.add_argument("--json", metavar="PATH", help="also write the full result to this JSON file")
    args = parser.parse_args()

    try:
        result = process_pdf(args.pdf, strip_citations=args.strip_citations)
    except ValueError as error:
        sys.exit(f"Error: {error}")

    print(
        f"{result['page_count']} page(s), {len(result['words'])} words, "
        f"{len(result['paragraphs'])} paragraphs; removed {len(result['removed'])} "
        f"header/footer/page-number lines; rejoined {len(result['joins'])} split words; "
        f"{len(result['equations'])} equations left out; {len(result['citations'])} citations removed"
    )
    if args.removed:
        print("\nRemoved:")
        for line in result["removed"]:
            print(f"  [p{line['page']}] {line['reason']}: {line['text']}")
    if args.joins:
        print("\nRejoined:")
        for join in result["joins"]:
            print(f"  [p{join['page']}] {' + '.join(join['parts'])} -> {join['word']}")
    if args.citations:
        print("\nCitations removed:")
        for citation in result["citations"]:
            print(f"  [p{citation['page']}] after word {citation['after_word']}: {citation['text']}")
    if args.equations:
        print("\nEquations left out:")
        for equation in result["equations"]:
            text = equation["text"].replace("\n", " / ")
            print(f"  [p{equation['page']}] after word {equation['after_word']}: {text}")

    print()
    shown = 0
    for paragraph in result["paragraphs"]:
        if shown >= args.words:
            break
        words = result["words"][paragraph["word_start"]:paragraph["word_end"]][: args.words - shown]
        shown += len(words)
        runs, current = [], None
        for word in words:
            if word["page"] != current:
                current = word["page"]
                runs.append(f"[p{current}]")
            runs.append(word["word"])
        print(" ".join(runs) + "\n")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as json_file:
            json.dump(result, json_file, indent=2, ensure_ascii=False)
        print(f"Full result written to {args.json}")


if __name__ == "__main__":
    main()
