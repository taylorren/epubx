"""Opening an EPUB: central directory + OPF, nothing more.

`open_book()` deliberately touches only the zip central directory, the OCF
container document and the OPF package document. Chapter documents are read on
access, which is what keeps open() under the 50ms target and makes the archive
never needing to be extracted to disk.
"""

from __future__ import annotations

import os
import re
import zipfile
from dataclasses import replace
from typing import Iterator
from urllib.parse import unquote

from .hrefs import normalize_href
from .model import Chapter, Creator, Image, Metadata, Resource, TocNode
from .xmlutil import local_name, parse_xml

CONTAINER_PATH = "META-INF/container.xml"
ENCRYPTION_PATH = "META-INF/encryption.xml"
OPF_NS = "http://www.idpf.org/2007/opf"
IMAGE_SUFFIXES = frozenset(
    {"jpg", "jpeg", "jpe", "png", "gif", "svg", "webp", "tif", "tiff", "bmp"}
)
# Content types for serving the book to a renderer. The OPF's declared
# `media-type` wins wherever it has one; this covers the files it does not name
# (and the handful of publishers who omit it). Anything still unknown is served
# as `application/octet-stream` rather than guessed at.
MEDIA_TYPES_BY_SUFFIX = {
    "xhtml": "application/xhtml+xml",
    "html": "text/html",
    "htm": "text/html",
    "css": "text/css",
    "js": "text/javascript",
    "opf": "application/oebps-package+xml",
    "ncx": "application/x-dtbncx+xml",
    "xml": "application/xml",
    "txt": "text/plain",
    "json": "application/json",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "jpe": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "svg": "image/svg+xml",
    "webp": "image/webp",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "bmp": "image/bmp",
    "ttf": "font/ttf",
    "otf": "font/otf",
    "ttc": "font/collection",
    "woff": "font/woff",
    "woff2": "font/woff2",
}
# Matches id="..." / id='...' in a raw content document.
_ID_ATTR = re.compile(rb"""\bid\s*=\s*["']([^"']+)["']""")

# DRM we can name and decline. Never attempted, always a named reason.
#
# `encryption.xml` on its own is NOT evidence of DRM. EPUB font obfuscation
# XOR-encodes font files to discourage extraction, and plenty of otherwise
# perfectly readable books do it — one in this corpus scrambles a single
# `00001.ttf` and leaves every word of text in the clear. Claiming DRM there
# refuses books that need no refusal, and asserts a vendor the evidence does not
# name: Adobe ADEPT uses a different scheme entirely.
#
# So the file's contents decide. An `EncryptedKey` means a licence is in play;
# encryption covering anything other than fonts means the payload is protected.
# Both are named. Fonts alone are recorded, not treated as a blocker.
FONT_SUFFIXES = (".ttf", ".otf", ".woff", ".woff2", ".eot", ".ttc", ".svg")

# Declared rather than guessed: the algorithm alone does not identify a vendor,
# and naming one we cannot see would be a claim we cannot support.
DRM_REASONS = {
    "META-INF/rights.xml": "drm:rights.xml present (Readium LCP licence)",
}


class EpubError(Exception):
    """Raised only for structurally unusable containers."""


# A book whose sampled chapters carry essentially no text but do carry images
# is a scan or a manga: pages delivered as pictures. Thresholds are deliberately
# generous so that a sparse but genuine text book is never mistaken for one.
IMAGE_ONLY_TEXT_CHARS = 200
# Chapters examined before giving up on finding prose. A bound, not a sample:
# the scan walks in order and stops at the first chapter with real text.
IMAGE_ONLY_SCAN_LIMIT = 200


class Book:
    """An opened EPUB. Metadata, spine and TOC are ready; content is lazy."""

    def __init__(self, path: str, zf: zipfile.ZipFile, opf_path: str):
        self.path = os.fspath(path)
        self._zf = zf
        self._opf_path = opf_path
        # DRM is decidable from the archive alone, so it is named eagerly.
        # Image-only books need their chapters parsed, so that is decided lazily
        # by the `unsupported` property to keep open() fast.
        self._unsupported: str | None = None
        self._checked_image_only = False
        # Font files scrambled to discourage extraction; not DRM, and the book
        # still reads. Empty when no such obfuscation is present.
        self.obfuscated_fonts: tuple[str, ...] = ()

        self._manifest: dict[str, dict] = {}
        self._spine: list[Chapter] = []
        self._toc: tuple[TocNode, ...] | None = None
        self.metadata = Metadata()
        self._cover_id: str | None = None
        self._id_index: dict[str, str] | None = None
        self._guide_cover: str | None = None
        self._parse_package()

    # -- container -------------------------------------------------------

    @property
    def opf_dir(self) -> str:
        return os.path.dirname(self._opf_path)

    def member(self, path: str) -> bytes:
        """Raw bytes of a zip member. Raises KeyError if absent."""
        return self._zf.read(path)

    def has(self, path: str) -> bool:
        return path in self._zf.namelist()

    def read_parsed(self, path: str):
        """Read a member and parse it leniently as XML/HTML."""
        from .xmlutil import parse_html, parse_xml

        try:
            data = self._zf.read(path)
        except KeyError:
            return None
        # Content documents are XHTML; parse as HTML so that unclosed tags and
        # bare `&` do not cost us the whole chapter.
        root = parse_html(data)
        return root if root is not None else parse_xml(data)

    # -- package document ------------------------------------------------

    def _parse_package(self) -> None:
        opf = parse_xml(self.member(self._opf_path))
        if opf is None:
            raise EpubError(f"unparseable OPF: {self._opf_path}")

        self._check_drm()

        metadata = None
        for child in opf:
            if local_name(child) == "metadata":
                metadata = child
                break
        self.metadata = self._parse_metadata(metadata)

        for child in opf:
            name = local_name(child)
            if name == "manifest":
                self._parse_manifest(child)
            elif name == "spine":
                self._parse_spine(child)
            elif name == "guide":
                self._parse_guide(child)

    def _parse_guide(self, guide) -> None:
        """Record the guide's `cover` reference, if it has one."""
        for reference in guide:
            if local_name(reference) != "reference":
                continue
            types = (reference.get("type") or "").lower()
            if "cover" in types and reference.get("href"):
                self._guide_cover = reference.get("href")

    # -- deferred cases --------------------------------------------------

    @property
    def unsupported(self) -> str | None:
        """A named reason this book cannot be represented, else None.

        SPEC.md requires deferred cases to be *named* rather than raised:
        DRM, image-only/manga books, vertical CJK. Deciding the image-only case
        requires parsing chapters, so it happens on first access rather than in
        `open()`, which must stay under 50ms. The result is memoised, and a DRM
        reason always wins because it needs no parsing at all.
        """
        if self._unsupported is None and not self._checked_image_only:
            self._checked_image_only = True
            reason = self._detect_image_only()
            if reason is not None:
                self._unsupported = reason
        return self._unsupported

    def _detect_image_only(self) -> str | None:
        """Name the image-only case, or None when the book carries real text.

        Chapters are walked *in order* and the scan stops at the first one with
        real prose, so a text book costs only as much as it takes to find its
        first page of text.

        Sampling at a fixed stride was tried first and is wrong: in a real book
        of 1,690 chapters, only 51 held prose and the rest were page images, so
        every stride landed on an image and the book was misreported as a scan.
        Books interleave text and images unpredictably, and a verdict that
        condemns a readable book is far worse than one that misses a rare scan —
        so the test is "is there any real text anywhere", answered by scanning.

        Text is counted per block, never via `plain_text`: that folds in image
        alt text, and a scan's alt text can be an entire title page.
        """
        chapters = self._spine
        if not chapters:
            return None

        images = 0
        chars = 0
        examined = 0
        for chapter in chapters:
            examined += 1
            chapter_chars = 0
            chapter_images = 0
            for block in chapter.blocks:
                chapter_chars += len(block.text or "")
                chapter_images += len(block.images)
            chars += chapter_chars
            images += chapter_images
            if chapter_chars > IMAGE_ONLY_TEXT_CHARS:
                # Real prose, so this is a text book with illustrations.
                return None
            if examined >= IMAGE_ONLY_SCAN_LIMIT:
                break

        if not images:
            # No images at all: an empty or unreadable book, not a scan. Naming
            # it image-only would send a consumer looking for OCR that cannot help.
            return None
        return (
            f"image-only: {images} images across {examined} chapters, "
            f"{chars} characters of text — scanned pages or manga, "
            f"no extractable text content"
        )

    def _check_drm(self) -> None:
        """Name DRM from the evidence, and do not mistake fonts for it.

        A mere `encryption.xml` is not DRM. Font obfuscation scrambles font
        files and leaves the text readable, so treating its presence as DRM
        refuses books that parse perfectly well.
        """
        for marker, reason in DRM_REASONS.items():
            if self.has(marker):
                self._unsupported = reason
                return

        if not self.has(ENCRYPTION_PATH):
            return
        try:
            raw = self.member(ENCRYPTION_PATH)
        except KeyError:
            return

        root = parse_xml(raw)
        if root is None:
            return
        if any(local_name(node) == "encryptedkey" for node in root.iter()):
            self._unsupported = (
                "drm:encryption.xml carries an EncryptedKey — the content is "
                "licence-protected and cannot be read without the key"
            )
            return

        targets = [
            node.get("URI") for node in root.iter()
            if local_name(node) == "cipherreference" and node.get("URI")
        ]
        if not targets:
            return
        non_fonts = [u for u in targets if not u.lower().endswith(FONT_SUFFIXES)]
        if non_fonts:
            self._unsupported = (
                f"drm:encryption.xml protects {len(non_fonts)} non-font "
                f"resources (e.g. {non_fonts[0]}) — content is encrypted"
            )
            return
        # Fonts only: recorded, but the book is perfectly readable.
        self.obfuscated_fonts = tuple(targets)

    @staticmethod
    def _opf_attr(element, name: str) -> str | None:
        """Read an OPF-namespaced attribute (`opf:file-as`), or a bare one."""
        return element.get(f"{{{OPF_NS}}}{name}") or element.get(name)

    def _refine_id(self, element) -> str | None:
        """The id a `<meta refines>` points at, from `id` or `opf:file-as`.

        Refinements are matched by id, not by position: `refines="#c02"` means
        the creator whose element carries `id="c02"`.
        """
        return element.get("id") or self._opf_attr(element, "file-as")

    def _parse_manifest(self, manifest) -> None:
        for item in manifest:
            if local_name(item) != "item":
                continue
            item_id = item.get("id")
            href = item.get("href")
            if not item_id or not href:
                continue
            self._manifest[item_id] = {
                "href": normalize_href(href, self._opf_path),
                "raw_href": href,
                "media_type": item.get("media-type"),
                "properties": (item.get("properties") or "").split(),
            }

    def _parse_spine(self, spine) -> None:
        """Spine order defines chapter order; `linear="no"` is kept, not judged."""
        for pos, ref in enumerate(spine):
            if local_name(ref) != "itemref":
                continue
            entry = self._manifest.get(ref.get("idref") or "")
            if entry is None:
                continue  # dangling idref: skip, never raise
            self._spine.append(
                Chapter(
                    index=pos,
                    href=entry["href"],
                    media_type=entry["media_type"],
                    _book=self,
                )
            )

    def _parse_metadata(self, metadata) -> Metadata:
        if metadata is None:
            return Metadata()

        fields: dict[str, str] = {}
        creators: list[Creator] = []
        role_refines: dict[str, str] = {}
        ns = "{http://purl.org/dc/elements/1.1/}"

        for child in metadata:
            tag = child.tag
            if not isinstance(tag, str):
                continue
            name = local_name(child)
            text = "".join(child.itertext()).strip()

            if name == "meta":
                # `epub:type` refinements (316/506 books) merge into creators.
                prop = child.get("property") or child.get("name") or ""
                if prop == "cover" and child.get("content"):
                    self._cover_id = child.get("content")
                refines = (child.get("refines") or "").lstrip("#")
                if refines and text and (
                    prop == "role" or prop.startswith("role:")
                ):
                    role_refines[refines] = text.lower()
                continue

            if name == "contributor":
                if text:
                    creators.append(Creator(name=text, role="contributor",
                                            refines=self._refine_id(child)))
            elif name == "creator" and tag.startswith(ns) and text:
                creators.append(Creator(name=text, file_as=self._opf_attr(child, "file-as"),
                                        refines=self._refine_id(child)))
            elif name in (
                "title",
                "language",
                "identifier",
                "publisher",
                "date",
                "description",
            ) and text and name not in fields:
                fields[name] = text

        # A role refinement names the element it refines by id, so match on that
        # id rather than on position.
        creators = tuple(
            c if c.role != "aut" or c.refines not in role_refines
            else replace(c, role=role_refines[c.refines])
            for c in creators
        )

        return Metadata(
            title=fields.get("title"),
            creators=tuple(creators),
            language=fields.get("language"),
            identifier=fields.get("identifier"),
            publisher=fields.get("publisher"),
            date=fields.get("date"),
            description=fields.get("description"),
        )

    # -- public surface --------------------------------------------------

    @property
    def manifest(self) -> dict[str, dict]:
        return dict(self._manifest)

    @property
    def chapters(self) -> tuple[Chapter, ...]:
        """Lazy chapters in spine order. The tuple itself is eager; content is not."""
        return tuple(self._spine)

    # -- serving ---------------------------------------------------------
    #
    # A renderer needs the book's own files, not a second model of them. These
    # hand over the bytes at their own paths so that the book's relative links
    # — stylesheets, fonts, images, footnotes — resolve in a browser with
    # nothing rewritten.

    @property
    def spine(self) -> tuple[str, ...]:
        """The reading order: content document paths, exactly as the OPF lists them."""
        return tuple(chapter.href for chapter in self._spine)

    def resource(self, path: str) -> Resource | None:
        """One file of the book, or None when the archive has no such member.

        `path` is the zip-relative path the book's own links use. A request URL
        is normalised rather than reported missing: a leading `/`, `./`,
        backslashes and percent-encoding are all resolved first, so
        `/OEBPS/img/plate%20one.png` finds `OEBPS/img/plate one.png`.
        """
        # `normalize_href` decodes relative hrefs but leaves an absolute
        # in-book path (`/OEBPS/...`) encoded, so decode once more here.
        name = unquote(normalize_href(path))
        if not name or not self.has(name):
            return None
        return Resource(path=name, media_type=self._media_type_of(name), _book=self)

    def resources(self) -> Iterator[Resource]:
        """Every file a renderer may need, in archive order.

        The container's own plumbing — `mimetype` and `META-INF/` — is left
        out: no content document references it, so serving it is noise. Bytes
        are not read here; `Resource.read()` fetches them on demand.
        """
        for name in self._zf.namelist():
            if name.endswith("/") or name == "mimetype" or name.startswith("META-INF/"):
                continue
            yield Resource(path=name, media_type=self._media_type_of(name), _book=self)

    def _media_type_of(self, path: str) -> str:
        """The OPF's declared media type where it has one, else the suffix."""
        for entry in self._manifest.values():
            if entry["href"] == path and entry["media_type"]:
                return entry["media_type"]
        suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        return MEDIA_TYPES_BY_SUFFIX.get(suffix, "application/octet-stream")

    def find_element_id(self, fragment: str) -> str | None:
        """Every content document that declares `id="fragment"`.

        Some publishers emit endnote references as a bare `#fn_5`, naming no
        document at all — the target lives in a per-chapter note file that is
        not even in the spine. Nothing in the href says where, so the archive
        itself is searched once and cached.

        Ids are *not* unique across a book: `#fn_1` recurs in every chapter, so
        this returns all candidates and the caller picks the sensible one.
        Built by scanning raw bytes, so no document is parsed to answer.
        """
        if self._id_index is None:
            index: dict[str, list[str]] = {}
            for entry in self._manifest.values():
                if not (entry["media_type"] or "").startswith("application/xhtml"):
                    continue
                try:
                    raw = self.member(entry["href"])
                except KeyError:
                    continue
                for match in _ID_ATTR.finditer(raw):
                    key = match.group(1).decode("utf-8", "replace")
                    index.setdefault(key, []).append(entry["href"])
            self._id_index = index
        return self._id_index.get(fragment)

    def chapter_for(self, href: str, fragment: str | None = None,
                    base: str | None = None) -> Chapter | None:
        """Find the spine chapter a link points at, or None."""
        target = normalize_href(href, base or self._opf_path)
        for chapter in self._spine:
            if chapter.href == target:
                return chapter
        return None

    @property
    def toc(self) -> tuple[TocNode, ...]:
        """nav if present, else NCX if present, else spine order.

        Detection is by feature present, never by declared version.
        """
        if self._toc is None:
            from .nav import build_toc

            self._toc = build_toc(self)
        return self._toc

    @property
    def cover(self) -> Image | None:
        """The cover image, or None.

        Three conventions coexist and all are accepted: EPUB 3's
        `properties="cover-image"`, an EPUB 2 `guide` reference of type `cover`
        (255 books), and `<meta name="cover">` naming a manifest item (251).

        A `guide` cover reference often points at a *document* rather than an
        image — a title page, say — so a candidate only wins if it is actually
        an image. Otherwise a book whose guide names `titlepage.xhtml` would
        report that page as its cover while ignoring a real `cover.jpeg` named
        by the meta element.
        """
        for entry in self._cover_candidates():
            if entry["href"] and self._is_image(entry) and self.has(entry["href"]):
                return Image(path=entry["href"], media_type=entry["media_type"],
                             _book=self)
        return None

    def _cover_candidates(self):
        """Manifest entries that might be the cover, most authoritative first."""
        cover_image = next(
            (e for e in self._manifest.values() if "cover-image" in e["properties"]),
            None,
        )
        if cover_image is not None:
            yield cover_image
        if self._guide_cover:
            target = normalize_href(self._guide_cover, self._opf_path)
            entry = next(
                (e for e in self._manifest.values() if e["href"] == target), None
            )
            if entry is not None:
                yield entry
        if self._cover_id:
            entry = self._manifest.get(self._cover_id)
            if entry is not None:
                yield entry

    @staticmethod
    def _is_image(entry) -> bool:
        """True when the manifest declares this item to be an image."""
        media_type = (entry.get("media_type") or "").lower()
        if media_type:
            return media_type.startswith("image/")
        # No declared type: fall back to the filename, since some publishers
        # omit media-type on image items.
        return entry["href"].rsplit(".", 1)[-1].lower() in IMAGE_SUFFIXES

    def iter_chapters(self) -> Iterator[Chapter]:
        return iter(self._spine)

    def close(self) -> None:
        self._zf.close()

    def __enter__(self) -> "Book":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __repr__(self) -> str:  # pragma: no cover - convenience
        return f"<Book {os.path.basename(self.path)!r} chapters={len(self._spine)}>"


def _find_opf(zf: zipfile.ZipFile) -> str:
    """Locate the package document via META-INF/container.xml."""
    try:
        container = parse_xml(zf.read(CONTAINER_PATH))
    except KeyError as exc:
        raise EpubError("not an EPUB: missing META-INF/container.xml") from exc
    if container is None:
        raise EpubError("unparseable META-INF/container.xml")
    for rootfile in container.iter():
        if local_name(rootfile) == "rootfile":
            full_path = rootfile.get("full-path")
            if full_path:
                return normalize_href(full_path)
    raise EpubError("no rootfile in META-INF/container.xml")


def open_book(path) -> Book:
    """Open an EPUB file. Reads the central directory and the OPF only."""
    zf = zipfile.ZipFile(os.fspath(path))
    try:
        opf_path = _find_opf(zf)
        if opf_path not in zf.namelist():
            raise EpubError(f"OPF not found in archive: {opf_path}")
        return Book(path, zf, opf_path)
    except Exception:
        zf.close()
        raise
