"""Navigation: EPUB 3 nav and EPUB 2 NCX are peer producers of one model.

There is no version dispatch here. We look for a `nav` element that declares
`epub:type="toc"`, and fall back to NCX, and finally to spine order. Which one
wins is a function of what is present, not of what the OPF declares.
"""

from __future__ import annotations

from .hrefs import normalize_href, split_fragment
from .model import TocNode
from .xmlutil import epub_type, local_name, parse_xml, text_of

NCX_MEDIA_TYPE = "application/x-dtbncx+xml"


def _manifest_entry(book, predicate) -> dict | None:
    for entry in book.manifest.values():
        if predicate(entry):
            return entry
    return None


def _toc_from_nav(book, root, base: str = "") -> tuple[TocNode, ...]:
    """Walk the nav element's ol/li tree."""
    nav = None
    for element in root.iter():
        if local_name(element) != "nav":
            continue
        types = set(epub_type(element))
        if "toc" in types or (not types and nav is None):
            nav = element
            if "toc" in types:
                break
    if nav is None:
        return ()

    ol = _first(nav, "ol")
    if ol is None:
        return ()
    return _walk_ol(book, ol, base)


def _first(parent, name):
    for child in parent:
        if local_name(child) == name:
            return child
    return None


def _walk_ol(book, ol, base: str = "") -> tuple[TocNode, ...]:
    nodes: list[TocNode] = []
    for li in ol:
        if local_name(li) != "li":
            continue
        anchor = _first(li, "a")
        if anchor is None:
            anchor = _first(li, "span")
        label = text_of(anchor) if anchor is not None else ""
        href = None
        anchor_id = None
        if anchor is not None and anchor.get("href"):
            raw, anchor_id = split_fragment(anchor.get("href"))
            href = normalize_href(raw, base) or None

        child_ol = _first(li, "ol")
        children = _walk_ol(book, child_ol, base) if child_ol is not None else ()
        nodes.append(TocNode(label=label, href=href, children=children, anchor=anchor_id))
    return tuple(nodes)


def _toc_from_ncx(book, root, base: str = "") -> tuple[TocNode, ...]:
    nav_map = None
    for element in root.iter():
        if local_name(element) == "navmap":
            nav_map = element
            break
    if nav_map is None:
        return ()
    return _walk_navpoints(book, nav_map, base)


def _walk_navpoints(book, parent, base: str) -> tuple[TocNode, ...]:
    nodes: list[TocNode] = []
    for point in parent:
        if local_name(point) != "navpoint":
            continue
        label = ""
        src = None
        anchor = None
        for child in point:
            name = local_name(child)
            if name == "navlabel":
                text_el = _first(child, "text")
                label = text_of(text_el) if text_el is not None else ""
            elif name == "content":
                raw, anchor = split_fragment(child.get("src") or "")
                src = normalize_href(raw, base) or None
        nodes.append(
            TocNode(
                label=label,
                href=src,
                children=_walk_navpoints(book, point, base),
                anchor=anchor,
            )
        )
    return tuple(nodes)


def _toc_from_spine(book) -> tuple[TocNode, ...]:
    nodes = []
    for chapter in book.chapters:
        label = chapter.href.rsplit("/", 1)[-1]
        nodes.append(TocNode(label=label, href=chapter.href))
    return tuple(nodes)


def build_toc(book, base: str = "") -> tuple[TocNode, ...]:
    """nav if present, else NCX if present, else spine order.

    `base` is the zip-relative path of the navigation document: its own relative
    hrefs resolve against its directory, not the OPF's.
    """
    # EPUB 3: a manifest item with `properties="nav"`.
    nav_entry = _manifest_entry(
        book, lambda e: "nav" in e["properties"]
    ) or _manifest_entry(
        book, lambda e: (e["media_type"] or "").startswith("application/xhtml")
        and e["href"].endswith("nav.xhtml")
    )
    if nav_entry:
        root = book.read_parsed(nav_entry["href"])
        if root is not None:
            toc = _toc_from_nav(book, root, nav_entry["href"])
            if toc:
                return toc

    # EPUB 2: NCX. Present in 299/300 books, so it is the primary source.
    ncx_entry = _manifest_entry(book, lambda e: e["media_type"] == NCX_MEDIA_TYPE)
    if ncx_entry is None:
        ncx_entry = _manifest_entry(book, lambda e: e["href"].endswith(".ncx"))
    if ncx_entry:
        try:
            data = book.member(ncx_entry["href"])
        except KeyError:
            data = None
        if data is not None:
            root = parse_xml(data)
            if root is not None:
                toc = _toc_from_ncx(book, root, ncx_entry["href"])
                if toc:
                    return toc

    # Missing nav is normal (95% of books in distribution terms); spine order
    # is a faithful, if flat, table of contents.
    return _toc_from_spine(book)
