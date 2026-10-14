"""The two Word documents: built with python-docx, exported to PDF by LibreOffice."""

# ruff: noqa: E501  (content tables: long literal lines are the data)

from __future__ import annotations

import io
import random
import tempfile
import zipfile
from pathlib import Path

import authored_chem as ch
import authored_maths as mt
import authored_text as tx
import pymupdf
from authored_core import (
    CONFIG,
    Built,
    Doc,
    Para,
    detect_hyphen_breaks,
    flat,
    parse_para,
    run_tectonic,
    script_runs,
    word_table,
)
from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, Inches, Pt, RGBColor

FIXED_ZIP_TIME = (2026, 10, 7, 0, 0, 0)
BUILD_NOTE = (
    "Made with python-docx (Word built-in styles, real superscript and subscript runs, "
    "automatic hyphenation on, en-US language), then exported to an untagged PDF by "
    "LibreOffice (writer_pdf_Export) with the hyph_en_US hyphenation dictionary.\n"
    "Authored for a11y-stem's development set (dev-v2). Licence: MIT. Author: "
    "a11y-stem contributors.\n"
)


def seed(base: str, variant: str) -> random.Random:
    return random.Random(f"a11y-dsv2/{base}/{variant}")


# --------------------------------------------------------------------------- docx helpers


def new_document(title: str, columns: int = 1, margin: float = 0.9) -> Document:
    d = Document()
    sec = d.sections[0]
    sec.page_width, sec.page_height = Inches(8.5), Inches(11)
    sec.left_margin = sec.right_margin = Inches(margin)
    sec.top_margin = sec.bottom_margin = Inches(0.9)
    if columns > 1:
        cols = sec._sectPr.find(qn("w:cols"))
        cols.set(qn("w:num"), str(columns))
        cols.set(qn("w:space"), "360")
    normal = d.styles["Normal"]
    normal.font.name = "Liberation Serif"
    normal.font.size = Pt(11)
    rpr = normal.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(attr), "Liberation Serif")
    lang = OxmlElement("w:lang")
    lang.set(qn("w:val"), "en-US")
    rpr.append(lang)
    normal.paragraph_format.space_after = Pt(6)
    sizes = {"Heading 1": 16, "Heading 2": 13, "Heading 3": 11.5, "Title": 20}
    for name, size in sizes.items():
        st = d.styles[name]
        st.font.name = "Liberation Sans"
        st.font.size = Pt(size)
        st.font.bold = True
        st.font.color.rgb = RGBColor(0, 0, 0)
        r = st.element.get_or_add_rPr()
        rf = r.find(qn("w:rFonts"))
        for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            rf.set(qn(attr), "Liberation Sans")
        for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
            if rf.get(qn(attr)) is not None:
                del rf.attrib[qn(attr)]
        st.paragraph_format.space_before = Pt(12 if name != "Heading 3" else 8)
        st.paragraph_format.space_after = Pt(4)
        st.paragraph_format.keep_with_next = True
    # automatic hyphenation, in schema order (after defaultTabStop)
    settings = d.settings.element
    hyph = OxmlElement("w:autoHyphenation")
    anchor = settings.find(qn("w:defaultTabStop"))
    anchor.addnext(hyph)
    d.core_properties.author = "a11y-stem contributors"
    d.core_properties.title = title
    d.core_properties.comments = ""
    d.core_properties.last_modified_by = "a11y-stem contributors"
    import datetime

    fixed = datetime.datetime(2026, 10, 7, 0, 0, 0)
    d.core_properties.created = fixed
    d.core_properties.modified = fixed
    d.core_properties.revision = 1
    return d


def add_runs(p, para: Para, *, bold: bool = False) -> None:
    for s in para:
        text = s.text if s.kind == "t" else s.text.replace("-", "\u2212")
        run = p.add_run(text)
        if bold:
            run.bold = True
        if s.italic:
            run.italic = True
        if s.kind == "sup":
            run.font.superscript = True
        elif s.kind == "sub":
            run.font.subscript = True


def deterministic_docx(d: Document) -> bytes:
    buf = io.BytesIO()
    d.save(buf)
    src = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            zi = zipfile.ZipInfo(info.filename, FIXED_ZIP_TIME)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = info.external_attr
            dst.writestr(zi, src.read(info.filename))
    return out.getvalue()


def set_cell_width(cell, inches: float) -> None:
    cell.width = Inches(inches)


def mark_header_row(table) -> None:
    trpr = table.rows[0]._tr.get_or_add_trPr()
    hdr = OxmlElement("w:tblHeader")
    hdr.set(qn("w:val"), "true")
    trpr.append(hdr)


def no_borders(table) -> None:
    tblpr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "nil")
        borders.append(el)
    tblpr.append(borders)


def grid_table(d: Document, rows: list[list[str]], widths: list[float]):
    table = d.add_table(rows=len(rows), cols=len(rows[0]))
    table.style = d.styles["Table Grid"]
    table.autofit = False
    for c, w in enumerate(widths):
        table.columns[c].width = Inches(w)
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            cell = table.cell(r, c)
            set_cell_width(cell, widths[c])
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(2)
            add_runs(p, parse_para(text), bold=(r == 0))
    mark_header_row(table)
    return table


def layout_table(d: Document, cells: list[str], widths: list[float]):
    table = d.add_table(rows=1, cols=len(cells))
    no_borders(table)
    table.autofit = False
    for c, w in enumerate(widths):
        table.columns[c].width = Inches(w)
    for c, text in enumerate(cells):
        cell = table.cell(0, c)
        set_cell_width(cell, widths[c])
        p = cell.paragraphs[0]
        p.paragraph_format.space_after = Pt(2)
        add_runs(p, parse_para(text))
    return table


# --------------------------------------------------------------------------- word-lecture

EXAMPLES_CHECK = [
    (
        f"A ball falls {20} m from rest in {2.0} s. Find the acceleration.",
        "Use d = (1/2) a t^{2|o}, so a = 2d / t^{2|o} = 2(20) / (2.0)^{2|o} = 10 m/s^{2|u}.",
    ),
    (
        "A brine contains 35 g of salt in 500 cm^{3|u} of solution. Find the concentration in g per cm^{3|u}.",
        "The concentration is 35 / 500 = 0.070 g/cm^{3|u}.",
    ),
    (
        "The sum of two numbers is 40 and their difference is 12. Find both numbers.",
        "Adding the two equations gives twice the larger number, 52, so the larger number is 26 and the smaller is 14.",
    ),
    (
        "A sample of 160 g of an isotope has a half-life of 4 days. How much remains after 12 days?",
        "Twelve days is 3 half-lives, so the mass is 160 / 2^{3|o} = 20 g.",
    ),
]
TAKEAWAYS_CHECK = [
    [
        "Write every quantity as a number and a unit.",
        "A power in a unit applies to the whole unit.",
        "Convert before you compute.",
    ],
    [
        "A formula shows the atoms in a unit of the substance.",
        "A charge is written as a superscript after the symbol.",
        "Balance atoms and charge on both sides.",
    ],
    [
        "Mass number and atomic number are conserved.",
        "A half-life is a time, not a fraction.",
        "Indices count the terms of a sequence.",
    ],
]
INTROS = [
    "A measurement is only useful when its unit is clear. In this unit we meet the units that carry powers and the habits that keep calculations honest.",
    "Many physical quantities are ratios of other quantities, and their units show it. Reading a unit from left to right tells how the quantity is built.",
    "Matter is made of atoms, and atoms combine in fixed proportions. Formulas record these proportions and the electric charge that ions carry.",
    "Reactions rearrange atoms without destroying them. Balanced equations keep count of every atom and every charge.",
    "Nuclei are described by two integers, one for the protons and one for the protons and neutrons together. Both numbers are written before the symbol.",
    "Radioactive nuclei decay at random, yet a large sample decays in a predictable pattern. The half-life is the usual measure of that pattern.",
    "A sequence is a list with an order, and the index of a term says where it stands. Subscripts and superscripts carry much of the notation.",
    "Data are described by a centre and a spread. Small symbols attached to letters keep the many quantities apart.",
    "Practice is the quickest way to find out what has been understood. The following exercises use only the notation of the units above.",
    "A good set of notes ends with the terms that were used. The table lists them in alphabetical order with a short definition.",
]
HEADINGS = {
    "dev": {
        "title": "Lecture Notes: Measurement, Matter and Mathematics",
        "u1": "Unit 1: Measurement and Units",
        "u1a": "Quantities and Their Units",
        "u1a3": "Powers in Compound Units",
        "u1b": "Density, Flow and Intensity",
        "u2": "Unit 2: Matter and Reactions",
        "u2a": "Atoms, Molecules and Ions",
        "u2a3": "Charges on Ions",
        "u2b": "Solutions and Reactions",
        "u3": "Unit 3: Nuclei and Radioactivity",
        "u3a": "Isotopes and Nuclear Notation",
        "u3b": "Decay and Half-Life",
        "u4": "Unit 4: Sequences and Statistics",
        "u4a": "Sequences and Series",
        "u4b": "Describing Data",
        "u5": "Unit 5: Practice and Review",
        "u5a": "Practice Exercises",
        "u5b": "Key Terms",
        "u5c": "Reading and Schedule",
        "data": "Measured densities of four materials",
    },
    "check": {
        "title": "Course Notes: Quantities, Substances and Sequences",
        "u1": "Part I: Quantities and How We Measure Them",
        "u1a": "Numbers with Units",
        "u1a3": "Exponents Inside Units",
        "u1b": "Rates, Densities and Energies",
        "u2": "Part II: Substances and Their Changes",
        "u2a": "Elements, Compounds and Charged Particles",
        "u2a3": "Reading the Charge of an Ion",
        "u2b": "Mixtures and Chemical Change",
        "u3": "Part III: The Nucleus and Its Decay",
        "u3a": "Isotopes and Their Symbols",
        "u3b": "How Fast a Sample Decays",
        "u4": "Part IV: Patterns, Terms and Spread",
        "u4a": "Terms of a Sequence",
        "u4b": "Summaries of Data",
        "u5": "Part V: Exercises and Glossary",
        "u5a": "Problems to Solve",
        "u5b": "Vocabulary",
        "u5c": "Announcements",
        "data": "Measured densities of some other materials",
    },
}
LABELS = {
    "dev": {
        "runin": "Definition.",
        "example": "Example",
        "solution": "Solution",
        "take": "Key Takeaways",
    },
    "check": {
        "runin": "Convention.",
        "example": "Worked Problem",
        "solution": "Answer",
        "take": "Summary",
    },
}
RUNIN_BODIES = {
    "dev": [
        "A unit is a standard quantity used to express the size of a measurement.",
        "An ion is an atom or group of atoms that carries a net electric charge.",
        "An isotope is a form of an element with a particular number of neutrons.",
        "A sequence is an ordered list of numbers given by a rule.",
        "An exercise is a short problem designed to practise one skill.",
    ],
    "check": [
        "Quantities are written as a number followed by a unit.",
        "Charges are written as a raised number and a sign after the symbol.",
        "The mass number stands before the symbol of the element.",
        "The index of a term is written as a small lowered letter or number.",
        "Exercises are numbered in the order in which they are set.",
    ],
}
BOLD_SENTENCES = {
    "dev": [
        "Why does the unit matter?",
        "Always convert before you compute.",
        "Remember: the exponent applies to the whole unit.",
        "Is the charge balanced?",
        "Check that mass number and atomic number are conserved:",
        "How do we describe the spread of data?",
    ],
    "check": [
        "What does a power in a unit mean?",
        "Never mix units within one formula.",
        "Note: a charge belongs to the whole ion.",
        "Are the atoms balanced on both sides?",
        "Confirm that the totals match before and after:",
        "What does an index tell us about a term?",
    ],
}
EX_LEAD = {
    "dev": "Solve each equation, and give a reason for every step.",
    "check": "Work through the problems in order, and show every step.",
}
LAYOUT_TEXT = {
    "dev": [
        "Reading list. Chapter 3 of the notes and the short article on measurement error.",
        "Schedule. Lecture on Monday, laboratory on Wednesday and a quiz on Friday.",
    ],
    "check": [
        "Bring. A calculator, a ruler and the data sheet from the last laboratory.",
        "Deadlines. The problem set is due on Thursday and the report on the following Monday.",
    ],
}


def unicode_exercises(rng: random.Random, v: str, n: int) -> list[str]:
    nums, kon = mt.NUMS[v], mt.KONST[v]

    def sq(x: int) -> str:
        return str(x)

    makers = [
        lambda: f"0 = {rng.choice(nums)}x\u00b2 \u2212 {rng.choice(kon)}x + {rng.choice(kon)}",
        lambda: f"y = ({rng.choice(nums)}x + {rng.choice(kon)}) / (x \u2212 {rng.choice(kon)})",
        lambda: f"\u221a(x + {rng.choice(kon)}) = {rng.choice(nums)}",
        lambda: f"|x \u2212 {rng.choice(kon)}| \u2264 {rng.choice(nums)}",
        lambda: (
            f"{rng.choice(nums)}x + {rng.choice(kon)} \u2265 {rng.choice(nums)}x \u2212 {rng.choice(kon)}"
        ),
        lambda: f"x/{rng.choice(nums)} + y/{rng.choice(kon)} = 1",
        lambda: f"f(x) = {rng.choice(nums)}x\u00b3 \u2212 {rng.choice(kon)}x",
        lambda: (
            f"(x \u2212 {rng.choice(nums)})\u00b2 + (y + {rng.choice(kon)})\u00b2 = {rng.choice([9, 16, 25, 36]) * rng.choice([1, 1, 4])}"
        ),
        lambda: f"{rng.choice(nums)}x\u00b2 + {rng.choice(kon)}x = 0",
        lambda: f"x\u00b2 \u2212 {rng.choice(nums) ** 2} = 0",
        lambda: f"sin \u03b8 = {(p := rng.choice(nums))}/{p + rng.choice([2, 3, 5])}",
        lambda: f"{rng.choice(nums)}/x = {rng.choice(kon)}/{rng.choice([4, 5, 6, 8])}",
        lambda: f"{rng.choice(nums)}x \u2212 {rng.choice(kon)}y = {rng.choice(kon) * 2}",
        lambda: f"2x + {rng.choice(kon)} = {rng.choice(nums)} \u00d7 {rng.choice([2, 3, 4])}",
    ]
    tails = [
        "",
        "",
        " Solve for x.",
        " Find all solutions.",
        " Explain your steps.",
        "",
        " Check by substitution.",
    ]
    out = []
    order: list[int] = []
    while len(out) < n:
        if not order:
            order = list(range(len(makers)))
            rng.shuffle(order)
        idx = order.pop()
        tail = rng.choice(tails)
        text = makers[idx]() + ("." + tail if tail else "")
        out.append(text)
    return out


def word_lecture(variant: str) -> Built:
    v = variant
    h = HEADINGS[v]
    lab = LABELS[v]
    rng = seed("word-lecture", v)
    doc = Doc("word-lecture", v, "word", h["title"])
    d = new_document(h["title"], columns=2, margin=0.8)
    words: list[str] = []

    def heading(level: int, text: str) -> None:
        d.add_heading(text, level=level)
        doc.heading(level, text)
        words.append(text)

    def plain(text: str) -> None:
        d.add_paragraph(text)
        words.append(text)

    def script_paragraph(markup: str, prefix: Para | None = None) -> None:
        para = (prefix or []) + parse_para(markup)
        p = d.add_paragraph()
        add_runs(p, para)
        if script_runs(para):
            doc.script_para(para)
        words.append(flat(para))

    def runin(k: int) -> None:
        text = lab["runin"]
        p = d.add_paragraph()
        r = p.add_run(text)
        r.bold = True
        p.add_run(" " + RUNIN_BODIES[v][k])
        doc.heading(4, text)
        words.append(RUNIN_BODIES[v][k])

    def bold_line(text: str) -> None:
        p = d.add_paragraph()
        p.add_run(text).bold = True
        p.paragraph_format.keep_with_next = True
        doc.heading(4, text)

    def example(k: int) -> None:
        pairs = tx.EXAMPLES if v == "dev" else EXAMPLES_CHECK
        problem, solution = pairs[k]
        bold_line(lab["example"])
        script_paragraph(problem)
        bold_line(lab["solution"])
        script_paragraph(solution)

    def takeaways(k: int) -> None:
        bold_line(lab["take"])
        items = tx.TAKEAWAYS[k] if v == "dev" else TAKEAWAYS_CHECK[k]
        for t in items:
            p = d.add_paragraph(t, style="List Bullet")
            p.paragraph_format.space_after = Pt(2)
            words.append(t)

    def paras(markups: list[str]) -> None:
        for m in markups:
            script_paragraph(m)

    bolds = BOLD_SENTENCES[v]

    t = d.add_paragraph(h["title"], style="Title")
    t.alignment = WD_ALIGN_PARAGRAPH.LEFT

    heading(1, h["u1"])
    plain(INTROS[0])
    heading(2, h["u1a"])
    plain(INTROS[1])
    runin(0)
    heading(3, h["u1a3"])
    units = ch.unit_sentences(rng, v, 26)
    paras(ch.group_paragraphs(units[:13], 4))
    bold_line(bolds[0])
    example(0)
    heading(2, h["u1b"])
    paras(ch.group_paragraphs(units[13:], 4))
    bold_line(bolds[2])
    bold_line(bolds[1])
    plain("Convert every quantity into one system of units before any number is substituted.")
    takeaways(0)

    heading(1, h["u2"])
    plain(INTROS[2])
    heading(2, h["u2a"])
    runin(1)
    chem = ch.chem_sentences(rng, v, 20)
    paras(ch.group_paragraphs(chem[:10], 3))
    heading(3, h["u2a3"])
    paras(ch.group_paragraphs(chem[10:], 3))
    bold_line(bolds[3])
    heading(2, h["u2b"])
    plain(INTROS[3])
    example(1)
    takeaways(1)

    heading(1, h["u3"])
    plain(INTROS[4])
    heading(2, h["u3a"])
    runin(2)
    nuc = ch.nuclide_prose(rng, v, 20)
    paras(ch.group_paragraphs(nuc[:10], 4))
    bold_line(bolds[4])
    heading(2, h["u3b"])
    plain(INTROS[5])
    paras(ch.group_paragraphs(nuc[10:], 4))
    example(3)

    heading(1, h["u4"])
    plain(INTROS[6])
    heading(2, h["u4a"])
    runin(3)
    other = ch.other_sentences(rng, v, 24)
    paras(ch.group_paragraphs(other[:12], 4))
    heading(2, h["u4b"])
    plain(INTROS[7])
    paras(ch.group_paragraphs(other[12:], 4))
    bold_line(bolds[5])
    example(2)
    takeaways(2)

    # a data table with a header row
    mats = ch.MATERIALS[v]
    rows = [["Material", "Mass (g)", "Volume (cm^{3|u})", "Density (g/cm^{3|u})"]]
    for name, dens in mats[:5]:
        vol = rng.choice([20, 25, 40, 50, 80])
        rows.append([name.capitalize(), f"{dens * vol:.1f}", str(vol), f"{dens:.2f}"])
    p = d.add_paragraph()
    p.add_run(f"Table 1: {h['data']}").bold = True
    p.paragraph_format.keep_with_next = True
    words.append(h["data"])
    grid_table(d, rows, [0.95, 0.7, 0.8, 0.75])
    doc.table(rows, caption=h["data"])
    d.add_paragraph()

    heading(1, h["u5"])
    plain(INTROS[8])
    heading(2, h["u5a"])
    bold_line(EX_LEAD[v])
    exercises = unicode_exercises(rng, v, 28)
    for i, text in enumerate(exercises, 1):
        p = d.add_paragraph(f"{i}. {text}")
        p.paragraph_format.space_after = Pt(3)
        doc.exercise_text(i, text)
        words.append(text)
    back = d.add_section(WD_SECTION.CONTINUOUS)
    back._sectPr.find(qn("w:cols")).set(qn("w:num"), "1")
    heading(2, h["u5b"])
    plain(INTROS[9])
    pool = tx.GLOSSARY[:10] if v == "dev" else tx.GLOSSARY[10:20]
    rows = [["Term", "Definition"]] + [[a, b] for a, b in pool]
    grid_table(d, rows, [1.5, 5.4])
    doc.table(rows)
    for a, b in pool:
        doc.glossary(a, b)
        words.append(b)
    d.add_paragraph()
    heading(2, h["u5c"])
    layout_table(d, LAYOUT_TEXT[v], [3.4, 3.4])
    doc.table([LAYOUT_TEXT[v]], layout=True)
    words.extend(LAYOUT_TEXT[v])

    def post(dc: Doc, pdf: bytes) -> None:
        table = word_table(words)
        for hb in detect_hyphen_breaks(pdf, table):
            dc.add(
                "hyphen_break",
                classes=["hyphen_break"],
                left=hb["left"],
                right=hb["right"],
                word=hb["word"],
                compound=hb["compound"],
                page=hb["page"],
            )

    return Built(
        doc,
        deterministic_docx(d),
        post,
        {f"{doc.doc_id}.build.txt": BUILD_NOTE},
    )


# --------------------------------------------------------------------------- word-eq-images

EQ_HEADINGS = {
    "dev": {
        "title": "Worked Problems with Equations Pasted as Pictures",
        "a": "Fractions Inside Sentences",
        "b": "Determinants and Matrices",
        "c": "Systems and Cases",
        "d": "Derivations and Other Displays",
        "e": "Summary of Results",
        "f": "Handout Details",
    },
    "check": {
        "title": "Practice Sheet Whose Equations Are Pictures",
        "a": "Ratios Within the Text",
        "b": "Arrays and Their Determinants",
        "c": "Linear Systems and Piecewise Rules",
        "d": "Longer Displays and Sums",
        "e": "Table of Answers",
        "f": "Notices",
    },
}


def render_equation_pngs(latexes: list[tuple[str, str]]) -> list[bytes]:
    """PNG of each (latex, style): one tightly cropped page per equation (Tectonic, with a
    papersize special set from the measured box), rendered at 220 dpi in grey."""
    body = []
    for latex, style in latexes:
        latex = latex.replace("{align*}", "{aligned}")
        inner = f"$\\displaystyle {latex}$" if style == "block" else f"${latex}$"
        body.append(f"\\eqpage{{{inner}}}")
    tex = (
        "\\documentclass[12pt]{article}\n"
        "\\usepackage[OT1]{fontenc}\n\\renewcommand{\\rmdefault}{cmr}\n"
        "\\usepackage{amsmath,amssymb}\n\\pagestyle{empty}\n"
        "\\newcommand{\\eqpage}[1]{%\n"
        "  \\setbox0=\\hbox{#1}%\n"
        "  \\dimen0=\\dimexpr\\wd0+4pt\\relax \\dimen1=\\dimexpr\\ht0+\\dp0+4pt\\relax\n"
        "  \\hoffset=-1in \\voffset=-1in\n"
        "  \\shipout\\vbox to \\dimen1{\\special{papersize=\\the\\dimen0,\\the\\dimen1}"
        "\\kern2pt\\hbox to \\dimen0{\\hss\\box0\\hss}\\vss}}\n"
        "\\begin{document}\n" + "\n".join(body) + "\n\\end{document}\n"
    )
    with tempfile.TemporaryDirectory(prefix="a11y-dsv2-eq-") as tmp:
        path = Path(tmp) / "equations.tex"
        path.write_text(tex, encoding="utf-8")
        pdf, _ = run_tectonic(path, CONFIG["tectonic"])
    out = []
    with pymupdf.open(stream=pdf, filetype="pdf") as src:
        for page in src:
            pix = page.get_pixmap(dpi=220, alpha=False)
            gray = pymupdf.Pixmap(pymupdf.csGRAY, pix)
            out.append(gray.tobytes("png"))
    if len(out) != len(latexes):
        raise RuntimeError(f"expected {len(latexes)} equation images, got {len(out)}")
    return out


def png_size(png: bytes) -> tuple[int, int]:
    pix = pymupdf.Pixmap(png)
    return pix.width, pix.height


def add_picture_run(p, png: bytes, *, inline_text: bool) -> tuple[int, int]:
    w, h = png_size(png)
    run = p.add_run()
    run.add_picture(
        io.BytesIO(png), width=Emu(int(w / 220 * 914400)), height=Emu(int(h / 220 * 914400))
    )
    if inline_text:
        # lower the picture so that its middle sits near the text's x-height
        h_pt = h / 220 * 72
        lower = max(0.0, h_pt / 2 - 3.5)
        rpr = run._r.get_or_add_rPr()
        pos = OxmlElement("w:position")
        pos.set(qn("w:val"), str(-round(lower * 2)))
        rpr.append(pos)
    return w, h


def word_eq_images(variant: str) -> Built:
    v = variant
    h = EQ_HEADINGS[v]
    rng = seed("word-eq-images", v)
    doc = Doc("word-eq-images", v, "word", h["title"])
    d = new_document(h["title"], columns=1, margin=1.0)

    # --- the equations (specs in document order)
    plan: list[dict] = []

    def inline_group(count: int) -> None:
        for it in mt.fraction_sentences(rng, v, count):
            plan.append({"kind": "sentence", "item": it})

    def block(latex: str, lead: str, classes: list[str], numbered: bool = False) -> None:
        plan.append(
            {
                "kind": "block",
                "lead": lead,
                "latex": latex,
                "classes": classes,
                "numbered": numbered,
            }
        )

    seen: set[str] = set()

    def draw(fn, i: int):
        for _ in range(40):
            latex, lead = fn(rng, v, i)
            if latex not in seen:
                seen.add(latex)
                return latex, lead
        raise RuntimeError("could not draw a distinct equation")

    inline_group(7)
    for i in range(6):
        latex, lead = draw(mt.determinant_latex, i)
        block(latex, lead, ["determinant"], numbered=(i % 3 == 0))
    for i in range(6):
        latex, lead = draw(mt.matrix_latex, i)
        block(latex, lead, ["matrix"], numbered=(i % 3 == 1))
    leads = mt.SYSTEM_LEADS[v]
    for i in range(4):
        it = mt.system_item(rng, v, None, leads[i % len(leads)], 2 if i < 3 else 3)
        block(it.eqs[0].latex, leads[i % len(leads)], ["system_braced"], numbered=(i == 1))
    for i in range(4):
        it = mt.piecewise_item(rng, v, None, mt.PIECEWISE_LEADS[v][i % 2], "")
        block(it.eqs[0].latex, mt.PIECEWISE_LEADS[v][i % 2], ["piecewise_words"], numbered=(i == 2))
    for i in range(4):
        latex, lead = draw(mt.derivation_latex, i * 2 + (1 if v == "check" else 0))
        block(latex, lead, ["multirow_equations"], numbered=(i == 0))
    for fn in (mt._open_integral, mt._open_sum, mt._open_derivative, mt._open_trig):
        latex, _ = fn(rng, v)
        block(latex, "A further result is displayed below:", [], numbered=False)

    # --- render all pictures once
    jobs: list[tuple[str, str]] = []
    for e in plan:
        if e["kind"] == "sentence":
            for spec in e["item"].eqs:
                jobs.append((spec.latex, "inline"))
        else:
            jobs.append((e["latex"], "block"))
    pngs = render_equation_pngs(jobs)
    cursor = 0
    assets: dict[str, bytes | str] = {}
    counter = 0

    def take_png() -> tuple[str, bytes]:
        nonlocal cursor, counter
        png = pngs[cursor]
        cursor += 1
        name = f"eq-{counter:03d}.png"
        counter += 1
        assets[name] = png
        return name, png

    # --- the document
    d.add_paragraph(h["title"], style="Title").alignment = WD_ALIGN_PARAGRAPH.LEFT
    sections = [("a", 0, 7), ("b", 7, 19), ("c", 19, 27), ("d", 27, 35)]
    chapter = {"a": 1, "b": 2, "c": 3, "d": 4}
    eq_no = {k: 0 for k in chapter}
    intros = {
        "a": "Fractions that sit inside a sentence were pasted from an equation editor as small pictures.",
        "b": "Arrays of numbers were pasted the same way, one picture for each display.",
        "c": "Braces and cases are pictures too, so the text layer holds none of their symbols.",
        "d": "The last group holds longer displays, some of them numbered at the right margin.",
    }
    for key, lo, hi in sections:
        d.add_heading(h[key], level=1)
        doc.heading(1, h[key])
        d.add_paragraph(intros[key])
        for e in plan[lo:hi]:
            if e["kind"] == "sentence":
                it = e["item"]
                p = d.add_paragraph()
                tex = it.tex
                parts = mt.MATH_RE.split(tex)
                ctx = mt.plain_context(tex)
                for k, piece in enumerate(parts):
                    if k % 2 == 0:
                        if piece:
                            p.add_run(piece)
                    else:
                        name, png = take_png()
                        w, hh = add_picture_run(p, png, inline_text=True)
                        spec = it.eqs[k // 2]
                        doc.equation(
                            spec.latex,
                            display="inline",
                            classes=["equation_image", *spec.classes],
                            context=ctx,
                            anchor=spec.anchor,
                            appearance="image",
                            image=f"authored/{doc.doc_id}/{name}",
                            width=w,
                            height=hh,
                            equation_number=None,
                            exercise_number=None,
                        )
                continue
            lead_p = d.add_paragraph(e["lead"])
            lead_p.paragraph_format.keep_with_next = True
            name, png = take_png()
            p = d.add_paragraph()
            if e["numbered"]:
                eq_no[key] += 1
                label = f"({chapter[key]}.{eq_no[key]})"
                ts = p.paragraph_format.tab_stops
                ts.add_tab_stop(Inches(3.25), WD_TAB_ALIGNMENT.CENTER)
                ts.add_tab_stop(Inches(6.5), WD_TAB_ALIGNMENT.RIGHT)
                p.add_run("\t")
                width, height = add_picture_run(p, png, inline_text=True)
                p.add_run("\t" + label)
            else:
                label = None
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                width, height = add_picture_run(p, png, inline_text=False)
            p.paragraph_format.space_before = Pt(4)
            p.paragraph_format.space_after = Pt(8)
            doc.equation(
                e["latex"],
                display="block",
                classes=["equation_image", *e["classes"]],
                context=e["lead"],
                anchor=mt.prose_anchor(e["lead"])
                if e["lead"] != "A further result is displayed below:"
                else e["lead"],
                appearance="image",
                image=f"authored/{doc.doc_id}/{name}",
                width=width,
                height=height,
                equation_number=label,
                exercise_number=None,
            )
    # --- a plain data table and a layout table
    d.add_heading(h["e"], level=1)
    doc.heading(1, h["e"])
    rows = [["Quantity", "Value", "Unit"]]
    base = {
        "dev": [
            ("Distance", "12", "m"),
            ("Acceleration", "1.5", "m/s^{2|u}"),
            ("Density", "0.072", "g/cm^{3|u}"),
            ("Area", "44", "cm^{2|u}"),
        ],
        "check": [
            ("Length", "20", "m"),
            ("Acceleration", "10", "m/s^{2|u}"),
            ("Density", "0.070", "g/cm^{3|u}"),
            ("Volume", "65", "cm^{3|u}"),
        ],
    }[v]
    rows += [list(r) for r in base]
    grid_table(d, rows, [2.0, 1.2, 1.5])
    doc.table(rows)
    d.add_paragraph()
    d.add_heading(h["f"], level=1)
    doc.heading(1, h["f"])
    cells = {
        "dev": [
            "Return the sheet. Hand in the answers on Friday at the start of class.",
            "Questions. Ask during office hours or write them on the board before the lecture.",
        ],
        "check": [
            "Collect your work. Pick up the marked sheets from the box outside the office.",
            "Help. Use the problem session on Tuesday for any step that is unclear.",
        ],
    }[v]
    layout_table(d, cells, [3.0, 3.0])
    doc.table([cells], layout=True)
    return Built(
        doc, deterministic_docx(d), None, {f"{doc.doc_id}.build.txt": BUILD_NOTE, **assets}
    )


BUILDERS = {"word-lecture": word_lecture, "word-eq-images": word_eq_images}
