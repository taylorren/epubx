"""The block-level content graph.

`ch.blocks` is a flat, ordered tuple; nesting (list items, table cells,
definition terms/definitions) lives inside the owning block. Identity is
positional — `{chapter_index}/b{ordinal}` — so it survives republishing and can
anchor annotations and reading positions.

Nothing is dropped because it is inconvenient: `<script>`, `<nav>`, `<table>`
and MathML are all reported as what they are. Sanitising for display is the
consumer's decision, and is unrecoverable if done here.
"""

from __future__ import annotations

import re

from .hrefs import normalize_href, split_fragment
from .images import resolve_dimensions
from .model import (
    DEFINITION_LIST,
    FIGURE,
    FOOTNOTE_REF,
    HEADING,
    LIST,
    MATH,
    PAGE_BREAK,
    PARAGRAPH,
    PREFORMATTED,
    QUOTE,
    TABLE,
    Block,
    Image,
)
from .xmlutil import epub_type, text_of

HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
QUOTE_TAGS = {"blockquote", "q"}
PREFORMATTED_TAGS = {"pre", "code", "kbd", "samp", "tt"}
MATH_TAGS = {"math"}
CONTAINER_TAGS = {"aside", "section", "nav", "header", "footer", "article", "div"}
# `epub:type="pagebreak"` appears 1,873 times in the corpus.
PAGEBREAK_MARKERS = {"pagebreak", "page-break"}
# 1,199 footnote markers against 12,737 hrefs in the corpus.
FOOTNOTE_CLASS = re.compile(r"(^|[-_ ])(fn|footnote|noteref|endnote)([-_ ]|$)")

MEDIA_TYPES_BY_SUFFIX = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "jpe": "image/jpeg",
    "png": "image/png", "gif": "image/gif", "svg": "image/svg+xml",
    "webp": "image/webp", "tiff": "image/tiff", "bmp": "image/bmp",
}


class _Builder:
    """Walks one content document, emitting blocks in document order.

    A single monotonic counter numbers every block, top-level and nested, so
    ids are unique within a chapter and stable across re-parses.
    """

    def __init__(self, book, chapter_index: int, href: str):
        self.book = book
        self.chapter_index = chapter_index
        self.href = href
        self.blocks: list[Block] = []
        self._ordinal = 0
        self._by_dom_id: dict[str, str] = {}
        self._emitted: list[tuple] = []
        self._pending_element = None
        self._pending_dom_id: str | None = None
        self.base = href  # relative hrefs resolve against this document

    # -- emission --------------------------------------------------------

    def make(self, kind: str, **kwargs) -> Block:
        """Create a block with the next ordinal."""
        self._ordinal += 1
        block = Block(id=f"c{self.chapter_index:04d}/b{self._ordinal:04d}",
                      kind=kind, **kwargs)
        dom_id, self._pending_dom_id = self._pending_dom_id, None
        element, self._pending_element = self._pending_element, None
        if element is not None:
            # Which element this block was emitted from. The element is held
            # strongly: keying on `id(element)` would alias once lxml recycles
            # a proxy, which silently mis-attributes every later id.
            self._emitted.append((element, block))
        if dom_id:
            self._by_dom_id.setdefault(dom_id, block.id)
            block.attributes["dom_ids"] = block.attributes.get("dom_ids", ()) + (dom_id,)
        self.blocks.append(block)
        return block

    def emit(self, element, kind: str, **kwargs) -> Block:
        """Create a block, recording the element's DOM id for link resolution."""
        self._pending_dom_id = element.get("id")
        self._pending_element = element
        return self.make(kind, **kwargs)

    def sub(self, fn, *args, **kwargs) -> tuple[Block, ...]:
        """Build nested blocks *without* adding them to the flat sequence.

        Nesting lives inside the owning block: nested blocks are reached
        through `Block.items` / `Block.rows`, not through `ch.blocks`. The
        ordinal counter keeps advancing, so nested ids stay unique and stable.
        """
        outer_blocks = self.blocks
        self.blocks = []
        try:
            fn(*args, **kwargs)
            return tuple(self.blocks)
        finally:
            self.blocks = outer_blocks

    # -- walking ---------------------------------------------------------

    def walk(self, parent, in_footnote: bool = False) -> None:
        for child in parent:
            if isinstance(getattr(child, "tag", None), str):
                self.visit(child, in_footnote)

    def visit(self, element, in_footnote: bool = False) -> None:
        tag = element.tag.lower()
        types = set(epub_type(element))

        if PAGEBREAK_MARKERS & types or tag == "pagebreak":
            self.emit(element, PAGE_BREAK)
            return
        if tag in HEADING_TAGS:
            self.emit(element, HEADING, text=text_of(element) or None,
                      attributes={"level": int(tag[1])})
            return
        if tag == "img":
            self._figure_images(element, self._images_of([element]))
            return
        if tag == "p":
            self._paragraph(element, in_footnote)
            return
        if tag in QUOTE_TAGS:
            self.emit(element, QUOTE, text=text_of(element) or None,
                      images=self._images_of(element.iter("img")))
            return
        if tag in PREFORMATTED_TAGS:
            self.emit(element, PREFORMATTED, text=text_of(element) or None)
            return
        if tag in MATH_TAGS or "mathml" in types:
            self.emit(element, MATH, text=text_of(element) or None,
                      attributes=_math_attrs(element))
            return
        if tag == "table":
            self._table(element)
            return
        if tag == "svg":
            # A cover wrapped in SVG: report the images it references.
            images = self._svg_images(element)
            if images:
                self.emit(element, FIGURE, images=images)
            else:
                self.walk(element, in_footnote)
            return
        if tag in ("ul", "ol"):
            self._list(element, in_footnote)
            return
        if tag == "dl":
            self._definition_list(element)
            return
        if tag == "figure":
            self._figure(element)
            return
        if tag == "a" and self._is_footnote_ref(element):
            self._footnote_ref(element)
            return
        if tag in ("script", "style"):
            # Parse, don't judge: reported, with its text, as its own block.
            self.emit(element, PARAGRAPH, text=text_of(element) or None,
                      attributes={"element": tag})
            return
        if tag in CONTAINER_TAGS:
            self._div(element, in_footnote, types)
            return
        if _holds_bare_text(element):
            # Prose sitting directly in a wrapper, with no <p> of its own.
            self._paragraph(element, in_footnote)
            return
        # Unknown inline or wrapper: recurse, so nothing is silently lost.
        self.walk(element, in_footnote)

    def _container(self, element, in_footnote: bool, types: set[str]) -> None:
        """A structural element: recurse, keeping its `epub:type` refinement.

        Corpus-observed refinements: footnote, bodymatter, frontmatter,
        backmatter, landmarks, titlepage, warning.
        """
        role = next(iter(sorted(types)), None)
        note = in_footnote or role in ("footnote", "footnote-body", "endnote")
        before = len(self.blocks)
        self.walk(element, note)
        produced = self.blocks[before:]
        if role:
            for block in produced:
                block.attributes.setdefault("role", role)
        # A container's own `id` is a link target as well — real books hang a
        # note id on the wrapping `<div epub:type="footnote">` rather than on
        # the paragraph inside it. Ownership is settled uniformly for every
        # element by `_claim_stray_ids` after the walk, so nothing to do here.

    def _div(self, element, in_footnote: bool, types: set[str]) -> None:
        """Decide whether a container element is a paragraph or just structure.

        Three real conventions, all of which cost text when mishandled:

        - Calibre marks body text by class (`p-indent`, `p-br`, `p-blanc`) and
          emits no `<p>` at all. Treating every div as structure lost all 79
          chapters of *Killing Lincoln*.
        - Chinese novels put whole pages of prose directly in a `<div>` with
          only `<br/>` between paragraphs. Recursing finds nothing, so ~130
          readable books parsed to empty.
        - A genuine wrapper (`<section>`, `<aside>`) holds blocks and is left
          to recurse.
        """
        if element.tag.lower() == "div" and (
            _is_paragraph_div(element) or _holds_bare_text(element)
        ):
            self._paragraph(element, in_footnote)
            return
        self._container(element, in_footnote, types)

    def _paragraph(self, element, in_footnote: bool) -> None:
        images = self._images_of(element.iter("img"))
        maths = list(element.iter("math"))
        if images and not text_of(element):
            self._figure_images(element, images)
            return
        attributes: dict = {}
        if in_footnote:
            attributes["footnote"] = True
        self.emit(element, PARAGRAPH, text=self._text_excluding(element, maths),
                  images=images, attributes=attributes)
        # Inline math is still a first-class block; it just is not also prose.
        for math in maths:
            self.emit(math, MATH, text=text_of(math) or None,
                      attributes=_math_attrs(math))
        # A noteref marker inside prose is a graph edge of its own, so that
        # `target_id` resolution has something to attach to.
        for anchor in element.iter("a"):
            if self._is_footnote_ref(anchor):
                self._footnote_ref(anchor)

    @staticmethod
    def _text_excluding(element, exclude) -> str | None:
        """The paragraph's text, minus any inline math it contains."""
        if not exclude:
            return text_of(element) or None
        skip = {id(node) for math in exclude for node in math.iter()}
        parts = [
            node.text for node in element.iter()
            if id(node) not in skip and isinstance(getattr(node, "tag", None), str)
            and node.tag not in ("script", "style") and node.text
        ]
        return " ".join("".join(parts).split()) or None

    # -- footnotes -------------------------------------------------------

    @staticmethod
    def _is_footnote_ref(element) -> bool:
        """A link out to a note: `epub:type="noteref"`, a class, or href shape."""
        if "noteref" in set(epub_type(element)):
            return True
        if FOOTNOTE_CLASS.search(element.get("class") or ""):
            return True
        href = element.get("href") or ""
        return href.startswith("#") and bool(FOOTNOTE_CLASS.search(href[1:]))

    def _footnote_ref(self, element) -> None:
        """A noteref marker: a graph edge, resolved to `target_id`."""
        href = element.get("href") or ""
        _, frag = split_fragment(href)
        attributes = {"href": href}
        if frag:
            attributes["fragment"] = frag
        self.emit(element, FOOTNOTE_REF, text=text_of(element) or None,
                  attributes=attributes)

    def _resolve_footnotes(self, blocks) -> None:
        """Turn footnote fragments into block ids — the graph edge.

        Resolution prefers a DOM id seen in this document; a link to another
        content document is resolved through the spine instead.
        """
        for block in self._all_blocks(blocks):
            attributes = block.attributes
            frag = attributes.pop("fragment", None)
            if not frag and attributes.get("href"):
                _, frag = split_fragment(attributes["href"])
            if not frag:
                continue
            target = self._by_dom_id.get(frag) or self._cross_document(attributes["href"])
            attributes["target_id"] = target

    @staticmethod
    def _all_blocks(blocks):
        for block in blocks:
            yield block
            yield from block.items
            for row in block.rows:
                yield from row

    def _cross_document(self, href: str) -> str | None:
        """A link leaving this chapter: find the id in the target chapter."""
        raw, frag = split_fragment(href)
        if not frag:
            return None
        if raw:
            # Resolve against this content document's directory, then the OP's.
            chapter = self.book.chapter_for(raw, frag, base=self.href)
            if chapter is None or chapter.index == self.chapter_index:
                return None
        else:
            # A bare `#fn_5` names no document, and the id recurs across
            # chapters, so try every document that declares it and take the
            # first that both lies in the spine and actually owns the id.
            for holder in self.book.find_element_id(frag) or ():
                if holder == self.href:
                    continue
                chapter = self.book.chapter_for(holder, frag)
                if chapter is None or chapter.index == self.chapter_index:
                    continue
                for block in chapter.blocks:  # parses that chapter on demand
                    for node in block:  # the target may be nested
                        if frag in node.attributes.get("dom_ids", ()):
                            return node.id
            # A target in a notes file outside the spine is a real reference
            # with no addressable block, so it stays unresolved by design.
            return None
        for block in chapter.blocks:  # parses that chapter on demand
            for node in block:  # the target may be nested (a list item, a cell)
                if frag in node.attributes.get("dom_ids", ()):
                    return node.id
        return None

    # -- containers ------------------------------------------------------

    def _list(self, element, in_footnote: bool) -> None:
        """A list owns its items; a nested list lives inside its item."""
        ordered = element.tag.lower() == "ol"
        items = self.sub(self._list_items, element, in_footnote)
        self.emit(element, LIST, items=items, ordered=ordered,
                  attributes={"start": element.get("start")})

    def _list_items(self, element, in_footnote: bool) -> None:
        for li in element:
            if not isinstance(getattr(li, "tag", None), str) or li.tag.lower() != "li":
                continue
            nested = self.sub(self._list, li, in_footnote) if self._has_list(li) else ()
            self._pending_dom_id = li.get("id")
            self._pending_element = li
            self.make(LIST, text=text_of(li) or None, items=nested,
                      attributes={"element": "li"})

    @staticmethod
    def _has_list(element) -> bool:
        return any(
            isinstance(getattr(c, "tag", None), str) and c.tag.lower() in ("ul", "ol", "dl")
            for c in element
        )

    def _definition_list(self, element) -> None:
        """`dt`/`dd` pairs; each definition carries the term it glosses."""
        items = self.sub(self._definition_items, element)
        self.emit(element, DEFINITION_LIST, items=items)

    def _definition_items(self, element) -> None:
        term = ""
        for child in element:
            if not isinstance(getattr(child, "tag", None), str):
                continue
            tag = child.tag.lower()
            if tag == "dt":
                text = text_of(child)
                term = f"{term} {text}" if term else text
            elif tag == "dd":
                # `make` rather than `emit`: the dd's own block must register as
                # the owner of its id, so that a noteref pointing at `#fn1`
                # resolves to the definition's text and not to the whole list.
                self._pending_dom_id = child.get("id")
                self._pending_element = child
                self.make(DEFINITION_LIST, text=text_of(child) or None,
                          attributes={"term": term, "glossdef": True})
                term = ""

    def _table(self, element) -> None:
        # Cells are numbered too (ids must be unique within the chapter) but
        # they belong to the table, not to the flat `ch.blocks` sequence.
        outer, self.blocks = self.blocks, []
        try:
            rows = []
            for tr in element.iter("tr"):
                if not isinstance(getattr(tr, "tag", None), str):
                    continue
                cells = [
                    self._cell(cell)
                    for cell in tr
                    if isinstance(getattr(cell, "tag", None), str)
                    and cell.tag.lower() in ("td", "th")
                ]
                if cells:
                    rows.append(tuple(cells))
        finally:
            self.blocks = outer
        self.emit(element, TABLE, rows=tuple(rows),
                  header_rows=_header_rows(rows),
                  attributes={"caption": self._caption_of(element)})

    def _cell(self, cell) -> Block:
        self._pending_dom_id = cell.get("id")
        self._pending_element = cell
        return self.make(PARAGRAPH, text=text_of(cell) or None,
                         images=self._images_of(cell.iter("img")),
                         attributes={"element": cell.tag.lower()})

    @staticmethod
    def _caption_of(element) -> str | None:
        for child in element:
            if isinstance(getattr(child, "tag", None), str) and \
                    child.tag.lower() == "caption":
                return text_of(child) or None
        return None

    def _figure(self, element) -> None:
        images = self._images_of(element.iter("img"))
        caption = ""
        for child in element:
            if isinstance(getattr(child, "tag", None), str) and \
                    child.tag.lower() == "figcaption":
                caption = text_of(child)
        self.emit(element, FIGURE, images=images, text=caption or None,
                  attributes={"caption": caption or None})

    def _figure_images(self, element, images) -> None:
        self.emit(element, FIGURE, images=tuple(images))

    # -- images ----------------------------------------------------------

    def _images_of(self, img_elements) -> tuple[Image, ...]:
        """Resolve `<img src>` to zip members, with coalesced dimensions."""
        images: list[Image] = []
        for img in img_elements:
            src = (img.get("src") or img.get("{http://www.w3.org/1999/xlink}href")
                   or img.get("xlink:href") or img.get("href"))
            if not src:
                continue
            path = normalize_href(src, self.base)
            if not path or not self.book.has(path):
                continue  # external or dangling: not a member of this archive
            width, height = resolve_dimensions(
                self.book, path, img.get("width"), img.get("height")
            )
            images.append(Image(path=path, media_type=self._media_type_for(path),
                                alt=img.get("alt"), width=width, height=height,
                                _book=self.book))
        return tuple(images)

    def _svg_images(self, element) -> tuple[Image, ...]:
        """Images embedded in SVG, as `<image xlink:href="...">`.

        Real books wrap a cover in an SVG with a `viewBox` and an `<image>`
        child rather than using `<img>`; those references are still images in
        the archive and are reported as such.
        """
        found = []
        for node in element.iter():
            tag = getattr(node, "tag", None)
            if not isinstance(tag, str) or tag.rsplit("}", 1)[-1] != "image":
                continue
            # lxml's HTML parser leaves `xlink:href` as a literal prefixed name
            # rather than expanding it to a Clark-notation attribute, while an
            # XML parse expands it. Accept whichever form arrived.
            src = (node.get("{http://www.w3.org/1999/xlink}href")
                   or node.get("xlink:href") or node.get("href"))
            if not src:
                continue
            path = normalize_href(src, self.base)
            if not path or not self.book.has(path):
                continue
            width, height = resolve_dimensions(
                self.book, path, node.get("width"), node.get("height")
            )
            found.append(Image(path=path, media_type=self._media_type_for(path),
                               width=width, height=height, _book=self.book))
        return tuple(found)

    def _media_type_for(self, path: str) -> str | None:
        for entry in self.book.manifest.values():
            if entry["href"] == path:
                return entry["media_type"]
        suffix = path.rsplit(".", 1)[-1].lower()
        return MEDIA_TYPES_BY_SUFFIX.get(suffix)


# Calibre and the publishers that copy it mark body text with a class rather than
# with `<p>`: `p-indent`, `p-br`, `p-blanc`. A div wearing one of these is a
# paragraph, not structure.
#
# Only the explicit convention is honoured. A "does this div merely hold text?"
# heuristic looks tempting and is wrong: it swallows `<body>` and `<section>`,
# collapsing a whole document into a single block.
PARAGRAPH_DIV_CLASS = re.compile(r"(^|\s)p-(indent|br|blanc|continuance)\b")


def _is_paragraph_div(element) -> bool:
    """True when a `<div>` is a Calibre-style paragraph rather than a container."""
    return bool(PARAGRAPH_DIV_CLASS.search(element.get("class") or ""))


# Tags that make an element a block in its own right. A wrapper containing any
# of these is structure, not a paragraph, however much text it also holds.
BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "dd", "div", "dl", "dt",
    "figure", "figcaption", "footer", "form", "h1", "h2", "h3", "h4", "h5",
    "h6", "header", "hr", "img", "li", "math", "nav", "ol", "p", "pre",
    "section", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
})


def _holds_bare_text(element) -> bool:
    """True when a wrapper's own prose is the content, with no block inside.

    Chinese novels in the corpus put whole pages of text directly in a `<div>`,
    separated by `<br/>`, with no `<p>` anywhere. Recursing into such an element
    finds nothing, and the text is silently lost — 130+ books that a reader can
    plainly read came out empty.

    Two details make this harder than it looks:

    - The prose lives in the *tails* between the `<br/>` elements, not in the
      element's own `.text`, which is usually just indentation whitespace.
    - The wrapper may also hold a heading, so "contains a block" is not by
      itself disqualifying. What disqualifies it is a block that *contains the
      prose* — a `<p>` or `<div>` means the text is already someone's job.
      A heading followed by loose text is a page, and the loose text is the
      part that would otherwise be lost.
    """
    own = (element.text or "").strip()
    tails = "".join(
        (child.tail or "")
        for child in element
        if isinstance(getattr(child, "tag", None), str)
        and child.tag.lower() == "br"
    ).strip()
    if not (own or tails):
        return False
    for child in element:
        if not isinstance(getattr(child, "tag", None), str):
            continue
        tag = child.tag.lower()
        if tag in HEADING_TAGS or tag in ("br", "hr", "img", "span", "a", "em",
                                          "strong", "b", "i", "sup", "sub"):
            continue  # inline or heading: not a container for the prose
        if tag in BLOCK_TAGS:
            return False  # a real block inside: the text is already handled
    return True


def _header_rows(rows) -> int:
    """Count the leading rows made entirely of `<th>` cells."""
    count = 0
    for row in rows:
        if all(cell.attributes.get("element") == "th" for cell in row):
            count += 1
        else:
            break
    return count


def _math_attrs(element) -> dict:
    """Preserve the MathML structure as an attribute payload.

    Math has 0 occurrences across the 506-book corpus, so this path is
    spec-derived and exercised only by hand-written fixtures: specification-
    correct, empirically unvalidated until a real math EPUB appears.
    """
    def walk(node):
        tag = node.tag if isinstance(node.tag, str) else None
        if tag is None:
            return None
        return {
            "tag": tag.rsplit("}", 1)[-1],
            "text": (node.text or "").strip() or None,
            "children": [c for c in (walk(child) for child in node) if c],
        }

    return {"mathml": walk(element)}


def parse_document(book, chapter_index: int, href: str) -> tuple[Block, ...]:
    """Parse one content document into its ordered block tuple."""
    root = book.read_parsed(href)
    if root is None:
        return ()
    builder = _Builder(book, chapter_index, href)
    builder.walk(root)
    _claim_stray_ids(builder, root)
    builder._resolve_footnotes(builder.blocks)
    return tuple(builder.blocks)


def _claim_stray_ids(builder, root) -> None:
    """Point every remaining element `id` at the block that contains it.

    Publishers hang ids on wrappers rather than on the text they wrap: a note
    id on `<div epub:type="footnote">`, a backlink id on a bare `<span>`. Those
    are still link targets, so each unclaimed id is attributed to the nearest
    enclosing block. Without this, a book's endnotes parse but never resolve.

    An id must resolve to exactly one block, and the block that owns the
    *nearest enclosing element* is the right one: a `<div id="fn_5">` inside a
    list `<li>` should point at that li's block, not at the enclosing `<ol>`.
    Ownership is decided by the DOM, by finding the closest ancestor-or-self
    that a block was emitted from.
    """
    for node in root.iter():
        if not isinstance(getattr(node, "tag", None), str):
            continue
        dom_id = node.get("id")
        if not dom_id:
            continue
        block = _owning_block(builder, node)
        if block is None:
            continue
        builder._by_dom_id[dom_id] = block.id
        _attach_dom_id(builder, block, dom_id)


def _attach_dom_id(builder, block, dom_id: str) -> None:
    known = block.attributes.get("dom_ids", ())
    if dom_id not in known:
        block.attributes["dom_ids"] = known + (dom_id,)
    for other in builder._all_blocks(builder.blocks):
        if other is block:
            continue
        stale = other.attributes.get("dom_ids", ())
        if dom_id in stale:
            other.attributes["dom_ids"] = tuple(d for d in stale if d != dom_id)


def _owning_block(builder, node):
    """The block that owns the element carrying `id`, or None.

    Ownership is the DOM's own answer, not a guess. A block was emitted *from*
    one element and owns that element, so ownership resolves in three steps,
    most specific first:

    1. the element itself, if a block was emitted from it;
    2. a block emitted from one of its *descendants* — a `<div id="fn">` that
       merely wraps a `<p>` owns that paragraph's block;
    3. the nearest ancestor-or-self that emitted a block.

    Step 3 alone leaves ids unresolved whenever a publisher wraps content in
    bare `<div>`s: a real book's 40 endnotes all sat on a `<div>` whose only
    child was the paragraph holding the note's text.
    """
    emitted = dict((id(element), block) for element, block in builder._emitted)
    block = emitted.get(id(node))
    if block is not None:
        return block
    for descendant in node.iterdescendants():
        block = emitted.get(id(descendant))
        if block is not None:
            return block
    for candidate in node.iterancestors():
        block = emitted.get(id(candidate))
        if block is not None:
            return block
    return None
