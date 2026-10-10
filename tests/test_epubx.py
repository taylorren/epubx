"""Phase I bootstrap tests.

Synthetic fixtures only; the real-book corpus is exercised by
`test_corpus.py`, which skips cleanly when `EPUBX_CORPUS` is unset.
"""

from __future__ import annotations

import sys
import time
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from epubx import open_book  # noqa: E402
from epubx.hrefs import normalize_href  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures import (  # noqa: E402
    write_calibre_epub,
    write_chapter_epub,
    write_epub,
    write_multiblock_note_epub,
    write_mutual_footnote_epub,
    write_scan_epub,
)


@pytest.fixture(scope="module")
def book_path(tmp_path_factory):
    return write_epub(tmp_path_factory.mktemp("epub") / "book.epub")


@pytest.fixture()
def book(book_path):
    with open_book(book_path) as b:
        yield b


# -- opening -------------------------------------------------------------

def test_open_reads_metadata(book):
    m = book.metadata
    assert m.title == "Test Book"
    assert [c.name for c in m.creators] == ["Jane Doe", "Richard Roe"]
    assert m.language == "en"
    assert m.publisher == "Test Press"
    assert m.date == "2021-03-04"
    assert m.identifier == "urn:uuid:0000-test"


def test_epub_type_role_refinements_merge_into_creators(tmp_path):
    """`<meta refines="#c02" property="role">` renames that creator's role."""
    path = write_epub(tmp_path / "roles.epub", roles=True)
    with open_book(path) as b:
        creators = b.metadata.creators
        assert [c.role for c in creators] == ["aut", "edt"]
        assert creators[1].file_as == "Roe, Richard"


def test_absent_role_refinement_leaves_the_default_role(book):
    assert [c.role for c in book.metadata.creators] == ["aut", "aut"]


def test_open_is_lazy_and_quick(book_path):
    """open() reads the central directory and the OPF — nothing else."""
    with open_book(book_path) as b:
        assert len(b.chapters) == 3
        assert b.chapters[0].__dict__.get("blocks") is None  # not yet parsed
    start = time.perf_counter()
    with open_book(book_path):
        pass
    assert (time.perf_counter() - start) < 0.050


def test_spine_order_matches_manifest(book):
    assert [c.href for c in book.chapters] == [
        "OEBPS/ch1.xhtml", "OEBPS/text/ch2.xhtml", "OEBPS/svgcover.xhtml",
    ]
    assert [c.index for c in book.chapters] == [0, 1, 2]


def test_bom_prefixed_documents_still_parse(tmp_path):
    """A BOM before `<?xml` must not break a document (no book has one yet)."""
    path = write_epub(tmp_path / "bom.epub", bom=True)
    with open_book(path) as b:
        assert b.metadata.title == "Test Book"
        assert len(b.chapters) == 3
        assert any(blk.text for blk in b.chapters[0].blocks)


def test_opf_version_is_not_dispatched(book_path):
    """The fixture declares package version 1.0 yet is a normal EPUB 2/3 book."""
    with open_book(book_path) as b:
        assert b.unsupported is None
        assert len(b.chapters) == 3


def test_drm_yields_named_reason(tmp_path):
    path = write_epub(tmp_path / "drm.epub", drm=True)
    with open_book(path) as b:
        assert b.unsupported is not None
        assert "drm" in b.unsupported.lower()


# -- navigation ----------------------------------------------------------

def test_nav_wins_when_present(book):
    toc = book.toc
    assert [n.label for n in toc] == ["Chapter One", "Chapter Two"]
    assert toc[0].href == "OEBPS/ch1.xhtml"
    assert [n.label for n in toc[0].children] == ["Section"]
    assert toc[0].children[0].anchor == "sec"


def test_ncx_used_when_nav_absent(tmp_path):
    path = write_epub(tmp_path / "nonav.epub", with_nav=False)
    with open_book(path) as b:
        toc = b.toc
        assert [n.label for n in toc] == ["Chapter One", "Chapter Two"]
        assert toc[1].href == "OEBPS/text/ch2.xhtml"
        assert toc[1].anchor == "top"


def test_spine_order_when_both_nav_sources_missing(tmp_path):
    path = write_epub(tmp_path / "bare.epub", with_nav=False, with_ncx=False)
    with open_book(path) as b:
        assert [n.href for n in b.toc] == [
            "OEBPS/ch1.xhtml", "OEBPS/text/ch2.xhtml", "OEBPS/svgcover.xhtml",
        ]


# -- content graph -------------------------------------------------------

def test_block_kinds_cover_the_model(book):
    kinds = {b.kind for b in book.chapters[0].blocks}
    assert {
        "heading", "paragraph", "quote", "preformatted",
        "figure", "list", "definition_list", "table", "math", "page_break",
    } <= kinds


def test_ids_are_positional_and_stable(book_path):
    with open_book(book_path) as b:
        first = [x.id for x in b.chapters[0].blocks]
    with open_book(book_path) as b:
        second = [x.id for x in b.chapters[0].blocks]
    assert first == second
    assert all(i.startswith("c0000/b") for i in first)
    assert len(set(first)) == len(first)


def test_ids_are_unique_across_nested_blocks(book):
    """Nested blocks (list items, cells) are numbered from the same counter."""
    ids = [node.id for block in book.chapters[0].blocks for node in block]
    assert len(set(ids)) == len(ids), "a nested id collided with another block"
    assert any("/b" in i for i in ids)
    # The second chapter restarts the ordinal but keeps the chapter prefix.
    assert all(i.startswith("c0001/") for i in
               (n.id for blk in book.chapters[1].blocks for n in blk))


def test_nesting_lives_inside_the_owning_block(book):
    blocks = book.chapters[0].blocks
    listing = next(b for b in blocks if b.kind == "list")
    assert listing.ordered is True
    assert len(listing.items) == 2
    assert listing.items[0].items, "a nested list lives inside its list item"
    table = next(b for b in blocks if b.kind == "table")
    assert len(table.rows) == 2 and len(table.rows[0]) == 2
    assert table.attributes["caption"] == "Measurements"


def test_definition_list_carries_term_and_definition(book):
    dl = next(b for b in book.chapters[0].blocks if b.kind == "definition_list")
    assert dl.items[0].attributes["term"] == "Term"
    assert "definition" in dl.items[0].text


def test_footnote_ref_resolves_to_its_block(book):
    """The marker is a graph edge pointing at the block holding the note."""
    blocks = book.chapters[0].blocks
    marker = next(
        b for b in blocks
        if b.kind == "footnote_ref" and b.attributes["href"] == "#fn1"
    )
    target_id = marker.attributes.get("target_id")
    assert target_id, "an intra-chapter noteref resolves to a block id"
    note = book.chapters[0].resolve(target_id)
    assert note is not None
    assert "definition" in note.text
    assert note.attributes.get("glossdef") is True


def test_cross_document_footnote_resolves(book):
    """A noteref pointing into another chapter resolves through the spine."""
    markers = [b for b in book.chapters[1].blocks if b.kind == "footnote_ref"]
    assert markers, "the marker is reported as its own block"
    target_id = markers[0].attributes.get("target_id")
    assert target_id and target_id.startswith("c0000/b")
    assert book.chapters[0].resolve(target_id) is not None


def test_script_is_reported_not_stripped(book):
    """Parse, don't judge: script content is a block like any other."""
    scripts = [
        b for b in book.chapters[0].blocks if b.attributes.get("element") == "script"
    ]
    assert scripts and "var x" in scripts[0].text


def test_page_break_and_math(book):
    blocks = book.chapters[0].blocks
    assert any(b.kind == "page_break" for b in blocks)
    math = next(b for b in blocks if b.kind == "math")
    assert math.attributes["mathml"]["tag"] == "math"


def test_plain_text_is_derived_and_memoized(book):
    ch = book.chapters[0]
    text = ch.plain_text
    assert "First paragraph" in text and "Quoted material" in text
    assert ch.plain_text is text  # memoized on the instance
    assert "var x" not in text  # script text is not display text


# -- images --------------------------------------------------------------

def test_images_resolve_within_the_zip(book):
    images = book.chapters[0].images
    paths = {i.path for i in images}
    assert "OEBPS/img/cover.jpg" in paths
    assert "OEBPS/img/plate one.png" in paths  # percent-decoded


def test_external_images_are_skipped(book):
    assert all(not i.path.startswith("http") for i in book.chapters[0].images)
    assert all("example.org" not in i.path for i in book.chapters[0].images)


def test_images_carry_no_measured_size(book):
    """Sizing is the renderer's job: the book's own attributes travel with the
    XHTML, and epubx decodes no image formats to second-guess them."""
    img = next(i for i in book.chapters[0].images if i.path.endswith("cover.jpg"))
    assert not hasattr(img, "width")
    assert not hasattr(img, "height")
    assert img.media_type == "image/jpeg"  # what the OPF declares is kept
    assert img.alt == "Cover"


def test_image_metadata_and_lazy_bytes(book):
    cover = next(i for i in book.chapters[0].images if i.path.endswith("cover.jpg"))
    assert cover.media_type == "image/jpeg"
    assert cover.alt == "Cover"
    assert cover.read()[:2] == b"\xff\xd8"  # JPEG SOI, fetched on demand


def test_relative_and_parent_hrefs_normalise(book):
    assert normalize_href("../img/a.png", "OEBPS/text/c.xhtml") == "OEBPS/img/a.png"
    assert normalize_href("a%20b.png", "OEBPS/") == "OEBPS/a b.png"
    assert normalize_href("https://x/y.png", "OEBPS/") == ""


# -- cover resolution ----------------------------------------------------

@pytest.mark.parametrize("via", ["guide", "meta", "properties"])
def test_cover_is_found_by_every_declared_convention(tmp_path, via):
    """255 books declare a guide cover, 251 a meta cover; both are accepted."""
    path = write_epub(tmp_path / f"cover-{via}.epub", cover_via=via)
    with open_book(path) as b:
        cover = b.cover
        assert cover is not None
        assert cover.path == "OEBPS/img/cover.jpg"
        assert cover.media_type == "image/jpeg"
        assert cover.read()[:2] == b"\xff\xd8"


def test_book_without_a_declared_cover_reports_none(book):
    assert book.cover is None


def test_cover_does_not_eagerly_parse_chapters(tmp_path):
    """Cover lookup reads one image header, not the content documents."""
    path = write_epub(tmp_path / "cover.epub", cover_via="guide")
    with open_book(path) as b:
        assert b.cover is not None
        assert all(ch.__dict__.get("blocks") is None for ch in b.chapters)


def test_guide_naming_a_document_does_not_beat_the_real_image(tmp_path):
    """A `guide` cover pointing at an XHTML page must not win over a meta cover.

    Found against a real 6MB book whose guide read
    `<reference type="cover" href="titlepage.xhtml"/>` while
    `<meta name="cover" content="cover"/>` named cover.jpeg.
    """
    path = write_epub(tmp_path / "guide-doc.epub", cover_via="guide-document")
    with open_book(path) as b:
        cover = b.cover
        assert cover is not None
        assert cover.path == "OEBPS/img/cover.jpg"
        assert cover.media_type == "image/jpeg"


def test_svg_wrapped_image_is_reported(book):
    """A cover inside `<svg><image xlink:href>` is still an image reference.

    Found against a real book whose titlepage.xhtml wrapped cover.jpeg in an
    SVG. lxml's HTML parser leaves `xlink:href` as a literal prefixed name, so
    the Clark-notation lookup alone silently found nothing.
    """
    chapter = next(c for c in book.chapters if c.href.endswith("svgcover.xhtml"))
    images = chapter.images
    assert len(images) == 1
    assert images[0].path == "OEBPS/img/cover.jpg"
    assert images[0].media_type == "image/jpeg"


def test_xlink_href_is_read_in_both_notations(book):
    """Both the literal `xlink:href` and the Clark-notation form resolve."""
    from epubx.content import _Builder

    builder = _Builder(book, 0, "OEBPS/svgcover.xhtml")
    svg = next(
        node for node in book.read_parsed("OEBPS/svgcover.xhtml").iter()
        if isinstance(node.tag, str) and node.tag.lower() == "svg"
    )
    assert builder._svg_images(svg)


def test_note_id_on_a_wrapper_resolves_to_the_enclosing_block(book):
    """A note id on a `<div>` inside a list `<li>` must resolve, not dangle.

    Found on a real book whose 1,077 endnote references all failed to resolve:
    the id sat on `<div epub:type="footnote">` inside an `<li>`, which no
    emission path registered.
    """
    marker = next(
        b for b in book.chapters[0].blocks
        if b.kind == "footnote_ref" and b.attributes["href"] == "#fn2"
    )
    target_id = marker.attributes.get("target_id")
    assert target_id, "a wrapper-borne note id resolves"
    note = book.chapters[0].resolve(target_id)
    assert note is not None
    assert "second note" in note.plain_text


def test_distinct_notes_resolve_to_distinct_blocks(book):
    """Two notes must not both collapse onto one enclosing container block."""
    markers = {
        b.attributes["href"]: b.attributes.get("target_id")
        for b in book.chapters[0].blocks if b.kind == "footnote_ref"
    }
    assert markers["#fn1"] and markers["#fn2"]
    assert markers["#fn1"] != markers["#fn2"], "each note points at its own block"


def test_note_id_on_a_bare_div_wrapper_resolves(book):
    """A note id on a plain `<div>` wrapping its paragraph must resolve.

    Found on a real book whose 40 endnote references all dangled: each id sat
    on a `<div>` whose only child was the paragraph holding the note, and
    nothing on the ancestor path had emitted a block.
    """
    marker = next(
        b for b in book.chapters[0].blocks
        if b.kind == "footnote_ref" and b.attributes["href"] == "#fn3"
    )
    target_id = marker.attributes.get("target_id")
    assert target_id, "a div-wrapped note id resolves"
    assert "third note" in book.chapters[0].resolve(target_id).plain_text


# -- deferred cases ------------------------------------------------------

def test_image_only_book_gets_a_named_reason(tmp_path):
    """A scanned book is named, never raised — SPEC.md's deferred-case rule."""
    path = write_scan_epub(tmp_path / "scan.epub", pages=6)
    with open_book(path) as b:
        reason = b.unsupported
        assert reason is not None
        assert reason.startswith("image-only:")
        assert "no extractable text" in reason


def test_ordinary_book_is_not_flagged(book):
    assert book.unsupported is None


def test_illustrated_text_book_is_not_flagged(tmp_path):
    """Prose plus a few pictures is a text book, not a scan."""
    path = write_epub(tmp_path / "illustrated.epub")
    with open_book(path) as b:
        assert b.unsupported is None


def test_drm_reason_takes_precedence_over_scanning(tmp_path):
    path = write_scan_epub(tmp_path / "drm-scan.epub", pages=6, drm=True)
    with open_book(path) as b:
        assert "drm" in b.unsupported.lower()


def test_text_book_with_many_image_pages_is_not_a_scan(tmp_path):
    """Prose interleaved with hundreds of page images is still a text book.

    Found on a real 1,690-chapter book where only 51 chapters held prose and
    the rest were page images: a fixed-stride sampler landed on images every
    time and wrongly reported the whole book as a scan. Detection must walk in
    order and stop at the first chapter carrying text.
    """
    path = write_scan_epub(tmp_path / "interleaved.epub", pages=60, text_every=12)
    with open_book(path) as b:
        assert len(b.chapters) == 60
        assert b.unsupported is None
        # and the prose really is reachable
        prose = [c for c in b.chapters if c.plain_text.strip()]
        assert prose, "the text chapters parse"


def test_font_obfuscation_is_not_drm(tmp_path):
    """Scrambled fonts leave the text readable, so the book is not refused.

    Two real books in the corpus encrypt only `.ttf` files — one of them a
    single `00001.ttf` — while every word of their text stays in the clear.
    Claiming DRM there refuses a book that parses perfectly well.
    """
    path = write_epub(tmp_path / "fonts.epub", obfuscated_fonts=True)
    with open_book(path) as b:
        assert b.unsupported is None
        assert b.obfuscated_fonts == ("OEBPS/fonts/font00001.ttf",)
        assert b.chapters[0].plain_text  # and the text is really there


def test_encrypted_key_is_named_drm(tmp_path):
    """An EncryptedKey means licence-protected content: named, never raised."""
    path = write_epub(tmp_path / "drm.epub", drm=True)
    with open_book(path) as b:
        assert "drm" in b.unsupported.lower()
        assert "EncryptedKey" in b.unsupported


# -- compact-id recognition (Word/Calibre exports) ------------------------

def test_compact_id_fragments_are_classified_and_resolved(tmp_path):
    """fn900/_ftn5 carry no separator and no semantics, yet are markers."""
    path = write_calibre_epub(tmp_path / "calibre.epub")
    with open_book(path) as b:
        markers = [node for block in b.chapters[0].blocks for node in block
                   if node.kind == "footnote_ref"]
        assert [m.attributes["href"] for m in markers] == [
            "cal.xhtml#fn900", "cal.xhtml#_ftn5",
        ]
        assert all(m.attributes.get("target_id") for m in markers), \
            "both markers resolve through the same-document graph"


def test_chapter_footnotes_normalizes_edges(book):
    """The edge list: marker, resolved target block, and the note's text."""
    edges = book.chapters[0].footnotes
    assert [e.href for e in edges] == ["#fn1", "#fn2", "#fn3"]
    first = edges[0]
    assert first.block_id.startswith("c0000/b")
    assert first.target_chapter == 0
    assert first.target_dom_ids == ("fn1",)
    assert "definition" in first.note_text


def test_chapter_footnotes_on_the_calibre_fixture(tmp_path):
    path = write_calibre_epub(tmp_path / "calibre.epub")
    with open_book(path) as b:
        edges = b.chapters[0].footnotes
        assert len(edges) == 2
        assert edges[0].text == "1"
        assert "December 1761" in edges[0].note_text
        assert edges[1].note_text and "Word-style" in edges[1].note_text


def test_note_backlinks_are_not_markers(tmp_path):
    """The note's own backlink (fragment fnrefN) stays a native link."""
    path = write_calibre_epub(tmp_path / "calibre.epub")
    with open_book(path) as b:
        hrefs = [node.attributes["href"]
                 for block in b.chapters[0].blocks for node in block
                 if node.kind == "footnote_ref"]
        assert "cal.xhtml#fnref900" not in hrefs
        assert "cal.xhtml#ftnref5" not in hrefs


def test_note_spanning_several_paragraphs_is_gathered(tmp_path):
    """A flat endnote runs from its own paragraph to the next note's, so the
    whole note reaches the reader — not only the paragraph its anchor owns."""
    path = write_multiblock_note_epub(tmp_path / "notes.epub")
    with open_book(path) as b:
        edges = b.chapters[0].footnotes
    assert [e.text for e in edges] == ["1", "2"]
    assert edges[0].note_text == (
        "The first note opens here, and\n\n"
        "it continues into a second paragraph that carries no id of its own."
    )
    # The second note is the last one: nothing bounds it, so it stays itself.
    assert edges[1].note_text == "The second note stands alone."


def test_a_note_no_other_note_follows_is_not_gathered(book):
    """The last note is bounded by nothing, so body content after it must not
    be folded in — the fixture's table and math follow its third note."""
    edges = book.chapters[0].footnotes
    assert edges[2].target_dom_ids == ("fn3",)
    assert edges[2].note_text.startswith("The third note")
    assert "Measurements" not in edges[2].note_text


# -- immutability ---------------------------------------------------------

def test_parsed_attributes_are_read_only(book):
    """`Block` is frozen, and so is the dict inside it.

    Rebinding a field already raised. Writing to `attributes` did not, and
    `ch.blocks` is memoised, so one consumer's write was every later reader's
    read. The mapping is read-only once parsing completes.
    """
    block = book.chapters[0].blocks[0]
    with pytest.raises(TypeError):
        block.attributes["element"] = "p"
    with pytest.raises(FrozenInstanceError):
        block.text = "rewritten"


def test_nested_blocks_are_frozen_too(book):
    """`items` and `rows` are frozen recursively, not just the top level."""
    listing = next(b for b in book.chapters[0].blocks if b.kind == "list")
    assert listing.items and listing.items[0].items, "a nested list lives here"
    for nested in (listing.items[0], listing.items[0].items[0]):
        with pytest.raises(TypeError):
            nested.attributes["element"] = "p"

    table = next(b for b in book.chapters[0].blocks if b.kind == "table")
    with pytest.raises(TypeError):
        table.rows[0][0].attributes["element"] = "p"


def test_freeze_survives_a_reentrant_parse(tmp_path):
    """Both chapters come back frozen when their notes reference each other.

    Resolution parses the other chapter, which parses back — the path where
    `Chapter.blocks` serves the *unfrozen* stash to a reentrant read
    (PITFALLS §17). The outer access must still hand back frozen blocks, so
    the guarantee is asserted here rather than assumed.
    """
    path = write_mutual_footnote_epub(tmp_path / "mutual.epub")
    with open_book(path) as b:
        first, second = b.chapters
        assert first.footnotes[0].target_id.startswith("c0001/")
        assert second.footnotes[0].target_id.startswith("c0000/")
        for chapter in (first, second):
            for block in chapter.blocks:
                for node in block:  # nested blocks included
                    with pytest.raises(TypeError):
                        node.attributes["element"] = "p"

