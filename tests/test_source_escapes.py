r"""Hostile LaTeX through the real sandbox and the real LaTeXML (no mocks).

Every file here goes **straight to `latexml.convert`**, with the lint (`source.lint`) bypassed
on purpose: the lint only refuses the plain forms, and the guarantee is the sandbox. What each
test shows is that the thing the file tries (reading a file of this machine or its environment,
running a program, reaching the network, writing outside its folder, running for ever, using
all memory) does not work, and that nothing of it reaches the output. The last tests send the
same kinds of file through `convert_source`, where the answer must be a content-free code.

Skipped where LaTeXML is missing, and per backend where bubblewrap or Landlock cannot start
(docs/development.md).
"""

from __future__ import annotations

import http.server
import os
import threading
import time
from pathlib import Path

import pytest

from readablestem.core import sandbox
from readablestem.core.types import ConversionOutput
from readablestem.source import envelope, latexml
from readablestem.source.convert import SourceFailed, convert_source
from source_support import make_zip, needs_latexml

pytestmark = needs_latexml


@pytest.fixture(autouse=True, params=["bwrap", "landlock"])
def backend(request):
    """Every test here runs on both sandboxes: bubblewrap, and Landlock (the one used where user
    namespaces are forbidden). Skipped where the named one cannot start."""
    previous = os.environ.get(sandbox.BACKEND_ENV)
    os.environ[sandbox.BACKEND_ENV] = request.param
    sandbox.backend.cache_clear()
    if sandbox.backend() != request.param:
        os.environ.pop(sandbox.BACKEND_ENV, None)
        sandbox.backend.cache_clear()
        pytest.skip(f"the {request.param} sandbox cannot start here")
    yield request.param
    if previous is None:
        os.environ.pop(sandbox.BACKEND_ENV, None)
    else:
        os.environ[sandbox.BACKEND_ENV] = previous
    sandbox.backend.cache_clear()


SECRET = "SECRET-VALUE-4417"
FAST = sandbox.Limits(wall_s=60, cpu_s=60, memory_bytes=1_500_000_000)


def document(body: str, preamble: str = "") -> str:
    return (
        f"\\documentclass{{article}}\n{preamble}\n\\begin{{document}}\n{body}\n\\end{{document}}\n"
    )


def convert(tmp_path: Path, body: str, *, preamble: str = "", limits=FAST, files=None):
    """Run `body` through LaTeXML in the sandbox; (outcome, the XML and XHTML it left, if any)."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    (project / "main.tex").write_text(document(body, preamble))
    for name, content in (files or {}).items():
        (project / name).write_text(content)
    out = tmp_path / "out"
    outcome = latexml.convert(project, "main.tex", out, limits=limits)
    text = ""
    for rel in (latexml.XML_PATH, latexml.DOC_PATH):
        path = out / rel
        if path.is_file():
            text += path.read_text(errors="replace")
    logs = "".join(p.read_text(errors="replace") for p in out.rglob("*.log") if p.is_file())
    return outcome, text, logs


@pytest.fixture
def secrets(tmp_path, monkeypatch):
    """Secrets of this machine in the places a document might look: a file outside the project,
    the process environment, and a file the project's own folder links to."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text(SECRET)
    return outside / "secret.txt"


def assert_no_secret(*texts: str) -> None:
    for text in texts:
        assert SECRET not in text


# --------------------------------------------------------------------------- reading files


def test_input_of_an_absolute_host_path_finds_nothing(tmp_path, secrets):
    _, text, logs = convert(tmp_path, f"A\\input{{{secrets}}}B", preamble="")
    assert_no_secret(text, logs)
    assert "A" in text  # the document still converts; the file was simply not there


def test_input_of_etc_passwd_finds_nothing(tmp_path):
    _, text, logs = convert(tmp_path, "\\input{/etc/passwd}")
    assert "root:" not in text and "/bin/bash" not in text
    assert "root:" not in logs


def test_input_of_proc_1_environ_reads_nothing(tmp_path, secrets):
    _, text, logs = convert(tmp_path, "\\input{/proc/1/environ}")
    assert_no_secret(text, logs)
    _, text, logs = convert(tmp_path, "\\input{/proc/self/environ}")
    assert_no_secret(text, logs)


def test_a_relative_path_out_of_the_project_finds_nothing(tmp_path, secrets):
    _, text, logs = convert(tmp_path, "\\input{../outside/secret.txt}\\input{../../../etc/passwd}")
    assert_no_secret(text, logs)
    assert "root:" not in text


def test_a_symlink_in_the_project_does_not_lead_out(tmp_path, secrets):
    project = tmp_path / "project"
    project.mkdir()
    os.symlink(secrets, project / "link.tex")
    os.symlink("/etc/passwd", project / "pw.tex")
    _, text, logs = convert(tmp_path, "\\input{link.tex}\\input{pw.tex}")
    assert_no_secret(text, logs)
    assert "root:" not in text


def test_a_catcode_trick_does_not_change_what_can_be_read(tmp_path, secrets):
    trick = f"\\catcode`\\!=0 !input{{{secrets}}} !input{{/etc/passwd}}"
    _, text, logs = convert(tmp_path, trick)
    assert_no_secret(text, logs)
    assert "root:" not in text
    built = f"\\expandafter\\csname inp\\string ut\\endcsname{{{secrets}}}"
    _, text, logs = convert(tmp_path, built)
    assert_no_secret(text, logs)


def test_includegraphics_of_a_host_file_reads_nothing(tmp_path, secrets):
    _, text, logs = convert(tmp_path, f"\\includegraphics{{{secrets}}}")
    assert_no_secret(text, logs)


def test_the_environment_holds_nothing_to_read(tmp_path, secrets):
    _, text, logs = convert(tmp_path, "\\input{|printenv}\\immediate\\write18{printenv}")
    assert_no_secret(text, logs)


# --------------------------------------------------------------------------- writing and running


def test_write18_does_not_run_a_program(tmp_path):
    marker = Path("/tmp") / f"rs-pwned-{os.getpid()}"
    marker.unlink(missing_ok=True)
    convert(tmp_path, f"\\immediate\\write18{{touch {marker}}}\\immediate\\write-1{{x}}")
    assert not marker.exists()


def test_openout_cannot_write_outside_its_own_folders(tmp_path):
    marker = Path("/tmp") / f"rs-openout-{os.getpid()}"
    marker.unlink(missing_ok=True)
    body = (
        f"\\newwrite\\w\\immediate\\openout\\w={marker}\\immediate\\write\\w{{pwned}}"
        "\\immediate\\closeout\\w "
        "\\immediate\\openout\\w=/work/planted.tex\\immediate\\write\\w{pwned}\\immediate\\closeout\\w"
    )
    convert(tmp_path, body)
    assert not marker.exists()  # /tmp inside the sandbox is a private tmpfs
    assert not (tmp_path / "project" / "planted.tex").exists()  # the project is read-only


def test_what_a_document_writes_into_its_output_cannot_stand_for_the_answer(tmp_path):
    """Step one writes into its own folder; the XHTML of step two is made by step two."""
    body = (
        "\\newwrite\\w\\immediate\\openout\\w=/out/doc.xhtml\\immediate\\write\\w{FORGED}"
        "\\immediate\\closeout\\w Real text."
    )
    outcome, _, _ = convert(tmp_path, body)
    html = (tmp_path / "out" / latexml.DOC_PATH).read_text() if outcome.ok else ""
    assert "FORGED" not in html


def test_the_network_is_not_reachable(tmp_path):
    hits: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.endswith("x.tex"):  # (other programs on a busy box probe local ports)
                hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"\\section{FROM THE NETWORK}")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/x.tex"
        _, text, _ = convert(tmp_path, f"\\input{{{url}}}")
    finally:
        server.shutdown()
        server.server_close()
    assert hits == [] and "FROM THE NETWORK" not in text


# --------------------------------------------------------------------------- limits


def test_an_infinite_loop_hits_the_time_cap(tmp_path):
    started = time.monotonic()
    limits = sandbox.Limits(wall_s=4, cpu_s=60, memory_bytes=1_500_000_000)
    outcome, _, _ = convert(tmp_path, "\\def\\a{x\\a}\\a", limits=limits)
    assert outcome.timed_out and not outcome.ok
    assert time.monotonic() - started < 40


def test_an_openin_loop_hits_the_time_cap_and_reads_nothing(tmp_path, secrets):
    body = (
        f"\\newread\\r\\def\\a{{\\openin\\r={secrets}\\closein\\r"
        "\\openin\\r=/etc/passwd\\closein\\r\\a}\\a"
    )
    limits = sandbox.Limits(wall_s=4, cpu_s=60, memory_bytes=1_500_000_000)
    outcome, text, logs = convert(tmp_path, body, limits=limits)
    assert not outcome.ok
    assert_no_secret(text, logs)


def test_a_runaway_macro_hits_the_memory_cap(tmp_path):
    limits = sandbox.Limits(wall_s=60, cpu_s=60, memory_bytes=400_000_000)
    outcome, _, _ = convert(tmp_path, "\\def\\a#1{\\a{#1#1}}\\a{x}", limits=limits)
    assert not outcome.ok


def test_runaway_output_hits_the_output_cap(tmp_path):
    body = (
        "\\newwrite\\w\\immediate\\openout\\w=/out/big.txt"
        "\\def\\a{\\immediate\\write\\w{xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx}\\a}\\a"
    )
    limits = sandbox.Limits(wall_s=30, cpu_s=60, memory_bytes=1_500_000_000, out_bytes=2_000_000)
    outcome, _, _ = convert(tmp_path, body, limits=limits)
    assert not outcome.ok


# --------------------------------------------------------------------------- the whole job


def job(project: dict[str, str]) -> bytes:
    return envelope.wrap("zip", "p.zip", "main.tex", make_zip(project))


def run(blob: bytes) -> ConversionOutput:
    from readablestem.config import get_settings
    from readablestem.models.fake import FakeVisionClient

    return convert_source(blob, settings=get_settings(), client=FakeVisionClient())


def failure(blob: bytes) -> str:
    with pytest.raises(SourceFailed) as raised:
        run(blob)
    return raised.value.code


@pytest.mark.parametrize(
    "body",
    [
        "\\immediate\\write18{id}",
        "\\openout3=/tmp/x",
        "\\input{/etc/passwd}",
        "\\input{../x}",
        "\\input{|ls}",
        "\\directlua{os.execute('id')}",
    ],
)
def test_a_job_with_a_plain_escape_is_refused_before_anything_runs(body):
    assert failure(job({"main.tex": document(body)})) == "source_refused"


def test_a_job_that_never_ends_fails_with_the_timeout_code(monkeypatch):
    monkeypatch.setattr(latexml, "LIMITS", sandbox.Limits(wall_s=4, cpu_s=60))
    assert failure(job({"main.tex": document("\\def\\a{x\\a}\\a")})) == "timeout"


def test_a_job_with_no_document_in_it_fails_cleanly():
    blob = job({"main.tex": "\\documentclass{article}\\begin{document}\\end{document}"})
    assert failure(blob) in {"source_unreadable", "source_failed"}


def test_the_error_codes_carry_no_content(monkeypatch):
    monkeypatch.setattr(latexml, "LIMITS", sandbox.Limits(wall_s=3, cpu_s=60))
    for body in ("\\def\\a{x\\a}\\a", "\\input{/etc/passwd}", "\\immediate\\write18{id}"):
        try:
            run(job({"main.tex": document(body)}))
        except SourceFailed as exc:
            assert exc.code in {"source_refused", "timeout", "source_failed", "source_unreadable"}
            assert "passwd" not in str(exc) and "write18" not in str(exc)
