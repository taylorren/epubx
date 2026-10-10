# epubx

Open EPUB files and expose their content as a **structured, lazily-parsed
graph**. It parses; it does not render, store, or decide what a reader shows.
See `SPEC.md` for the full design and **`PITFALLS.md`** for the silent-failure
modes found by running against real books — read that one before changing the
parser.

## The short version

An EPUB already holds everything a reader needs: the text, the table of
contents, the references between them, the images, and the files a renderer
needs. So epubx invents no model of its own — it **translates what the book
already declares** into a graph you can walk, and hands the book's own files
back for display.

Nothing is inferred, scored or guessed. The navigation is the book's own
nav/NCX, the reading order is its own spine, the footnote edges are its own
links, and the cover is whichever candidate the book declares. A book that
states something epubx cannot represent is named in `book.unsupported` rather
than given an invented answer.

The translation is checked against **~300 real EPUBs** — a working Calibre
library, 1.39 GB, mostly Chinese. All of them open and parse with zero uncaught
exceptions, 7,900 of 7,905 footnote references resolve, and every modelled block
kind except `math` occurs in real data. See *Verified against ~300 real EPUBs*
below for what that does and does not prove.

```python
from epubx import open_book

book = open_book("path.epub")        # central dir + OPF only

book.metadata      # Metadata(title, creators, language, identifier, ...)
book.chapters      # lazy Sequence[Chapter], spine order
book.toc           # TocNode tree — nav if present, NCX if present, else spine
book.outline       # TocNode tree — toc with heading-derived children filling
                   #   entries the nav left empty (opt-in; reads every chapter)
book.unsupported   # None | reason string
book.obfuscated_fonts  # fonts scrambled to discourage extraction — not DRM
book.cover         # Image | None — guide, meta or cover-image, whichever is declared

ch = book.chapters[0]                # parses this chapter now
ch.blocks                           # tuple[Block, ...] — ordered content
ch.images                           # resolved image references
ch.plain_text                       # derived, memoized
ch.footnotes                        # footnote edges, normalized: marker →
                                    #   target block + the note's text

b = ch.blocks[3]
b.id            # "c0000/b0003" — stable across re-parses
b.kind          # heading | paragraph | quote | table | list | definition_list
                # | figure | math | preformatted | page_break | footnote_ref
b.text          # plain text, when meaningful
b.target_id     # footnote_ref -> the block it resolves to

img = ch.images[0]
img.path, img.media_type, img.alt
data = img.read()   # bytes, fetched from the zip on demand
```

## Serving a book to a renderer

An EPUB is a zip of XHTML and its resources, and a browser already knows how to
render that. So epubx does not re-model the book: it hands over the book's own
files, at the book's own paths, in reading order.

```python
book.spine                              # ('OEBPS/Text/ch1.xhtml', ...)
book.resource("OEBPS/Text/ch1.xhtml")   # Resource | None
    .media_type                         # 'application/xhtml+xml' — for Content-Type
    .read()                             # the bytes, straight from the zip

for res in book.resources():            # every file a renderer may need
    serve(res.path, res.read(), content_type=res.media_type)
```

Serve those paths one-to-one — a custom scheme, blob URLs, or a local HTTP
route — and the book's own relative links (CSS, fonts, images, footnotes)
resolve in the browser with nothing rewritten. `resource()` also accepts the
request URL the browser asked for (`/OEBPS/img/plate%20one.png`), so
percent-encoding does not become a 404. `mimetype` and `META-INF/` are left
out of `resources()`: no content document references them.

The parsed content above (`ch.blocks`, `ch.plain_text`, footnote edges) remains
available for anything that needs text rather than pixels — search, TTS,
annotation anchors — but it is not on the path to the screen.

### Try it

```
python3 tools/serve.py "path/to/book.epub"     # then open http://127.0.0.1:8000/
```

A minimal reader for exactly this path: the book's own files served at their
own paths, with a TOC, next/previous along the spine, the footnote edges epubx
resolved, and the extracted text beside it for comparison. Rendering — and
whatever the book's own links do — is the browser's. The iframe is sandboxed,
so no script from a book can run. `--strict-types` serves the declared media
types instead of `text/html` for XHTML.

## Install

The PyPI distribution is **`epub-extended`** (`epubx` is taken; the import name
stays `epubx`):

```
pip install epub-extended         # requires Python >= 3.10 and lxml
pip install 'epub-extended[test]' # adds pytest

# development, from the source tree:
pip install -e .
pip install -e '.[test]'
```

## Design notes

- **The book is the source of truth.** Where the book already declares an
  answer, nothing is inferred: nav/NCX is the TOC, the spine is the reading
  order, the book's own links are the footnote edges. Where a book declares
  something unrepresentable, `book.unsupported` names it instead of guessing.
- **Laziness.** `open_book` reads the zip central directory and the OPF only;
  chapters parse on first access. The archive is never extracted to disk.
- **Identity is positional.** `{chapter_index}/b{ordinal}`, numbered from one
  monotonic counter per document so nested blocks stay unique. Not
  content-hashed, so ids survive republishing.
- **Nesting lives in the owner.** `ch.blocks` is flat; list items, table cells
  and definition bodies are reached through `Block.items` / `Block.rows`, or by
  walking `iter(block)`.
- **Coexisting conventions are peers.** The cover is found via EPUB 3
  `properties="cover-image"`, an EPUB 2 `guide` reference, or
  `<meta name="cover">` — whichever the book declares. Likewise `epub:type`
  role refinements merge into `metadata.creators`, matched by the id they
  refine rather than by position.
- **Detection by feature, not version.** An OPF declaring `version="1.0"` is
  not treated differently from one declaring 3.0; nav/NCX/spine are chosen by
  what is present.
- **Parse, don't judge.** `<script>`, `<nav>`, `<table>` and MathML are
  reported as what they are. Sanitising for display is the consumer's call.
- **Derived, never persisted.** `plain_text` is computed and memoized. No
  pickle, no SQLite, no cache files.
- **Immutable once parsed.** `Block` is a frozen dataclass and its `attributes`
  mapping is read-only, so the memoised graph cannot be edited by one consumer
  behind another's back.

## Tests

```
python -m pytest tests -q
```

The suite builds synthetic EPUBs covering every modelled block kind, so it
passes with no books present. Corpus tests read from `EPUBX_CORPUS` — the
~300-book library described under *Verified against ~300 real EPUBs* — and skip
when it is unset:

```
EPUBX_CORPUS=/path/to/books python -m pytest tests -q
```

## Verified against a real book

`Killing Jesus - Bill O'Reilly.epub` (6.2 MB, 40 spine documents, 86 zip
entries) exercises paths the synthetic fixtures missed and found three bugs:

- a `guide` cover reference pointing at an XHTML *document* outranked the real
  `cover.jpeg` named by `<meta name="cover">` — cover candidates must be images
- a cover wrapped in `<svg><image xlink:href>` reported zero images, because
  lxml's HTML parser keeps `xlink:href` as a literal prefixed name rather than
  expanding it to Clark notation
- `opf:file-as` was read unqualified, so `Creator.file_as` was always `None`

Measured on that book: `open()` median **0.48 ms**, 1,890 blocks, 33 images all
resolving, 72,629 words, zero uncaught exceptions. It has no
`h1`–`h6` at all (calibre output: `p`/`span` only), so its block kinds are
`paragraph` and `figure` only — correct for that book, not a parser gap.

## Verified against ~300 real EPUBs

The reference corpus is a working Calibre library — **300 books, 1.39 GB, mostly
Chinese** — walked for `*.epub` with no manifest, so the count drifts as the
library changes. Every figure SPEC.md reports about the corpus is measured over
this set rather than estimated from it; see *Reference corpus* there for the full
inventory.

- **300/300 books parse with zero uncaught exceptions**, and the audit reports
  zero RED findings — nothing that should render fails to. With a local copy of
  the corpus the whole suite is green: 92 passed, 0 skipped. (Over a network
  mount the `open()` timing test can measure the mount instead of the parser —
  PITFALLS 11.)
- **Every modelled kind except `math` occurs in real data** — 721,089 blocks
  across 15,308 chapters, including the five that synthetic fixtures had been
  the only cover for: `table`, `definition_list`, `preformatted`, `quote`,
  `figure`.
- **7,900 of 7,905 footnote references resolve.** The 5 that do not are 2
  publisher typos (`#fn__1` referenced, `#fnt__1` defined) and 3 external web
  URLs — correctly left unresolved rather than guessed at.
- `open()` median **13 ms on a 176 MB book** (criterion 1).

Two bugs this corpus found, both invisible to the synthetic fixtures:

- a note id on a bare `<div>` wrapping its paragraph never resolved — ownership
  walked ancestors but never descendants, so 40 endnotes dangled;
- the same, for ids on wrappers inside list items.

A caution on timing: measured over SMB, `open()` appears to take up to 386 ms.
That is network latency, not parsing — the same book opens in 1.2 ms locally.
Benchmark against a local copy.

### Bugs the corpus found that fixtures never would

Every one of these produced readable-looking output and was caught only by
running against real books:

- **Calibre paragraphs are `<div class="p-indent">`, not `<p>`.** 79 chapters of
  *Killing Lincoln* parsed to zero characters.
- **Chinese novels set prose loose in a `<div>`**, separated by `<br/>`, with no
  `<p>` anywhere — and the text lives in the *tails* between the `<br/>`
  elements, not in the div's own `.text`. ~130 readable books parsed to ~465
  characters each; they now yield their full text.
- **The same div, once it also held a block.** Loose prose beside a real block
  (a `<ul>`, a nested `<div>`) was dropped again, because the wrapper then
  counts as structure and the walk read only child elements. *On China* lost
  67,104 characters (6% of the book) from divs' own text; *Sheng Si Suo* lost
  13,571 of a chapter's 13,756 characters to `<br/>` tails. Four chapters went
  185 / 165 / 50 / 230 characters → 13,811 / 5,713 / 6,348 / 10,385. Calibre
  showed every word throughout; only the parser could not see them.
- **`<meta charset>` absent + lxml's HTML parser** reads UTF-8 as latin-1:
  "What's" became "Whatâ\x80\x99s". Re-encoding the bytes is not enough — the
  parser needs `encoding="utf-8"` passed explicitly.
- **Note ids on wrappers** (`<div epub:type="footnote">`, a `<div>` inside an
  `<li>`) resolved to nothing; 1,077 endnotes dangled.
- **`xlink:href` inside SVG** is left as a literal prefixed name by lxml's HTML
  parser, so a cover wrapped in `<svg>` reported zero images.
- **`opf:file-as` read unqualified** left `Creator.file_as` always `None`.
- **A `guide` cover pointing at an XHTML page** outranked the real cover image.
- **`encryption.xml` is not DRM.** Font obfuscation encrypts `.ttf` files and
  leaves text readable; two such books were being refused. Only an
  `EncryptedKey`, or encryption of non-font resources, is now named as DRM.

### Deferred cases

`book.unsupported` names what cannot be represented, never raises:

- **DRM** — decided at `open()`, since it needs no parsing.
- **Image-only / manga** — decided lazily on first access, because it requires
  parsing. Detection walks chapters in order and stops at the first real prose;
  a fixed-stride sampler was tried first and condemned a 1,690-chapter text
  book whose prose sat in 51 of them. Of 300 corpus books, exactly 4 are named
  image-only, and all 4 are genuine scans.

## Coverage honesty

Corpus-verified: navigation, metadata, spine, tables, footnotes, figures, lists,
images.

Spec-derived only: **math** — 0 occurrences across ~300 real EPUBs, so the
MathML path is exercised by hand-written fixtures alone. It is specification-
correct and empirically unvalidated until a real math EPUB appears.

Unmodelled, because also absent from the corpus: ruby annotations, iframes.
Deferred but named (`book.unsupported`): DRM. Deferred and unmodelled: image-only
books, vertical CJK layout, persistence.
