# epubx — Design Spec

## Goal

A standalone Python library that opens EPUB files and exposes their content as a
**structured, lazily-parsed graph**. It parses; it does not render, store, or
decide what a reader should show.

Phase I is minimal but foundational: the content model and paragraph identity
are designed to be final, so later phases add capability without migration.

---

## Principles

1. **Standards coexist.** EPUB 2 (NCX) and EPUB 3 (nav) are peer producers of
   one model. No version dispatch, no "legacy path". Detection is by *feature
   present*, never by declared version.
2. **Lazy by default.** `open()` reads the zip central directory and the OPF
   only. Chapter parsing happens on access.
3. **Structure, not strings.** Output is a node graph. HTML is not the
   canonical form and is not exposed in Phase I.
4. **Parse, don't judge.** epubx reports what a document contains — including
   `<script>`, `<nav>`, `<table>`. Sanitising for display is the consumer's
   decision and is unrecoverable if done here.
5. **Derived, never persisted.** `plain_text` is computed and memoized, not
   stored — as is `outline`, the declared TOC with heading-derived children
   filled in where the nav left a node empty.
6. **No persistence in Phase I.** No pickle, no SQLite, no cache files.
   Storage is a later, separate decision.

---

## Scope

**In:** OPF metadata · spine → chapters · TOC (nav or NCX) · block-level
content graph · footnotes as resolved links · images as resolved references ·
math as a first-class block kind.

**Out (deferred, but named):** DRM handling · image-only/manga books · vertical
CJK layout · persistence · any UI concern.

Deferred cases return a named `unsupported` reason. They are never an uncaught
exception.

---

## Public API

```python
from epubx import open_book

book = open_book("path.epub")        # central dir + OPF only; <50ms target

book.metadata      # Metadata(title, creators, language, identifier, publisher, date)
book.chapters      # lazy Sequence[Chapter], spine order
book.toc           # TocNode tree — nav if present, NCX if present, else spine
book.outline       # TocNode tree — toc with heading-derived children filling
                   #   entries the nav left empty (opt-in; reads every chapter)
book.unsupported   # None | reason string
book.obfuscated_fonts  # fonts scrambled to discourage extraction — not DRM

book.spine         # ('OEBPS/Text/ch1.xhtml', ...) — reading order
book.resource(path)  # Resource(path, media_type).read() — the book's own bytes
book.resources()     # every file a renderer may need

ch = book.chapters[0]                # parses this chapter now
ch.blocks                           # tuple[Block, ...] — ordered content
ch.images                           # resolved image references
ch.plain_text                       # derived, memoized
ch.footnotes                        # footnote edges, normalized: marker →
                                    #   target block + the note's text

b = ch.blocks[3]
b.id                                # "c0007/b0012" — stable across re-parses
b.kind                              # heading | paragraph | quote | table | list
                                   # | definition_list | figure | math
                                   # | preformatted | page_break
b.text                              # plain text, when meaningful
b.target_id                         # footnote_ref → resolves to its block
b.attributes                        # read-only Mapping of parse detail
```

### Identity

`{chapter_index}/b{ordinal}` — positional, not content-hashed. Survives
republishing, so it anchors AI annotations and reading positions.

### Immutability

Everything the public API hands back is immutable. `Block` is a frozen
dataclass and its `attributes` mapping is read-only once the chapter is parsed,
so the memoised graph (`ch.blocks`) cannot be edited out from under another
consumer. `attributes` carries parse detail — `element`, `level`, `dom_ids`,
`target_id`, `mathml` — and is a `Mapping`, not a `dict`.

### Images

```python
img = ch.images[0]
img.path        # 'OEBPS/img/cover.jpg' — resolved within the zip
img.media_type  # from the OPF manifest
img.alt         # from alt=
data = img.read()   # bytes, lazily fetched
```

No size is measured or reported. Sizing is the renderer's job: it reads the
book's own `width`/`height` attributes from the XHTML it is already rendering,
and CSS overrides them anyway. Measuring here would duplicate that from the
wrong source — and it is the only reason epubx ever needed Pillow, since it
meant decoding image formats the library otherwise never touches. `media_type`
(the OPF's declaration) is kept because a renderer needs it for the
Content-Type; the bytes are handed over as they are.

### Serving

Rendering is not this library's job. `book.spine`, `book.resource(path)` and
`book.resources()` hand the renderer the book's own files at the book's own
paths — bytes, with a `media_type` for the Content-Type — so the book's
relative links (CSS, fonts, images, footnotes) resolve in a browser with
nothing rewritten. The parsed content model below is for consumers that need
*text* rather than pixels; it is not on the path to the screen.

---

## Content model

`ch.blocks` is a flat ordered tuple. Nesting (lists, definition lists, table
cells) lives inside the owning block.

| Kind | Blocks / books | Notes |
|---|---|---|
| `paragraph` | 661,108 / 296 | |
| `heading` | 16,799 / 139 | `level` in `attributes` |
| `quote` | 10,804 / 27 | |
| `list` | 9,477 / 47 | `items`; ordered/unordered, nested |
| `figure` | 9,082 / 294 | caption in `text` and `attributes` |
| `footnote_ref` | 7,905 / 23 | graph edge to `target_id` |
| `preformatted` | 2,812 / 17 | |
| `definition_list` | 2,058 / 8 | `glossterm`/`glossdef` |
| `table` | 528 / 30 | `rows`, `header_rows` |
| `page_break` | 516 / 4 | `epub:type="pagebreak"` |
| `math` | **0** | spec-derived, synthetic fixtures |

Counts are measured over the 300-book corpus and **include nested blocks**
(721,089 in total), so a list's items are counted alongside the list itself.

Also observed and modelled: `bodymatter` / `frontmatter` / `backmatter` /
`landmarks` / `titlepage` / `warning` semantics.

---

## Technology

### Standard library (load-bearing)

- **`zipfile`** — the container. Reads the central directory for `open()`, then
  fetches individual members on demand; the archive is never extracted to disk.
  An EPUB is a zip, so this is not incidental — laziness, and the <50ms `open()`
  target, both rest on it.
- `dataclasses`, `typing`, `pathlib`, `urllib.parse`, `collections`, `functools`
  — model definitions, href normalisation, memoisation.

### Third-party (the complete pip list)

- **`lxml`** (`lxml.etree`, `lxml.html`) — XML and HTML parsing. XPath only;
  **`cssselect` is deliberately excluded** to keep the dependency set minimal.
  Verified absent in the reference environment.
- Python ≥ 3.10.

One package, pure-wheel. No `ebooklib`, no BeautifulSoup, no cssselect, and —
since sizing belongs to the renderer — no Pillow either: epubx decodes no
image format, it hands over the book's own bytes.

---

## Robustness rules

Ranked by measured frequency over the 300-book corpus, none privileged in code:

| Rule | Measured | Response |
|---|---|---|
| NCX present | 299/300 (99.7%) | primary nav source; nav preferred when both exist |
| OPF declares 2.0, is EPUB 2 | 284/300 (95%) | trust structure over version string |
| Missing nav | 284/300 (95%) | NCX, else spine order |
| Cover via `meta name="cover"` vs `guide` | 295 vs 282 books | accept both |
| `epub:type` role refinements | 9/300 (3%) | merge into `metadata.creators` |
| Percent-encoded / `../` hrefs | 3/300 books percent-encoded | normalise |
| Missing NCX | 1/300 | spine order |
| Content not well-formed XML | 4 of 3,513 documents | `recover=True` |
| Font obfuscation (fonts only in `encryption.xml`) | 2/300 books | not DRM — parse, record `obfuscated_fonts` |
| DRM | **0/300** | named `unsupported`, no attempt |
| BOM before `<?xml` | **0 of 16,178 members** | stripped; lxml tolerates them anyway |

---

## Reference corpus

A working Calibre library: **300 books, 1.39 GB, mostly Chinese**, discovered by
walking `*.epub`. One further book sits under `.caltrash/` and is excluded. The
count drifts as the library changes, so it is always quoted as approximate. There
is no manifest — expected values come from the books themselves and from the
assertions in `tests/test_corpus.py`.

Measured over the full set with `tools/audit_corpus.py`:

- **15,308 chapters, 721,089 blocks, 104,659,634 characters** of text across 296
  supported books.
- **Zero uncaught exceptions and zero RED findings** — nothing that should render
  fails to. 23 AMBER findings, all degraded-but-readable: 3 books with TOC
  entries pointing at missing members, 4 with images referenced but not bundled,
  2 with unresolved footnotes.
- **7,905 footnote references, 5 unresolved** — 2 are the publisher typo recorded
  under Known limitations, 3 point at external web URLs.
- OPF version: **2.0 → 284, 3.0 → 16**. No book declares 1.0 or 1.1.
- TOC source: **NCX → 284, nav → 16**.
- Metadata: title 298/300, author 297/300, language 298/300; a cover is present
  in 294/300.
- Images: **14,113 JPEG, 1,196 PNG, 107 GIF, 1 SVG**.
- Deferred by design: **4 image-only books, 0 DRM**. Font obfuscation (not DRM)
  in 2 books.
- **Zero BOMs** across 16,178 XML-ish members.

Tests read books from a local path defined by `EPUBX_CORPUS` and skip cleanly
when it is absent, so CI works without books. With a **local** copy of the corpus
present the whole suite is green: **92 passed, 0 skipped**. Over a network mount
`test_open_is_under_fifty_milliseconds` measures the mount rather than the parser
and can fail on that alone (PITFALLS 11), so benchmark against a local copy.

---

## Success criteria

1. `open()` returns in <50ms on the largest corpus book
2. Correct metadata for 100% of parseable corpus books
3. Chapter count matches spine for 100%
4. Zero uncaught exceptions across the corpus
5. Every deferred case yields a named `unsupported` reason
6. Block kinds cover every measured element above

---

## Honest coverage statement

Corpus-verified: navigation, metadata, spine, tables, footnotes, figures, lists,
images.

**Footnote normalization** — compact-id recognition (Word/Calibre exports:
`fn674`, `_ftn5`, `note12`) and the normalized edge list `Chapter.footnotes`
(marker → resolved target block + the note's text) — was verified against a
300-book Calibre library (the project's `EPUBX_CORPUS`; the count drifts as
the library changes, hence "approximately 300 books"): **7,905 footnote
markers across 23 books, 7,900 resolved (99.9%)**. 5,890 of those markers
(75% of the total) exist only through the compact-id recognition — six books
gained ten or more markers each, one alone gained 3,916. Five markers are
unresolved corpus-wide: 2 are the publisher typo recorded under Known
limitations, 3 point at external web URLs. The same verification run caught
a reentrant-parse defect in four books (PITFALLS 17) — the corpus doing its
job.

**Spec-derived only:** math — **0 occurrences across the 300-book corpus**.
Hand-written synthetic MathML fixtures. This code path will be
specification-correct and empirically unvalidated until a real math EPUB
appears; recorded as such rather than claimed as tested.

Also absent from the corpus, therefore unmodelled: ruby annotations, iframes
(both 0 occurrences).

---

## Deliberate non-goals

**No dependency on `ebooklib`.** It is the current parser's bottleneck and forces
eager full-book parsing. Reading the zip directly is both the performance fix and
the premise of the library.

**No `ai-reader` integration in Phase I.** Adopting epubx later gains lazy
parsing, a content graph, and footnote resolution — but every existing
`book.pkl` becomes invalid. That is a separate, deliberate migration.
