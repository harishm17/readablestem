"""The image's LaTeXML switch (Dockerfile `INSTALL_LATEXML`) and ImageMagick's locked-down policy.

No Docker here: these read the Dockerfile, `railway.ts`, the CI workflow and the policy file, so
the pieces that must agree cannot drift. The build itself runs in CI's `docker` job."""

from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "src" / "readablestem" / "source" / "imagemagick-policy.xml"


def policies() -> list[tuple[str, str, str, str]]:
    tree = ElementTree.parse(POLICY)
    return [
        (
            p.get("domain", ""),
            p.get("name", ""),
            p.get("rights", ""),
            p.get("pattern") or p.get("value", ""),
        )
        for p in tree.getroot().iter("policy")
    ]


def test_the_final_stage_runs_as_the_app_user_after_the_install():
    text = (ROOT / "Dockerfile").read_text()
    assert text.index("INSTALL_LATEXML") < text.index("USER 10001:10001")


def test_policy_denies_everything_but_three_raster_formats():
    rows = policies()
    assert ("coder", "", "none", "*") in rows
    allowed = [r for r in rows if r[0] == "coder" and r[2] != "none"]
    assert len(allowed) == 1 and allowed[0][3] == "{GIF,JPEG,JPG,PNG}"
    # the "none *" row comes before the allow row, as ImageMagick reads them in order
    order = [r for r in rows if r[0] == "coder"]
    assert order.index(("coder", "", "none", "*")) < order.index(allowed[0])


def test_policy_names_the_dangerous_formats_as_denied():
    denied = {r[3] for r in policies() if r[0] == "coder" and r[2] == "none"}
    for name in ("PS", "PS2", "PS3", "EPS", "EPI", "EPSF", "PDF", "XPS", "URL", "HTTP", "HTTPS",
                 "FTP", "MVG", "MSL", "TEXT", "LABEL", "SVG"):  # fmt: skip
        assert name in denied, name
    assert ("path", "", "none", "@*") in policies()
    assert ("delegate", "", "none", "*") in policies()
    assert ("filter", "", "none", "*") in policies()


def test_policy_caps_resources():
    resources = {r[1]: r[3] for r in policies() if r[0] == "resource"}
    for name in ("memory", "map", "width", "height", "area", "disk", "time"):
        assert name in resources
    assert re.fullmatch(r"\d+", resources["time"]) and int(resources["time"]) <= 60
