"""Markup where paragraphs are not `<p>`: classed `<div>`s, and prose set loose
in a wrapper separated by `<br/>`.

Found against a real book: *Killing Lincoln* emits `<div class="p-indent">`
for body text. Treating every div as a pure container lost the entire text —
all 79 chapters parsed to zero characters while the HTML plainly held prose.
"""

from epubx.content import _holds_bare_text, _is_paragraph_div
from epubx.xmlutil import parse_html


def _div(class_: str = "", inner: str = "text") -> str:
    cls = f' class="{class_}"' if class_ else ""
    return f"<div{cls}>{inner}</div>"


def _is_para(html: str) -> bool:
    root = parse_html(f"<html><body>{html}</body></html>".encode())
    return _is_paragraph_div(root.find(".//div"))


def test_calibre_paragraph_classes_are_paragraphs():
    for cls in ("p-indent", "p-br", "p-blanc", "p-continuance"):
        assert _is_para(_div(cls)), cls


def test_structural_divs_are_not_paragraphs():
    for cls in ("part", "titlePage", "bodymatter", "footnotes", "cover", "calibre"):
        assert not _is_para(_div(cls)), cls


def test_classless_div_is_structure_not_paragraph():
    """No class means no claim to being prose.

    A "does this div just hold text?" heuristic looks reasonable and is wrong:
    it swallows `<body>` and `<section>`, collapsing a whole chapter into one
    block. Only the explicit convention is honoured.
    """
    assert not _is_para(_div("", "plain text"))
    assert not _is_para(_div("", "<span>wrapped</span>"))


BARE_TEXT_PAGE = """<html xmlns="http://www.w3.org/1999/xhtml"><body>
  <div class="calibre1">
    <h3 id="h1">Chapter Title</h3><br/>
    <br/>
    　　The first paragraph of prose, set directly in the div.<br/>
    <br/>
    　　The second paragraph, separated only by a line break.
  </div>
</body></html>"""


def test_bare_text_paragraph_is_detected():
    """The detector fires on a heading plus loose prose in one wrapper."""
    root = parse_html(BARE_TEXT_PAGE.encode())
    div = root.find(".//div")
    assert _is_paragraph_div(div) is False, "class does not say paragraph"
    assert _holds_bare_text(div) is True, "loose prose beside a heading counts"


def test_div_holding_a_real_block_is_not_bare_text():
    """A `<p>` inside means the text is already someone's job: recurse."""
    html = ('<html><body><div><p>Real paragraph</p></div></body></html>')
    root = parse_html(html.encode())
    assert _holds_bare_text(root.find(".//div")) is False


def test_empty_div_is_not_bare_text():
    root = parse_html(b"<html><body><div><br/></div></body></html>")
    assert _holds_bare_text(root.find(".//div")) is False
