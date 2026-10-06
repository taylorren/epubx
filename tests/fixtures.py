"""Synthetic EPUB fixtures.

The reference corpus (506 books) is read from `EPUBX_CORPUS`; these fixtures
are the floor beneath it, so the suite passes in CI with no books present.
"""

from __future__ import annotations

import io
import zipfile

CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf"
              media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def _png(width: int, height: int) -> bytes:
    """A real PNG of the given size, so Pillow reads its header."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (255, 255, 255)).save(buf, "PNG")
    return buf.getvalue()


def _jpeg(width: int, height: int) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buf, "JPEG")
    return buf.getvalue()


OPF_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="{version}"
         unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"
            xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>{title}</dc:title>
    <dc:creator id="c01" opf:file-as="Doe, Jane" opf:role="aut">{author}</dc:creator>
    <dc:creator id="c02" opf:file-as="Roe, Richard">{author2}</dc:creator>
{role_meta}
    <dc:language>en</dc:language>
    <dc:identifier id="bookid">urn:uuid:{identifier}</dc:identifier>
    <dc:publisher>Test Press</dc:publisher>
    <dc:date>2021-03-04</dc:date>
{extra_metadata}  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml"
          properties="nav"/>
{ncx_item}    <item id="cover-image" href="img/cover.jpg" media-type="image/jpeg"{cover_prop}/>
    <item id="plate" href="img/plate%20one.png" media-type="image/png"/>
    <item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="text/ch2.xhtml" media-type="application/xhtml+xml"/>
    <item id="c3" href="svgcover.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
{guide}  <spine toc="ncx">
    <itemref idref="c1"/>
    <itemref idref="c2"/>
    <itemref idref="c3"/>
  </spine>
</package>
"""

# Real content DRM: an EncryptedKey means a licence is in play. Modelled on a
# Chinese publisher edition that was since removed from the corpus.
DRM_ENCRYPTION = """<?xml version="1.0" encoding="UTF-8"?>
<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
            xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
  <enc:EncryptedKey Id="KEY">
    <enc:EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#rsa-1_5"/>
  </enc:EncryptedKey>
</encryption>
"""

# Font obfuscation: a real, harmless thing that is NOT DRM. Two readable books
# in the corpus ship exactly this.
FONT_OBFUSCATION_ENCRYPTION = """<?xml version="1.0" encoding="UTF-8"?>
<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
            xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
  <enc:EncryptedData>
    <enc:EncryptionMethod Algorithm="http://ns.adobe.com/pdf/enc#RC"/>
    <enc:CipherData>
      <enc:CipherReference URI="OEBPS/fonts/font00001.ttf"/>
    </enc:CipherData>
  </enc:EncryptedData>
</encryption>
"""

NCX_ITEM = '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>\n'

NAV = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>Contents</title></head>
<body>
  <nav epub:type="toc" id="toc">
    <h1>Contents</h1>
    <ol>
      <li><a href="ch1.xhtml">Chapter One</a>
        <ol><li><a href="ch1.xhtml#sec">Section</a></li></ol>
      </li>
      <li><a href="text/ch2.xhtml">Chapter Two</a></li>
    </ol>
  </nav>
</body>
</html>
"""

NCX = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="urn:uuid:test"/></head>
  <docTitle><text>Test Book</text></docTitle>
  <navMap>
    <navPoint id="np1" playOrder="1">
      <navLabel><text>Chapter One</text></navLabel>
      <content src="ch1.xhtml"/>
    </navPoint>
    <navPoint id="np2" playOrder="2">
      <navLabel><text>Chapter Two</text></navLabel>
      <content src="text/ch2.xhtml#top"/>
    </navPoint>
  </navMap>
</ncx>
"""

CHAPTER_1 = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>Chapter One</title></head>
<body>
  <section epub:type="bodymatter">
    <h1 id="top">Chapter One</h1>
    <p id="p1">First paragraph with a marker<a id="r1" epub:type="noteref"
       href="#fn1">1</a> and <a id="r2" epub:type="noteref" href="#fn2">2</a> and <a id="r3" epub:type="noteref"
       href="#fn3">3</a> and a broken link to
       <img src="../OEBPS/img/plate%20one.png" alt="A plate" width="40"/></p>
    <p>An external image is not a zip member:
       <img src="https://example.org/remote.png" alt="remote"/></p>
    <span epub:type="pagebreak" id="pb1"></span>
    <h2 id="sec">A Section</h2>
    <blockquote><p>Quoted material.</p></blockquote>
    <pre>literal   spacing</pre>
    <figure>
      <img src="img/cover.jpg" alt="Cover"/>
      <figcaption>The cover</figcaption>
    </figure>
    <ol>
      <li>First item
        <ul><li>Nested item</li></ul>
      </li>
      <li>Second item</li>
    </ol>
    <dl>
      <dt>Term</dt><dd id="fn1">Its definition, reached by the marker above.</dd>
    </dl>
    <ol>
      <li><div id="fn2" class="footnote" epub:type="footnote"><p>The second note,
        wrapped in a div inside a list item.</p></div></li>
    </ol>
    <div class="endnote" id="fn3"><div><p>The third note, wrapped in bare divs with
      no epub:type anywhere.</p></div></div>
    <table>
      <caption>Measurements</caption>
      <tr><th>Name</th><th>Value</th></tr>
      <tr><td>Width</td><td>10</td></tr>
    </table>
    <p><math xmlns="http://www.w3.org/1998/Math/MathML">
      <mi>x</mi><mo>+</mo><mn>1</mn></math></p>
    <script>var x = 1;</script>
  </section>
</body>
</html>
"""

# A cover wrapped in SVG, as a real book shipped it. lxml's HTML parser keeps
# `xlink:href` as a literal prefixed name rather than expanding the namespace.
SVG_COVER = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Cover</title></head>
<body>
  <div>
    <svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
         width="100%" height="100%" viewBox="0 0 640 480">
      <image width="640" height="480" xlink:href="img/cover.jpg"/>
    </svg>
  </div>
</body>
</html>
"""

CHAPTER_2 = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Chapter Two</title></head>
<body>
  <h1 id="top">Chapter Two</h1>
  <p>A paragraph with a relative link up a level:
     <a epub:type="noteref" href="../ch1.xhtml#fn1">back</a>.</p>
</body>
</html>
"""


def write_epub(path, *, with_ncx: bool = True, with_nav: bool = True,
               drm: bool = False, bom: bool = False, roles: bool = False,
               cover_via: str | None = None, obfuscated_fonts: bool = False):
    """Write a synthetic EPUB exercising every modelled block kind.

    `cover_via` selects the cover convention: "guide", "meta", "properties" or
    None for a book that declares no cover at all. All three appear in the
    corpus, and none is privileged.
    """
    role_meta = (
        '    <meta refines="#c02" property="role" scheme="marc:relators">edt</meta>\n'
        if roles else ""
    )
    guide = ""
    if cover_via == "guide":
        guide = ('  <guide><reference type="cover" title="Cover"'
                 ' href="img/cover.jpg"/></guide>\n')
    elif cover_via == "meta":
        role_meta += '    <meta name="cover" content="cover-image"/>\n'
    elif cover_via == "guide-document":
        # Real books do this: the guide names a title *page*, not an image,
        # while the meta element names the actual cover file.
        guide = ('  <guide><reference type="cover" title="Cover"'
                 ' href="ch1.xhtml"/></guide>\n')
        role_meta += '    <meta name="cover" content="cover-image"/>\n'
    opf = OPF_TEMPLATE.format(
        version="1.0",  # 77% of the corpus declares 1.0 while being EPUB 2
        title="Test Book",
        author="Jane Doe",
        author2="Richard Roe",
        identifier="0000-test",
        extra_metadata="",
        role_meta=role_meta,
        guide=guide,
        cover_prop=' properties="cover-image"' if cover_via == "properties" else "",
        ncx_item=NCX_ITEM if with_ncx else "",
    )
    if not with_nav:  # nav detection is by manifest `properties`
        opf = opf.replace('          properties="nav"', "")

    files = {
        "mimetype": "application/epub+zip",
        "META-INF/container.xml": CONTAINER,
        "OEBPS/content.opf": opf,
        "OEBPS/ch1.xhtml": CHAPTER_1,
        "OEBPS/text/ch2.xhtml": CHAPTER_2,
        "OEBPS/svgcover.xhtml": SVG_COVER,
        "OEBPS/img/cover.jpg": _jpeg(640, 480),
        "OEBPS/img/plate one.png": _png(120, 90),
    }
    if with_nav:
        files["OEBPS/nav.xhtml"] = NAV
    if with_ncx:
        files["OEBPS/toc.ncx"] = NCX
    if drm:
        files["META-INF/encryption.xml"] = DRM_ENCRYPTION
    if obfuscated_fonts:
        files["META-INF/encryption.xml"] = FONT_OBFUSCATION_ENCRYPTION

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        # mimetype must be first and stored, per the OCF spec.
        zf.writestr(
            zipfile.ZipInfo("mimetype"),
            files.pop("mimetype"),
            compress_type=zipfile.ZIP_STORED,
        )
        for name, data in files.items():
            raw = data if isinstance(data, bytes) else data.encode("utf-8")
            # 25% of the corpus puts a BOM before `<?xml`; lxml rejects those.
            if bom and name.endswith((".opf", ".xhtml", ".ncx")):
                raw = b"\xef\xbb\xbf" + raw
            zf.writestr(name, raw)
    return path



def write_scan_epub(path, *, pages: int = 6, drm: bool = False,
                   text_every: int | None = None):
    """A scanned book: one page image per chapter, no prose at all.

    Modelled on two real books in the corpus — a three-volume manga and a
    794-image scan — both of which SPEC.md defers by name.
    """
    manifest = "".join(
        f'    <item id="p{i}" href="p{i}.xhtml" media-type="application/xhtml+xml"/>\n'
        f'    <item id="i{i}" href="img/p{i}.jpg" media-type="image/jpeg"/>\n'
        for i in range(pages)
    )
    spine = "".join(f'    <itemref idref="p{i}"/>\n' for i in range(pages))
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Scanned Volume</dc:title><dc:language>en</dc:language>
    <dc:identifier id="id">urn:uuid:scan</dc:identifier>
  </metadata>
  <manifest>
{manifest}  </manifest>
  <spine>
{spine}  </spine>
</package>
"""
    # `text_every` interleaves a prose chapter among the page images, the shape
    # of a real book that mixes OCR'd text with scanned illustrations.
    image_doc = ('<?xml version="1.0" encoding="utf-8"?>'
                 '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>p</title></head>'
                 '<body><div><img src="img/p{i}.jpg" alt="Image"/></div></body></html>')
    text_doc = ('<?xml version="1.0" encoding="utf-8"?>'
                '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title></head>'
                '<body><p>' + ('Real prose for this chapter. ' * 30) + '</p></body></html>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip",
                    compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", CONTAINER)
        zf.writestr("OEBPS/content.opf", opf)
        for i in range(pages):
            is_text = text_every is not None and i % text_every == 0
            doc = text_doc if is_text else image_doc
            zf.writestr(f"OEBPS/p{i}.xhtml", doc.format(i=i))
            zf.writestr(f"OEBPS/img/p{i}.jpg", _jpeg(600, 800))
        if drm:
            zf.writestr("META-INF/encryption.xml", DRM_ENCRYPTION)
    return path


def write_chapter_epub(path, chapter_html, *, href: str = "chapter.xhtml"):
    """A one-chapter book whose content document is exactly `chapter_html`.

    Used to pin the shapes real books put *loose prose* in: a container's own
    text beside a nested block (*On China*), and `<br/>` tails inside a div
    that also holds a list (*Sheng Si Suo*). See PITFALLS §1.
    """
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Loose Text</dc:title><dc:language>en</dc:language>
    <dc:identifier id="id">urn:uuid:loose-text</dc:identifier>
  </metadata>
  <manifest>
    <item id="c1" href="{href}" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="c1"/>
  </spine>
</package>
"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip",
                    compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", CONTAINER)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr(f"OEBPS/{href}", chapter_html)
    return path
