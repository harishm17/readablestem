"""Tiny extended development sets for the tests: one synthetic OpenStax page and two
authored documents, written the way `devset_build.build_set` writes a real set."""

from __future__ import annotations

import json
from pathlib import Path

from readablestem.gold import devset as ds
from readablestem.gold import openstax as ox

NS = "http://www.w3.org/1998/Math/MathML"

PAGE = f"""<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title></head><body>
<h2>Systems of equations</h2><h3>Key Takeaways</h3>
<p id="p1">A sample of H<sub>2</sub>O at 1.0 g/cm<sup>3</sup> and <sup>14</sup>C.</p>
<ol start="3"><li><math xmlns="{NS}" display="inline"><mfrac><mn>1</mn><mn>2</mn></mfrac>
</math> of it</li></ol>
<div data-type="equation" id="e1"><math xmlns="{NS}" display="block"><mrow><mo>{{</mo><mtable>
<mtr><mtd><mi>x</mi><mo>=</mo><mn>1</mn></mtd></mtr><mtr><mtd><mi>y</mi><mo>=</mo><mn>2</mn></mtd>
</mtr></mtable></mrow></math></div>
<h3>Key Takeaways</h3>
<pre>print(1)</pre>
<dl><dt>term</dt><dd>meaning</dd></dl>
<table><tr><th>a</th></tr><tr><td>1</td></tr></table>
</body></html>"""


def book() -> ox.Book:
    return ox.Book(
        slug="demo",
        title="Demo",
        uuid="u",
        version="v1",
        archive_path="/apps/archive/20990101.000000",
        state="live",
        license_name="Creative Commons Attribution License",
        license_url="http://creativecommons.org/licenses/by/4.0/",
        cms_license_name=None,
        cms_license_url=None,
        authors=["A. Author"],
    )


def page(slug: str = "1-1-demo") -> ox.PageRef:
    return ox.PageRef("p-uuid", slug, "1.1 Demo", 1, 1)


def write_authored(pool: Path, doc: str, variant: str) -> None:
    folder = pool / doc
    folder.mkdir(parents=True)
    record = {
        "id": f"authored/{doc}/hd-000",
        "source": "authored",
        "kind": "heading",
        "book_slug": "authored-latex",
        "page_slug": doc,
        "page_title": doc,
        "toolchain": "tectonic",
        "variant": variant,
        "pdf": f"authored/{doc}/{doc}.pdf",
        "classes": ["heading_sentence"],
        "text": "A sentence.",
        "level": 1,
    }
    (folder / "gold.jsonl").write_bytes(ds.jsonl_bytes([record]))
    (folder / f"{doc}.pdf").write_bytes(b"%PDF-1.4 fake")


def tiny_set(tmp_path: Path) -> Path:
    """A dev part and a sealed check part, written the way `build_set` writes them."""
    pool = tmp_path / "pool"
    write_authored(pool, "doc-a", "dev")
    write_authored(pool, "doc-a-chk", "check")
    dev_files = {
        **ds.openstax_files({("demo", "1-1-demo"): ds.build_page(book(), page(), PAGE, "d")},
                            {"demo": book()}, "2026-10-14"),
        **ds.authored_files(pool, "dev"),
    }  # fmt: skip
    check_files = ds.authored_files(pool, "check")
    root = tmp_path / "set"
    for path, data in dev_files.items():
        (root / "dev" / path).parent.mkdir(parents=True, exist_ok=True)
        (root / "dev" / path).write_bytes(data)
    dev_manifest = ds.part_manifest(dev_files, part="dev", date="2026-10-14")
    (root / "dev" / "manifest.json").write_bytes(ds.manifest_json(dev_manifest))
    check_manifest = ds.part_manifest(check_files, part="check", date="2026-10-14")
    archive = ds.make_tar_gz({**check_files, "manifest.json": ds.manifest_json(check_manifest)})
    (root / ds.CHECK_ARCHIVE).write_bytes(archive)
    seal = ds.seal_record(archive, check_files, check_manifest, "2026-10-14")
    (root / ds.SEAL_FILE).write_text(json.dumps(seal), encoding="utf-8")
    return root
