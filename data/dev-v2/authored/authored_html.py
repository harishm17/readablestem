"""The two HTML documents printed by Chromium: large-formula slides and a study guide."""

# ruff: noqa: E501  (content tables: long literal lines are the data)

from __future__ import annotations

import html as htmllib

import authored_text as tx
from authored_core import Built, Doc, html_text

from readablestem.eval.equations import latex_to_mathml

CSS_SLIDES = """
@page { size: 1280px 720px; margin: 0; }
html, body { margin: 0; padding: 0; }
.slide { width: 1280px; height: 720px; box-sizing: border-box; padding: 56px 80px; overflow: hidden;
  break-after: page; page-break-after: always; background: #ffffff; color: #17203a;
  font-family: 'Liberation Sans', 'DejaVu Sans', sans-serif; }
.slide:last-child { break-after: auto; page-break-after: auto; }
.slide h1 { font-size: 56px; margin: 150px 0 12px 0; color: #0b3d91; }
.slide h2 { font-size: 40px; margin: 0 0 36px 0; color: #0b3d91; font-weight: 700; }
.slide p.sub { font-size: 30px; margin: 0; color: #4a5568; }
.slide .formula { text-align: center; margin-top: 96px; }
.slide .note { position: absolute; left: 80px; bottom: 44px; font-size: 24px; color: #4a5568; }
.slide ul { font-size: 34px; line-height: 2.1; margin: 0; padding-left: 40px; }
math { font-family: 'Noto Sans Math', 'Latin Modern Math', 'STIX Two Math', math; }
"""

# (title, latex, font size in pt, note, classes)
DISPLAY_SLIDES: list[tuple[str, str, int, str, list[str]]] = [
    ("The Pythagorean Identity", r"\sin^2\theta + \cos^2\theta = 1", 54, "True for every angle", []),
    ("The Product Rule", r"(fg)' = f'g + fg'", 54, "Derivative of a product", []),
    ("Integration by Parts", r"\int u\,dv = uv - \int v\,du", 54, "Choose u so that du is simpler", []),
    ("Sum of an Arithmetic Series", r"S_n = \frac{n}{2}\left(a_1 + a_n\right)", 54, "Average of the ends times the count", []),
    ("The Distance Formula", r"d = \sqrt{(x_2 - x_1)^2 + (y_2 - y_1)^2}", 48, "Pythagoras in the plane", []),
    ("Standard Error of the Mean", r"SE = \frac{s}{\sqrt{n}}", 60, "Shrinks as the sample grows", []),
    ("Kinetic Energy", r"K = \frac{1}{2}mv^2", 60, "Energy of motion", []),
    ("Coulomb's Law", r"F = k\,\frac{q_1 q_2}{r^2}", 60, "An inverse-square law", []),
    ("Radioactive Decay", r"N(t) = N_0\, e^{-\lambda t}", 60, "Exponential decrease", []),
    ("The pH Scale", r"\mathrm{pH} = -\log_{10}\left[\mathrm{H^+}\right]", 48, "Concentration in moles per litre", ["chem_formula", "chem_charge"]),
    ("An Equilibrium Constant", r"K = \frac{[\mathrm{C}]^c\,[\mathrm{D}]^d}{[\mathrm{A}]^a\,[\mathrm{B}]^b}", 48, "For aA + bB reacting to give cC + dD", []),
    ("Burning Butane", r"2\,\mathrm{C_4H_{10}(g)} + 13\,\mathrm{O_2(g)} \longrightarrow 8\,\mathrm{CO_2(g)} + 10\,\mathrm{H_2O(l)}", 36, "A balanced combustion equation", ["chem_equation", "chem_formula"]),
    ("Beta Decay of Strontium", r"{}^{90}_{38}\mathrm{Sr} \longrightarrow {}^{90}_{39}\mathrm{Y} + {}^{0}_{-1}\mathrm{e} + \bar{\nu}_{e}", 40, "Mass number and charge are conserved", ["nuclide"]),
    ("Inverse of a Two by Two Matrix", r"A^{-1} = \frac{1}{ad - bc}\begin{pmatrix} d & -b \\ -c & a \end{pmatrix}", 44, "Needs a nonzero determinant", ["matrix"]),
    ("The Sign Function", r"\mathrm{sgn}(x) = \begin{cases} 1 & \text{if } x > 0 \\ 0 & \text{if } x = 0 \\ -1 & \text{if } x < 0 \end{cases}", 40, "Defined by cases", ["piecewise_words"]),
    ("A Determinant of Order Three", r"\begin{vmatrix} a & b & c \\ d & e & f \\ g & h & i \end{vmatrix} = a(ei - fh) - b(di - fg) + c(dh - eg)", 36, "Expansion along the first row", ["determinant"]),
    ("Two Equations Together", r"\left\{ \begin{array}{l} x + y = 7 \\ x - y = 1 \end{array} \right.", 54, "The solution is x = 4 and y = 3", ["system_braced"]),
]  # fmt: skip

# bullet slides: (title, [bullet text with ⟦latex⟧ for the recorded fractions])
BULLET_SLIDES: list[tuple[str, list[str]]] = [
    (
        "Slopes and Rates",
        [
            "The slope of a line is \u27e6\\frac{y_2 - y_1}{x_2 - x_1}\u27e7",
            "Average speed is \u27e6\\frac{\\Delta d}{\\Delta t}\u27e7 over the interval",
            "A gradient of \u27e6\\frac{3}{4}\u27e7 rises three units for every four across",
        ],
    ),
    (
        "Fractions in Practice",
        [
            "A pump fills \u27e6\\frac{1}{15}\u27e7 of the pool each hour",
            "Two pumps together fill \u27e6\\frac{1}{15} + \\frac{1}{20}\u27e7 per hour",
            "The probability of a head is \u27e6\\frac{1}{2}\u27e7",
            "A quarter of a quarter is \u27e6\\frac{1}{16}\u27e7",
        ],
    ),
    (
        "Averages",
        [
            "The mean of n values is \u27e6\\frac{x_1 + x_2 + \\cdots + x_n}{n}\u27e7",
            "The variance divides by \u27e6n - 1\u27e7, not by n",
            "A weight of \u27e6\\frac{2}{5}\u27e7 means forty percent",
        ],
    ),
    (
        "Ratios and Proportions",
        [
            "Equal ratios satisfy \u27e6\\frac{a}{b} = \\frac{c}{d}\u27e7",
            "Cross-multiplying gives \u27e6ad = bc\u27e7",
            "A scale of \u27e6\\frac{1}{50}\u27e7 shrinks every length fifty times",
        ],
    ),
]


def _math(latex: str, display: str) -> str:
    return latex_to_mathml(latex, display)


def html_slides() -> Built:
    doc = Doc("html-slides", "dev", "html", "Formulas at Presentation Size: A Short Slide Deck")
    slides = []
    # title slide
    doc.heading(1, "Formulas at Presentation Size")
    doc.heading(2, "A short deck of large equations")
    slides.append(
        '<section class="slide"><h1>Formulas at Presentation Size</h1>'
        '<h2 style="font-size:32px;color:#4a5568;margin-top:0">A short deck of large equations</h2></section>'
    )
    bullets = list(BULLET_SLIDES)
    for k, (title, latex, size, note, classes) in enumerate(DISPLAY_SLIDES):
        doc.heading(2, title)
        doc.equation(
            latex,
            display="block",
            classes=["display_large", *classes],
            context=title,
            anchor="=",
        )
        slides.append(
            f'<section class="slide"><h2>{htmllib.escape(title)}</h2>'
            f'<div class="formula" style="font-size:{size}pt">{_math(latex, "block")}</div>'
            f'<div class="note">{htmllib.escape(note)}</div></section>'
        )
        if k % 4 == 3 and bullets:
            btitle, items = bullets.pop(0)
            doc.heading(2, btitle)
            lis = []
            for text in items:
                pieces = []
                parts = text.split("\u27e6")
                pieces.append(htmllib.escape(parts[0]))
                for part in parts[1:]:
                    latex_b, rest = part.split("\u27e7")
                    classes_b = ["inline_fraction"] if "\\frac" in latex_b else []
                    doc.equation(
                        latex_b,
                        display="inline",
                        classes=classes_b,
                        context=text.replace("\u27e6", "").replace("\u27e7", ""),
                        anchor="=",
                    )
                    pieces.append(_math(latex_b, "inline"))
                    pieces.append(htmllib.escape(rest))
                lis.append("<li>" + "".join(pieces) + "</li>")
            slides.append(
                f'<section class="slide"><h2>{htmllib.escape(btitle)}</h2><ul>'
                + "".join(lis)
                + "</ul></section>"
            )
    source = (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        f"<title>{htmllib.escape(doc.title)}</title><style>{CSS_SLIDES}</style></head><body>\n"
        + "\n".join(slides)
        + "\n</body></html>\n"
    )
    return Built(doc, source)


CSS_GUIDE = """
@page { size: 210mm 297mm; margin: 16mm 18mm; }
body { font-family: 'Liberation Serif', 'DejaVu Serif', serif; font-size: 11pt; line-height: 1.4; color: #111; }
h1 { font-family: 'Liberation Sans', sans-serif; font-size: 22pt; margin: 0 0 4pt 0; }
h2 { font-family: 'Liberation Sans', sans-serif; font-size: 14pt; margin: 16pt 0 4pt 0; border-bottom: 1px solid #999; }
pre { background: #f3f4f6; border: 1px solid #d0d4da; padding: 6pt 8pt; font-size: 9pt; line-height: 1.3; margin: 4pt 0 8pt 0;
  break-inside: avoid; }
code { font-family: 'Liberation Mono', 'DejaVu Sans Mono', monospace; }
dl { margin: 0; } dt { font-weight: 700; margin-top: 5pt; } dd { margin: 0 0 0 18pt; }
table { border-collapse: collapse; margin: 6pt 0; } caption { font-weight: 700; text-align: left; padding-bottom: 3pt; }
table.data th, table.data td { border: 1px solid #555; padding: 3pt 8pt; text-align: left; }
table.data th { background: #e8ecf4; }
table.layout td { vertical-align: top; padding: 0 14pt 0 0; width: 50%; }
"""

GUIDE_SNIPPETS = [
    (0, "Find a root by repeated halving."),
    (1, "Estimate an area with trapezoids."),
    (2, "Find a square root with Newton's method."),
    (12, "Sum the entries of an array in C."),
    (13, "Swap two integers through pointers."),
    (21, "Summarise a column of numbers from the shell."),
    (19, "Count the lines of every data file."),
    (25, "Group quiz scores in a database query."),
    (27, "Clamp a value to an interval in JavaScript."),
    (34, "Check whether a word reads the same backwards."),
    (33, "Sort a short list by hand."),
    (36, "Test a number for primality."),
]


def html_code_glossary() -> Built:
    doc = Doc("html-code-glossary", "dev", "html", "Programming and Mathematics Study Guide")
    parts: list[str] = [f"<h1>{doc.title}</h1>"]
    doc.heading(1, doc.title)
    parts.append(
        "<p>A one-page guide with short programs, a list of key terms and two small tables.</p>"
    )

    def h2(text: str) -> None:
        doc.heading(2, text)
        parts.append(f"<h2>{htmllib.escape(text)}</h2>")

    h2("Short Programs")
    for idx, lead in GUIDE_SNIPPETS:
        lang, text = tx.CODE[idx]
        parts.append(f"<p>{htmllib.escape(lead)}</p>")
        doc.code(text, lang)
        parts.append(f'<pre><code class="language-{lang}">{htmllib.escape(text)}</code></pre>')
    h2("Key Terms")
    parts.append("<dl>")
    for term, definition in tx.GLOSSARY[34:49]:
        doc.glossary(term, definition)
        parts.append(f"<dt>{htmllib.escape(term)}</dt><dd>{htmllib.escape(definition)}</dd>")
    parts.append("</dl>")
    h2("Run Times of the Examples")
    rows = [
        ["Program", "Input size", "Steps", "Time (ms)"],
        ["Bisection", "1", "30", "0.02"],
        ["Trapezoid rule", "100", "100", "0.05"],
        ["Sieve", "10000", "24000", "1.4"],
        ["Binary search", "1000000", "20", "0.01"],
    ]
    caption = "Measured run times of four of the programs"
    doc.table(rows, header_rows=1, header_cols=1, caption=caption)
    head = "".join(f'<th scope="col">{html_text(c)}</th>' for c in rows[0])
    body = "".join(
        f'<tr><th scope="row">{html_text(r[0])}</th>'
        + "".join(f"<td>{html_text(c)}</td>" for c in r[1:])
        + "</tr>"
        for r in rows[1:]
    )
    parts.append(
        f'<table class="data"><caption>{htmllib.escape(caption)}</caption>'
        f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    )
    h2("Reading and Schedule")
    cells = [
        "Reading list. The chapter on loops and the short article on numerical error.",
        "Schedule. Lecture on Tuesday, laboratory on Thursday and a quiz on Friday.",
    ]
    doc.table([cells], layout=True)
    parts.append(
        '<table class="layout" role="presentation"><tr>'
        + "".join(f"<td>{htmllib.escape(c)}</td>" for c in cells)
        + "</tr></table>"
    )
    source = (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        f"<title>{htmllib.escape(doc.title)}</title><style>{CSS_GUIDE}</style></head><body>\n"
        + "\n".join(parts)
        + "\n</body></html>\n"
    )
    return Built(doc, source)


BUILDERS = {"html-slides": html_slides, "html-code-glossary": html_code_glossary}
