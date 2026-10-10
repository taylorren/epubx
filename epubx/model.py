"""The content model.

These dataclasses are the public surface and are designed to be final for
Phase I: later phases add capability without migration. Everything here is
immutable, positional, and never persisted.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, Iterator

# Block kinds, per the spec's content-model table.
HEADING = "heading"
PARAGRAPH = "paragraph"
QUOTE = "quote"
TABLE = "table"
LIST = "list"
DEFINITION_LIST = "definition_list"
FIGURE = "figure"
FOOTNOTE_REF = "footnote_ref"
PREFORMATTED = "preformatted"
PAGE_BREAK = "page_break"
MATH = "math"


@dataclass(frozen=True)
class Creator:
    """An author/contributor, with the `epub:type` role refinements merged in."""

    name: str
    role: str = "aut"
    file_as: str | None = None
    refines: str | None = field(default=None, compare=False, repr=False)

    def __str__(self) -> str:  # pragma: no cover - convenience
        return self.file_as or self.name


@dataclass(frozen=True)
class Metadata:
    title: str | None = None
    creators: tuple[Creator, ...] = ()
    language: str | None = None
    identifier: str | None = None
    publisher: str | None = None
    date: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class Image:
    """A resolved image reference.

    No size is measured or reported. Sizing belongs to the renderer, which
    reads the book's own `width`/`height` attributes from the XHTML it is
    already rendering; guessing here would only duplicate that, from the wrong
    source, and would make epubx decode image formats it otherwise never
    touches.
    """

    path: str  # zip-relative, normalised, e.g. 'OEBPS/img/cover.jpg'
    media_type: str | None = None
    alt: str | None = None
    _book: "object" = field(repr=False, compare=False, default=None)

    def read(self) -> bytes:
        """The image bytes, fetched from the zip on demand."""
        if self._book is None:
            raise ValueError(f"image is not attached to a book: {self.path}")
        return self._book.member(self.path)


@dataclass(frozen=True)
class Resource:
    """A file inside the EPUB, served exactly as the book shipped it.

    Rendering is the renderer's job. epubx hands over the book's own bytes at
    the book's own paths, so the book's relative links — stylesheets, fonts,
    images, footnotes — resolve in a browser with nothing rewritten.
    """

    path: str  # zip-relative, e.g. 'OEBPS/Text/ch1.xhtml'
    media_type: str = "application/octet-stream"
    _book: "object" = field(repr=False, compare=False, default=None)

    def read(self) -> bytes:
        """The member's bytes, fetched from the zip on demand."""
        if self._book is None:
            raise ValueError(f"resource is not attached to a book: {self.path}")
        return self._book.member(self.path)


@dataclass(frozen=True)
class Block:
    """One unit of ordered content.

    `id` is `{chapter_index}/b{ordinal}` — positional, not content-hashed, so
    it survives republishing and can anchor annotations and reading positions.
    """

    id: str
    kind: str
    text: str | None = None
    target_id: str | None = None  # footnote_ref -> the block it resolves to
    items: tuple["Block", ...] = ()  # nested blocks (list items, cells, defs)
    rows: tuple[tuple["Block", ...], ...] = ()  # table
    header_rows: int = 0  # table: leading rows that are headers
    ordered: bool = False  # list
    images: tuple[Image, ...] = ()
    # Parse detail: `element`, `level`, `dom_ids`, `target_id`, `mathml`, ...
    # A plain dict while the parser builds the block, and a read-only Mapping
    # once `parse_document` freezes it.
    attributes: Mapping[str, Any] = field(default_factory=dict, compare=False)

    @property
    def plain_text(self) -> str:
        """Block-local text, including nested blocks, whitespace-collapsed."""
        parts: list[str] = []
        self._collect_text(parts)
        return " ".join(p for p in parts if p)

    def _collect_text(self, out: list[str]) -> None:
        if self.text:
            out.append(" ".join(self.text.split()))
        for item in self.items:
            item._collect_text(out)
        for row in self.rows:
            for cell in row:
                cell._collect_text(out)
        for img in self.images:
            if img.alt:
                out.append(img.alt)

    def __iter__(self) -> Iterator["Block"]:
        """Depth-first walk over this block and everything nested inside it."""
        yield self
        for item in self.items:
            yield from item
        for row in self.rows:
            for cell in row:
                yield from cell


@dataclass(frozen=True)
class TocNode:
    label: str
    href: str | None = None
    children: tuple["TocNode", ...] = ()
    anchor: str | None = None


@dataclass(frozen=True)
class Footnote:
    """One footnote edge, normalized: a marker and the note it opens.

    `note_text` is extracted here so a reader can show a footnote without
    fetching or re-parsing anything. A note that runs on for several
    paragraphs — the paragraphs after the anchor's own carry no id of their
    own — is gathered up to the next note in document order; a note that no
    other note follows keeps its own paragraph alone. `target_id` is None for
    markers the graph could not resolve — the book's own link still serves
    them.
    """

    block_id: str                        # the marker's block, e.g. 'c0003/b0012'
    text: str | None                     # the marker as printed, e.g. '12'
    href: str                            # the href exactly as the book wrote it
    target_id: str | None = None         # the block holding the note
    target_chapter: int | None = None    # the chapter holding that block
    target_dom_ids: tuple[str, ...] = () # DOM ids on the note block
    note_text: str | None = None         # the note's text


@dataclass(frozen=True)
class Chapter:
    """A spine document. Content is parsed on first access, not on open()."""

    index: int
    href: str  # zip-relative path of the content document
    media_type: str | None = None
    _book: "object" = field(repr=False, compare=False, default=None)

    @cached_property
    def blocks(self) -> tuple[Block, ...]:
        from .content import parse_document, parse_stash

        stashed = parse_stash.get((id(self._book), self.index))
        if stashed is not None:
            return stashed  # reentrant access during footnote resolution
        return parse_document(self._book, self.index, self.href)

    @cached_property
    def images(self) -> tuple[Image, ...]:
        seen: dict[str, Image] = {}
        for block in self.blocks:
            for img in block.images:
                seen.setdefault(img.path, img)
        return tuple(seen.values())

    @cached_property
    def plain_text(self) -> str:
        """Derived and memoized, never stored.

        Script and style blocks are part of the graph but are not display text,
        so they are excluded here rather than being absent from `blocks`.
        """
        return "\n\n".join(
            b.plain_text
            for b in self.blocks
            if b.text and b.attributes.get("element") not in ("script", "style")
        )

    @cached_property
    def _index(self) -> dict[str, Block]:
        """Every block in the chapter, nested ones included, by id."""
        index: dict[str, Block] = {}
        for block in self.blocks:
            for node in block:  # Block.__iter__ walks nested blocks too
                index.setdefault(node.id, node)
        return index

    def block_by_id(self, block_id: str) -> Block | None:
        """Look up any block by id, including nested ones (list items, cells)."""
        return self._index.get(block_id)

    def resolve(self, target_id: str) -> Block | None:
        """Resolve a footnote target id to its block, within this chapter."""
        return self.block_by_id(target_id)

    @cached_property
    def _note_text_by_id(self) -> dict[str, str]:
        """The full text of each multi-paragraph note in this chapter, by id.

        A note's target is the single paragraph its anchor owns; when the
        paragraphs after it carry no id of their own, the note runs on to the
        next note in document order and is gathered whole here. A note that no
        other note follows, or whose span holds a marker (the body resumed —
        an inline note, not a run of endnotes), keeps its own paragraph alone
        and is absent from this map.

        Note starts are read straight from `blocks`, never from `footnotes`:
        a same-chapter note would otherwise resolve through the very property
        being computed.
        """
        top = self.blocks
        position = {block.id: i for i, block in enumerate(top)}
        starts: set[int] = set()
        for block in top:
            for node in block:  # nested blocks included
                if node.kind == FOOTNOTE_REF:
                    target = node.attributes.get("target_id")
                    if target in position:
                        starts.add(position[target])
        ordered = sorted(starts)
        texts: dict[str, str] = {}
        for i, start in enumerate(ordered[:-1]):  # the last note is unbounded
            span = top[start:ordered[i + 1]]
            if any(block.kind == FOOTNOTE_REF for block in span):
                continue
            texts[top[start].id] = "\n\n".join(
                block.plain_text for block in span if block.plain_text
            )
        return texts

    @cached_property
    def footnotes(self) -> tuple[Footnote, ...]:
        """Every footnote marker in this chapter, normalized.

        The graph's edge list in document order: each marker with the block
        it resolves to, the chapter holding it, and the note's text already
        extracted. A marker the graph could not resolve stays listed with
        `target_id` None — the book's own link still serves it.
        """
        edges: list[Footnote] = []
        for block in self.blocks:
            for node in block:  # Block.__iter__ walks nested blocks too
                if node.kind != FOOTNOTE_REF:
                    continue
                target_id = node.attributes.get("target_id")
                target_chapter: int | None = None
                dom_ids: tuple[str, ...] = ()
                note_text: str | None = None
                if target_id and "/" in target_id:
                    target_chapter = int(target_id[1:target_id.index("/")])
                    chapter = self._book.chapters[target_chapter]
                    target = chapter.block_by_id(target_id)
                    if target is not None:
                        dom_ids = tuple(target.attributes.get("dom_ids") or ())
                        note_text = (chapter._note_text_by_id.get(target_id)
                                     or target.plain_text or None)
                edges.append(Footnote(
                    block_id=node.id,
                    text=node.text,
                    href=node.attributes.get("href") or "",
                    target_id=target_id,
                    target_chapter=target_chapter,
                    target_dom_ids=dom_ids,
                    note_text=note_text,
                ))
        return tuple(edges)
