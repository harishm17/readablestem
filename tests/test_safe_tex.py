"""Unpacking and compiling downloaded TeX safely (`readablestem.gold.safe_tex`).

The archive tests run anywhere. The compile tests run a real TeX engine in the real sandbox, with
no mocks: they are skipped where bubblewrap or TeX Live is missing (CI), and run on the box."""

from __future__ import annotations

import gzip
import io
import shutil
import tarfile
import time
from pathlib import Path

import pytest

from readablestem.gold import safe_tex

# ---------------------------------------------------------------------------- archives


def make_tar(members: list[tuple[str, bytes | None, str]], gz: bool = True) -> bytes:
    """(name, data, kind) with kind 'file', 'dir', 'symlink', 'hardlink'; data is the link target
    for links."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz" if gz else "w") as archive:
        for name, data, kind in members:
            info = tarfile.TarInfo(name)
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = (data or b"").decode()
                archive.addfile(info)
            elif kind == "hardlink":
                info.type = tarfile.LNKTYPE
                info.linkname = (data or b"").decode()
                archive.addfile(info)
            else:
                payload = data or b""
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def test_extract_unpacks_files_and_strips_a_single_top_directory(tmp_path):
    data = make_tar([("repo-abc/", None, "dir"), ("repo-abc/a/b.tex", b"x", "file")])
    safe_tex.extract_tar(data, tmp_path / "src", strip_top=True)
    assert (tmp_path / "src" / "a" / "b.tex").read_bytes() == b"x"
    assert [p.name for p in tmp_path.iterdir()] == ["src"]  # no temporary folder left behind


@pytest.mark.parametrize(
    ("members", "code"),
    [
        ([("/etc/evil.tex", b"x", "file")], "unsafe_path"),
        ([("../evil.tex", b"x", "file")], "unsafe_path"),
        ([("a/../../evil.tex", b"x", "file")], "unsafe_path"),
        ([("link", b"/etc/passwd", "symlink")], "unsafe_member"),
        ([("link", b"inside.tex", "symlink")], "unsafe_member"),
        ([("hard", b"a.tex", "hardlink")], "unsafe_member"),
    ],
)
def test_extract_refuses_unsafe_members_and_leaves_nothing(tmp_path, members, code):
    with pytest.raises(safe_tex.UnsafeArchive) as info:
        safe_tex.extract_tar(make_tar([("a.tex", b"ok", "file"), *members]), tmp_path / "src")
    assert info.value.code == code
    assert list(tmp_path.iterdir()) == []  # not even the files before the bad member


def test_extract_caps_files_and_unpacked_size(tmp_path):
    many = make_tar([(f"f{i}.tex", b"x", "file") for i in range(30)])
    with pytest.raises(safe_tex.UnsafeArchive) as info:
        safe_tex.extract_tar(many, tmp_path / "a", max_files=10)
    assert info.value.code == "too_many_files"
    big = make_tar([("big.tex", b"x" * 100_000, "file")])
    with pytest.raises(safe_tex.UnsafeArchive) as info:
        safe_tex.extract_tar(big, tmp_path / "b", max_bytes=10_000)
    assert info.value.code == "unpacked_too_large"
    assert list(tmp_path.iterdir()) == []


def test_a_gzip_bomb_is_refused_before_it_is_unpacked(tmp_path):
    bomb = make_tar([("big.tex", b"\0" * 30_000_000, "file")])
    assert len(bomb) < 100_000  # tens of megabytes of zeros compress to almost nothing
    with pytest.raises(safe_tex.UnsafeArchive) as info:
        safe_tex.extract_tar(bomb, tmp_path / "src", max_bytes=1_000_000)
    assert info.value.code == "unpacked_too_large"
    assert list(tmp_path.iterdir()) == []


def test_a_broken_archive_is_refused_and_does_not_reuse_a_folder(tmp_path):
    with pytest.raises(safe_tex.UnsafeArchive) as info:
        safe_tex.extract_tar(b"not an archive at all" * 50, tmp_path / "src")
    assert info.value.code == "bad_archive"
    (tmp_path / "taken").mkdir()
    with pytest.raises(FileExistsError):
        safe_tex.extract_tar(make_tar([("a", b"x", "file")]), tmp_path / "taken")


def test_single_gzipped_file_is_capped(tmp_path):
    safe_tex.extract_single_file(gzip.compress(b"\\documentclass{x}"), tmp_path / "ok", "main.tex")
    assert (tmp_path / "ok" / "main.tex").read_text() == "\\documentclass{x}"
    with pytest.raises(safe_tex.UnsafeArchive):
        safe_tex.extract_single_file(
            gzip.compress(b"x" * 10_000), tmp_path / "big", "main.tex", max_bytes=1_000
        )


# ------------------------------------------------------------------------------ compile

BWRAP = shutil.which("bwrap") is not None
TEXLIVE = safe_tex.texlive_bin() is not None
needs_tex = pytest.mark.skipif(
    not (BWRAP and TEXLIVE), reason="needs bubblewrap and a user-level TeX Live"
)

DOC = "\\documentclass{article}\\begin{document}Hello $x^2$ SENTINELTEXT\\end{document}\n"


def pdf_text(path: Path) -> str:
    import pymupdf

    with pymupdf.open(path) as doc:
        return "".join(page.get_text() for page in doc)


def compile_pdf(tmp_path: Path, main: str, engine: str = "pdflatex"):
    out = tmp_path / f"out-{engine}-{main}"
    result = safe_tex.compile_tex(tmp_path / "src", main, out, engine, timeout=120)
    return result, out


@needs_tex
def test_an_ordinary_document_compiles_in_the_sandbox(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "ok.tex").write_text(DOC)
    result, out = compile_pdf(tmp_path, "ok.tex")
    assert result.returncode == 0 and not result.timed_out
    assert "SENTINELTEXT" in pdf_text(out / "ok.pdf")


@needs_tex
def test_a_malicious_latexmkrc_is_not_run(tmp_path):
    """A `.latexmkrc` in the source is Perl. `-norc` must keep it from running."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "ok.tex").write_text(DOC)
    (src / ".latexmkrc").write_text('system("touch /out/PWNED_BY_RC");\n')
    (src / "latexmkrc").write_text('system("touch /out/PWNED_BY_RC2");\n')
    result, out = compile_pdf(tmp_path, "ok.tex")
    assert result.returncode == 0
    assert not (out / "PWNED_BY_RC").exists() and not (out / "PWNED_BY_RC2").exists()


@needs_tex
def test_the_rc_test_would_notice_if_norc_were_missing(tmp_path):
    """The control: the same source with latexmk started without -norc does run the rc file,
    so the test above proves something."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "ok.tex").write_text(DOC)
    (src / ".latexmkrc").write_text('system("touch /out/PWNED_BY_RC");\n')
    out = tmp_path / "out"
    bin_dir = safe_tex.texlive_bin()
    command = [c for c in safe_tex.latexmk_command("pdflatex", "ok.tex") if c != "-norc"]
    env = safe_tex.tex_env("/tmp/home", "/work", "/work")
    safe_tex.run_in_sandbox(
        command, src=src, out=out, cwd_rel="", env=env, bin_dir=bin_dir, timeout=120
    )
    assert (out / "PWNED_BY_RC").exists()


@needs_tex
@pytest.mark.parametrize("engine", ["pdflatex", "xelatex"])
def test_input_of_an_absolute_path_does_not_pull_a_local_file_into_the_pdf(tmp_path, engine):
    secret = tmp_path / "outside" / "secret.tex"
    secret.parent.mkdir()
    secret.write_text("TOPSECRETVALUE\n")
    src = tmp_path / "src"
    src.mkdir()
    for name in ("abs", "relative_escape"):
        target = str(secret) if name == "abs" else "../outside/secret.tex"
        (src / f"{name}.tex").write_text(
            "\\documentclass{article}\\begin{document}BEFORE "
            f"\\input{{{target}}} AFTER\\end{{document}}\n"
        )
        result, out = compile_pdf(tmp_path, f"{name}.tex", engine)
        pdf = out / f"{name}.pdf"
        assert not pdf.exists() or "TOPSECRETVALUE" not in pdf_text(pdf)
        assert result.returncode != 0 or not pdf.exists()


@needs_tex
def test_a_file_in_the_home_or_etc_is_not_visible_to_the_engine(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "peek.tex").write_text(
        "\\documentclass{article}\\begin{document}"
        "\\openin5=/etc/passwd \\ifeof5 NOFILE\\else FILEREAD\\fi"
        "\\end{document}\n"
    )
    _result, out = compile_pdf(tmp_path, "peek.tex")
    text = pdf_text(out / "peek.pdf") if (out / "peek.pdf").exists() else ""
    assert "FILEREAD" not in text


@needs_tex
def test_the_source_tree_cannot_be_written_to_from_a_compile(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "w.tex").write_text(
        "\\documentclass{article}\\begin{document}"
        "\\newwrite\\f\\immediate\\openout\\f=written.txt \\immediate\\write\\f{x}"
        "\\immediate\\closeout\\f ok\\end{document}\n"
    )
    compile_pdf(tmp_path, "w.tex")
    assert not (src / "written.txt").exists()  # writes land in a discarded overlay


@needs_tex
def test_shell_escape_is_refused(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "s.tex").write_text(
        "\\documentclass{article}\\begin{document}"
        "\\immediate\\write18{touch /out/PWNED_BY_WRITE18}x\\end{document}\n"
    )
    _, out = compile_pdf(tmp_path, "s.tex")
    assert not (out / "PWNED_BY_WRITE18").exists()


@needs_tex
def test_a_timeout_kills_the_whole_process_group(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "spin_forever_7c1f.tex").write_text(
        "\\documentclass{article}\\begin{document}\\def\\x{\\x}\\x\\end{document}\n"
    )
    start = time.monotonic()
    result = safe_tex.compile_tex(
        src, "spin_forever_7c1f.tex", tmp_path / "out", "pdflatex", timeout=5
    )
    assert time.monotonic() - start < 60
    assert result.timed_out
    time.sleep(1)
    import subprocess

    left = subprocess.run(["pgrep", "-f", "spin_forever_7c1f.tex"], capture_output=True, text=True)
    assert left.stdout.strip() == ""


needs_bwrap = pytest.mark.skipif(not BWRAP, reason="needs bubblewrap")
TECTONIC = safe_tex.tectonic_path() is not None and safe_tex.tectonic_cache().is_dir()
needs_tectonic = pytest.mark.skipif(
    not (BWRAP and TECTONIC), reason="needs bubblewrap and a warm Tectonic cache"
)


def sh(tmp_path: Path, script: str, **kwargs):
    """Run a shell script in the sandbox, with the source folder empty and `out` at /out."""
    (tmp_path / "src").mkdir(exist_ok=True)
    kwargs.setdefault("timeout", 30)
    result = safe_tex.run_in_sandbox(
        ["/bin/sh", "-c", script], src=tmp_path / "src", out=tmp_path / "out", cwd_rel="",
        env={"HOME": "/tmp/home"}, bin_dir=None, **kwargs,
    )  # fmt: skip
    return result, tmp_path / "out"


@needs_bwrap
def test_the_parents_environment_is_not_visible_in_the_sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_API_KEY_FOR_TEST", "S3CR3T-VALUE-xyz")
    _, out = sh(
        tmp_path,
        "cat /proc/1/environ /proc/self/environ /proc/1/cmdline > /out/leak.bin;"
        " env > /out/env.txt",
    )
    assert b"S3CR3T-VALUE" not in (out / "leak.bin").read_bytes()
    assert "S3CR3T-VALUE" not in (out / "env.txt").read_text()


@needs_tex
def test_a_document_cannot_read_proc_environ_of_the_sandbox_or_itself(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_API_KEY_FOR_TEST", "S3CR3T-VALUE-xyz")
    src = tmp_path / "src"
    src.mkdir()
    body = ""
    for i, path in enumerate(("/proc/1/environ", "/proc/self/environ")):
        body += (
            f"\\newread\\r{'a' * (i + 1)}\\openin\\r{'a' * (i + 1)}={path}\\relax "
            f"\\ifeof\\r{'a' * (i + 1)}\\else\\read\\r{'a' * (i + 1)} to\\x\\x\\fi "
        )
    (src / "env.tex").write_text(
        "\\documentclass{article}\\catcode`\\^^@=12 \\endlinechar=-1 \\begin{document}"
        + body
        + "END\\end{document}\n"
    )
    _, out = compile_pdf(tmp_path, "env.tex")
    texts = [p.read_bytes() for p in out.glob("*.log")]
    if (out / "env.pdf").exists():
        texts.append(pdf_text(out / "env.pdf").encode())
    assert texts and all(b"S3CR3T-VALUE" not in t for t in texts)


@needs_bwrap
def test_output_written_past_the_quota_kills_the_run(tmp_path):
    result, out = sh(
        tmp_path,
        "while :; do head -c 1000000 /dev/zero >> /out/big; done",
        max_total_bytes=5_000_000,
    )
    assert result.over_quota and not result.timed_out
    assert (out / "big").stat().st_size < 100_000_000


@needs_bwrap
def test_writes_into_the_overlay_count_toward_the_quota_too(tmp_path):
    result, _ = sh(
        tmp_path,
        "while :; do head -c 1000000 /dev/zero >> /work/big; done",
        max_total_bytes=5_000_000,
    )
    assert result.over_quota


@needs_bwrap
def test_the_sandbox_leaves_no_scratch_folder_behind(tmp_path):
    import tempfile

    before = set(Path(tempfile.gettempdir()).glob("sandbox-*"))
    sh(tmp_path, "echo hi > /work/x")
    assert set(Path(tempfile.gettempdir()).glob("sandbox-*")) == before


@pytest.mark.parametrize("root", ["/usr/bin", "/usr/local/bin", "/bin", "/opt/bin"])
def test_a_distribution_texlive_is_refused_as_a_sandbox_root(root):
    with pytest.raises(safe_tex.SandboxUnavailable):
        safe_tex.texlive_root(Path(root))


def test_a_dedicated_texlive_tree_is_accepted():
    bin_dir = Path("/home/someone/.local/texlive/2026/bin/x86_64-linux")
    assert safe_tex.texlive_root(bin_dir) == Path("/home/someone/.local/texlive")


def test_directories_count_toward_the_entry_cap(tmp_path):
    many_dirs = make_tar([(f"d{i}/", None, "dir") for i in range(50)])
    with pytest.raises(safe_tex.UnsafeArchive) as info:
        safe_tex.extract_tar(many_dirs, tmp_path / "src", max_files=10)
    assert info.value.code == "too_many_files"
    assert list(tmp_path.iterdir()) == []


def test_extract_can_keep_only_some_suffixes(tmp_path):
    data = make_tar([("a.tex", b"x", "file"), ("b.png", b"y", "file"), ("s/c.tex", b"z", "file")])
    safe_tex.extract_tar(data, tmp_path / "src", suffixes=(".tex",))
    assert sorted(
        p.relative_to(tmp_path / "src").as_posix() for p in (tmp_path / "src").rglob("*.*")
    ) == [
        "a.tex",
        "s/c.tex",
    ]


def test_a_single_file_is_streamed_with_a_cap_and_leaves_nothing(tmp_path):
    bomb = gzip.compress(b"\0" * 20_000_000, compresslevel=9)
    with pytest.raises(safe_tex.UnsafeArchive) as info:
        safe_tex.extract_single_file(bomb, tmp_path / "x", "main.tex", max_bytes=1_000_000)
    assert info.value.code == "unpacked_too_large"
    assert list(tmp_path.iterdir()) == []


@needs_tectonic
def test_tectonic_also_cannot_read_a_local_file_by_absolute_path(tmp_path):
    secret = tmp_path / "outside" / "secret.tex"
    secret.parent.mkdir()
    secret.write_text("TOPSECRETVALUE\n")
    src = tmp_path / "src"
    src.mkdir()
    (src / "t.tex").write_text(
        "\\documentclass{article}\\begin{document}BEFORE "
        f"\\input{{{secret}}} AFTER\\end{{document}}\n"
    )
    out = tmp_path / "out"
    result = safe_tex.compile_tex(src, "t.tex", out, "tectonic", timeout=120)
    pdf = out / "t.pdf"
    assert result.returncode != 0 or not pdf.exists() or "TOPSECRETVALUE" not in pdf_text(pdf)
    assert not pdf.exists() or "TOPSECRETVALUE" not in pdf_text(pdf)


def test_the_environment_switches_and_command_are_set():
    env = safe_tex.tex_env("/tmp/home", "/work/d", "/work")
    assert (env["openin_any"], env["openout_any"], env["shell_escape"]) == ("p", "p", "f")
    assert env["HOME"] == "/tmp/home" and env["TEXMFHOME"].startswith("/tmp/home")
    command = safe_tex.latexmk_command("pdflatex", "a.tex")
    assert "-norc" in command and "-no-shell-escape" in command


def test_compile_is_refused_without_bubblewrap(tmp_path, monkeypatch):
    monkeypatch.setattr(safe_tex.shutil, "which", lambda name: None)
    with pytest.raises(safe_tex.SandboxUnavailable):
        safe_tex.run_in_sandbox(
            ["true"], src=tmp_path, out=tmp_path / "o", cwd_rel="", env={"HOME": "/tmp/h"},
            bin_dir=None, timeout=5,
        )  # fmt: skip
