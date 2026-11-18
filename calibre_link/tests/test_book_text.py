import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import adapter as A

buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr(
        "META-INF/container.xml",
        '<?xml version="1.0"?><container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>',
    )
    z.writestr(
        "OEBPS/content.opf",
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0">'
        "<manifest>"
        '<item id="t" href="title.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="c" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
        '</manifest><spine><itemref idref="t"/><itemref idref="c"/></spine></package>',
    )
    z.writestr(
        "OEBPS/title.xhtml", "<html><body><h1>Pestilence</h1><p>by Gav Thorpe</p><p>(ebook by Undead)</p></body></html>"
    )
    z.writestr("OEBPS/ch1.xhtml", "<html><body><p>The plague came to the hive city...</p></body></html>")
path = "/tmp/test-book.epub"
open(path, "wb").write(buf.getvalue())
got = A.extract_text_from_epub(path, max_chars=4000)
assert got["supported"], got
assert "Pestilence" in got["head"] and "Gav Thorpe" in got["head"], got["head"]
assert "plague" in got["head"], got["head"]
stamps = A.find_stamps(got["head"])
assert any("Undead" in s for s in stamps), stamps
print("stamps found:", stamps)
print("ALL BOOK-TEXT TESTS PASSED")
