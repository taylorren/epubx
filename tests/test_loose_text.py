"""Loose prose around blocks: a container's own text, and children's tails.

Two real books put whole paragraphs where a child-only walk never looks, and
both parsed with the text silently missing (PITFALLS §1):

- *On China* keeps a section's lead-in prose in a `<div>`'s **own text**,
  beside a nested `<div>` holding the quoted passage. 67,000 characters of a
  well-formed book were dropped — 6% of it — with no error.
- *Sheng Si Suo* keeps its paragraphs in the **tails of `<br/>` elements**
  inside a div that also holds a `<ul>`. One chapter of 13,700 characters
  parsed to 185.

The rule: when a container is walked, the text that belongs to no block — its
own `.text` before the first child, and each child's `.tail` — is content and
becomes a paragraph of its own. Text a block already carries is not repeated.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from epubx import open_book  # noqa: E402
from fixtures import write_chapter_epub  # noqa: E402

HEAD = ('<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml">'
        '<head><title></title></head><body>')
TAIL = "</body></html>"


def _write(tmp_path, name, body):
    return write_chapter_epub(tmp_path / name, HEAD + body + TAIL)


def test_container_own_text_beside_a_nested_block_is_kept(tmp_path):
    """*On China*: `<div class="tx">prose<div>…</div></div>`."""
    path = _write(tmp_path, "own-text.epub",
                  '<div class="tx">Lead-in prose that must survive.'
                  '<div class="atx"><div class="q">Inner quoted passage.</div>'
                  '</div></div>')
    with open_book(path) as b:
        text = b.chapters[0].plain_text
    assert "Lead-in prose that must survive." in text
    assert "Inner quoted passage." in text
    # Document order: the lead-in precedes the passage it introduces.
    assert text.index("Lead-in") < text.index("Inner quoted")


def test_br_tail_prose_beside_a_list_is_kept(tmp_path):
    """*Sheng Si Suo*: prose in `<br/>` tails inside a div holding a `<ul>`."""
    path = _write(
        tmp_path, "br-tails.epub",
        '<div class="calibre1">'
        '<h3>Chapter Title</h3><br/>'
        '<br/>'
        '\u3000\u3000The first paragraph of prose, set directly in the div.<br/>'
        '<br/>'
        '\u3000\u3000The second paragraph, separated only by a line break.<br/>'
        '<ul><li>A list beside the prose</li></ul>'
        '\u3000\u3000Prose after the list, in the ul tail.<br/>'
        '</div>')
    with open_book(path) as b:
        blocks = b.chapters[0].blocks
        text = b.chapters[0].plain_text
    assert "The first paragraph of prose" in text
    assert "The second paragraph, separated only by a line break." in text
    assert "Prose after the list, in the ul tail." in text
    # The list survives as a list beside the prose, not swallowed into one.
    listing = next(bl for bl in blocks if bl.kind == "list")
    assert "A list beside the prose" in listing.plain_text


def test_prose_a_block_already_carries_is_not_repeated(tmp_path):
    """`text_of` folds a paragraph's tail into it; the walk must not re-emit it."""
    path = _write(tmp_path, "no-double.epub",
                  '<div><p>One paragraph.</p>Trailing prose.'
                  '<p>Another paragraph.</p></div>')
    with open_book(path) as b:
        text = b.chapters[0].plain_text
    assert text.count("Trailing prose.") == 1


def test_whitespace_between_blocks_is_not_a_paragraph(tmp_path):
    """Layout whitespace adds no blocks: the fix must not pad every chapter."""
    path = _write(tmp_path, "whitespace.epub",
                  '<div>\n  <p>Only paragraph.</p>\n</div>')
    with open_book(path) as b:
        blocks = b.chapters[0].blocks
    assert [bl.kind for bl in blocks] == ["paragraph"]
    assert blocks[0].text == "Only paragraph."


def test_loose_text_ids_stay_unique_and_positional(tmp_path):
    """Loose-text paragraphs join the same monotonic numbering as every block."""
    path = _write(tmp_path, "ids.epub",
                  '<div>Lead-in.<div><p>Inner.</p></div>After.</div>')
    with open_book(path) as b:
        blocks = b.chapters[0].blocks
    ids = [node.id for block in blocks for node in block]
    assert len(set(ids)) == len(ids)
    assert [bl.text for bl in blocks if bl.text] == ["Lead-in.", "Inner.", "After."]
