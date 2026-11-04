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


def test_latexml_is_a_build_argument_that_defaults_off():
    text = (ROOT / "Dockerfile").read_text()
    assert "ARG INSTALL_LATEXML=false" in text
    run = text[text.index('if [ "$INSTALL_LATEXML" = "true" ]') :]
    run = run[: run.index("fi\n")]
    assert "apt-get install -y --no-install-recommends latexml bubblewrap util-linux" in run
    # the sandbox's programs are checked in the same layer: no bubblewrap, no image
    assert "command -v bwrap && command -v prlimit" in run
    assert "rm -rf /var/lib/apt/lists/*" in run
    # the policy is replaced in the same layer, and the build fails when it did not take
    assert "imagemagick-policy.xml" in run
    assert "identify -list policy | grep -c 'pattern: PDF'" in run


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


def test_the_build_argument_is_declared_for_railway_on_web_only():
    ts = (ROOT / ".railway" / "railway.ts").read_text()
    match = re.search(r"const webOnlyVariables = \[(.*?)\];", ts, re.DOTALL)
    assert match and '"INSTALL_LATEXML"' in match.group(1)
    appvars = re.search(r"const appVariables = \[(.*?)\];", ts, re.DOTALL)
    assert appvars and "INSTALL_LATEXML" not in appvars.group(1)


def test_ci_builds_with_the_argument_and_checks_the_policy():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "--build-arg INSTALL_LATEXML=true" in ci
    assert "latexml --VERSION" in ci and "identify -list policy" in ci


def test_notices_and_deploy_docs_name_it():
    assert "LaTeXML" in (ROOT / "THIRD_PARTY_NOTICES.md").read_text()
    deploy = (ROOT / "docs" / "deploy-railway.md").read_text()
    assert "**LaTeXML.**" in deploy and "INSTALL_LATEXML" in deploy


def test_tini_is_pid_1_so_the_orphans_of_the_sandbox_are_reaped():
    text = (ROOT / "Dockerfile").read_text()
    assert "install -y --no-install-recommends tini" in text and "tini --version" in text
    assert 'ENTRYPOINT ["tini", "--"]' in text
    assert text.index('ENTRYPOINT ["tini", "--"]') < text.index('CMD ["readablestem", "serve"')
    # CMD is the app: the entrypoint only puts an init in front of it
    assert text.rstrip().splitlines()[-1].startswith('CMD ["readablestem", "serve"')


def test_the_web_service_does_not_override_the_start_command_that_carries_the_entrypoint():
    """Railway runs the image's CMD behind its ENTRYPOINT; a `start` on `web` would replace the
    CMD (and may bypass the init), so `web` has none. The cron service has one but runs no
    sandbox (docs/deploy-railway.md, "LaTeXML")."""
    text = (ROOT / ".railway" / "railway.ts").read_text()
    web = text[text.index('service("web", {') : text.index('service("cron", {')]
    assert "start:" not in web
