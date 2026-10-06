#!/usr/bin/env python3
"""Corpus audit — a content-first structural report over $EPUBX_CORPUS.

The test suite answers "did anything raise?". This answers "would the book
render?" — graded by the project's own standard: a missing author does not
stop a reader, missing content does.

Findings are graded:

  RED     content that should render but will not: a spine document missing
          from the archive, a chapter whose markup produced zero blocks,
          a book with neither text nor images, an uncaught error.
  AMBER   degraded but readable: dangling footnotes, TOC entries pointing at
          missing members, referenced images that are not bundled, manifest
          entries missing from the archive, chapters whose extracted text is
          far smaller than their markup (the lost-text smell, PITFALLS 1/14).
  FACTS   inventory only, never flagged: missing title/author/language, EPUB
          version mix, block-kind histogram, covers, font obfuscation.

Deferred cases (DRM, image-only) are listed as named by the library, since
naming them is the design, not a defect.

Usage:
    EPUBX_CORPUS=/path/to/books python3 tools/audit_corpus.py [--limit N] [--only SUB]

Exit status: 1 if any RED finding, else 0.
"""

from __future__ import annotations

import argparse
import html
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import epubx
from epubx.hrefs import normalize_href
from epubx.xmlutil import local_name, parse_xml

CONTAINER = "META-INF/container.xml"

# Raw-markup heuristics: strip script/style, then tags, then count visible
# characters. Crude by design - only ever used against generous thresholds,
# and always shown alongside its numbers so a human can judge.
_SCRIPT_STYLE = re.compile(rb"<(script|style)\b.*?</\1\s*>", re.I | re.S)
_TAG = re.compile(rb"<[^>]*>")
_IMG_SRC = re.compile(
    rb"""<(?:img|image)\b[^<]*?\b(?:src|xlink:href|href)\s*=\s*["']([^"']+)["']""",
    re.I,
)

# Flagging thresholds: generous on purpose. A flag is a question for a human,
# never a verdict - PITFALLS.md's lesson is that confident wrong statistics
# are worse than silence.
RAW_TEXT_MIN = 500          # below this, raw-vs-block ratios prove nothing
BLOCK_RATIO = 0.20          # extracted text below 20% of raw text -> AMBER
BLANK_CHAPTER_MIN = 100     # zero blocks with more markup than this -> RED
BOOK_TEXT_MIN = 500         # supported book below this total -> AMBER


def discover(corpus: Path) -> tuple[list[Path], list[str]]:
    """Visible .epub files, plus excluded ones under hidden dirs (e.g. .caltrash)."""
    visible, excluded = [], []
    for path in sorted(corpus.rglob("*.epub")):
        rel = path.relative_to(corpus)
        if any(part.startswith(".") for part in rel.parts):
            excluded.append(str(rel))
        else:
            visible.append(path)
    return visible, excluded


def visible_chars(raw: bytes) -> int:
    raw = _SCRIPT_STYLE.sub(b"", raw)
    text = _TAG.sub(b" ", raw)
    text = html.unescape(text.decode("utf-8", "replace"))
    return len("".join(text.split()))


def walk_blocks(blocks):
    for block in blocks:
        yield from block  # Block.__iter__ walks nested blocks too


def flatten_toc(nodes):
    for node in nodes:
        yield node
        yield from flatten_toc(node.children)


def opf_facts(book) -> tuple[str | None, int, list[str]]:
    """(declared EPUB version, spine itemref count, dangling idrefs).

    Read straight from the OPF: a dangling idref is invisible through the
    public API because `_parse_spine` skips it silently.
    """
    try:
        container = parse_xml(book.member(CONTAINER))
        opf_path = None
        if container is not None:
            for node in container.iter():
                if local_name(node) == "rootfile":
                    opf_path = node.get("full-path")
                    if opf_path:
                        break
        if not opf_path:
            return None, 0, []
        opf = parse_xml(book.member(opf_path))
    except Exception:  # noqa: BLE001 - the audit never dies on one book
        return None, 0, []
    if opf is None:
        return None, 0, []
    version = opf.get("version")
    itemrefs: list[str] = []
    for child in opf:
        if local_name(child) == "spine":
            for ref in child:
                if local_name(ref) == "itemref" and ref.get("idref"):
                    itemrefs.append(ref.get("idref"))
    dangling = [r for r in itemrefs if r not in book.manifest]
    return version, len(itemrefs), dangling


def audit_book(path: Path, corpus: Path) -> tuple[dict, list[tuple[str, str]]]:
    """Return (facts, findings) for one book. Never raises."""
    rel = str(path.relative_to(corpus))
    facts: dict = {"rel": rel}
    findings: list[tuple[str, str]] = []
    try:
        with epubx.open_book(path) as book:
            facts["chapters"] = len(book.chapters)
            facts["title"] = book.metadata.title
            facts["creators"] = len(book.metadata.creators)
            facts["language"] = book.metadata.language
            facts["cover"] = book.cover is not None
            facts["obfuscated_fonts"] = len(book.obfuscated_fonts)

            version, itemrefs, dangling = opf_facts(book)
            facts["version"] = version
            if dangling:
                findings.append((
                    "amber",
                    f"spine references {len(dangling)} unknown item(s): "
                    f"{', '.join(dangling[:5])} - publisher defect, content unreachable",
                ))

            # TOC source by presence (mirrors nav.py's preference order).
            has_nav = any("nav" in e["properties"] for e in book.manifest.values())
            has_ncx = any((e["media_type"] or "") == "application/x-dtbncx+xml"
                          or e["href"].endswith(".ncx")
                          for e in book.manifest.values())
            facts["toc_source"] = "nav" if has_nav else ("ncx" if has_ncx else "spine")
            toc_missing = [n.href for n in flatten_toc(book.toc)
                           if n.href and not book.has(n.href)]
            if toc_missing:
                findings.append((
                    "amber",
                    f"{len(toc_missing)} TOC entry/entries point at missing members "
                    f"(e.g. {toc_missing[0]})",
                ))

            manifest_missing = [e["href"] for e in book.manifest.values()
                                if not book.has(e["href"])]
            if manifest_missing:
                findings.append((
                    "amber",
                    f"{len(manifest_missing)} manifest item(s) missing from the archive "
                    f"(e.g. {manifest_missing[0]})",
                ))

            if book.unsupported is not None:
                # Named by design (DRM / image-only): record, judge nothing.
                facts["unsupported"] = book.unsupported
                facts["blocks"] = facts["images"] = facts["text_chars"] = None
                facts["foot_refs"] = facts["foot_dangling"] = None
                return facts, findings

            if not book.chapters:
                findings.append(("red", "zero chapters - nothing to render"))
                facts["blocks"] = facts["images"] = facts["text_chars"] = None
                facts["foot_refs"] = facts["foot_dangling"] = None
                return facts, findings

            kinds: Counter = Counter()
            images_seen: set[str] = set()
            text_chars = blocks_total = 0
            foot_refs = foot_dangling = 0
            external_imgs = 0
            blank_chapters = 0
            suspicious: list[str] = []

            for chapter in book.chapters:
                if not book.has(chapter.href):
                    findings.append((
                        "red",
                        f"spine document missing from archive: {chapter.href}",
                    ))
                    continue
                try:
                    raw = book.member(chapter.href)
                except KeyError:
                    raw = b""
                blocks = chapter.blocks

                if not blocks:
                    chars = visible_chars(raw)
                    if chars > BLANK_CHAPTER_MIN:
                        findings.append((
                            "red",
                            f"chapter {chapter.href} holds {chars} chars of markup "
                            f"but produced 0 blocks - text would render blank",
                        ))
                    blank_chapters += 1
                    continue

                block_text = 0
                for node in walk_blocks(blocks):
                    blocks_total += 1
                    kinds[node.kind] += 1
                    block_text += len(node.text or "")
                    for img in node.images:
                        images_seen.add(img.path)
                    if node.kind == "footnote_ref":
                        foot_refs += 1
                        if not node.attributes.get("target_id"):
                            foot_dangling += 1
                text_chars += block_text

                raw_chars = visible_chars(raw)
                if (raw_chars > RAW_TEXT_MIN
                        and block_text < raw_chars * BLOCK_RATIO):
                    suspicious.append(
                        f"{chapter.href} (markup ~{raw_chars} chars, "
                        f"extracted {block_text})"
                    )

                for match in _IMG_SRC.finditer(raw):
                    src = match.group(1).decode("utf-8", "replace").strip()
                    if not src or src.startswith(("http:", "https:", "data:", "//")):
                        external_imgs += 1
                        continue
                    resolved = normalize_href(src, chapter.href)
                    if resolved and not book.has(resolved):
                        findings.append((
                            "amber",
                            f"image referenced but not bundled: {src} (in {chapter.href})",
                        ))

            if text_chars == 0 and not images_seen:
                findings.append((
                    "red",
                    "book has neither extracted text nor images - renders blank",
                ))
            elif text_chars < BOOK_TEXT_MIN:
                findings.append((
                    "amber",
                    f"only {text_chars} chars of text across "
                    f"{len(book.chapters)} chapters - verify not lost text",
                ))
            if suspicious:
                findings.append((
                    "amber",
                    "extracted text far below markup: " + "; ".join(suspicious[:3]),
                ))
            if foot_dangling:
                findings.append((
                    "amber",
                    f"{foot_dangling}/{foot_refs} footnote references do not resolve",
                ))
            if blank_chapters and blank_chapters == len(book.chapters):
                findings.append(("red", "every chapter produced 0 blocks"))

            facts.update(
                blocks=blocks_total,
                images=len(images_seen),
                text_chars=text_chars,
                foot_refs=foot_refs,
                foot_dangling=foot_dangling,
                kinds=kinds,
                external_imgs=external_imgs,
            )
            return facts, findings
    except Exception as exc:  # noqa: BLE001 - catching is the audit's whole job
        findings.append(("red", f"uncaught {type(exc).__name__}: {exc}"))
        facts.setdefault("chapters", None)
        return facts, findings


def main() -> int:
    parser = argparse.ArgumentParser(description="epubx corpus audit")
    parser.add_argument("corpus", nargs="?",
                        default=os.environ.get("EPUBX_CORPUS"),
                        help="corpus directory (default: EPUBX_CORPUS)")
    parser.add_argument("--limit", type=int, default=0,
                        help="audit only the first N books")
    parser.add_argument("--only", default="",
                        help="only books whose path contains SUB")
    args = parser.parse_args()

    if not args.corpus or not Path(args.corpus).is_dir():
        print("error: pass a corpus directory or set EPUBX_CORPUS", file=sys.stderr)
        return 2

    corpus = Path(args.corpus)
    books, excluded = discover(corpus)
    if args.only:
        books = [p for p in books if args.only in str(p)]
    if args.limit:
        books = books[: args.limit]

    print(f"corpus: {corpus}", file=sys.stderr)
    print(f"discovered {len(books)} books"
          + (f" ({len(excluded)} excluded under hidden dirs)" if excluded else ""),
          file=sys.stderr)

    start = time.perf_counter()
    facts: list[dict] = []
    findings: list[tuple[str, str, str]] = []  # (grade, book, message)
    for i, path in enumerate(books, 1):
        f, found = audit_book(path, corpus)
        facts.append(f)
        for grade, message in found:
            findings.append((grade, f["rel"], message))
        if i % 25 == 0 or i == len(books):
            print(f"  {i}/{len(books)} ({time.perf_counter() - start:.0f}s)",
                  file=sys.stderr)
    elapsed = time.perf_counter() - start

    print()
    print("=" * 72)
    print(f" epubx corpus audit - {corpus}")
    print("=" * 72)
    print(f"books audited: {len(facts)} in {elapsed:.1f}s")
    if excluded:
        print(f"excluded (hidden dirs): {len(excluded)}")
        for rel in excluded:
            print(f"    {rel}")

    reds = [f for f in findings if f[0] == "red"]
    ambers = [f for f in findings if f[0] == "amber"]

    print()
    print(f"RED - content that will not render ({len(reds)} findings)")
    if not reds:
        print("  none")
    for _, book, message in reds:
        print(f"  {book}")
        print(f"      {message}")

    print()
    print(f"AMBER - readable but degraded ({len(ambers)} findings)")
    if not ambers:
        print("  none")
    by_book: dict[str, list[str]] = {}
    for _, book, message in ambers:
        by_book.setdefault(book, []).append(message)
    for book in sorted(by_book):
        print(f"  {book}")
        for message in by_book[book]:
            print(f"      {message}")

    supported = [f for f in facts if f.get("unsupported") is None and f.get("chapters")]
    deferred = [f for f in facts if f.get("unsupported")]
    versions = Counter(f.get("version") or "?" for f in facts)
    toc_sources = Counter(f.get("toc_source") or "?" for f in facts)
    kinds: Counter = Counter()
    for f in supported:
        kinds.update(f.get("kinds") or {})

    def pct(n: int, of: int) -> str:
        return f"{n}/{of} ({100 * n // max(of, 1)}%)"

    total = len(facts)
    n_title = sum(1 for f in facts if f.get("title"))
    n_author = sum(1 for f in facts if f.get("creators"))
    n_lang = sum(1 for f in facts if f.get("language"))
    n_cover = sum(1 for f in facts if f.get("cover"))
    n_fonts = sum(1 for f in facts if f.get("obfuscated_fonts"))
    total_chapters = sum(f.get("chapters") or 0 for f in facts)
    total_blocks = sum(f.get("blocks") or 0 for f in facts)
    total_text = sum(f.get("text_chars") or 0 for f in facts)
    total_foot = sum(f.get("foot_refs") or 0 for f in facts)
    total_dangle = sum(f.get("foot_dangling") or 0 for f in facts)

    print()
    print("FACTS - inventory, never flagged")
    print(f"  metadata      title {pct(n_title, total)}"
          + f" - author {pct(n_author, total)}"
          + f" - language {pct(n_lang, total)}")
    print("  epub version  " + " - ".join(f"{v}: {n}" for v, n in sorted(versions.items())))
    print("  toc source    " + " - ".join(f"{s}: {n}" for s, n in sorted(toc_sources.items())))
    print(f"  chapters      {total_chapters} total")
    print(f"  blocks        {total_blocks} total")
    print(f"  text          {total_text:,} chars across {len(supported)} supported books")
    print(f"  footnotes     {total_foot} refs, {total_dangle} dangling")
    print(f"  cover present {pct(n_cover, total)}")
    print(f"  obfuscated fonts in {n_fonts} books (not DRM - readable)")
    print("  block kinds   " + " - ".join(f"{k} {n}" for k, n in kinds.most_common()))

    print()
    print(f"DEFERRED - named by the library, by design ({len(deferred)})")
    if not deferred:
        print("  none")
    for f in deferred:
        reason = f.get("unsupported") or ""
        kind = "image-only" if reason.startswith("image-only") else "DRM"
        print("  [" + kind + "] " + f["rel"])
        print("      " + reason)

    print()
    print(f"TOTALS: {len(reds)} red - {len(ambers)} amber - {len(facts)} books")
    return 1 if reds else 0


if __name__ == "__main__":
    sys.exit(main())
