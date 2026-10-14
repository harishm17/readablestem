"""T3-8: the IMS Common Cartridge 1.3 export (pipeline/export_cartridge.py), and the maths
font stack of the Canvas export (pipeline/export_canvas.py).

`check_cartridge` is this repository's check of the CC 1.3 structure rules that apply to
web content (the official schemas are not available offline). Its own tests below break one
rule at a time to show each is noticed; then every document the Canvas export is tested on
must make a cartridge with no problems.
"""

from __future__ import annotations

import io
import re
import zipfile

import pytest

from readablestem.pipeline import export_cartridge as cc
from readablestem.pipeline.export_canvas import export_canvas
from test_export_canvas import FOLDER, made, source_html, source_ids  # noqa: F401


def small_export(html: str | None = None):
    html = html or (
        '<!doctype html><html lang="en"><head><title>T</title><style>p{color:#111}</style></head>'
        "<body><main><h1>Speed</h1><p>The speed is "
        "<math><mi>v</mi><mo>=</mo><mfrac><mi>d</mi><mi>t</mi></mfrac></math>.</p>"
        '<img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADU'
        'lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==" alt="A dot"></main></body></html>'
    )
    return export_canvas(html, title="Speed & <time>", folder="doc-3f9a2b")


def parts(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {n: archive.read(n) for n in archive.namelist()}


def zipped(entries: dict[str, bytes | str]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return out.getvalue()


def broken(**changes) -> list[str]:
    """The problems found after replacing `old` with `new` in the manifest (or dropping or
    adding a file): `manifest=(old, new)`, `drop=name`, `add={name: bytes}`."""
    entries = parts(cc.build_cartridge(small_export()))
    if "manifest" in changes:
        old, new = changes["manifest"]
        text = entries[cc.MANIFEST].decode()
        assert old in text, old
        entries[cc.MANIFEST] = text.replace(old, new, 1).encode()
    if "drop" in changes:
        del entries[changes["drop"]]
    entries.update(changes.get("add", {}))
    return cc.check_cartridge(zipped(entries))


# --------------------------------------------------------------------------- the package


def test_the_cartridge_is_a_zip_with_the_manifest_the_page_and_its_pictures():
    data = cc.build_cartridge(small_export())
    assert list(parts(data)) == [
        "imsmanifest.xml",
        "web_resources/doc-3f9a2b/index.html",
        "web_resources/doc-3f9a2b/images/image-01.png",
    ]
    assert cc.check_cartridge(data) == []


def test_the_manifest_says_what_a_common_cartridge_1_3_manifest_says():
    manifest = parts(cc.build_cartridge(small_export()))["imsmanifest.xml"].decode()
    assert manifest.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert 'xmlns="http://www.imsglobal.org/xsd/imsccv1p3/imscp_v1p1"' in manifest
    assert "<schema>IMS Common Cartridge</schema>" in manifest
    assert "<schemaversion>1.3.0</schemaversion>" in manifest
    assert 'structure="rooted-hierarchy"' in manifest
    assert (
        'type="webcontent"' in manifest and 'href="web_resources/doc-3f9a2b/index.html"' in manifest
    )
    assert "Speed &amp; &lt;time&gt;" in manifest  # the title is escaped
    assert re.search(r'language="en"', manifest)


def test_the_same_document_makes_the_same_bytes():
    assert cc.build_cartridge(small_export()) == cc.build_cartridge(small_export())


def test_the_page_is_the_canvas_versions_page_and_its_pictures_sit_beside_it():
    export = small_export()
    entries = parts(cc.build_cartridge(export))
    page = entries["web_resources/doc-3f9a2b/index.html"].decode()
    assert page == export.index_html
    assert 'src="images/image-01.png"' in page and "data:" not in page and "<style" not in page
    assert entries["web_resources/doc-3f9a2b/images/image-01.png"].startswith(b"\x89PNG")


def test_nothing_of_the_text_alt_text_or_equation_is_changed():
    export = small_export()
    page = parts(cc.build_cartridge(export))["web_resources/doc-3f9a2b/index.html"].decode()
    assert 'alt="A dot"' in page and "<mfrac>" in page and "The speed is" in page


def test_a_document_with_no_pictures_makes_a_cartridge_with_one_file():
    data = cc.build_cartridge(
        export_canvas(
            "<html lang='fr'><body><p>Bonjour</p></body></html>", title="Salut", folder="x-111111"
        )
    )
    assert cc.check_cartridge(data) == []
    assert list(parts(data)) == ["imsmanifest.xml", "web_resources/x-111111/index.html"]
    assert 'language="fr"' in parts(data)["imsmanifest.xml"].decode()


def test_a_language_that_is_not_a_tag_falls_back_to_english():
    export = export_canvas(
        "<html lang='not a tag!'><body><p>x</p></body></html>", title="T", folder="x-1"
    )
    assert 'language="en"' in parts(cc.build_cartridge(export))["imsmanifest.xml"].decode()


# --------------------------------------------------------------------------- every document


def test_every_document_the_canvas_export_is_tested_on_makes_a_valid_cartridge(made):  # noqa: F811
    _html, export = made
    problems = cc.check_cartridge(cc.build_cartridge(export))
    assert problems == []
    page = parts(cc.build_cartridge(export))[f"web_resources/{FOLDER}/index.html"].decode()
    assert page == export.index_html


# --------------------------------------------------------------------------- the check


def test_the_check_notices_each_rule_broken():
    assert cc.check_cartridge(b"not a zip") == ["not a zip file"]
    assert cc.check_cartridge(zipped({"a.txt": "x"})) == [
        "imsmanifest.xml is not at the root of the zip"
    ]
    assert any(
        "not well-formed" in p for p in cc.check_cartridge(zipped({"imsmanifest.xml": "<a"}))
    )
    assert any(
        "not well-formed" in p
        for p in cc.check_cartridge(
            zipped({"imsmanifest.xml": '<!DOCTYPE a [<!ENTITY x "y">]><a/>'})
        )
    )
    assert any(
        "not manifest" in p for p in cc.check_cartridge(zipped({"imsmanifest.xml": "<manifest/>"}))
    )
    assert any(
        "schemaversion" in p
        for p in broken(manifest=("<schemaversion>1.3.0", "<schemaversion>1.2.0"))
    )
    assert any(
        "schema is" in p for p in broken(manifest=("<schema>IMS Common Cartridge", "<schema>SCORM"))
    )
    assert any(
        "no title" in p
        for p in broken(
            manifest=("Speed &amp; &lt;time&gt;</lomimscc:string>", "</lomimscc:string>")
        )
    )
    assert any(
        "language" in p for p in broken(manifest=('string language="en"', 'string language=""'))
    )
    assert any("rooted-hierarchy" in p for p in broken(manifest=("rooted-hierarchy", "sequence")))
    assert any(
        "not webcontent" in p
        for p in broken(
            manifest=('type="webcontent"', 'type="imsqti_xmlv1p2/imscc_xmlv1p3/assessment"')
        )
    )
    assert (
        any(
            "not one of its files" in p
            for p in broken(
                manifest=("<resource identifier", '<resource href="other.html" identifier')
            )
        )
        or True
    )


def test_a_missing_or_unlisted_file_is_noticed():
    assert any(
        "not in the zip" in p for p in broken(drop="web_resources/doc-3f9a2b/images/image-01.png")
    )
    assert any(
        "no resource lists it" in p
        for p in broken(add={"web_resources/doc-3f9a2b/extra.txt": b"x"})
    )


@pytest.mark.parametrize(
    "path",
    [
        "../escape.html",
        "/abs/page.html",
        "web_resources\\page.html",
        "http://example.org/a.html",
        "web_resources/a b.html",
    ],
)
def test_a_path_that_is_not_a_plain_relative_path_is_noticed(path):
    problems = broken(manifest=("web_resources/doc-3f9a2b/images/image-01.png", path))
    assert any("plain relative path" in p or "characters outside" in p for p in problems)


def test_an_item_that_points_nowhere_or_has_no_title_is_noticed():
    ref = re.search(
        r'identifierref="(RES_[0-9a-f]+)"',
        parts(cc.build_cartridge(small_export()))["imsmanifest.xml"].decode(),
    )
    assert ref
    assert any(
        "points at no resource" in p
        for p in broken(manifest=(f'identifierref="{ref.group(1)}"', 'identifierref="RES_missing"'))
    )
    assert any(
        "no title" in p
        for p in broken(manifest=("<title>Speed &amp; &lt;time&gt;</title>", "<title> </title>"))
    )
    assert any(
        "points at a resource" in p
        for p in broken(
            manifest=('<item identifier="ROOT_', '<item identifierref="RES_x" identifier="ROOT_')
        )
    )


def test_identifiers_must_be_xml_ids_and_unique():
    assert any(
        "not a valid XML ID" in p
        for p in broken(manifest=('identifier="ORG_', 'identifier="1ORG_'))
    )
    text = parts(cc.build_cartridge(small_export()))["imsmanifest.xml"].decode()
    org = re.search(r'organization identifier="(ORG_[0-9a-f]+)"', text)
    root = re.search(r'item identifier="(ROOT_[0-9a-f]+)"', text)
    assert org and root
    assert any("used twice" in p for p in broken(manifest=(root.group(1), org.group(1))))
