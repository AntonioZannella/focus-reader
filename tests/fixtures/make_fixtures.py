"""Builds the small sample PDFs in this folder (run once; the PDFs are
committed). Every line is placed by hand so the tests know exactly what the
extraction should return: which words are on which page, the reading order
across columns, the running header and page numbers to strip, a word
hyphenated across a line break, and one display equation.

    backend/venv/bin/python tests/fixtures/make_fixtures.py
"""

import os

import pymupdf

HERE = os.path.dirname(os.path.abspath(__file__))
WIDTH, HEIGHT = 612, 792  # US Letter, points
SIZE = 10
LEADING = 14


def header_and_page_number(page, number):
    # running header (repeats on every page) and a bare page number, both in
    # the top/bottom 10% where cleanup looks for them
    page.insert_text((72, 40), "Journal of Sample Studies, Volume 7", fontsize=8)
    page.insert_text((WIDTH / 2 - 3, 765), str(number), fontsize=8)


def column(page, x, y, lines):
    for i, line in enumerate(lines):
        page.insert_text((x, y + i * LEADING), line, fontsize=SIZE)


# Each column is a paragraph of plain sentences, wrapped by hand, ending with
# a sentence end so columns and pages don't run together.
TWO_COLUMN = [
    (   # page 1, left column (holds the hyphenated word "experiments")
        ["Alpha opens the first column of this page.",
         "Readers follow the text from top to bottom",
         "before moving across to the next column. The",
         "authors ran several careful experi-",
         "ments to compare reading order in print.",
         "Each sentence here is short and plain so the",
         "extracted words are easy to check by hand.",
         "This column ends with a full stop."],
        # page 1, right column
        ["Bravo starts the second column on page one.",
         "It must be read only after the whole left",
         "column, never interleaved with it line by line.",
         "Two columns side by side are common in",
         "journal articles and conference papers.",
         "The extraction keeps this column together and",
         "places it after the first one in the output.",
         "This column also ends with a full stop."],
    ),
    (   # page 2, left column
        ["Charlie begins the left column of page two.",
         "Words on this page must be tagged with page",
         "two, whatever number is printed at the bottom.",
         "The running header above should not appear in",
         "the spoken text, and neither should the page",
         "numbers printed in the footer of each page.",
         "Only the body of the article is read aloud.",
         "This column ends the left side of page two."],
        # page 2, right column
        ["Delta closes the article in the final column.",
         "Nothing after this column should be missing,",
         "and nothing from the header or footer should",
         "slip in between the columns or the pages.",
         "A reader hearing this text would never know",
         "that it was laid out in two narrow columns.",
         "That is the whole point of the reading order.",
         "This is the last sentence of the article."],
    ),
]

SINGLE_COLUMN = [
    ["Echo is the first word of a single column document. It runs across the",
     "full width of the page, the way a working paper or a thesis is usually",
     "set. Long lines like these are read in order from top to bottom, and the",
     "hyphenated word at the end of this line is rejoined: imple-",
     "mentation should come out as one word. The paragraph ends here."],
    ["Foxtrot opens the second page of the single column document. Its words",
     "must carry page two, and the running header and the page number printed",
     "on this page must not be read aloud as part of the text. The document",
     "ends with this sentence."],
]


def build_two_column(path):
    doc = pymupdf.open()
    for number, (left, right) in enumerate(TWO_COLUMN, start=1):
        page = doc.new_page(width=WIDTH, height=HEIGHT)
        header_and_page_number(page, number)
        column(page, 72, 100, left)
        column(page, 324, 100, right)
    doc.save(path)


def build_single_column(path):
    doc = pymupdf.open()
    for number, lines in enumerate(SINGLE_COLUMN, start=1):
        page = doc.new_page(width=WIDTH, height=HEIGHT)
        header_and_page_number(page, number)
        column(page, 72, 100, lines)
    doc.save(path)


def build_equation(path):
    doc = pymupdf.open()
    page = doc.new_page(width=WIDTH, height=HEIGHT)
    column(page, 72, 100, [
        "Golf introduces a model in which the total cost depends on two parts.",
        "The relation between them is the following:"])
    # a display equation: its own block, mostly symbols, with a real operator
    page.insert_text((250, 160), "c = a + b x", fontsize=12)
    column(page, 72, 200, [
        "Hotel continues after the equation, which is shown but not read aloud.",
        "The paragraph ends here."])
    doc.save(path)


if __name__ == "__main__":
    build_two_column(os.path.join(HERE, "two_column.pdf"))
    build_single_column(os.path.join(HERE, "single_column.pdf"))
    build_equation(os.path.join(HERE, "equation.pdf"))
    print("Wrote two_column.pdf, single_column.pdf, equation.pdf")
