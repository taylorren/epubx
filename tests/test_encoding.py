"""Text encoding of content documents.

Found against a real book (`Becoming Steve Jobs`): `next-reads.xhtml` was valid
UTF-8 with no charset declaration at all, and lxml's HTML parser fell back to
latin-1, so "What's" was extracted as "Whata\\x80\\x99s".
"""

import pytest

from epubx.xmlutil import _charset, _decoded, parse_html, text_of

SMART_QUOTE = "What’s next?"


def _doc(body: str, declaration: str = "") -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
        f"{declaration}</head><body><h1>{body}</h1></body></html>"
    ).encode("utf-8")


def test_undeclared_utf8_document_is_not_mojibake():
    """No charset declaration at all, valid UTF-8: text must survive intact."""
    root = parse_html(_doc(SMART_QUOTE))
    assert text_of(root) == SMART_QUOTE
    assert "â" not in text_of(root)


def test_explicit_meta_charset_is_honoured():
    root = parse_html(_doc(SMART_QUOTE, '<meta charset="utf-8"/>'))
    assert text_of(root) == SMART_QUOTE


def test_utf8_bom_is_detected_and_stripped():
    data = b"\xef\xbb\xbf" + _doc(SMART_QUOTE)
    assert _charset(data) == "utf-8"
    assert text_of(parse_html(data)) == SMART_QUOTE


def test_latin1_document_with_a_declaration_is_decoded():
    """A genuinely latin-1 document is honoured, not double-decoded."""
    doc = (
        '<?xml version="1.0"?><html><head>'
        '<meta http-equiv="Content-Type" content="text/html; charset=iso-8859-1"/>'
        f'</head><body><h1>caf{chr(0xE9)}</h1></body></html>'
    ).encode("iso-8859-1")
    assert _charset(doc) == "iso-8859-1"
    assert text_of(parse_html(doc)) == "café"


@pytest.mark.parametrize("encoding", ["windows-1252", "utf-16"])
def test_other_declared_encodings_round_trip(encoding):
    doc = (
        '<?xml version="1.0"?><html><head>'
        f'<meta charset="{encoding}"/>'
        f'</head><body><h1>na{chr(0xEF)}ve</h1></body></html>'
    )
    expected = "naïve"
    assert text_of(parse_html(doc.encode(encoding))) == expected


def test_undecodable_bytes_do_not_raise():
    """Bytes that are neither UTF-8 nor a declared encoding parse leniently."""
    data = b"<html><body><h1>caf\xe9</h1></body></html>"
    assert parse_html(data) is not None
    assert _decoded(data) == data  # left as-is rather than raising
