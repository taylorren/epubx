"""Serving the book to a renderer.

An EPUB is a zip of XHTML and its resources; a browser already renders that.
So epubx hands over the book's own files at the book's own paths, in reading
order, and the renderer renders — nothing is re-modelled, nothing rewritten.

These tests pin that contract: reading order, bytes with a usable
Content-Type, a request URL that is normalised rather than missed, and a
resource listing that leaves out the container's own plumbing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from epubx import open_book  # noqa: E402
from fixtures import write_epub  # noqa: E402


@pytest.fixture(scope="module")
def book_path(tmp_path_factory):
    return write_epub(tmp_path_factory.mktemp("serve") / "book.epub")


@pytest.fixture()
def book(book_path):
    with open_book(book_path) as b:
        yield b


def test_spine_is_the_reading_order(book):
    assert book.spine == (
        "OEBPS/ch1.xhtml", "OEBPS/text/ch2.xhtml", "OEBPS/svgcover.xhtml",
    )
    assert book.spine == tuple(ch.href for ch in book.chapters)


def test_resource_returns_the_books_own_bytes(book):
    res = book.resource("OEBPS/ch1.xhtml")
    assert res is not None
    assert res.path == "OEBPS/ch1.xhtml"
    assert res.media_type == "application/xhtml+xml"  # declared by the OPF
    assert res.read().startswith(b"<?xml")


def test_resource_serves_a_declared_image_with_its_media_type(book):
    res = book.resource("OEBPS/img/cover.jpg")
    assert res is not None
    assert res.media_type == "image/jpeg"
    assert res.read()[:2] == b"\xff\xd8"


def test_resource_accepts_a_request_url_not_just_a_zip_path(book):
    """A renderer hands back what the browser asked for, percent-encoding and all."""
    res = book.resource("/OEBPS/img/plate%20one.png")
    assert res is not None
    assert res.path == "OEBPS/img/plate one.png"
    assert res.media_type == "image/png"
    assert res.read()[:4] == b"\x89PNG"


def test_unknown_or_external_resource_is_none(book):
    assert book.resource("OEBPS/nope.xhtml") is None
    assert book.resource("https://example.org/remote.png") is None
    assert book.resource("") is None


def test_media_type_falls_back_to_the_suffix(book):
    """The OPF does not name its own package document; the suffix decides."""
    res = book.resource("OEBPS/content.opf")
    assert res is not None
    assert res.media_type == "application/oebps-package+xml"


def test_resources_lists_the_books_files_not_the_containers(book):
    resources = list(book.resources())
    paths = {res.path for res in resources}
    assert "OEBPS/ch1.xhtml" in paths
    assert "OEBPS/nav.xhtml" in paths
    assert "OEBPS/toc.ncx" in paths
    # The container's own plumbing is not a book file.
    assert "mimetype" not in paths
    assert not any(path.startswith("META-INF/") for path in paths)
    assert not any(path.endswith("/") for path in paths)
    assert all(res.media_type for res in resources)


def test_resources_does_not_read_bytes_until_asked(book_path):
    """Listing is cheap: opening a book and enumerating it parses nothing."""
    with open_book(book_path) as b:
        assert all(ch.__dict__.get("blocks") is None for ch in b.chapters)
        for res in b.resources():
            assert res.path  # enumeration alone touches no member
        assert all(ch.__dict__.get("blocks") is None for ch in b.chapters)
