"""PDF extraction and cleanup on the hand-built fixtures in tests/fixtures
(see make_fixtures.py for exactly what each page contains)."""

import os

import pytest
from conftest import FIXTURES
from pdf_cleanup import process_pdf


@pytest.fixture(scope="module")
def two_column():
    return process_pdf(os.path.join(FIXTURES, "two_column.pdf"))


@pytest.fixture(scope="module")
def single_column():
    return process_pdf(os.path.join(FIXTURES, "single_column.pdf"))


@pytest.fixture(scope="module")
def equation():
    return process_pdf(os.path.join(FIXTURES, "equation.pdf"))


def index_of(result, word):
    return next(i for i, w in enumerate(result["words"]) if w["word"] == word)


def test_two_columns_are_read_column_by_column(two_column):
    # Each column opens with a unique word; reading order must be left column,
    # right column, then the next page, never line by line across columns.
    order = [index_of(two_column, w) for w in ("Alpha", "Bravo", "Charlie", "Delta")]
    assert order == sorted(order)
    # every line of the left column comes before the right column starts
    assert two_column["text"].index("This column ends with a full stop.") < two_column["text"].index("Bravo")


@pytest.mark.parametrize("name", ["two_column", "single_column"])
def test_every_word_has_its_physical_page(name, request):
    # Words carry the page they're printed on (page 1 = first page of the
    # file); page tags never go backwards and offsets point at the word.
    result = request.getfixturevalue(name)
    pages = [w["page"] for w in result["words"]]
    assert pages == sorted(pages) and set(pages) == {1, 2}
    first_on_page_two = {"two_column": "Charlie", "single_column": "Foxtrot"}[name]
    assert pages[index_of(result, first_on_page_two)] == 2
    assert pages[index_of(result, first_on_page_two) - 1] == 1
    for w in result["words"]:
        assert result["text"][w["start"]:w["end"]] == w["word"]


@pytest.mark.parametrize("name", ["two_column", "single_column"])
def test_header_and_page_numbers_removed(name, request):
    # The running header repeats on both pages and the footers are bare page
    # numbers: neither may be read aloud.
    result = request.getfixturevalue(name)
    assert "Journal of Sample Studies" not in result["text"]
    assert {w["word"] for w in result["words"]}.isdisjoint({"1", "2"})
    removed = [r["text"] for r in result["removed"]]
    assert removed.count("Journal of Sample Studies, Volume 7") == 2
    assert "1" in removed and "2" in removed


def test_hyphenated_words_rejoined(two_column, single_column):
    # "experi-" / "ments" and "imple-" / "mentation" are split at line ends;
    # spoken, they must be one word with no stray hyphen.
    assert "experiments" in two_column["text"] and "experi-" not in two_column["text"]
    assert "implementation" in single_column["text"] and "imple-" not in single_column["text"]


def test_equation_left_out_and_recorded(equation):
    # The display equation "c = a + b x" is shown, not read: it's absent from
    # the spoken text and recorded as a marker after the word before it.
    assert "c = a + b x" not in equation["text"] and "=" not in equation["text"]
    assert len(equation["equations"]) == 1
    marker = equation["equations"][0]
    assert marker["text"] == "c = a + b x"
    assert marker["page"] == 1 and marker["inline"] is False
    assert equation["words"][marker["after_word"]]["word"] == "following:"
    assert equation["words"][marker["after_word"] + 1]["word"] == "Hotel"
