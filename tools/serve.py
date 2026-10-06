#!/usr/bin/env python3
"""A minimal reader, to test what epubx serves.

The book's own files are served at the book's own paths, wrapped in a shell
that adds what a renderer cannot know on its own: the table of contents, the
spine order, and the footnote edges epubx resolved. Rendering stays the
browser's job — the iframe is sandboxed so nothing from the book can run.

    python3 tools/serve.py path/to/book.epub [--port 8000] [--strict-types]

Then open http://127.0.0.1:8000/ .

Routes:
    /                             the shell: TOC, prev/next, footnotes, text
    /book/<path>                  one of the book's own files, at its own path
    /api/book.json                title, spine order, TOC tree
    /api/text.json?chapter=N      the chapter's text, as epubx extracted it
    /api/footnotes.json?chapter=N footnote markers and where they resolve
"""

from __future__ import annotations

import argparse
import html
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import epubx


def book_url(href: str) -> str:
    """The URL a book file is served at — its own path, escaped for a browser."""
    return "/book/" + quote(href, safe="/")


def toc_json(nodes) -> list[dict]:
    """The TOC tree, as the shell needs it."""
    return [
        {
            "label": node.label,
            "href": node.href,
            "anchor": node.anchor,
            "children": toc_json(node.children),
        }
        for node in nodes
    ]


def footnotes(book, index: int) -> list[dict]:
    """Every footnote marker in a chapter, with the block it resolves to.

    This is the part a browser cannot do on its own: the marker may sit in one
    document and its note in another, and `epubx` has already resolved that
    edge to a block id.
    """
    chapter = book.chapters[index]
    edges: list[dict] = []
    for block in chapter.blocks:
        for node in block:  # Block.__iter__ walks nested blocks too
            if node.kind != "footnote_ref":
                continue
            target = node.attributes.get("target_id")
            edge = {
                "text": node.text,
                "href": node.attributes.get("href"),
                "resolved": bool(target),
                "target_chapter": None,
                "target_url": None,
            }
            if target:
                target_index = int(target[1:5])
                target_chapter = book.chapters[target_index]
                target_block = target_chapter.block_by_id(target)
                dom_ids = (target_block.attributes.get("dom_ids") or ()) if target_block else ()
                url = book_url(target_chapter.href)
                edge["target_chapter"] = target_index
                edge["target_url"] = f"{url}#{quote(dom_ids[0])}" if dom_ids else url
            edges.append(edge)
    return edges


def content_type(media_type: str, lenient: bool) -> str:
    """The Content-Type to serve a book file with.

    Lenient by default: XHTML goes out as `text/html`, which is how readers
    render the many books whose markup a strict XML parser would reject. Text
    types get a charset so the browser does not have to guess.
    """
    if lenient and media_type == "application/xhtml+xml":
        media_type = "text/html"
    if media_type.startswith("text/") or media_type in (
        "application/xhtml+xml", "application/xml", "application/json",
        "application/oebps-package+xml", "image/svg+xml",
    ):
        media_type = f"{media_type}; charset=utf-8"
    return media_type


class Handler(BaseHTTPRequestHandler):
    """Serves the shell, the book's own files, and the three small APIs."""

    book = None
    lenient = True
    lock = threading.Lock()

    def do_GET(self):  # noqa: N802 - stdlib naming
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path == "/":
                title = html.escape(self.book.metadata.title or "Book")
                return self.send_html(SHELL.replace("{title}", title))
            if path == "/api/book.json":
                with self.lock:
                    payload = {
                        "title": self.book.metadata.title,
                        "spine": list(self.book.spine),
                        "toc": toc_json(self.book.toc),
                    }
                return self.send_json(payload)
            if path == "/api/text.json":
                index = self.chapter_index(query)
                with self.lock:
                    text = self.book.chapters[index].plain_text
                return self.send_json({"chapter": index, "text": text})
            if path == "/api/footnotes.json":
                index = self.chapter_index(query)
                with self.lock:
                    edges = footnotes(self.book, index)
                return self.send_json({"chapter": index, "footnotes": edges})
            if path.startswith("/book/"):
                requested = unquote(path[len("/book/"):])
                with self.lock:
                    resource = self.book.resource(requested)
                    if resource is None:
                        return self.send_error_text(
                            404, f"not a file in this book: {requested}")
                    data = resource.read()
                    media_type = resource.media_type
                return self.send_bytes(data, content_type(media_type, self.lenient))
            return self.send_error_text(404, "not found")
        except (IndexError, ValueError) as exc:
            return self.send_error_text(400, str(exc))

    def chapter_index(self, query) -> int:
        return int((query.get("chapter") or ["0"])[0])

    def _respond(self, status: int, body: bytes, content_type_: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type_)
        self.send_header("Content-Length", str(len(body)))
        # Local testing: never let the browser cache a book that may change.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, markup: str) -> None:
        self._respond(200, markup.encode("utf-8"), "text/html; charset=utf-8")

    def send_json(self, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._respond(200, body, "application/json; charset=utf-8")

    def send_bytes(self, data: bytes, media_type: str) -> None:
        self._respond(200, data, media_type)

    def send_error_text(self, status: int, message: str) -> None:
        self._respond(status, message.encode("utf-8"), "text/plain; charset=utf-8")

    def log_message(self, fmt, *args):  # keep the console readable
        sys.stderr.write("  %s\n" % (fmt % args))


class Server(ThreadingHTTPServer):
    """An HTTP server that does not look up its own hostname.

    `HTTPServer.server_bind` calls `socket.getfqdn()` to fill in `server_name`.
    On a machine whose own name resolves slowly — a NAS mount, a VPN, a
    half-configured resolver — that single call blocks for tens of seconds
    before the server ever listens. The name is used for logging only, so the
    address is kept as-is instead.
    """

    def server_bind(self) -> None:
        import socketserver

        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def create_server(book, host: str = "127.0.0.1", port: int = 8000,
                  lenient: bool = True) -> Server:
    """A server for one already-open book. Port 0 picks a free one."""
    handler = type("BoundHandler", (Handler,), {"book": book, "lenient": lenient})
    return Server((host, port), handler)



SHELL_HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title} — epubx</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 15px/1.5 system-ui, sans-serif; display: flex; height: 100vh; }
  #side { width: 320px; min-width: 220px; border-right: 1px solid #8884; overflow: auto; padding: 12px; }
  #side h1 { font-size: 15px; margin: 0 0 10px; }
  #side ul { list-style: none; margin: 0; padding-left: 12px; }
  #side > nav > ul { padding-left: 0; }
  #side a { color: inherit; text-decoration: none; display: block; padding: 2px 4px; border-radius: 4px; }
  #side a:hover { background: #8882; }
  #side a.here { background: #0af3; font-weight: 600; }
  main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
  #bar { display: flex; gap: 8px; align-items: center; padding: 8px 12px; border-bottom: 1px solid #8884; }
  button { font: inherit; padding: 3px 10px; }
  #where { opacity: .7; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #content { flex: 1; border: 0; width: 100%; background: #fff; }
  #panel { flex: 1; overflow: auto; padding: 16px 20px; margin: 0; white-space: pre-wrap; }
  #panel ul { list-style: none; padding: 0; }
  #panel li { margin: 0 0 8px; }
  #panel .unresolved { opacity: .6; }
</style>
</head>
<body>
<aside id="side">
  <h1>{title}</h1>
  <nav id="toc">loading…</nav>
</aside>
<main>
  <div id="bar">
    <button id="prev">‹ Prev</button>
    <button id="next">Next ›</button>
    <button id="notes">Footnotes</button>
    <button id="text">epubx text</button>
    <span id="where"></span>
  </div>
  <iframe id="content" sandbox="allow-same-origin"></iframe>
  <pre id="panel" hidden></pre>
</main>
"""


SHELL_JS = """<script>
let spine = [], index = 0;

const frame = document.getElementById('content');
const panel = document.getElementById('panel');
const where = document.getElementById('where');

const path = (p) => p.split('/').map(encodeURIComponent).join('/');
const frag = (a) => a ? '#' + encodeURIComponent(a) : '';

function renderToc(nodes, parent) {
  const ul = document.createElement('ul');
  for (const node of nodes) {
    const li = document.createElement('li');
    if (node.href) {
      const a = document.createElement('a');
      a.textContent = node.label || node.href;
      a.dataset.href = node.href;
      a.href = '#';
      a.onclick = (e) => { e.preventDefault(); open(node); };
      li.appendChild(a);
    } else {
      const span = document.createElement('span');
      span.textContent = node.label;
      li.appendChild(span);
    }
    if (node.children.length) renderToc(node.children, li);
    ul.appendChild(li);
  }
  parent.appendChild(ul);
}

function open(node) {
  const i = spine.indexOf(node.href);
  if (i >= 0) return load(i, node.anchor);
  // A TOC target outside the spine — a notes file, say. Show the file itself.
  show('/book/' + path(node.href) + frag(node.anchor));
}

function load(i, anchor) {
  index = Math.max(0, Math.min(i, spine.length - 1));
  panel.hidden = true;
  frame.hidden = false;
  frame.src = '/book/' + path(spine[index]) + frag(anchor);
  mark();
}

function show(url) {
  panel.hidden = true;
  frame.hidden = false;
  frame.src = url;
}

function mark() {
  where.textContent = (index + 1) + ' / ' + spine.length + '  ' + spine[index];
  for (const a of document.querySelectorAll('#toc a')) {
    a.classList.toggle('here', a.dataset.href === spine[index]);
  }
}

async function api(route) {
  const res = await fetch(route);
  return res.json();
}

async function footnotes() {
  const data = await api('/api/footnotes.json?chapter=' + index);
  const list = document.createElement('ul');
  if (!data.footnotes.length) {
    list.innerHTML = '<li>no footnote markers in this chapter</li>';
  }
  for (const edge of data.footnotes) {
    const li = document.createElement('li');
    if (edge.resolved) {
      const a = document.createElement('a');
      a.href = '#';
      a.textContent = (edge.text || edge.href) + ' → chapter ' + (edge.target_chapter + 1);
      a.onclick = (e) => { e.preventDefault(); show(edge.target_url); };
      li.appendChild(a);
    } else {
      li.className = 'unresolved';
      li.textContent = (edge.text || edge.href) + ' → unresolved';
    }
    list.appendChild(li);
  }
  panel.hidden = false;
  frame.hidden = true;
  panel.textContent = '';
  panel.appendChild(list);
}

async function text() {
  const data = await api('/api/text.json?chapter=' + index);
  panel.hidden = false;
  frame.hidden = true;
  panel.textContent = data.text || '(epubx extracted no text from this chapter)';
}

document.getElementById('prev').onclick = () => load(index - 1);
document.getElementById('next').onclick = () => load(index + 1);
document.getElementById('notes').onclick = footnotes;
document.getElementById('text').onclick = text;
document.addEventListener('keydown', (e) => {
  if (e.key === 'ArrowLeft') load(index - 1);
  if (e.key === 'ArrowRight') load(index + 1);
});

// The book's own links (footnotes, cross-references) navigate the iframe.
// Follow along, so prev/next and the TOC stay in step.
frame.addEventListener('load', () => {
  try {
    const p = frame.contentWindow.location.pathname;
    if (!p.startsWith('/book/')) return;
    const href = decodeURIComponent(p.slice('/book/'.length));
    const i = spine.indexOf(href);
    if (i >= 0 && i !== index) { index = i; mark(); }
  } catch (e) { /* opaque origin: nothing to sync */ }
});

(async () => {
  const data = await api('/api/book.json');
  spine = data.spine;
  document.getElementById('toc').textContent = '';
  renderToc(data.toc, document.getElementById('toc'));
  load(0);
})();
</script>
</body>
</html>
"""

SHELL = SHELL_HEAD + SHELL_JS



def main() -> int:
    parser = argparse.ArgumentParser(description="serve one EPUB to a browser")
    parser.add_argument("book", help="path to an .epub file")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--strict-types", action="store_true",
                        help="serve declared media types instead of text/html for XHTML")
    args = parser.parse_args()

    with epubx.open_book(args.book) as book:
        if book.unsupported:
            print(f"note: epubx names this book unsupported: {book.unsupported}",
                  file=sys.stderr)
        server = create_server(book, args.host, args.port,
                               lenient=not args.strict_types)
        host, port = server.server_address[:2]
        print(f"serving {Path(args.book).name} — {len(book.spine)} chapters, "
              f"{book.metadata.title!r}", file=sys.stderr)
        print(f"  open http://{host}:{port}/   (Ctrl-C to stop)", file=sys.stderr)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("stopped", file=sys.stderr)
        finally:
            server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

