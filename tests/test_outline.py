"""The derived outline: the declared TOC with heading gaps filled.

`book.toc` stays the book's declared contents; `book.outline` adds the chapters
a volume-only NCX leaves out, without the book being touched. See PITFALLS 19.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from epubx import open_book  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures import (  # noqa: E402
    write_chapter_epub,
    write_epub,
    write_outline_epub,
)


@pytest.fixture()
def outline_book(tmp_path):
    path = write_outline_epub(tmp_path / "outline.epub")
    with open_book(path) as b:
        yield b


def test_toc_stays_the_declared_navigation(outline_book):
    """The book's own TOC is untouched: two volumes, neither with chapters."""
    toc = outline_book.toc
    assert [n.label for n in toc] == ["Volume One", "Volume Two"]
    assert all(n.children == () for n in toc)


def test_outline_fills_chapters_under_a_volume(outline_book):
    one = outline_book.outline[0]
    assert one.label == "Volume One"
    assert [(c.label, c.href, c.anchor) for c in one.children] == [
        ("Chapter 1", "OEBPS/vol1.xhtml", "c1"),
        # A heading with no id still names its chapter; only the anchor is None.
        ("Chapter 2", "OEBPS/vol1.xhtml", None),
    ]


def test_outline_scans_forward_for_a_volume_title_page(outline_book):
    """Volume Two's title is one document; its chapters are the next."""
    two = outline_book.outline[1]
    assert two.href == "OEBPS/vol2a.xhtml"
    assert [c.label for c in two.children] == ["A Dedication"]
    assert [c.label for c in two.children[0].children] == ["Chapter 3"]
    assert two.children[0].children[0].href == "OEBPS/vol2b.xhtml"
    assert two.children[0].children[0].anchor == "c3"


def test_outline_keeps_declared_children(tmp_path):
    """A node the book gave children is not re-derived, so nothing duplicates."""
    path = write_epub(tmp_path / "book.epub")
    with open_book(path) as b:
        assert b.outline == b.toc


def test_outline_equals_toc_when_there_are_no_headings(tmp_path):
    chapter = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml">'
        '<head><title>t</title></head>'
        '<body><p>Prose with no heading anywhere.</p></body></html>'
    )
    path = write_chapter_epub(tmp_path / "flat.epub", chapter)
    with open_book(path) as b:
        assert b.outline == b.toc


def test_outline_is_memoized(outline_book):
    assert outline_book.outline is outline_book.outline
