"""The book's full outline: declared navigation, with heading gaps filled.

`book.toc` reports exactly what a book *declares* — its nav document, else its
NCX, else spine order. Some books declare less than their documents contain. A
Calibre MOBI->EPUB conversion, for one, names only the volumes in its NCX while
the chapters inside each volume exist solely as `<h3>` headings in the volume's
own document (PITFALLS 19). A reader that trusts the declared TOC alone shows
nine volumes and no way into any of them.

`build_outline` keeps the declared tree as the backbone and fills in children
only where a declared entry has none, drawing them from the headings its span
contains. Declared structure always wins: a node the book gave children is left
exactly as written, so nothing is duplicated and nothing declared is replaced.

Which heading is "really" a chapter is not decided here. Every heading below the
entry's own is reported, because that judgement belongs to the reader — parse,
don't judge. A dedication or a sub-section is a heading like any other.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import HEADING, TocNode


@dataclass(frozen=True)
class _Heading:
    """One heading block, located in the book rather than in its document."""

    position: tuple[int, int]  # (spine index, block ordinal) — document order
    level: int                 # h1..h6, from the block's attributes
    text: str
    href: str                  # the chapter document it lives in
    anchor: str | None         # its first DOM id, if it has one


def _ordinal(block) -> int:
    """A block's position within its chapter, read from its id `cNNNN/bMMMM`."""
    tail = block.id.rpartition("/b")[2]
    try:
        return int(tail)
    except ValueError:  # pragma: no cover - ids are always cNNNN/bMMMM
        return 0


def _heading_list(book, chapter_index: int) -> tuple[_Heading, ...]:
    """Every heading block in one chapter, in document order, nested included."""
    chapter = book.chapters[chapter_index]
    out: list[_Heading] = []
    for block in chapter.blocks:
        for node in block:  # Block.__iter__ walks nested blocks too
            if node.kind != HEADING:
                continue
            level = node.attributes.get("level")
            if level is None:  # pragma: no cover - a heading always carries one
                continue
            dom_ids = node.attributes.get("dom_ids") or ()
            out.append(_Heading(
                position=(chapter_index, _ordinal(node)),
                level=int(level),
                text=node.text or "",
                href=chapter.href,
                anchor=dom_ids[0] if dom_ids else None,
            ))
    return tuple(out)


def _flatten(nodes, depth: int = 0) -> list[tuple[TocNode, int]]:
    """Pre-order walk of the declared tree, each node paired with its depth."""
    out: list[tuple[TocNode, int]] = []
    for node in nodes:
        out.append((node, depth))
        out.extend(_flatten(node.children, depth + 1))
    return out


def _anchor_position(book, href, anchor, index_by_href) -> tuple[int, int] | None:
    """`(spine index, block ordinal)` for an href#anchor, or None if unmapped.

    A node with no fragment sits at the start of its document; an unmapped href
    (a resource, or nothing) has no position at all, and the node is left alone.
    """
    index = index_by_href.get(href)
    if index is None:
        return None
    if not anchor:
        return (index, -1)
    chapter = book.chapters[index]
    for block in chapter.blocks:
        for node in block:
            if anchor in (node.attributes.get("dom_ids") or ()):
                return (index, _ordinal(node))
    return (index, -1)  # an anchor the document never defines: treat as its start

def _headings_in_span(book, headings_of, start, end) -> list[_Heading]:
    """Headings after `start` and before `end` (None = to the end of the book)."""
    last = len(book.chapters) if end is None else end[0] + 1
    out: list[_Heading] = []
    for index in range(start[0], last):
        for heading in headings_of(index):
            if heading.position <= start:
                continue
            if end is not None and heading.position >= end:
                continue
            out.append(heading)
    return out


def _first_heading_at_or_after(book, headings_of, start, end) -> _Heading | None:
    last = len(book.chapters) if end is None else end[0] + 1
    for index in range(start[0], last):
        for heading in headings_of(index):
            if heading.position >= start:
                return heading
    return None


def _freeze(items: list[dict]) -> tuple[TocNode, ...]:
    return tuple(
        TocNode(label=item["label"], href=item["href"], anchor=item["anchor"],
                children=_freeze(item["children"]))
        for item in items
    )


def _nest(headings: list[_Heading]) -> tuple[TocNode, ...]:
    """Turn a flat heading run into a tree, nesting by heading level.

    A heading is the child of the nearest preceding heading shallower than it,
    which is the outline the document's own h1..h6 structure describes.
    """
    roots: list[dict] = []
    stack: list[tuple[int, dict]] = []
    for heading in headings:
        item = {"label": heading.text, "href": heading.href,
                "anchor": heading.anchor, "children": []}
        while stack and stack[-1][0] >= heading.level:
            stack.pop()
        (stack[-1][1]["children"] if stack else roots).append(item)
        stack.append((heading.level, item))
    return _freeze(roots)


def _children_for(book, headings_of, start, end) -> tuple[TocNode, ...]:
    """Headings below the entry's own, within its span, as a nested tree."""
    base = _first_heading_at_or_after(book, headings_of, start, end)
    if base is None:
        return ()
    deeper = [h for h in _headings_in_span(book, headings_of, start, end)
              if h.level > base.level]
    return _nest(deeper)


def build_outline(book) -> tuple[TocNode, ...]:
    """The declared TOC, with heading-derived children filling its empty nodes.

    The declared tree is authoritative: every node the book gave children keeps
    them untouched. A node with no children is filled from the headings its span
    contains — the span running from the node's own position to the next declared
    entry, which is what lets a volume whose title page is one document and whose
    chapters are the next be read as a whole.
    """
    toc = book.toc
    if not toc:
        return toc

    index_by_href = {chapter.href: chapter.index for chapter in book.chapters}
    cache: dict[int, tuple[_Heading, ...]] = {}

    def headings_of(index: int) -> tuple[_Heading, ...]:
        if index not in cache:
            cache[index] = _heading_list(book, index)
        return cache[index]

    flat = _flatten(toc)
    starts = [_anchor_position(book, node.href, node.anchor, index_by_href)
              for node, _ in flat]
    # A node's span ends where the next node outside its subtree begins: the
    # next entry in pre-order at the same or shallower depth.
    ends: list[tuple[int, int] | None] = []
    for i, (_, depth) in enumerate(flat):
        j = i + 1
        while j < len(flat) and flat[j][1] > depth:
            j += 1
        ends.append(starts[j] if j < len(flat) else None)

    cursor = iter(range(len(flat)))  # advances in pre-order as we rebuild

    def rebuild(nodes) -> tuple[TocNode, ...]:
        out: list[TocNode] = []
        for node in nodes:
            i = next(cursor)
            if node.children or starts[i] is None:
                children = rebuild(node.children)
            else:
                children = _children_for(book, headings_of, starts[i], ends[i])
            out.append(TocNode(label=node.label, href=node.href,
                                anchor=node.anchor, children=children))
        return tuple(out)

    return rebuild(toc)

