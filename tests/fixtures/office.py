"""Small Word and PowerPoint files, generated here (no binary fixtures), and hostile variants.

`docx()` is a Word document with a Heading 1, a Heading 2, two paragraphs and a picture that
has alt text; `pptx()` is a two-slide presentation (a title and a text box on each, a picture
with alt text on the first). Both are minimal OOXML written by hand, so a test can change a
part (`rebuild`) to make a macro, a linked picture, a DOCTYPE and so on. The pictures are one
pixel. LibreOffice opens `docx()` (checked); no Impress component is installed on the
development box, so `pptx()` is only checked structurally here (`office_convert.preflight`).
"""

from __future__ import annotations

import base64
import io
import zipfile

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OD = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
OLE_HEADER = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504
ALT = "A cart rolling down a ramp"

# --------------------------------------------------------------------------- Word

_DOCX_TYPES = (
    HEAD + f'<Types xmlns="{CT_NS}">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Default Extension="png" ContentType="image/png"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    "</Types>"
)
_ROOT_RELS = (
    HEAD + f'<Relationships xmlns="{RELS_NS}"><Relationship Id="rId1" '
    f'Type="{OD}/officeDocument" Target="%s"/></Relationships>'
)
_DOCX_RELS = (
    HEAD + f'<Relationships xmlns="{RELS_NS}">'
    f'<Relationship Id="rId1" Type="{OD}/styles" Target="styles.xml"/>'
    f'<Relationship Id="rId2" Type="{OD}/image" Target="media/image1.png"/>'
    "</Relationships>"
)
_STYLES = (
    HEAD + f'<w:styles xmlns:w="{W_NS}">'
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
    '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:pPr><w:keepNext/><w:outlineLvl w:val="0"/></w:pPr>'
    '<w:rPr><w:b/><w:sz w:val="40"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/>'
    '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:pPr><w:keepNext/><w:outlineLvl w:val="1"/></w:pPr>'
    '<w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style></w:styles>'
)
_DOCUMENT = (
    HEAD + f'<w:document xmlns:w="{W_NS}" xmlns:r="{OD}" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><w:body>'
    '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Kinematics in one dimension</w:t></w:r></w:p>'
    "<w:p><w:r><w:t>The average speed is the distance divided by the time taken.</w:t></w:r></w:p>"
    '<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>A worked example</w:t></w:r></w:p>'
    '<w:p><w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
    f'<wp:extent cx="1440000" cy="1440000"/><wp:docPr id="1" name="Picture 1" descr="{ALT}"/>'
    '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
    f'<pic:pic><pic:nvPicPr><pic:cNvPr id="1" name="ramp.png" descr="{ALT}"/><pic:cNvPicPr/></pic:nvPicPr>'
    '<pic:blipFill><a:blip r:embed="rId2"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
    '<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="1440000" cy="1440000"/></a:xfrm>'
    '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
    "</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"
    "<w:p><w:r><w:t>A cart covers 12 m in 3 s, so its average speed is 4 m/s.</w:t></w:r></w:p>"
    '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" '
    'w:bottom="1440" w:left="1440" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
    "</w:body></w:document>"
)


def _zip(parts: dict[str, bytes | str]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return out.getvalue()


def docx_parts() -> dict[str, bytes | str]:
    return {
        "[Content_Types].xml": _DOCX_TYPES,
        "_rels/.rels": _ROOT_RELS % "word/document.xml",
        "word/document.xml": _DOCUMENT,
        "word/styles.xml": _STYLES,
        "word/_rels/document.xml.rels": _DOCX_RELS,
        "word/media/image1.png": PNG,
    }


def docx() -> bytes:
    return _zip(docx_parts())


# --------------------------------------------------------------------------- PowerPoint

_P_NS = (
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    f'xmlns:r="{OD}" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
)
_PPT = "application/vnd.openxmlformats-officedocument.presentationml"
_PPTX_TYPES = (
    HEAD + f'<Types xmlns="{CT_NS}">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Default Extension="png" ContentType="image/png"/>'
    f'<Override PartName="/ppt/presentation.xml" ContentType="{_PPT}.presentation.main+xml"/>'
    f'<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="{_PPT}.slideMaster+xml"/>'
    f'<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="{_PPT}.slideLayout+xml"/>'
    f'<Override PartName="/ppt/slides/slide1.xml" ContentType="{_PPT}.slide+xml"/>'
    f'<Override PartName="/ppt/slides/slide2.xml" ContentType="{_PPT}.slide+xml"/>'
    '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
    "</Types>"
)
_TREE = (
    '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
    '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/>'
    '<a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
)


def _rels(*items: tuple[str, str, str]) -> str:
    body = "".join(f'<Relationship Id="{i}" Type="{OD}/{t}" Target="{g}"/>' for i, t, g in items)
    return HEAD + f'<Relationships xmlns="{RELS_NS}">{body}</Relationships>'


def _slide(title: str, text: str, picture: bool) -> str:
    pic = (
        f'<p:pic><p:nvPicPr><p:cNvPr id="4" name="Picture 3" descr="{ALT}"/><p:cNvPicPr/><p:nvPr/>'
        '</p:nvPicPr><p:blipFill><a:blip r:embed="rId2"/><a:stretch><a:fillRect/></a:stretch>'
        '</p:blipFill><p:spPr><a:xfrm><a:off x="457200" y="3000000"/><a:ext cx="1800000" cy="1800000"/>'
        '</a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'
        if picture
        else ""
    )
    return (
        HEAD + f"<p:sld {_P_NS}><p:cSld><p:spTree>{_TREE}"
        '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title 1"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
        f'<p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:p><a:r>'
        f'<a:rPr lang="en-US"/><a:t>{title}</a:t></a:r></a:p></p:txBody></p:sp>'
        '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Text 2"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="457200" y="1600200"/><a:ext cx="8229600" cy="1000000"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr><p:txBody><a:bodyPr/><a:p><a:r>'
        f'<a:rPr lang="en-US" sz="2400"/><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp>'
        f"{pic}</p:spTree></p:cSld></p:sld>"
    )


def pptx_parts() -> dict[str, bytes | str]:
    return {
        "[Content_Types].xml": _PPTX_TYPES,
        "_rels/.rels": _ROOT_RELS % "ppt/presentation.xml",
        "ppt/presentation.xml": HEAD
        + f'<p:presentation {_P_NS}><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/>'
        '</p:sldMasterIdLst><p:sldIdLst><p:sldId id="256" r:id="rId2"/><p:sldId id="257" r:id="rId3"/>'
        '</p:sldIdLst><p:sldSz cx="9144000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/>'
        "</p:presentation>",
        "ppt/_rels/presentation.xml.rels": _rels(
            ("rId1", "slideMaster", "slideMasters/slideMaster1.xml"),
            ("rId2", "slide", "slides/slide1.xml"),
            ("rId3", "slide", "slides/slide2.xml"),
            ("rId4", "theme", "theme/theme1.xml"),
        ),
        "ppt/slideMasters/slideMaster1.xml": HEAD
        + f"<p:sldMaster {_P_NS}><p:cSld><p:spTree>{_TREE}</p:spTree></p:cSld>"
        '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" '
        'accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" '
        'folHlink="folHlink"/><p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/>'
        "</p:sldLayoutIdLst></p:sldMaster>",
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": _rels(
            ("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml"),
            ("rId2", "theme", "../theme/theme1.xml"),
        ),
        "ppt/slideLayouts/slideLayout1.xml": HEAD
        + f'<p:sldLayout {_P_NS} type="titleOnly"><p:cSld name="Title Only"><p:spTree>{_TREE}'
        "</p:spTree></p:cSld></p:sldLayout>",
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": _rels(
            ("rId1", "slideMaster", "../slideMasters/slideMaster1.xml")
        ),
        "ppt/theme/theme1.xml": HEAD
        + '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="T"/>',
        "ppt/slides/slide1.xml": _slide(
            "Average speed", "Speed is distance divided by time.", True
        ),
        "ppt/slides/_rels/slide1.xml.rels": _rels(
            ("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml"),
            ("rId2", "image", "../media/image1.png"),
        ),
        "ppt/slides/slide2.xml": _slide("Worked example", "A cart covers 12 m in 3 s.", False),
        "ppt/slides/_rels/slide2.xml.rels": _rels(
            ("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml")
        ),
        "ppt/media/image1.png": PNG,
    }


def pptx() -> bytes:
    return _zip(pptx_parts())


# --------------------------------------------------------------------------- variants


def rebuild(
    base: dict[str, bytes | str],
    *,
    replace: dict[str, bytes | str] | None = None,
    add: dict[str, bytes | str] | None = None,
    drop: tuple[str, ...] = (),
) -> bytes:
    """`base` with parts replaced, added or dropped, as a ZIP."""
    parts = {name: content for name, content in base.items() if name not in drop}
    parts.update(replace or {})
    parts.update(add or {})
    return _zip(parts)


def with_external_picture(url: str = "http://127.0.0.1:9/ext.png") -> bytes:
    """A Word document whose picture is linked, not embedded (LibreOffice fetches such a
    picture's address when it converts)."""
    parts = docx_parts()
    rels = str(parts["word/_rels/document.xml.rels"]).replace(
        'Target="media/image1.png"/>',
        f'Target="media/image1.png"/><Relationship Id="rId9" Type="{OD}/image" '
        f'Target="{url}" TargetMode="External"/>',
    )
    document = str(parts["word/document.xml"]).replace('r:embed="rId2"', 'r:link="rId9"')
    return rebuild(
        parts, replace={"word/_rels/document.xml.rels": rels, "word/document.xml": document}
    )


def with_hyperlink(url: str = "https://example.org/") -> bytes:
    """A Word document with an ordinary external hyperlink (allowed)."""
    parts = docx_parts()
    rels = str(parts["word/_rels/document.xml.rels"]).replace(
        "</Relationships>",
        f'<Relationship Id="rId8" Type="{OD}/hyperlink" Target="{url}" TargetMode="External"/>'
        "</Relationships>",
    )
    return rebuild(parts, replace={"word/_rels/document.xml.rels": rels})


def docx_with_equation() -> bytes:
    """`docx()` plus a displayed equation, v = d / t, written as Word's own maths (OMML)."""
    parts = docx_parts()
    math = (
        "<w:p><m:oMathPara><m:oMath><m:r><m:t>v=</m:t></m:r><m:f><m:num><m:r><m:t>d</m:t></m:r>"
        "</m:num><m:den><m:r><m:t>t</m:t></m:r></m:den></m:f></m:oMath></m:oMathPara></w:p>"
    )
    document = (
        str(parts["word/document.xml"])
        .replace(
            "<w:document ",
            '<w:document xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" ',
            1,
        )
        .replace("<w:sectPr>", math + "<w:sectPr>", 1)
    )
    return rebuild(parts, replace={"word/document.xml": document})
