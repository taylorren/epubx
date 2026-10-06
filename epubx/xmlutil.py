"""Reading a zip member as parsed XML or HTML.

Two rules from the robustness table live here:

* 25% of the corpus puts a BOM before `<?xml`; lxml rejects those outright, so
  parsing always uses `recover=True`.
* We trust structure over declared version.
"""

from __future__ import annotations

import re

from lxml import etree, html

_XML_PARSER = etree.XMLParser(recover=True, resolve_entities=False, huge_tree=False)


def _strip_bom(data: bytes) -> bytes:
    return data[3:] if data.startswith(b"\xef\xbb\xbf") else data


def _charset(data: bytes) -> str | None:
    """The encoding a document declares, from a BOM or a meta charset.

    Content documents are XML, so UTF-8 or UTF-16 is overwhelmingly the norm,
    but a real book shipped an XHTML file with no declaration whatsoever — and
    lxml's HTML parser then reads it as latin-1, turning "What's" into
    "Whatâ\x80\x99s".
    """
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    head = data[:2048].lower()
    match = re.search(rb'charset\s*=\s*["\']?\s*([a-z0-9_.:+-]+)', head)
    if match:
        try:
            return match.group(1).decode("ascii").strip().lower() or None
        except UnicodeDecodeError:
            return None
    return None


def _decoded(data: bytes) -> bytes:
    """Normalise a document to UTF-8 bytes before handing it to lxml.

    lxml's HTML parser falls back to latin-1 when it cannot see a charset, which
    turns "What's" into "Whatâ\\x80\\x99s" — the failure seen on a real book's
    `next-reads.xhtml`, which declared nothing at all.

    So: honour a declaration when there is one, and when there is not, trust
    UTF-8 if the bytes actually decode as UTF-8. Content documents are XML,
    where UTF-8 is the default; genuine latin-1 bytes fail the strict decode
    and are left alone.
    """
    encoding = _charset(data)
    if encoding is None:
        # Undeclared: XHTML is XML, so UTF-8 unless the bytes disagree.
        try:
            return data.decode("utf-8").encode("utf-8")
        except UnicodeDecodeError:
            return data
    if encoding.replace("_", "-") in ("utf-8", "utf8", "ascii", "us-ascii"):
        return data
    try:
        return data.decode(encoding).encode("utf-8")
    except (LookupError, UnicodeDecodeError, ValueError):
        return data  # an encoding we cannot honour: parse the bytes as-is


def parse_xml(data: bytes) -> etree._Element | None:
    """Parse XML leniently. Returns None if nothing usable survives."""
    data = _strip_bom(data)
    try:
        root = etree.fromstring(data, parser=_XML_PARSER)
    except etree.XMLSyntaxError:
        return None
    return root


def parse_html(data: bytes) -> html.HtmlElement | None:
    """Parse an (X)HTML content document leniently, honouring its encoding.

    `encoding` must be passed explicitly: lxml's HTML parser otherwise sniffs
    the bytes itself and falls back to latin-1, which is how a valid UTF-8
    document came out as "Whatâ\x80\x99s". Feeding it a *declared* UTF-8 wins.
    """
    data = _decoded(_strip_bom(data))
    try:
        parser = html.HTMLParser(recover=True, encoding="utf-8")
        return html.document_fromstring(data, parser=parser)
    except (etree.XMLSyntaxError, ValueError):
        return None


def text_of(element) -> str:
    """Collapsed text content, with `<script>`/`<style>` text excluded."""
    if element is None:
        return ""
    parts: list[str] = []

    def walk(node, is_root: bool = False) -> None:
        if not isinstance(getattr(node, "tag", None), str):
            # Comment / PI: only its tail is real text.
            if not is_root and node.tail:
                parts.append(node.tail)
            return
        if not is_root and node.tag == "br":
            parts.append("\n")
        if node.text:
            parts.append(node.text)
        # "Parse, don't judge": script/style are reported, not rendered, so
        # their text is excluded here rather than stripped from the tree.
        if node.tag not in ("script", "style"):
            for child in node:
                walk(child)
        if node.tail:
            parts.append(node.tail)

    walk(element, is_root=True)
    return " ".join("".join(parts).split())


def local_name(element) -> str:
    tag = getattr(element, "tag", None)
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def epub_type(element) -> list[str]:
    """The `epub:type` refinements on an element, lowercased."""
    raw = element.get("epub:type") or element.get("{http://www.idpf.org/2007/ops}type")
    if not raw:
        return []
    return [t.lower() for t in raw.split() if t]
