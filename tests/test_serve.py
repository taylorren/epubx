"""The MVP reader's routes, tested against a synthetic book.

`tools/serve.py` is the manual test bed for content serving: the book's own
files at their own paths, plus the TOC, spine order and footnote edges a
renderer cannot work out for itself. These tests pin the routes it exposes.
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from epubx import open_book  # noqa: E402
from fixtures import write_epub  # noqa: E402
import serve  # noqa: E402


def _serve(book, **kwargs):
    httpd = serve.create_server(book, port=0, **kwargs)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    path = write_epub(tmp_path_factory.mktemp("serve") / "book.epub")
    book = open_book(path)
    httpd, url = _serve(book)
    yield url, path
    httpd.shutdown()
    httpd.server_close()
    book.close()


def get(url: str):
    with urllib.request.urlopen(url) as res:
        return res.status, res.headers.get("Content-Type"), res.read()


def test_shell_renders_with_a_sandboxed_frame(served):
    url, _ = served
    status, content_type, body = get(url + "/")
    assert status == 200
    assert content_type.startswith("text/html")
    page = body.decode("utf-8")
    assert "Test Book" in page
    assert 'id="content"' in page
    # The book's own scripts must not run in the reader.
    assert 'sandbox="allow-same-origin"' in page
    assert "allow-scripts" not in page


def test_book_json_gives_spine_and_toc(served):
    url, _ = served
    _, _, body = get(url + "/api/book.json")
    data = json.loads(body)
    assert data["title"] == "Test Book"
    assert data["spine"] == [
        "OEBPS/ch1.xhtml", "OEBPS/text/ch2.xhtml", "OEBPS/svgcover.xhtml",
    ]
    labels = [node["label"] for node in data["toc"]]
    assert "Chapter One" in labels and "Chapter Two" in labels


def test_chapter_is_served_at_its_own_path(served):
    url, _ = served
    status, content_type, body = get(url + "/book/OEBPS/ch1.xhtml")
    assert status == 200
    assert content_type.startswith("text/html")  # lenient by default
    assert body.startswith(b"<?xml")
    assert b"First paragraph" in body


def test_strict_types_serves_the_declared_media_type(served):
    _, path = served
    book = open_book(path)
    httpd, url = _serve(book, lenient=False)
    try:
        _, content_type, _ = get(url + "/book/OEBPS/ch1.xhtml")
        assert content_type.startswith("application/xhtml+xml")
    finally:
        httpd.shutdown()
        httpd.server_close()
        book.close()


def test_a_percent_encoded_request_finds_the_file(served):
    """The browser asks for what the book's markup says, spaces and all."""
    url, _ = served
    status, content_type, body = get(url + "/book/OEBPS/img/plate%20one.png")
    assert status == 200
    assert content_type.startswith("image/png")
    assert body[:4] == b"\x89PNG"


def test_a_file_the_book_does_not_have_is_404(served):
    url, _ = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        get(url + "/book/OEBPS/nope.xhtml")
    assert caught.value.code == 404


def test_footnotes_route_resolves_the_edges(served):
    url, _ = served
    _, _, body = get(url + "/api/footnotes.json?chapter=0")
    edges = json.loads(body)["footnotes"]
    assert len(edges) == 3  # the fixture has three markers, all resolvable
    assert all(edge["resolved"] for edge in edges)
    assert all(edge["target_url"].startswith("/book/") for edge in edges)
    assert {edge["href"] for edge in edges} == {"#fn1", "#fn2", "#fn3"}


def test_cross_document_footnote_points_at_its_own_chapter(served):
    """Chapter 2's marker resolves back into chapter 1."""
    url, _ = served
    _, _, body = get(url + "/api/footnotes.json?chapter=1")
    edges = json.loads(body)["footnotes"]
    assert edges and edges[0]["target_chapter"] == 0
    assert edges[0]["target_url"].startswith("/book/OEBPS/ch1.xhtml#")


def test_text_route_returns_what_epubx_extracted(served):
    url, _ = served
    _, _, body = get(url + "/api/text.json?chapter=0")
    text = json.loads(body)["text"]
    assert "First paragraph" in text
    assert "Quoted material" in text
