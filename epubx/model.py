"""The content model.

These dataclasses are the public surface and are designed to be final for
Phase I: later phases add capability without migration. Everything here is
immutable, positional, and never persisted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import Iterator

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

    Dimensions are coalesced, not privileged: the publisher's declared
    `width`/`height` wins where present, intrinsic dimensions from the file
    are the fallback (see `epubx.images`).
    """

    path: str  # zip-relative, normalised, e.g. 'OEBPS/img/cover.jpg'
    media_type: str | None = None
    alt: str | None = None
    width: int | None = None
    height: int | None = None
    _book: "object" = field(repr=False, compare=False, default=None)

    def read(self) -> bytes:
        """The image bytes, fetched from the zip on demand."""
        if self._book is None:
            raise ValueError(f"image is not attached to a book: {self.path}")
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
    attributes: dict = field(default_factory=dict, compare=False)

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
class Chapter:
    """A spine document. Content is parsed on first access, not on open()."""

    index: int
    href: str  # zip-relative path of the content document
    media_type: str | None = None
    _book: "object" = field(repr=False, compare=False, default=None)

    @cached_property
    def blocks(self) -> tuple[Block, ...]:
        from .content import parse_document

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
