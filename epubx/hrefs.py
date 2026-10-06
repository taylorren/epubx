"""Href normalisation.

Corpus hrefs are percent-encoded and/or contain `../` segments; both must be
resolved to a zip-relative path. Per the robustness rules we normalise rather
than reject.
"""

from __future__ import annotations

import posixpath
from urllib.parse import unquote, urldefrag, urlsplit


def normalize_href(href: str, base: str = "") -> str:
    """Resolve `href` against zip-relative `base`, returning a clean path.

    Fragments are stripped; `#frag` is returned separately by `split_fragment`.
    Backslashes are treated as separators, since some publishers emit them.
    """
    href = (href or "").strip().replace("\\", "/")
    href, _ = urldefrag(href)
    if not href:
        return ""
    if urlsplit(href).scheme:  # absolute URL: external, not a zip member
        return ""
    if href.startswith("/"):
        return posixpath.normpath(href.lstrip("/"))
    if base:
        href = posixpath.join(posixpath.dirname(base), href)
    return posixpath.normpath(unquote(href))


def split_fragment(href: str) -> tuple[str, str | None]:
    """Split `href` into (path, fragment) without resolving it."""
    path, frag = urldefrag((href or "").strip())
    return path, (unquote(frag) if frag else None)
