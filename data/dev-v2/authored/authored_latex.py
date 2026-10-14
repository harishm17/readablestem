"""The six LaTeX documents (built with Tectonic)."""

# ruff: noqa: E501  (content tables: long literal lines are the data)

from __future__ import annotations

import random
import re
from collections.abc import Callable

import authored_chem as ch
import authored_maths as mt
import authored_text as tx
from authored_core import (
    Built,
    Doc,
    detect_hyphen_breaks,
    flat,
    para_to_tex,
    parse_para,
    script_runs,
    word_table,
)

PREAMBLE = r"""\documentclass[11pt]{article}
% Computer Modern text fonts, as pdfLaTeX writes them; XeTeX defaults to Latin Modern.
\usepackage[OT1]{fontenc}
\renewcommand{\rmdefault}{cmr}
\renewcommand{\ttdefault}{cmtt}
\usepackage{amsmath,amssymb}
\usepackage[margin=1in]{geometry}
"""

HEADER = (
    "% Authored for ReadableSTEM's development set (dev-v2). Original text, no third-party "
    "material.\n% Licence: MIT. Author: ReadableSTEM contributors.\n"
)


def seed(base: str, variant: str) -> random.Random:
    return random.Random(f"a11y-dsv2/{base}/{variant}")


class Emitter:
    """Registers equation records and writes the matching \\label so the page is exact."""

    def __init__(self, doc: Doc):
        self.doc = doc
        self.n = 0

    def eq(self, spec: mt.EqSpec) -> str:
        label = f"eq{self.n:03d}"
        self.n += 1
        rec = self.doc.equation(
            spec.latex,
            display=spec.display,
            classes=spec.classes,
            context=spec.context,
            anchor=spec.anchor,
            exercise_number=spec.exercise_number,
        )
        rec["_label"] = label
        return f"\\label{{{label}}}"

    def item(self, it: mt.Item) -> str:
        tex = it.tex
        eqs = list(it.eqs)
        k = 0

        def repl(m: re.Match) -> str:
            nonlocal k
            lab = self.eq(eqs[k])
            k += 1
            return lab + "$" + m.group(1) + "$"

        tex = mt.MATH_RE.sub(repl, tex)
        while k < len(eqs):
            positions = [p for p in (tex.find("\\["), tex.find("\\begin{align*}")) if p >= 0]
            pos = min(positions)
            lab = self.eq(eqs[k])
            k += 1
            tex = tex[:pos] + lab + tex[pos:]
        return tex


def display(latex: str) -> str:
    return "\\[\n  " + latex + "\n\\]"


def script_blocks(doc: Doc, markups: list[str]) -> str:
    """Paragraphs with scripts: LaTeX text, and one `script` record each."""
    out = []
    for m in markups:
        para = parse_para(m)
        if script_runs(para):
            doc.script_para(para)
        out.append(para_to_tex(para))
    return "\n\n".join(out)


def unique(gen: Callable[[], str], seen: set[str]) -> str:
    for _ in range(40):
        latex = gen()
        if latex not in seen:
            seen.add(latex)
            return latex
    raise RuntimeError("could not draw a distinct equation")


# --------------------------------------------------------------------------- 1. latex-exercises

PART_NAMES = {
    "dev": [
        "Part A: Equations and Inequalities",
        "Part B: Systems of Equations",
        "Part C: Functions Defined by Cases",
        "Part D: Mixed Review",
    ],
    "check": [
        "Section 1: Solving for the Unknown",
        "Section 2: Linear Systems",
        "Section 3: Rules That Change with the Input",
        "Section 4: Cumulative Practice",
    ],
}


def latex_exercises(variant: str) -> Built:
    v = variant
    rng = seed("latex-exercises", v)
    title = (
        "Worksheet 4: Equations, Functions and Systems"
        if v == "dev"
        else "Worksheet 7: Algebra Practice with Fractions, Systems and Cases"
    )
    doc = Doc("latex-exercises", v, "latex", title)
    em = Emitter(doc)
    seen: set[str] = set()
    number = 0
    open_order = list(range(len(mt.OPENING)))
    rng.shuffle(open_order)
    pf_order = list(range(len(mt.PROSE_FIRST)))
    rng.shuffle(pf_order)
    oi = pi = 0
    frac = mt.fraction_sentences(rng, v, 34)
    frac_paras = [frac[i : i + 4] for i in range(0, len(frac), 4)]
    out: list[str] = [
        HEADER + PREAMBLE + r"\setlength{\parindent}{0pt}\setlength{\parskip}{6pt}",
        r"\begin{document}",
        r"\begin{center}",
        r"{\Large\bfseries " + title + r"}\\[4pt]",
        "Write each answer in the space provided and show your working.",
        r"\end{center}",
    ]

    def notes(k: int) -> str:
        paras = []
        for _ in range(k):
            group = frac_paras.pop(0)
            tex = " ".join(em.item(it) for it in group)
            paras.append(tex)
        return "\n\n".join(paras)

    def enum(items: list[str], start: int) -> str:
        body = "\n".join("\\item " + t for t in items)
        return (
            f"\\begin{{enumerate}}\n\\setcounter{{enumi}}{{{start}}}\n"
            f"\\setlength{{\\itemsep}}{{8pt}}\n{body}\n\\end{{enumerate}}"
        )

    def opening() -> mt.Item:
        nonlocal number, oi
        number += 1
        it = mt.opening_exercise(rng, v, number, open_order[oi % len(open_order)])
        oi += 1
        return it

    def prose_first() -> mt.Item:
        nonlocal number, pi
        number += 1
        it = mt.prose_first_exercise(rng, v, number, pf_order[pi % len(pf_order)])
        pi += 1
        return it

    names = PART_NAMES[v]

    def part(heading: str, n_notes: int, makers: list[Callable[[], mt.Item]]) -> None:
        doc.heading(1, heading)
        out.append(f"\\section*{{{heading}}}")
        if n_notes:
            out.append(notes(n_notes))
        start = number
        items = [em.item(mk()) for mk in makers]
        out.append(enum(items, start))

    def make_system(i: int) -> Callable[[], mt.Item]:
        def mk() -> mt.Item:
            nonlocal number
            number += 1
            leads = mt.SYSTEM_LEADS[v]
            size = 3 if i % 4 == 3 else 2
            for _ in range(40):
                it = mt.system_item(rng, v, None, leads[i % len(leads)], size)
                if it.eqs[0].latex not in seen:
                    seen.add(it.eqs[0].latex)
                    return it
            raise RuntimeError("system")

        return mk

    def make_piece(i: int) -> Callable[[], mt.Item]:
        def mk() -> mt.Item:
            nonlocal number
            number += 1
            leads = mt.PIECEWISE_LEADS[v]
            for _ in range(40):
                it = mt.piecewise_item(
                    rng,
                    v,
                    None,
                    leads[i % len(leads)],
                    "Evaluate the function at three inputs of your choice and describe its graph.",
                )
                if it.eqs[0].latex not in seen:
                    seen.add(it.eqs[0].latex)
                    return it
            raise RuntimeError("piecewise")

        return mk

    part(
        names[0],
        2,
        [opening] * 5
        + [prose_first]
        + [opening] * 5
        + [prose_first]
        + [opening] * 3
        + [prose_first],
    )
    part(names[1], 2, [make_system(i) for i in range(12)])
    part(names[2], 1, [make_piece(i) for i in range(10)])
    part(
        names[3],
        3,
        [opening] * 4 + [prose_first] * 2 + [opening] * 4 + [prose_first] * 3 + [opening] * 5,
    )
    out.append(r"\end{document}")
    return Built(doc, "\n".join(out) + "\n")


# --------------------------------------------------------------------------- 2. latex-matrices


def latex_matrices() -> Built:
    v = "dev"
    rng = seed("latex-matrices", v)
    doc = Doc("latex-matrices", v, "latex", "Determinants, Matrices and Systems: A Reference Sheet")
    em = Emitter(doc)
    seen: set[str] = set()
    out = [
        HEADER + PREAMBLE + r"\setlength{\parindent}{0pt}\setlength{\parskip}{8pt}",
        r"\begin{document}",
        r"\begin{center}{\Large\bfseries " + doc.title + r"}\end{center}",
    ]

    def sec(title: str, intro: str) -> None:
        doc.heading(1, title)
        out.append(f"\\section*{{{title}}}\n{intro}")

    sec(
        "Determinants",
        "A determinant is a single number computed from a square array. The examples below "
        "show how it is written and evaluated.",
    )
    for i in range(13):
        for _ in range(30):
            latex, lead = mt.determinant_latex(rng, v, i)
            if latex not in seen:
                seen.add(latex)
                break
        out.append(em.item(mt.block_item(latex, lead, ["determinant"])).replace("\n\n", "\n"))
    sec(
        "Matrices",
        "Matrices record data, transformations and systems of equations. Brackets and "
        "parentheses are both common.",
    )
    for i in range(15):
        for _ in range(30):
            latex, lead = mt.matrix_latex(rng, v, i)
            if latex not in seen:
                seen.add(latex)
                break
        out.append(em.item(mt.block_item(latex, lead, ["matrix"])))
    sec(
        "Systems of Equations",
        "A brace on the left groups equations that must hold together. Each system below has "
        "exactly one solution.",
    )
    leads = mt.SYSTEM_LEADS[v]
    for i in range(10):
        size = 3 if i % 3 == 2 else 2
        for _ in range(30):
            it = mt.system_item(rng, v, None, leads[i % len(leads)], size)
            if it.eqs[0].latex not in seen:
                seen.add(it.eqs[0].latex)
                break
        out.append(em.item(it))
    sec(
        "Worked Derivations",
        "Several lines of an argument can share one display, aligned on the equals sign.",
    )
    for i in range(9):
        for _ in range(30):
            latex, lead = mt.derivation_latex(rng, v, i)
            if latex not in seen:
                seen.add(latex)
                break
        spec = mt.EqSpec(latex, "block", ["multirow_equations"], lead, mt.prose_anchor(lead))
        out.append(em.item(mt.Item(lead + "\n" + latex, [spec])))
    out.append(r"\end{document}")
    return Built(doc, "\n".join(out) + "\n")


# --------------------------------------------------------------------------- 3. latex-chemistry


def _classed_items(sentences: list[str], classes_for: Callable[[str], list[str]]) -> list[mt.Item]:
    items = []
    for s in sentences:
        _, pieces = mt.split_math(s)
        ctx = mt.plain_context(s)
        anchor = mt.prose_anchor(s)
        eqs = [
            mt.EqSpec(p, "inline", classes_for(p), ctx, anchor if k == 0 else "=")
            for k, p in enumerate(pieces)
        ]
        items.append(mt.Item(s, eqs))
    return items


CHEM_SECTIONS_CHECK = {
    "Formulas and Ions": "Names, Formulas and Charges",
    "Chemical Equations": "Balanced Reactions",
    "Nuclear Equations": "Radioactive Decay and Nuclear Reactions",
    "Measurements and Units": "Quantities with Powers",
    "Atoms and Nuclei in Running Text": "Isotopes in Sentences",
    "Other Scripts in Running Text": "Indices and Ordinals in Sentences",
}


def latex_chemistry(variant: str) -> Built:
    v = variant
    rng = seed("latex-chemistry", v)
    title = (
        "Atoms, Ions and Reactions: Notation for Chemistry and Nuclear Science"
        if v == "dev"
        else "Formulas, Charges and Decay: A Second Notation Guide for Chemistry"
    )
    doc = Doc("latex-chemistry", v, "latex", title)
    em = Emitter(doc)
    out = [
        HEADER + PREAMBLE + r"\setlength{\parindent}{0pt}\setlength{\parskip}{8pt}",
        r"\begin{document}",
        r"\begin{center}{\Large\bfseries " + title + r"}\end{center}",
    ]

    def sec(name: str, intro: str = "") -> None:
        name = CHEM_SECTIONS_CHECK.get(name, name) if v == "check" else name
        doc.heading(1, name)
        out.append(f"\\section*{{{name}}}")
        if intro:
            out.append(intro)

    sec(
        "Formulas and Ions",
        "Chemical formulas are set upright, with subscripts for the number of atoms and "
        "superscripts for charge.",
    )
    comp = _classed_items([s for s, _ in ch.compound_sentences(v)], lambda p: ["chem_formula"])
    ions = _classed_items(
        ch.ion_sentences(v),
        lambda p: ["chem_formula", "chem_charge"],
    )
    for group in (comp, ions):
        for i in range(0, len(group), 5):
            out.append(" ".join(em.item(it) for it in group[i : i + 5]))
    out.append(script_blocks(doc, mt_group(ch.chem_sentences(rng, v, 32), 4)))

    sec(
        "Chemical Equations",
        "Each equation is balanced for atoms and, where ions appear, for charge.",
    )
    for _, eqs in ch.chem_equations(v):
        lead = eqs[0].lead
        out.append(lead + r"\nopagebreak")
        for e in eqs:
            spec = mt.EqSpec(e.latex, "block", e.classes, lead, None)
            out.append(em.item(mt.Item(display(e.latex), [spec])))

    sec(
        "Nuclear Equations",
        "In nuclear notation the mass number is a superscript and the atomic number a subscript, both before the symbol.",
    )
    nitems = _classed_items(ch.nuclide_sentences(v), lambda p: ["nuclide"])
    out.append(" ".join(em.item(it) for it in nitems))
    for lead, latex in ch.nuclear_equations(v):
        spec = mt.EqSpec(latex, "block", ["nuclide"], lead, None)
        out.append(lead + r"\nopagebreak" + "\n" + em.item(mt.Item(display(latex), [spec])))

    sec(
        "Measurements and Units",
        "Quantities in the laboratory carry units, and many of those units have powers.",
    )
    out.append(script_blocks(doc, mt_group(ch.unit_sentences(rng, v, 30), 4)))
    sec(
        "Atoms and Nuclei in Running Text",
        "The mass number of a nuclide stands before its symbol, as a raised number.",
    )
    out.append(script_blocks(doc, mt_group(ch.nuclide_prose(rng, v, 24), 4)))
    sec(
        "Other Scripts in Running Text",
        "Indices, powers of ten and ordinal endings also appear inside sentences.",
    )
    out.append(script_blocks(doc, mt_group(ch.other_sentences(rng, v, 34), 4)))
    out.append(r"\end{document}")
    return Built(doc, "\n".join(out) + "\n")


def mt_group(sentences: list[str], per: int) -> list[str]:
    return ch.group_paragraphs(sentences, per)


# --------------------------------------------------------------------------- 4. latex-slides

SLIDES: list[tuple[str, list[tuple[str, str, list[str]]], str]] = [
    ("The Quadratic Formula", [(r"x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}", "Huge", [])], "The roots of a quadratic polynomial"),
    ("Derivative as a Limit", [(r"f'(x) = \lim_{h \to 0} \frac{f(x+h) - f(x)}{h}", "huge", [])], "The slope of the tangent line"),
    ("The Fundamental Theorem of Calculus", [(r"\int_a^b f'(x)\,dx = f(b) - f(a)", "Huge", [])], "Integration undoes differentiation"),
    ("The Gaussian Integral", [(r"\int_{-\infty}^{\infty} e^{-x^2}\,dx = \sqrt{\pi}", "Huge", [])], "Area under the bell curve"),
    ("Geometric Series", [(r"\sum_{n=0}^{\infty} r^n = \frac{1}{1 - r}", "Huge", []), (r"S_N = \frac{a(1 - r^N)}{1 - r}", "Large", [])], "The infinite sum needs |r| less than 1"),
    ("Euler's Identity", [(r"e^{i\pi} + 1 = 0", "Huge", [])], "Five constants in one line"),
    ("A Two by Two Determinant", [(r"\begin{vmatrix} a & b \\ c & d \end{vmatrix} = ad - bc", "Huge", ["determinant"])], "Zero means the rows are dependent"),
    ("A Linear System as a Matrix Equation", [(r"\begin{bmatrix} 2 & 1 \\ 1 & 3 \end{bmatrix}\begin{bmatrix} x \\ y \end{bmatrix} = \begin{bmatrix} 5 \\ 10 \end{bmatrix}", "huge", ["matrix"])], "Written as A x = b"),
    ("Newton's Second Law", [(r"F = ma", "Huge", []), (r"p = mv", "Huge", [])], "Force, mass and acceleration"),
    ("Mass and Energy", [(r"E = mc^2", "Huge", [])], "Rest energy of a mass m"),
    ("The Schrodinger Equation", [(r"i\hbar \frac{\partial \psi}{\partial t} = \hat{H}\psi", "Huge", [])], "Time evolution of a quantum state"),
    ("The Ideal Gas Law", [(r"PV = nRT", "Huge", []), (r"\frac{P_1 V_1}{T_1} = \frac{P_2 V_2}{T_2}", "Large", [])], "Pressure, volume and temperature"),
    ("Photosynthesis", [(r"6\,\mathrm{CO_2} + 6\,\mathrm{H_2O} \longrightarrow \mathrm{C_6H_{12}O_6} + 6\,\mathrm{O_2}", "huge", ["chem_equation", "chem_formula"])], "Sunlight drives the reaction"),
    ("Self-Ionisation of Water", [(r"2\,\mathrm{H_2O(l)} \rightleftharpoons \mathrm{H_3O^+(aq)} + \mathrm{OH^-(aq)}", "huge", ["chem_equation", "chem_formula", "chem_charge"])], "A very small equilibrium constant"),
    ("Radiocarbon Decay", [(r"{}^{14}_{6}\mathrm{C} \longrightarrow {}^{14}_{7}\mathrm{N} + {}^{0}_{-1}\mathrm{e} + \bar{\nu}_{e}", "huge", ["nuclide"])], "Beta decay with a half-life of 5730 years"),
    ("Alpha Decay of Uranium", [(r"{}^{238}_{92}\mathrm{U} \longrightarrow {}^{234}_{90}\mathrm{Th} + {}^{4}_{2}\mathrm{He}", "huge", ["nuclide"])], "Mass number and atomic number are conserved"),
    ("Bayes' Theorem", [(r"P(A \mid B) = \frac{P(B \mid A)\,P(A)}{P(B)}", "Huge", [])], "Updating a probability with evidence"),
    ("The Normal Distribution", [(r"f(x) = \frac{1}{\sigma\sqrt{2\pi}}\, e^{-\frac{(x-\mu)^2}{2\sigma^2}}", "huge", [])], "Mean mu and standard deviation sigma"),
    ("Taylor Series", [(r"f(x) = \sum_{n=0}^{\infty} \frac{f^{(n)}(a)}{n!}(x - a)^n", "huge", [])], "A function as a power series about a"),
    ("The Binomial Theorem", [(r"(x + y)^n = \sum_{k=0}^{n} \binom{n}{k} x^{n-k} y^k", "huge", [])], "Expanding a power of a sum"),
    ("The Law of Cosines", [(r"c^2 = a^2 + b^2 - 2ab\cos C", "Huge", [])], "A generalisation of Pythagoras"),
    ("Logarithm of a Product", [(r"\log_b(xy) = \log_b x + \log_b y", "Huge", [])], "Logarithms turn products into sums"),
    ("Cramer's Rule", [(r"x = \frac{\begin{vmatrix} 5 & 1 \\ 10 & 3 \end{vmatrix}}{\begin{vmatrix} 2 & 1 \\ 1 & 3 \end{vmatrix}}", "huge", ["determinant"])], "Each unknown is a ratio of determinants"),
    ("The Absolute Value Function", [(r"|x| = \begin{cases} x & \text{if } x \ge 0 \\ -x & \text{if } x < 0 \end{cases}", "huge", ["piecewise_words"])], "Defined by cases"),
    ("A System with a Brace", [(r"\left\{ \begin{array}{l} 2x + y = 5 \\ x + 3y = 10 \end{array} \right.", "huge", ["system_braced"])], "Solve for x and y together"),
    ("The Chain Rule", [(r"\frac{dy}{dx} = \frac{dy}{du} \cdot \frac{du}{dx}", "Huge", [])], "Differentiating a composite function"),
    ("Pythagoras", [(r"a^2 + b^2 = c^2", "Huge", [])], "For a right-angled triangle"),
    ("Combustion of Methane", [(r"\mathrm{CH_4(g)} + 2\,\mathrm{O_2(g)} \longrightarrow \mathrm{CO_2(g)} + 2\,\mathrm{H_2O(l)}", "huge", ["chem_equation", "chem_formula"])], "Carbon dioxide and water are the products"),
    ("Precipitation of Silver Chloride", [(r"\mathrm{Ag^+(aq)} + \mathrm{Cl^-(aq)} \longrightarrow \mathrm{AgCl(s)}", "Huge", ["chem_equation", "chem_formula", "chem_charge"])], "The net ionic equation"),
]  # fmt: skip


def latex_slides() -> Built:
    v = "dev"
    doc = Doc("latex-slides", v, "latex", "Formulas on Slides: A Short Lecture Deck")
    em = Emitter(doc)
    out = [
        HEADER
        + r"\documentclass[aspectratio=169]{beamer}"
        + "\n"
        + r"\usepackage{amsmath,amssymb}"
        + "\n"
        + r"\usefonttheme{professionalfonts}"
        + "\n"
        + r"\setbeamertemplate{navigation symbols}{}",
        r"\begin{document}",
    ]
    for title, formulas, caption in SLIDES:
        doc.heading(1, title)
        body = []
        for latex, size, classes in formulas:
            spec = mt.EqSpec(latex, "block", ["display_large", *classes], title, None)
            lab = em.eq(spec)
            body.append(f"{lab}{{\\{size} \\[ {latex} \\]}}")
        out.append(
            f"\\begin{{frame}}{{{title}}}\n\\centering\n"
            + "\n\\vspace{0.4em}\n".join(body)
            + f"\n\\vspace{{0.6em}}\n\n{{\\small {caption}}}\n\\end{{frame}}"
        )
    out.append(r"\end{document}")
    return Built(doc, "\n".join(out) + "\n")


# --------------------------------------------------------------------------- 5. latex-structure

_LEAD_BY_LANGUAGE = {
    "python": "A short Python function for this step is shown below.",
    "c": "The same idea written in C looks like this.",
    "bash": "From the command line the steps are as follows.",
    "sql": "A database query for the same summary is given next.",
    "javascript": "In JavaScript the idea is expressed like this.",
}
_LST_LANG = {"python": "Python", "c": "C", "bash": "bash", "sql": "SQL"}


def _table_tex(rows: list[list[str]], header_cols: int, caption: str) -> str:
    ncols = len(rows[0])
    spec = "l" + "c" * (ncols - 1)

    def cell(text: str, bold: bool) -> str:
        t = para_to_tex(parse_para(text))
        return f"\\textbf{{{t}}}" if bold else t

    lines = []
    for r, row in enumerate(rows):
        lines.append(
            " & ".join(
                cell(c, r == 0 or (header_cols and k < header_cols)) for k, c in enumerate(row)
            )
            + r" \\"
        )
        if r == 0:
            lines.append(r"\midrule")
    return (
        "\\begin{table}[H]\n\\centering\n"
        f"\\caption{{{caption}}}\n\\begin{{tabular}}{{{spec}}}\n\\toprule\n"
        + "\n".join(lines).replace(r"\midrule", r"\midrule", 1)
        + "\n\\bottomrule\n\\end{tabular}\n\\end{table}"
    )


def _layout_tex(kind: str, rows: list[list[str]]) -> str:
    if kind in {"side-by-side", "given-find"}:
        cells = " & ".join(rows[0])
        return (
            "\\noindent\\begin{tabular}{@{}p{0.46\\textwidth}@{\\hspace{0.06\\textwidth}}"
            f"p{{0.46\\textwidth}}@{{}}}}\n{cells} \\\\\n\\end{{tabular}}"
        )
    if kind == "form":
        body = " \\\\[10pt]\n".join(f"{a} & \\rule{{7cm}}{{0.4pt}}" for a, _ in rows)
        return f"\\noindent\\begin{{tabular}}{{@{{}}ll@{{}}}}\n{body} \\\\\n\\end{{tabular}}"
    return (
        "\\noindent\\begin{tabular}{@{}p{0.46\\textwidth}@{\\hspace{0.06\\textwidth}}"
        "p{0.46\\textwidth}@{}}\n\\rule{6cm}{0.4pt} & \\rule{6cm}{0.4pt} \\\\[2pt]\n"
        f"{rows[0][0]} & {rows[0][1]} \\\\\n\\end{{tabular}}"
    )


def latex_structure() -> Built:
    v = "dev"
    doc = Doc(
        "latex-structure", v, "latex", "Study Skills for Science: Notes, Examples and Key Terms"
    )
    out = [
        HEADER
        + PREAMBLE.replace(
            r"\usepackage[margin=1in]{geometry}", r"\usepackage[margin=1.15in]{geometry}"
        )
        + "\\usepackage{float,booktabs,listings}\n"
        + "\\setcounter{secnumdepth}{-1}\n"
        + "\\lstset{basicstyle=\\ttfamily\\small,columns=fixed,keepspaces=true,showstringspaces=false}\n"
        + "\\setlength{\\parindent}{0pt}\\setlength{\\parskip}{7pt}",
        r"\begin{document}",
        r"\begin{center}{\Large\bfseries " + doc.title + r"}\end{center}",
    ]
    run_in = list(tx.RUN_IN)
    plain = list(tx.PLAIN_RUN_IN)
    code = tx.CODE[:28]
    ci = 0
    ri = 0
    layout_at = {1: 0, 4: 1, 7: 2, 10: 3}
    data_at = {2: 0, 5: 1, 8: 2, 11: 3}
    ex_at = {1: 0, 3: 1, 5: 2, 7: 3, 9: 4, 11: 5}
    take_at = {2: 0, 4: 1, 6: 2, 8: 3, 10: 4}
    cyu_at = {3: 0, 5: 1, 7: 2, 9: 3, 11: 4}
    for i, title in enumerate(tx.LONG_TITLES):
        doc.heading(1, title)
        out.append(f"\\section{{{title}}}")
        out.append(tx.SECTION_INTROS[i])
        # run-in headings
        for _ in range(2):
            if ri >= len(run_in):
                break
            head, body = run_in[ri]
            if ri % 3 == 2:
                doc.heading(4, head)
                out.append(f"\\textbf{{{head}}} {body}")
            else:
                doc.heading(4, head)
                out.append(f"\\paragraph{{{head}}} {body}")
            ri += 1
        if i < len(plain):
            head, body = plain[i]
            doc.heading(4, head)
            out.append(f"\\paragraph{{{head}}} {body}")
        if i in layout_at:
            kind, rows = tx.LAYOUT_TABLES[layout_at[i]]
            doc.table([[c for c in r] for r in rows], layout=True)
            out.append(_layout_tex(kind, rows))
        if i in data_at:
            caption, rows, hr, hc = tx.DATA_TABLES[data_at[i]]
            doc.table(rows, header_rows=hr, header_cols=hc, caption=caption)
            out.append(_table_tex(rows, hc, caption))
        if i in ex_at:
            problem, solution = tx.EXAMPLES[ex_at[i]]
            doc.heading(3, "Example")
            out.append("\\subsubsection*{Example}")
            out.append(script_blocks(doc, [problem]))
            doc.heading(3, "Solution")
            out.append("\\subsubsection*{Solution}")
            out.append(script_blocks(doc, [solution]))
        for _ in range(2):
            lang, text = code[ci % len(code)]
            ci += 1
            out.append(_LEAD_BY_LANGUAGE[lang])
            doc.code(text, lang)
            if ci % 2 == 0 and lang in _LST_LANG:
                out.append(
                    f"\\begin{{lstlisting}}[language={_LST_LANG[lang]}]\n{text}\n\\end{{lstlisting}}"
                )
            else:
                out.append("{\\small\\begin{verbatim}\n" + text + "\n\\end{verbatim}}")
        if i in take_at:
            doc.heading(2, "Key Takeaways")
            items = "\n".join("\\item " + t for t in tx.TAKEAWAYS[take_at[i]])
            out.append(
                f"\\subsection*{{Key Takeaways}}\n\\begin{{itemize}}\n{items}\n\\end{{itemize}}"
            )
        if i in cyu_at:
            doc.heading(2, "Check Your Understanding")
            items = "\n".join("\\item " + t for t in tx.QUESTIONS[cyu_at[i]])
            out.append(
                f"\\subsection*{{Check Your Understanding}}\n\\begin{{enumerate}}\n{items}\n\\end{{enumerate}}"
            )
    doc.heading(1, "Key Terms")
    out.append("\\section{Key Terms}")
    out.append("\\begin{description}")
    for term, definition in tx.GLOSSARY[:34]:
        doc.glossary(term, definition)
        out.append(f"\\item[{term}] {definition}")
    out.append("\\end{description}")
    out.append(r"\end{document}")
    return Built(doc, "\n\n".join(out) + "\n")


# --------------------------------------------------------------------------- 6. latex-hyphenation


def latex_hyphenation() -> Built:
    v = "dev"
    rng = seed("latex-hyphenation", v)
    doc = Doc("latex-hyphenation", v, "latex", "Notes on Measurement, Models and Uncertainty")
    paras = tx.hyphenation_paragraphs(rng, 18, 9)
    names = [
        "Measurement and Models",
        "Uncertainty in Practice",
        "Approximation and Error",
        "Computation and Simulation",
        "Interpretation of Results",
        "Reproducibility",
    ]
    out = [
        HEADER
        + "\\documentclass[10pt,twocolumn]{article}\n\\usepackage[OT1]{fontenc}\n"
        + "\\renewcommand{\\rmdefault}{cmr}\n\\usepackage{amsmath}\n"
        + "\\usepackage[margin=0.85in,columnsep=0.3in]{geometry}\n"
        + "\\setlength{\\parindent}{1.2em}\\setlength{\\parskip}{0pt}\n",
        r"\begin{document}",
        r"\twocolumn[\begin{center}{\Large\bfseries " + doc.title + r"}\end{center}\vspace{6pt}]",
    ]
    per = len(paras) // len(names)
    for k, name in enumerate(names):
        doc.heading(1, name)
        out.append(f"\\section*{{{name}}}")
        out.extend(paras[k * per : (k + 1) * per])
    out.append(r"\end{document}")

    def post(d: Doc, pdf: bytes) -> None:
        words = word_table(paras)
        seen_keys = set()
        for hb in detect_hyphen_breaks(pdf, words):
            key = (hb["page"], hb["left"], hb["right"], hb["word"])
            d.add(
                "hyphen_break",
                classes=["hyphen_break"],
                left=hb["left"],
                right=hb["right"],
                word=hb["word"],
                compound=hb["compound"],
                page=hb["page"],
                _dup=key in seen_keys,
            )
            seen_keys.add(key)

    return Built(doc, "\n".join(out) + "\n", post)


BUILDERS: dict[str, Callable[..., Built]] = {
    "latex-exercises": latex_exercises,
    "latex-matrices": latex_matrices,
    "latex-chemistry": latex_chemistry,
    "latex-slides": latex_slides,
    "latex-structure": latex_structure,
    "latex-hyphenation": latex_hyphenation,
}
CHECK_BASES = {"latex-exercises", "latex-chemistry"}
_ = flat
