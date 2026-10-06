"""Image dimensions, coalesced rather than privileged.

Measured rationale from the spec: only 4.6% of `<img>` tags declare `width`
and 0.4% `height`, and no book in the corpus uses `<meta epub:type="viewport">`
— so intrinsic size is the primary source and the declared value is the
override. Both are reported; the override wins.

Intrinsic reads are header-only: `PIL.Image.open()` without `load()`, and SVG
via its `viewBox`/width/height attributes. Nothing is ever extracted to disk.
"""

from __future__ import annotations

import io
from functools import lru_cache

from .xmlutil import parse_xml

# Intrinsic size lookups are per-archive; the cache is cleared by open_book().
_LRU = 64


def _int(value) -> int | None:
    """Parse a CSS-ish length. Percentages and units are not usable."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text or text.endswith("%"):
        return None
    for unit in ("px", "pt", "em", "rem", "mm", "cm", "in", "pc"):
        if text.endswith(unit):
            text = text[: -len(unit)]
            break
    try:
        n = int(round(float(text)))
    except ValueError:
        return None
    return n if n > 0 else None


def _svg_size(data: bytes) -> tuple[int | None, int | None]:
    root = parse_xml(data)
    if root is None:
        return None, None
    width, height = _int(root.get("width")), _int(root.get("height"))
    if width and height:
        return width, height
    view_box = root.get("viewBox") or root.get("viewbox")
    if not view_box:
        return width, height
    parts = view_box.replace(",", " ").split()
    if len(parts) != 4:
        return width, height
    try:
        return _int(float(parts[2])), _int(float(parts[3]))
    except ValueError:
        return width, height


def _raster_size(data: bytes) -> tuple[int | None, int | None]:
    try:
        from PIL import Image
    except ImportError:  # Pillow is a declared dependency; degrade, don't crash.
        return None, None
    try:
        with Image.open(io.BytesIO(data)) as img:
            return img.width, img.height  # header only: no load()
    except Exception:
        return None, None


@lru_cache(maxsize=_LRU)
def intrinsic_size(path: str, data: bytes) -> tuple[int | None, int | None]:
    """Intrinsic (width, height) of an image, or (None, None)."""
    if path.lower().endswith(".svg") or data[:5].lstrip()[:4] in (b"<svg", b"<?xm"):
        return _svg_size(data)
    return _raster_size(data)


def clear_cache() -> None:
    """Drop memoised dimensions (called when a Book is closed)."""
    intrinsic_size.cache_clear()


def resolve_dimensions(
    book, path: str, declared_width, declared_height
) -> tuple[int | None, int | None]:
    """Coalesce declared and intrinsic dimensions, declared taking precedence."""
    width, height = _int(declared_width), _int(declared_height)
    if width and height:
        return width, height
    try:
        data = book.member(path)
    except KeyError:
        return width, height
    iw, ih = intrinsic_size(path, data)
    return width or iw, height or ih
