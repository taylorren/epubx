"""epubx — open EPUB files and expose them as a lazily-parsed node graph.

    from epubx import open_book

    book = open_book("path.epub")
    book.metadata, book.chapters, book.toc

Phase I: parse, don't render; structure, not strings; nothing persisted.
"""

from .model import (
    Block,
    Chapter,
    Creator,
    Footnote,
    Image,
    Metadata,
    Resource,
    TocNode,
)
from .package import Book, EpubError, open_book

__version__ = "0.2.1"

__all__ = [
    "open_book",
    "Book",
    "EpubError",
    "Block",
    "Chapter",
    "Creator",
    "Footnote",
    "Image",
    "Metadata",
    "Resource",
    "TocNode",
    "__version__",
]
