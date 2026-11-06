"""Extract text from a PDF in reading order, tracking each word's page.

Each page's text is read as positioned blocks. The page is checked for
columns by clustering the blocks' left edges, and the blocks are then ordered
column by column (top to bottom within each column). Blocks that run across a
column gutter (titles, full-width abstracts, figure captions) split the page
into horizontal bands, and each band is read on its own, so a full-width title
is read before the columns under it and a full-width caption in the middle of
the page is read between the column text above and below it.

Every word keeps the physical page it came from: 1-indexed, in the file's own
page order, never a number printed on the page.

Usage: python pdf_extract.py paper.pdf [--words 500] [--json out.json]
"""

import argparse
import itertools
import json
import re
import statistics
import sys
from dataclasses import dataclass, field

import pymupdf

# No TEXT_PRESERVE_LIGATURES, so ligature glyphs come out as plain letters
# ("ﬁ" -> "fi"); no TEXT_PRESERVE_IMAGES, so only text blocks are returned;
# text outside the page's visible area is ignored.
TEXT_FLAGS = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP
# Fonts used for typeset math (TeX's Computer Modern math fonts, Latin Modern
# / Cambria / STIX math, AMS symbols, Symbol). Text in them is the strongest
# sign of an equation.
# A line's vertical extent, in font sizes above and below its baseline.
LINE_ASCENT = 0.8
LINE_DESCENT = 0.25
MATH_FONT_PATTERN = re.compile(r"math|^cm(mi|sy|ex)|^ms(am|bm)|stix|symbol|euclid|mt ?extra|rsfs|eufm", re.IGNORECASE)

# Left edges further apart than this share of the text area start different
# columns; smaller differences are indents, list items, centered lines.
COLUMN_MIN_GAP = 0.15
# A left-edge cluster is only a column if it holds running text: at least one
# block with this many lines and at least this share of the text area's width.
# Chart labels, headings, page numbers and centered author lines are shorter;
# a table's label column is narrower (a PNAS sidebar is ~0.29, a column of a
# three-column page ~0.31).
COLUMN_MIN_BLOCK_LINES = 3
COLUMN_MIN_BLOCK_WIDTH = 0.2
# Two clusters only count as side-by-side columns when the page height they
# both cover overlaps by at least this many (median) line heights.
COLUMN_MIN_SIDE_BY_SIDE_LINES = 3
# How far (in points) a block must reach into the columns on both sides of a
# gutter to count as running across it.
GUTTER_TOLERANCE = 2.0


@dataclass
class Block:
    x0: float
    y0: float
    x1: float
    y1: float
    lines: list = field(default_factory=list)  # list of lists of words
    line_bboxes: list = field(default_factory=list)  # (x0, y0, x1, y1) per line
    column: int | None = None  # None for a block running across columns
    chars: int = 0  # non-space characters
    math_chars: int = 0  # of which set in a math font

    @property
    def line_heights(self):
        return [y1 - y0 for _, y0, _, y1 in self.line_bboxes]

    @property
    def width(self):
        return self.x1 - self.x0

    @property
    def center_y(self):
        return (self.y0 + self.y1) / 2


def _page_blocks(page):
    """Text blocks of a page, keeping only horizontal left-to-right lines.

    Rotated or vertical text (e.g. the arXiv identifier stamped up the left
    margin) is skipped: it isn't part of the reading text and would confuse
    column detection.
    """
    blocks = []
    for raw in page.get_text("dict", flags=TEXT_FLAGS)["blocks"]:
        if raw.get("type") != 0:
            continue
        block = None
        for line in raw["lines"]:
            dir_x, dir_y = line["dir"]
            if dir_x <= 0 or abs(dir_y) > 0.01:
                continue
            words = "".join(span["text"] for span in line["spans"]).split()
            if not words:
                continue
            x0, y0, x1, y1 = line["bbox"]
            # Vertical extent from each span's baseline and font size, not its
            # glyph boxes: math fonts (e.g. Cambria Math) report glyph boxes
            # several lines tall, which would sort a line above the previous one.
            text_spans = [span for span in line["spans"] if span["text"].strip()]
            y0 = min(span["origin"][1] - LINE_ASCENT * span["size"] for span in text_spans)
            y1 = max(span["origin"][1] + LINE_DESCENT * span["size"] for span in text_spans)
            if block is None:
                block = Block(x0, y0, x1, y1)
            else:
                block.x0, block.y0 = min(block.x0, x0), min(block.y0, y0)
                block.x1, block.y1 = max(block.x1, x1), max(block.y1, y1)
            for span in line["spans"]:
                span_chars = sum(1 for c in span["text"] if not c.isspace())
                block.chars += span_chars
                if MATH_FONT_PATTERN.search(span["font"]):
                    block.math_chars += span_chars
            if block.lines and _same_printed_line(block.line_bboxes[-1], (x0, y0, x1, y1)):
                # PyMuPDF can split one printed line at wide justified gaps
                # ("voice" | "assistants." | "https://..."); keep it one line
                px0, py0, px1, py1 = block.line_bboxes[-1]
                block.lines[-1].extend(words)
                block.line_bboxes[-1] = (min(px0, x0), min(py0, y0), max(px1, x1), max(py1, y1))
                continue
            block.lines.append(words)
            block.line_bboxes.append((x0, y0, x1, y1))
        if block is not None:
            blocks.append(block)
    return blocks


def _same_printed_line(previous, current):
    """Whether line bbox `current` continues `previous` on the same printed
    line: it starts to the right of it and they share most of their height."""
    _, py0, px1, py1 = previous
    x0, y0, _, y1 = current
    shared = min(py1, y1) - max(py0, y0)
    return x0 >= px1 - 1 and shared >= 0.7 * min(py1 - py0, y1 - y0)


def _covered_spans(blocks):
    """The page's vertical extent covered by `blocks`, as merged (y0, y1) spans."""
    spans = []
    for y0, y1 in sorted((b.y0, b.y1) for b in blocks):
        if spans and y0 <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], y1)
        else:
            spans.append([y0, y1])
    return spans


def _shared_height(spans_a, spans_b):
    return sum(
        max(0.0, min(a1, b1) - max(a0, b0)) for a0, a1 in spans_a for b0, b1 in spans_b
    )


def _detect_columns(blocks):
    """The page's columns as (left, right) x-positions, left to right; one
    entry means a single column.

    Clusters the blocks' left edges. Columns needn't be equal: a wide main
    column beside a narrow sidebar (e.g. PNAS's "Significance" box) is two
    columns. Full-width blocks share the left column's edge and simply join
    its cluster. A cluster is a column if it holds running text and sits side
    by side with another such cluster (any other cluster, not just its
    neighbour, since a centered heading or author line can cluster between
    the two columns) for a few lines' worth of height. A column's edges come
    from its running-text blocks, so a page number just left of a column
    can't move them.
    """
    if not blocks:
        return []
    area_x0 = min(b.x0 for b in blocks)
    area_x1 = max(b.x1 for b in blocks)
    area_width = area_x1 - area_x0
    single = [(area_x0, area_x1)]
    if area_width <= 0:
        return single

    clusters = []
    for block in sorted(blocks, key=lambda b: b.x0):
        if clusters and block.x0 - clusters[-1][-1].x0 <= COLUMN_MIN_GAP * area_width:
            clusters[-1].append(block)
        else:
            clusters.append([block])
    def is_running_text(block):
        return (
            len(block.lines) >= COLUMN_MIN_BLOCK_LINES
            and block.width >= COLUMN_MIN_BLOCK_WIDTH * area_width
        )

    text_clusters = [
        prose for prose in ([b for b in c if is_running_text(b)] for c in clusters) if prose
    ]
    if len(text_clusters) < 2:
        return single

    line_height = statistics.median(h for b in blocks for h in b.line_heights)
    min_shared = COLUMN_MIN_SIDE_BY_SIDE_LINES * line_height
    spans = [_covered_spans(c) for c in text_clusters]
    is_column = [False] * len(text_clusters)
    for i in range(len(text_clusters)):
        for j in range(i + 1, len(text_clusters)):
            if _shared_height(spans[i], spans[j]) >= min_shared:
                is_column[i] = is_column[j] = True
    # Typical edges (line-weighted medians), not extremes: a wide table or a
    # table cell that clustered with a column mustn't move its edges.
    columns = [
        (_weighted_median(c, lambda b: b.x0), _weighted_median(c, lambda b: b.x1))
        for c, keep in zip(text_clusters, is_column)
        if keep
    ]
    return columns if len(columns) >= 2 else single


def _weighted_median(blocks, value):
    """Median of value(block) over the blocks' lines."""
    return statistics.median(value(b) for b in blocks for _ in b.lines)


def _order_blocks(blocks, columns):
    """Reading order: band by band, and column by column within a band."""
    if len(columns) < 2:
        for block in blocks:
            block.column = 0
        return sorted(blocks, key=lambda b: (round(b.y0), b.x0))

    # A block runs across a gutter only if it covers the gap: it starts inside
    # the column on the left and ends inside the column on the right. (Testing
    # against the right column's left edge alone would catch a hanging-indent
    # entry's first line, which starts a few points left of its indented
    # continuation lines.)
    gutters = [
        (left_end, right_start) if left_end < right_start else (right_start, right_start)
        for (_, left_end), (right_start, _) in itertools.pairwise(columns)
    ]
    spanning, in_columns = [], []
    for block in blocks:
        crosses = any(
            block.x0 < gap_left - GUTTER_TOLERANCE and block.x1 > gap_right + GUTTER_TOLERANCE
            for gap_left, gap_right in gutters
        )
        if crosses:
            block.column = None
            spanning.append(block)
        else:
            # columns whose gutter the block starts past (column 0 for anything
            # left of the first column, e.g. a margin note)
            block.column = sum(
                1 for gap_left, gap_right in gutters if block.x0 >= (gap_left + gap_right) / 2
            )
            in_columns.append(block)

    spanning.sort(key=lambda b: (round(b.y0), b.x0))
    bands = [[] for _ in range(len(spanning) + 1)]
    for block in in_columns:
        band = sum(1 for s in spanning if s.center_y <= block.center_y)
        bands[band].append(block)

    ordered = []
    for i, band in enumerate(bands):
        ordered.extend(sorted(band, key=lambda b: (b.column, round(b.y0), b.x0)))
        if i < len(spanning):
            ordered.append(spanning[i])
    return ordered


def extract_pdf(source):
    """Extract a PDF's text in reading order.

    `source` is a file path or the PDF's bytes. Returns a dict:
      text       - all words in reading order. Words on a line are joined by
                   a space, lines by "\\n", blocks and pages by "\\n\\n".
      words      - [{word, page, start, end}] in reading order; page is the
                   1-indexed physical page, start/end are character offsets
                   into `text` (text[start:end] == word).
      blocks     - [{page, bbox, column, math_font_share, word_start,
                   word_end}] in reading order; column is None for a block
                   running across columns, math_font_share is the share of
                   its characters set in a math font, and the block's words
                   are words[word_start:word_end].
      lines      - [{page, bbox, block, word_start, word_end}] in reading
                   order; block is the line's index into `blocks`.
      pages      - [{page, width, height, columns, word_count}] one entry per
                   page; width/height are the page size in points.
      page_count - number of pages in the file.

    Raises ValueError for a file that isn't a readable PDF, has no pages or
    needs a password. A scanned PDF (images, no text layer) is not an error:
    it comes back with pages but no words.
    """
    try:
        if isinstance(source, (bytes, bytearray)):
            doc = pymupdf.open(stream=bytes(source), filetype="pdf")
        else:
            doc = pymupdf.open(source, filetype="pdf")
    except Exception as error:  # PyMuPDF raises several types for bad files
        raise ValueError(f"Not a readable PDF: {error}") from error

    with doc:
        if doc.needs_pass:
            raise ValueError("This PDF is password-protected")
        # a damaged file can open "repaired" with nothing left in it
        if doc.page_count == 0:
            raise ValueError("This PDF has no readable pages (the file may be damaged)")

        parts = []  # pieces of `text`, joined at the end
        length = 0
        words, blocks_out, lines_out, pages_out = [], [], [], []

        def append(piece):
            nonlocal length
            parts.append(piece)
            length += len(piece)

        for page in doc:
            page_number = page.number + 1
            blocks = _page_blocks(page)
            columns = _detect_columns(blocks)
            page_word_count = 0
            for block in _order_blocks(blocks, columns):
                word_start = len(words)
                for line_index, (line, line_bbox) in enumerate(zip(block.lines, block.line_bboxes)):
                    if words:
                        append("\n" if line_index > 0 else "\n\n")
                    line_start = len(words)
                    for word_index, word in enumerate(line):
                        if word_index > 0:
                            append(" ")
                        words.append(
                            {"word": word, "page": page_number, "start": length, "end": length + len(word)}
                        )
                        append(word)
                    lines_out.append(
                        {
                            "page": page_number,
                            "bbox": [round(v, 1) for v in line_bbox],
                            "block": len(blocks_out),
                            "word_start": line_start,
                            "word_end": len(words),
                        }
                    )
                page_word_count += len(words) - word_start
                blocks_out.append(
                    {
                        "page": page_number,
                        "bbox": [round(v, 1) for v in (block.x0, block.y0, block.x1, block.y1)],
                        "column": block.column,
                        "math_font_share": round(block.math_chars / block.chars, 2) if block.chars else 0.0,
                        "word_start": word_start,
                        "word_end": len(words),
                    }
                )
            pages_out.append(
                {
                    "page": page_number,
                    "width": round(page.rect.width, 1),
                    "height": round(page.rect.height, 1),
                    "columns": max(1, len(columns)),
                    "word_count": page_word_count,
                }
            )

        return {
            "text": "".join(parts),
            "words": words,
            "blocks": blocks_out,
            "lines": lines_out,
            "pages": pages_out,
            "page_count": doc.page_count,
        }


def _print_words(result, limit):
    """Print the first `limit` words, one run of lines per page, tagged [p N]."""
    shown = result["words"][:limit]
    current_page, line = None, []
    for word in shown:
        if word["page"] != current_page or len(line) == 14:
            if line:
                print(f"[p{current_page}] " + " ".join(line))
            current_page, line = word["page"], []
        line.append(word["word"])
    if line:
        print(f"[p{current_page}] " + " ".join(line))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("pdf", help="path to the PDF file")
    parser.add_argument("--words", type=int, default=500, help="how many words to print (default 500)")
    parser.add_argument("--json", metavar="PATH", help="also write the full result to this JSON file")
    args = parser.parse_args()

    try:
        result = extract_pdf(args.pdf)
    except ValueError as error:
        sys.exit(f"Error: {error}")

    print(f"{result['page_count']} page(s), {len(result['words'])} words")
    for page in result["pages"]:
        layout = "single-column" if page["columns"] == 1 else f"{page['columns']} columns"
        print(f"  page {page['page']}: {layout}, {page['word_count']} words")
    print()
    _print_words(result, args.words)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as json_file:
            json.dump(result, json_file, indent=2, ensure_ascii=False)
        print(f"\nFull result written to {args.json}")


if __name__ == "__main__":
    main()
