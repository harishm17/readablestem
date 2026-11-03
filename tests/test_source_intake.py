"""What an uploaded `.tex` or `.zip` is before anything runs (`readablestem.source.intake`,
`source.lint`, `source.envelope`): the kind, the main file, the lint, the page estimate."""

from __future__ import annotations

import pytest

from readablestem.source import envelope, intake, lint
from source_support import make_zip, png_bytes

ARTICLE = "\\documentclass{article}\n\\begin{document}\nHello $x$.\n\\end{document}\n"


def refusal(filename: str, data: bytes, **kwargs) -> str:
    with pytest.raises(intake.SourceRefused) as raised:
        intake.inspect(filename, data, **kwargs)
    return raised.value.code


def test_a_tex_file_is_its_own_main_file():
    info = intake.inspect("notes.tex", ARTICLE.encode())
    assert (info.kind, info.main, info.pages, info.beamer) == ("tex", "main.tex", 1, False)


def test_kind_follows_the_name_and_the_bytes():
    assert intake.kind_of("a.tex", b"x") == "tex"
    assert intake.kind_of("A.TEX", b"x") == "tex"
    assert intake.kind_of("a.tex", b"%PDF-1.5") is None
    assert intake.kind_of("a.zip", make_zip({"a.tex": "x"})) == "zip"
    assert intake.kind_of("a.zip", b"not a zip") is None
    assert intake.kind_of("a.docx", make_zip({"a.tex": "x"})) is None  # Office files go their way
    assert intake.kind_of("a.pdf", b"%PDF-1.7") is None


def test_a_zip_finds_the_main_file_that_nothing_includes():
    data = make_zip(
        {
            "paper.tex": ARTICLE + "\\input{chapters/one}",
            "chapters/one.tex": "\\section{One}",
            "fig.png": png_bytes(),
        }
    )
    info = intake.inspect("p.zip", data)
    assert info.main == "paper.tex" and info.files == 2


def test_two_mains_are_narrowed_by_depth_then_by_a_usual_name_then_refused():
    deep = make_zip({"main.tex": ARTICLE, "old/draft.tex": ARTICLE})
    assert intake.inspect("p.zip", deep).main == "main.tex"
    named = make_zip({"main.tex": ARTICLE, "other.tex": ARTICLE})
    assert intake.inspect("p.zip", named).main == "main.tex"
    ambiguous = make_zip({"a.tex": ARTICLE, "b.tex": ARTICLE})
    assert refusal("p.zip", ambiguous) == "source_many_mains"


def test_the_person_can_name_the_main_file():
    data = make_zip({"a.tex": ARTICLE, "b.tex": ARTICLE})
    assert intake.inspect("p.zip", data, main="b.tex").main == "b.tex"
    assert refusal("p.zip", data, main="c.tex") == "source_no_main"
    assert refusal("p.zip", data, main="../etc/passwd") == "source_no_main"


def test_no_document_is_no_main_file():
    assert refusal("p.zip", make_zip({"a.tex": "\\section{x}"})) == "source_no_main"
    assert refusal("a.tex", b"") == "source_no_main"
    assert refusal("a.tex", b"\x00\x01binary") == "source_no_main"
    commented = "% \\documentclass{article}\n% \\begin{document}\n"
    assert refusal("a.tex", commented.encode()) == "source_no_main"


def test_latin1_source_is_read():
    text = "\\documentclass{article}\n\\begin{document}caf\xe9\\end{document}"
    assert intake.inspect("a.tex", text.encode("latin-1")).pages == 1


def test_pages_are_a_page_per_3000_characters_or_a_figure_whichever_is_more():
    body = "word " * 3000  # 15,000 characters
    long = f"\\documentclass{{article}}\\begin{{document}}{body}\\end{{document}}"
    assert intake.inspect("a.tex", long.encode()).pages == 6
    figs = "\\includegraphics{a.png}" * 7
    many = f"\\documentclass{{article}}\\begin{{document}}{figs}\\end{{document}}"
    assert intake.inspect("a.tex", many.encode()).pages == 7
    tikz = "\\begin{tikzpicture}\\end{tikzpicture}" * 3
    assert (
        intake.inspect("a.tex", f"\\documentclass{{a}}\\begin{{document}}{tikz}".encode()).pages
        == 3
    )
    commented = "% " + "x" * 9000 + "\n" + ARTICLE
    assert intake.inspect("a.tex", commented.encode()).pages == 1  # comments do not count


def test_too_many_pages_is_refused_with_the_limit_given():
    body = "word " * 3000
    long = f"\\documentclass{{article}}\\begin{{document}}{body}\\end{{document}}"
    assert refusal("a.tex", long.encode(), max_pages=5) == "source_too_many_pages"
    assert intake.inspect("a.tex", long.encode(), max_pages=6).pages == 6


def test_beamer_is_refused_until_it_is_switched_on():
    deck = "\\documentclass[aspectratio=169]{beamer}\n\\begin{document}\\end{document}"
    assert refusal("a.tex", deck.encode()) == "source_beamer"
    assert intake.inspect("a.tex", deck.encode(), beamer_ok=True).beamer is True


@pytest.mark.parametrize(
    ("snippet", "code"),
    [
        ("\\immediate\\write18{id}", "source_unsafe"),
        ("\\write18{id}", "source_unsafe"),
        ("\\write 18{id}", "source_unsafe"),
        ("\\write{18}{id}", "source_unsafe"),
        ("\\openout3=/tmp/x", "source_unsafe"),
        ("\\input{|ls}", "source_unsafe"),
        ("\\directlua{os.execute('id')}", "source_unsafe"),
        ("\\input{/etc/passwd}", "source_unsafe"),
        ("\\input /etc/passwd", "source_unsafe"),
        ("\\include{../secret}", "source_unsafe"),
        ("\\input{a/../../b}", "source_unsafe"),
        ("\\includegraphics[width=2cm]{/proc/1/environ}", "source_unsafe"),
        ("\\includegraphics{~/x.png}", "source_unsafe"),
        ("\\input{https://example.org/x}", "source_unsafe"),
        ("\\usepackage{shellesc}", "source_unsafe"),
        ("\\usepackage[cache=false]{minted}", "source_unsafe"),
        ("\\InputIfFileExists{/etc/hosts}{}{}", "source_unsafe"),
        ("\\bibliography{../../x}", "source_unsafe"),
    ],
)
def test_the_lint_refuses_the_plain_forms(snippet, code):
    source = f"\\documentclass{{article}}\n\\begin{{document}}{snippet}\\end{{document}}"
    assert refusal("a.tex", source.encode()) == code
    assert refusal("p.zip", make_zip({"main.tex": source})) == code


def test_the_lint_reads_every_text_file_of_a_zip():
    data = make_zip({"main.tex": ARTICLE, "evil.sty": "\\immediate\\write18{id}"})
    assert refusal("p.zip", data) == "source_unsafe"


@pytest.mark.parametrize(
    "snippet",
    [
        "% \\write18{commented out}",
        "100\\% sure \\input{chapter}",
        "\\input{chapters/one}",
        "\\includegraphics[width=3cm]{figs/a.png}",
        "\\usepackage{amsmath,amssymb}",
        "\\newcommand{\\writer}{x}\\writer",
        "\\inputencoding is not an input",
        "\\write\\mystream{text}",
    ],
)
def test_ordinary_documents_pass_the_lint(snippet):
    assert lint.problem(snippet) is None


def test_the_lint_is_not_the_guarantee_catcode_tricks_get_past_it():
    """The lint cannot see a command rebuilt from characters: that is what the sandbox is for
    (tests/test_source_escapes.py runs these through it)."""
    trick = "\\catcode`\\!=0 !write18{id}"
    assert lint.problem(trick) is None
    built = "\\expandafter\\csname wri\\string te\\endcsname 18{id}"
    assert lint.problem(built) is None


def test_the_envelope_round_trips_and_refuses_anything_else():
    blob = envelope.wrap("zip", "Ünï.zip", "main.tex", b"PK\x03\x04data\nmore")
    assert envelope.is_envelope(blob) and not envelope.is_envelope(b"%PDF-1.7")
    assert envelope.unwrap(blob) == ("zip", "main.tex", b"PK\x03\x04data\nmore")
    assert envelope.unwrap(envelope.wrap("tex", "a.tex", None, b"x"))[1] is None
    for bad in (
        b"%PDF-1.7",
        envelope.MAGIC + b"{not json}\nx",
        envelope.MAGIC + b'{"kind":"exe"}\n',
    ):
        with pytest.raises(envelope.EnvelopeError):
            envelope.unwrap(bad)
