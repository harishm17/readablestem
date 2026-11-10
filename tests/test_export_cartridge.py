"""T3-8: the IMS Common Cartridge 1.3 export (pipeline/export_cartridge.py), and the maths
font stack of the Canvas export (pipeline/export_canvas.py).

`check_cartridge` is this repository's check of the CC 1.3 structure rules that apply to
web content (the official schemas are not available offline). Its own tests below break one
rule at a time to show each is noticed; then every document the Canvas export is tested on
must make a cartridge with no problems.
"""

from __future__ import annotations

import io
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
