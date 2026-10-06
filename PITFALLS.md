# epubx — Pitfalls

Every entry here is a bug that **epubx actually shipped**, found by running
against real books. Each one produced plausible-looking output — no exception,
no warning — and silently lost or misreported content. Synthetic fixtures did
not catch any of them.

Read this before changing the parser. Most of these are ways to be *quietly*
wrong, which is the only failure mode that matters in a library whose job is to
report what a document contains.

**Evidence base:** 299 EPUB files (1.3 GB, mostly Chinese, from a Calibre
library) plus three hand-placed books. Current state: **0 uncaught exceptions**,
645,555 paragraphs, 1,261/1,263 footnote references resolved, 4 books named
image-only.

---

## The rule that explains all of them

> **A block walker that finds no block has not found no content.**

Recursing into a wrapper and emitting nothing looks identical to a wrapper that
genuinely is empty. Every data-loss bug below is that confusion. When a
container yields zero blocks, ask what it *actually* held before concluding it
was empty — the text is usually still there, sitting somewhere you did not look.

Corollary, learned the hard way twice: **never infer a book's nature from an
aggregate.** Sampled chapters, summed character counts and averaged ratios each
produced a confident, wrong verdict on a real book. Look at one document's raw
markup before believing any statistic.

---

## 1. Paragraphs that are not `<p>`

**The single largest source of lost text.**

`<p>` is not a requirement of XHTML-in-practice. Real books emit paragraphs as:

```html
<!-- Calibre convention: a class, and no <p> anywhere in the file -->
<div class="p-indent"><span>Body text…</span></div>
```

```html
<!-- Chinese novels: prose set loose, separated by <br/> -->
<div class="calibre1">
  <h3>第九部：神秘敵人</h3><br/>
  <br/>
  　　黃俊和兩個大漢，跟在我們背後…<br/>
  <br/>
  　　「死神」？不可能的…
</div>
```

Two separate traps:

- **The Calibre class.** Treat every `<div>` as structure and *Killing Lincoln*
  (79 chapters, 511,617 characters) parsed to **zero**. Honour
  `p-indent`/`p-br`/`p-blanc`/`p-continuance`.
- **Prose in the tails.** The second form's text is in the **`.tail` of each
  `<br/>`**, not in the div's own `.text` (which is indentation whitespace).
  Checking only `.text` finds nothing. ~130 readable books parsed to ~465
  characters each; they now yield their full text.

**And the trap in the fix.** The obvious generalisation — "a div with no block
children is a paragraph" — is wrong: it swallows `<body>` and `<section>` and
collapses an entire chapter into one block (it broke 10 tests). And "contains a
block child" is *also* wrong, because the wrapper above legitimately contains an
`<h3>`. The rule that holds: a block **containing the prose** disqualifies;
inline markup and headings alongside loose prose do not.

## 2. lxml's HTML parser reads undeclared files as latin-1

A document with **no charset declaration at all** — valid UTF-8 — came out as
`Whatâ\x80\x99s next`. lxml sniffs, fails to find a declaration, falls back to
latin-1, and the damage is invisible until you read the text.

**The fix that does not work:** decoding the bytes yourself and re-encoding as
UTF-8. lxml re-sniffs and guesses latin-1 again. You must pass the encoding to
the parser:

```python
html.HTMLParser(recover=True, encoding="utf-8")   # this is the load-bearing part
```

## 3. `xlink:href` is not namespaced by the HTML parser

An SVG-wrapped cover reported **zero images**. The XML parser expands
`xlink:href` to `{http://www.w3.org/1999/xlink}href`; the **HTML parser leaves
the literal string `"xlink:href"`**. Reading only the Clark-notation form finds
nothing, with no error. Accept both.

Corollary: books wrap covers in `<svg><image>` rather than `<img>`. Handle SVG
images or whole pages vanish.

## 4. `opf:file-as` is namespaced

`<dc:creator opf:file-as="Doe, Jane">` parses to
`{http://www.idpf.org/2007/opf}file-as`. `element.get("file-as")` returns `None`,
silently, for every creator in the book.

## 5. `encryption.xml` is not DRM

Font obfuscation XOR-encodes `.ttf` files to discourage extraction and **leaves
the text in the clear**. Two corpus books do exactly this — one scrambles a
single `00001.ttf`. epubx refused both as "likely Adobe ADEPT", which was wrong
twice over: the books are perfectly readable, and ADEPT is a different scheme
that the evidence never named.

Distinguish by contents, not presence:

| Evidence | Verdict |
|---|---|
| `EncryptedKey` present | named DRM — licence-protected |
| non-font resources encrypted | named DRM, citing the resource |
| **fonts only** | **not DRM** — parse normally, record `book.obfuscated_fonts` |

Do not name a vendor you cannot see. "Likely Adobe ADEPT" was an invented claim.

## 6. Footnote ids live on wrappers

```html
<li><div id="fn_5" epub:type="footnote"><p>The note text…</p></div></li>
```

No emission path registers that `<div>`: it is a wrapper, not a block. **1,077
endnote references in one real book resolved to nothing.** Ownership must be
resolved from the DOM after the walk, searching, most specific first:

1. the element itself, if a block was emitted from it;
2. a block emitted from one of its **descendants** (a `<div>` wrapping a `<p>`);
3. the nearest **ancestor-or-self** that emitted a block.

Getting this wrong is silent and expensive. Three wrong models were tried
before the right one:

- *descendants first* → the `<section>` wrapper swallows every id; 1,077 notes
  collapse onto ~200 shared blocks;
- *a "deepest block wins" depth heuristic* → guesswork about which block looks
  nested, still wrong;
- *keying blocks by `id(element)`* → **lxml recycles element proxies, so `id()`
  aliases.** A `<dd>` resolved to an unrelated `<script>` paragraph. Hold
  elements strongly.

A bare `#fn_5` names no document, so cross-document resolution needs an
id→document index built from raw bytes (ids are *not* unique across a book:
`#fn_1` recurs in every chapter).

## 7. Guide covers can point at documents

`<reference type="cover" href="titlepage.xhtml"/>` names an **XHTML page**, not
an image. Preferring the guide over `<meta name="cover" content="cover"/>` made
epubx return `Image(path='titlepage.xhtml', media_type='application/xhtml+xml')`
— an "image" that is a web page — while ignoring the real `cover.jpeg`.

Cover candidates are ordered by authority but must **be images** to win. Three
conventions coexist: `properties="cover-image"`, `guide`, `<meta name="cover">`.

## 8. Element identity is not stable

Covered in §6, but it generalises: **do not key anything on `id(element)`** in
an lxml tree. Proxies are created and released as you iterate, and a recycled
address silently attributes data to the wrong node. Key on the element, or hold
a strong reference.

## 9. Sampling will condemn a readable book

A stride sampler (`chapters[::len//8]`) reported a 1,690-chapter C++ textbook
as image-only. Only **51** chapters held prose; the other 1,639 were page
images. Every stride landed on an image.

Books interleave text and images unpredictably. Walk chapters **in order** and
stop at the first real prose; then a text book costs only what it takes to find
its first paragraph.

The same book was nearly condemned a second way: its "2.1M characters" were
**image alt text**, not prose. Count `block.text`, never `plain_text` — that
folds alt text in, and a scan's alt text can be a whole title page.

## 10. Silent truncation looks like corruption

A book opened as `BadZipFile: File is not a zip file`. The archive was fine —
a concurrent process had truncated the copy mid-write, at exactly 10,485,760
bytes (10 MB). **Verify the input before blaming the library.** Three of the
four "corrupt books" investigated in this project were artefacts of the test
harness, not the corpus.

## 11. Network latency is not parse time

`open()` appeared to take **386 ms** over SMB and was about to be "optimised".
On a local copy the same book opens in **1.2 ms**; the 176 MB book in 13 ms.

Benchmark against a local copy, and take a **median over repeats**. A single
timing over a network mount measures the network.

## 12. Truth-testing an lxml element

```python
return parse_html(data) or parse_xml(data)   # FutureWarning, and wrong on <html/>
```

An element with no children is falsy. `parse_html` returning a valid empty
document silently falls through to the XML parser. Test `is not None`.

## 13. Nested blocks must not rewind the ordinal counter

Building nested blocks into a temporary list, then restoring the counter along
with it, hands the same id to a nested block and a later top-level one. Ids
must come from one monotonic counter per document so `c0000/b0012` is unique
across the whole chapter — that uniqueness is what makes the id safe to anchor
annotations and reading positions.

## 14. Zero-length text is not zero content — and not a bug either

An "empty" chapter is ambiguous in both directions:

- **It may be correct.** In the three *Dr. Slump* manga volumes a typical chapter
  is a single full-page scan: 2 blocks, 1 image, ~3 characters. That is the
  whole page. Nothing is wrong.
- **It may be a lost-text bug** — and it usually is. During this project, 79
  chapters and then ~130 books reported near-zero text, and *every one* was a
  parser failure (§1), not an empty book.

So: before treating an empty chapter as a bug, check whether it holds images or
is a plausible page; and before treating a book as text-bearing, remember that
alt attributes count as characters (see §9). "This book has text" deserves to be
an assertion in a test, not an impression.

## 15. Percent-encoding and `../` in hrefs

`src="../OEBPS/img/plate%20one.png"` must normalise to `OEBPS/img/plate one.png`.
Normalise against the **referring document's** directory, not the OPF's, and
percent-decode once. Absolute URLs are not zip members and must be skipped —
silently, since a book referencing a CDN image is still a readable book.

## 16. A walked container drops the text that owns no block

The §1 fix handles a `<div>` that holds *only* loose prose. A `<div>` that
holds loose prose **and** a real block is a different animal, and it cost real
books their text twice over.

`_holds_bare_text` answers "is this wrapper prose, or structure?". A block
child makes the answer "structure" — correctly, since the `<ul>` must stay a
list — and the wrapper is then *walked*. But the walk visited child elements
only, and two kinds of text live in no element at all:

- the container's own `.text`, before its first child;
- every child's `.tail`.

*On China* keeps a section's lead-in prose in the div's own text beside a
nested `<div>`: **67,104 characters dropped, 6% of the book**, while Calibre
shows every word. A chapter of *Sheng Si Suo* keeps its paragraphs in the
tails of `<br/>` elements inside a div that also holds a `<ul>`: **13,756
characters in, 185 out** — and that book's other eight chapters were fine,
which is exactly why it went unnoticed for so long. Both are §1's rule in a
new disguise: a block walker that finds no block has not found no content.

The fix: `walk` emits the container's own text and every child's tail as
paragraphs — unless that child's tail is already inside the child's own
block, which `text_of` arranges by reading an element's tail along with its
content. `_text()` records that consumption; without the record every
paragraph's trailing text would be emitted a second time.

Measured after: the four chapters recover 13,811 / 5,713 / 6,348 / 10,385
characters, *On China*'s gap goes to −1%, and the corpus gains 128,052
characters across 1,663 new paragraphs with **no other block kind changed by
one**. No fixture held the shape; the audit found it, and Calibre settled it.

---

## 17. Resolving cross-document footnotes can re-enter the parser

Resolving a marker whose note lives in another chapter walks that chapter's
blocks — which parses it. Once compact-id recognition widened the graph
(`fn674`, `_ftn5` — markers the old classifier never saw), two chapters whose
notes reference each other re-entered each other's parse: A resolves → walks
B → B resolves → walks A — and A's `blocks` cached_property was still
mid-computation, so it *re-ran* instead of returning. Unbounded recursion.
Four corpus books (Simon & Schuster-style exports) hit `RecursionError` the
day the recognition shipped; the corpus suite caught all four in one run.

Fix: `parse_document` stashes the built (pre-resolution) blocks keyed by
book + chapter before resolution runs, and `Chapter.blocks` serves the stash
to reentrant access. Resolution may nest, but every chapter parses exactly
once, however the references weave. The stash is dropped when the parse
completes, so nothing lingers.

Lesson: widening a classifier widens the *graph* — the resolution order must
be reentrancy-proof before the widening ships, not after. The corpus guard
added alongside the recognition is what turned four crashes into one
afternoon's fix.

---

## Known limitations (not bugs)

- **Vertical CJK layout** is named as deferred in SPEC.md and is **not
  implemented**. Note what the corpus does and does not say about it: all 299
  books are Chinese or English, but **none declares `writing-mode`**, and their
  stylesheets are plainly horizontal (`text-align: justify`, left/right margins).
  So this corpus provides *no evidence* about vertical layout either way — it is
  untested because the corpus is silent, not because the corpus exercises it.
  An earlier draft of this file claimed the corpus was "almost entirely
  vertical-writing Chinese"; that was an assumption, not a measurement, and it
  was wrong.
- **Math** has 0 occurrences across the corpus. The MathML path is
  specification-correct and empirically unvalidated — synthetic fixtures only.
- **2 of 1,263 footnote references cannot resolve**, in *Zhe Ben Shu Jiao Shi
  Yao*. The book references `#fn__1`/`#fn__2` but defines `#fnt__1`/`#fnt__2` —
  a publisher typo, missing `t`. Nothing a parser can do; correctly left
  unresolved rather than guessed at.
- **`Block.attributes` is a mutable `dict` on a frozen dataclass.** The model is
  only shallowly immutable.
- **Image-only detection is a heuristic** — first-200-chapters-or-first-prose.
  It is deliberately biased toward *not* flagging, because refusing a readable
  book is worse than missing a rare scan.

## How to avoid adding to this list

1. **Run against real books before believing any fix.** Four of these bugs were
   introduced *by* a fix and only caught by re-running the corpus.
2. **When a statistic surprises you, read the raw markup** before theorising.
   Twice the surprising statistic was correct and my reading of it was not.
3. **Assert absence loudly in tests.** "This book has text" is a test. So is
   "these two notes resolve to different blocks."
4. **Prefer naming to guessing.** When the evidence cannot identify a vendor or
   a cause, say what was observed.
