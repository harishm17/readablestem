"""Word files made to hurt the Word reader (`source.docx_package`, `source.pandoc`): a part that
inflates, a member named to leave the package, a macro part, a linked picture, a document type
with an entity that names a file of the host, a document nested past any reader's stack, and an
embedded object. Each is a sound one-paragraph document plus the one thing the case is about, so
that what ends the file is that thing. Made for the hostile-input set with the Word reader
(WS18.14, WS18.15), November 18, 2026."""

from __future__ import annotations

import io
import zipfile

from fixtures import office

from . import fixture
from .rawpdf import ZIP_DATE

ORIGIN = "hostile-input set, Word reader, 2026-11-18"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
EMBEDDED_MARKER = "EMBEDDED-SECRET-MARKER"


def _zip(parts: dict[str, bytes | str]) -> bytes:
    """The package with every member dated the same, so the bytes do not change."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return out.getvalue()


def _with(document: str) -> bytes:
    """The sound document with `word/document.xml` replaced."""
    return _zip(office.docx_parts() | {"word/document.xml": document})


def _paragraph(inner: str) -> str:
    document = str(office.docx_parts()["word/document.xml"])
    return document.replace("<w:body>", f"<w:body>{inner}", 1)


@fixture(
    "docx-inflating-part.docx", origin=ORIGIN, shows="a document part of 60 MB that packs to 60 KB"
)
def inflating_part() -> bytes:
    text = "<w:p><w:r><w:t>" + "a" * 60_000_000 + "</w:t></w:r></w:p>"
    return _with(_paragraph(text))


@fixture(
    "docx-member-leaves-package.docx", origin=ORIGIN, shows="a member named word/../../evil.xml"
)
def member_leaves_package() -> bytes:
    parts = office.docx_parts()
    parts["word/../../evil.xml"] = "<x/>"
    return _zip(parts)


@fixture("docx-macro-part.docx", origin=ORIGIN, shows="a vbaProject.bin part")
def macro_part() -> bytes:
    parts = office.docx_parts()
    parts["word/vbaProject.bin"] = b"\xd0\xcf\x11\xe0 not a real macro"
    return _zip(parts)


@fixture("docx-linked-picture.docx", origin=ORIGIN, shows="a picture linked to an outside address")
def linked_picture() -> bytes:
    parts = office.docx_parts()
    parts["word/_rels/document.xml.rels"] = str(parts["word/_rels/document.xml.rels"]).replace(
        'Target="media/image1.png"/>',
        f'Target="media/image1.png"/><Relationship Id="rId9" Type="{NS_R}/image" '
        'Target="http://127.0.0.1:9/outside.png" TargetMode="External"/>',
    )
    parts["word/document.xml"] = str(parts["word/document.xml"]).replace(
        'r:embed="rId2"', 'r:link="rId9"'
    )
    return _zip(parts)


@fixture("docx-entity.docx", origin=ORIGIN, shows="an external entity naming /etc/passwd")
def entity() -> bytes:
    document = str(office.docx_parts()["word/document.xml"])
    declared = '<!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
    head, _, rest = document.partition("?>")
    return _with(f"{head}?>{declared}{rest.replace('<w:t>', '<w:t>&x;', 1)}")


@fixture("docx-nested-deep.docx", origin=ORIGIN, shows="content nested 5,000 elements deep")
def nested_deep() -> bytes:
    deep = "<w:sdt><w:sdtContent>" * 2500 + "<w:p/>" + "</w:sdtContent></w:sdt>" * 2500
    return _with(_paragraph(deep))


@fixture("docx-embedded-object.docx", origin=ORIGIN, shows="an embedded OLE object with a marker")
def embedded_object() -> bytes:
    parts = office.docx_parts()
    rels = str(parts["word/_rels/document.xml.rels"]).replace(
        "</Relationships>",
        f'<Relationship Id="rId7" Type="{NS_R}/oleObject" Target="embeddings/oleObject1.bin"/>'
        "</Relationships>",
    )
    parts["word/_rels/document.xml.rels"] = rels
    parts["word/embeddings/oleObject1.bin"] = EMBEDDED_MARKER.encode() + b"\x00" * 64
    parts["word/document.xml"] = _paragraph(
        '<w:p><w:r><w:object><v:shape xmlns:v="urn:schemas-microsoft-com:vml">'
        '<v:imagedata r:id="rId2"/></v:shape>'
        '<o:OLEObject xmlns:o="urn:schemas-microsoft-com:office:office" r:id="rId7"/>'
        "</w:object></w:r></w:p>"
    )
    return _zip(parts)
