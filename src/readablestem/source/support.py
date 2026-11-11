"""Which LaTeX classes and packages this tool reads from source, and which it refuses (WS18.1).

A class or a package is on one of three lists (`SUPPORT`):

- `bound`: LaTeXML 0.8.8 ships a binding for it (`<name>.cls.ltxml` or `<name>.sty.ltxml` in
  `LaTeXML/Package/`). The lists are copied from the installed folder, and a test that runs
  where LaTeXML is installed compares them with it (tests/test_source_convert.py), so an
  upgrade that adds or drops a binding fails that test until the list is copied again. A
  binding is not proof that the package is read: one that only loads the package's own TeX
  finds nothing with the TeX tree hidden (`source.latexml.HIDDEN_FILES`). Moving such a name
  to `plain` or `refused` is the package sweep's job (WS18.0), done from a run, never guessed.
- `plain`: no binding is used, and the document is read without one. For a class: its
  commands carry layout, not content, so LaTeXML's fallback reads the document as an
  article. Each class here stays only while a small document of it converts with no error
  (tests/test_source_convert.py). For a package: its binding loads nothing here, the document
  is read without it, and a command of it that the document uses stops the job with
  `source_unsupported_package`.
- `refused`: refused at upload by name (`source.intake.inspect`), because its commands carry
  meaning this tool does not read yet and LaTeXML's guess at them reaches students: the
  `exam` class prints the solutions and marks the correct choice. A package is refused only
  when loading it alone already fails here (`csquotes`: 24 errors with nothing of it used).

A class or package on no list goes to LaTeXML as before, and the report stops the job when
errors follow (`source.report.stop`).

Separately, `distributed` says whether a name belongs to the TeX distribution: the names of
TeX Live's packages and of the files they install, written beside this module by the image
build (`readablestem ops image-facts --write`) from texdoc's data, which comes with
`texlive-base`. A package the source loads that is not one of them is the instructor's own,
and the stop sentence says so. In the repository and in an image without TeX the file is
absent, and nothing is said either way.
"""

from __future__ import annotations

import functools
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Written by the image build (`write_distribution_names`); absent in the repository.
NAMES_PATH = Path(__file__).with_name("tex_distribution_names.txt")
# Where texdoc's data lives under a TeX tree (texlive-base ships it): it maps every run file
# to its TeX Live package and lists each package's documentation.
TEXDOC_DATA = "scripts/texdoc/Data.tlpdb.lua"
DEBIAN_TEXMFDIST = Path("/usr/share/texlive/texmf-dist")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")


@dataclass(frozen=True)
class Lists:
    bound: frozenset[str]
    plain: frozenset[str]
    refused: frozenset[str]

    def kind(self, name: str) -> str | None:
        """ "bound", "plain", "refused", or None for a name on no list."""
        for kind in ("refused", "plain", "bound"):
            if name in getattr(self, kind):
                return kind
        return None


@dataclass(frozen=True)
class Support:
    classes: Lists
    packages: Lists


# The class bindings of LaTeXML 0.8.8 (/usr/share/perl5/LaTeXML/Package/*.cls.ltxml).
_CLASS_BINDINGS = frozenset(
    {
        "IEEEtran", "JHEP", "JHEP2", "JHEP3", "OmniBus", "PoS", "a0poster", "aa", "aastex",
        "acmart", "aipproc", "ams_core", "amsart", "amsbook", "amsproc", "article", "beamer",
        "book", "elsart", "elsarticle", "emulateapj", "gen-j-l", "gen-m-l", "gen-p-l",
        "ieeeconf", "iopart", "llncs", "minimal", "mn", "mn2e", "mnras", "moderncv",
        "quantumarticle", "report", "revtex", "revtex4-1", "revtex4", "slides", "standalone",
        "subfiles", "svjour", "svjour3", "svmult",
    }
)  # fmt: skip

# The package bindings of LaTeXML 0.8.8 (/usr/share/perl5/LaTeXML/Package/*.sty.ltxml).
_PACKAGE_BINDINGS = frozenset(
    {
        "SIunits", "a0size", "a4", "a4wide", "aa_support", "aas_macros", "aas_support",
        "aasms", "aaspp", "aastex", "accents", "acronym", "actuarialangle", "adjustbox", "ae",
        "aecompl", "afterpage", "aipproc", "algc", "algcompatible", "algmatlab", "algorithm",
        "algorithm2e", "algorithmic", "algorithmicx", "algpascal", "algpseudocode", "alltt",
        "ams_support", "amsaddr", "amsbsy", "amscd", "amsfonts", "amsgen", "amsmath", "amsopn",
        "amsppt", "amsrefs", "amssymb", "amstex", "amstext", "amsthm", "amsxtra", "apjfonts",
        "appendix", "array", "attachfile", "atveryend", "authblk", "auxhook", "avant", "babel",
        "babel_support", "balance", "bbding", "bbm", "bbold", "beton", "bezier", "bibunits",
        "bigintcalc", "bitset", "blindtext", "bm", "bookman", "bookmark", "booktabs",
        "boxedminipage", "braket", "breakurl", "calc", "calrsfs", "cancel", "caption", "cases",
        "ccfonts", "chancery", "chapterbib", "charter", "chngcntr", "circuitikz", "cite",
        "citesort", "cleveref", "cmap", "cmbright", "color", "colordvi", "colortbl", "comment",
        "concmath", "courier", "crop", "cropmark", "csquotes", "dcolumn", "deluxetable",
        "diagbox", "doi", "doublespace", "dsfont", "ed", "ellipsis", "elsart",
        "elsart_support", "elsart_support_core", "empheq", "emulateapj", "emulateapj5",
        "endnotes", "english", "enumerate", "enumitem", "epigraph", "epsf", "epsfig",
        "epstopdf", "esint", "espcrc", "etex", "etoolbox", "eucal", "eufrak", "euler",
        "eulervm", "eurosym", "euscript", "expl3", "exscale", "fancybox", "fancyhdr",
        "fancyheadings", "fancyvrb", "feynmf", "filehook", "fix-cm", "fixltx2e", "fixme",
        "flafter", "fleqn", "float", "floatfig", "floatflt", "floatpag", "flowchart",
        "flushend", "fontenc", "fontspec", "footmisc", "footnote", "fourier", "framed",
        "fullpage", "gensymb", "geometry", "german", "gettitlestring", "glossaries",
        "graphics", "graphicx", "grffile", "helvet", "hepunits", "here", "hhline", "html",
        "hypcap", "hyperref", "hyperxmp", "icml", "icml_support", "ifdraft", "ifetex",
        "ifluatex", "ifpdf", "ifplatform", "ifsym", "iftex", "ifthen", "ifvtex", "ifxetex",
        "ijcai", "import", "indentfirst", "infwarerr", "inputenc", "inst_support", "intcalc",
        "iopams", "iopart_support", "jheppub", "keyval", "kvdefinekeys", "kvoptions",
        "kvsetkeys", "l3keys2e", "lastpage", "latexml", "latexsym", "lineno", "lipsum",
        "listings", "listingsutf8", "lmodern", "longtable", "lscape", "ltxcmds", "luximono",
        "lxRDFa", "makecell", "makeidx", "marginnote", "marvosym", "mathabx", "mathbbol",
        "mathdots", "mathpazo", "mathpple", "mathptm", "mathptmx", "mathrsfs", "mathtools",
        "microtype", "mleftright", "mn2e_support", "multicol", "multido", "multirow",
        "nameref", "natbib", "neurips", "newcent", "newfloat", "newlfont", "newtxmath",
        "newtxtext", "ngerman", "nicefrac", "nomencl", "nopageno", "ntheorem", "numprint",
        "overpic", "palatino", "paralist", "parskip", "pdflscape", "pdfpages", "pdfsync",
        "pdftexcmds", "pgf", "pgfplots", "pgfplotstable", "physics", "pifont", "placeins",
        "prettyref", "preview", "proof", "proofwiki", "psfig", "psfrag", "pslatex",
        "pspicture", "pst-grad", "pst-node", "pstricks", "pxfonts", "ragged2e", "refcount",
        "relsize", "remreset", "revsymb", "revtex", "revtex3_support", "revtex4",
        "revtex4_support", "rotate", "rotating", "rsfs", "scalefnt", "sectsty", "setspace",
        "showkeys", "sidecap", "siunitx", "slashbox", "slashed", "soul", "srcltx",
        "standalone", "stfloats", "stmaryrd", "subcaption", "subeqn", "subeqnarray", "subfig",
        "subfigure", "subfiles", "subfloat", "supertabular", "sv_support", "svg", "t1enc",
        "tablefootnote", "tabularx", "tabulary", "tcolorbox", "textcase", "textcomp", "texvc",
        "theorem", "thm-restate", "thmtools", "threeparttable", "tikz-3dplot", "tikz-cd",
        "tikz", "tikzbricks", "times", "titlesec", "titling", "tocbibind", "todonotes",
        "tracefnt", "transparent", "turing", "twoopt", "txfonts", "type1cm", "ulem",
        "underscore", "undertilde", "units", "upgreek", "upquote", "upref", "url", "utopia",
        "varioref", "varwidth", "verbatim", "vmargin", "wasysym", "wiki", "wrapfig", "xargs",
        "xcolor", "xfrac", "xkeyval", "xkvview", "xparse", "xspace", "xunicode", "xurl", "xy",
        "xypic", "yfonts",
    }
)  # fmt: skip

_ARTICLE_CLASSES = frozenset({"scrartcl", "scrreprt", "scrbook", "memoir", "extarticle"})
_REFUSED_CLASSES = frozenset({"exam"})
# Measured here with the repository's own LaTeX path: `babel`'s binding loads nothing, and a
# document that loads it and uses none of its commands converts with no error; one that uses
# `\selectlanguage` or `\foreignlanguage` shows the command as raw text, which the report
# stops. `csquotes` alone gives 24 errors before the document starts.
_PLAIN_PACKAGES = frozenset({"babel"})
_REFUSED_PACKAGES = frozenset({"csquotes"})

SUPPORT = Support(
    classes=Lists(
        bound=_CLASS_BINDINGS - _ARTICLE_CLASSES - _REFUSED_CLASSES,
        plain=_ARTICLE_CLASSES,
        refused=_REFUSED_CLASSES,
    ),
    packages=Lists(
        bound=_PACKAGE_BINDINGS - _PLAIN_PACKAGES - _REFUSED_PACKAGES,
        plain=_PLAIN_PACKAGES,
        refused=_REFUSED_PACKAGES,
    ),
)


# ---------------------------------------------------------------- TeX distribution names


def texdoc_names(data: Path) -> set[str]:
    """The names in texdoc's data file: every TeX Live package and the base name of every
    run file it maps to one (`mhchem`, `exam`, `scrartcl`, `tikz`...)."""
    names: set[str] = set()
    section = ""
    with data.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.startswith("local "):
                section = line.split()[1]
                continue
            if section not in ("tlp_from_runfile", "tlp_doclist"):
                continue
            names.update(re.findall(r'\["([^"]+)"\]\s*=', line))
    return {n for n in names if _NAME.match(n) and "/" not in n}


def _texmfdist() -> Path:
    try:
        done = subprocess.run(
            ["kpsewhich", "--var-value", "TEXMFDIST"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return DEBIAN_TEXMFDIST
    value = done.stdout.strip()
    return Path(value) if done.returncode == 0 and value else DEBIAN_TEXMFDIST


def distribution_names() -> set[str]:
    """The TeX distribution's names on this machine (`texdoc_names`), empty when it has no
    texdoc data (no TeX installed)."""
    data = _texmfdist() / TEXDOC_DATA
    return texdoc_names(data) if data.is_file() else set()


def write_distribution_names(names: set[str], path: Path = NAMES_PATH) -> None:
    """Write `names` beside this module, one per line, for `distributed` (the image build:
    `readablestem ops image-facts --write`). No names, no file: `distributed` then says
    nothing either way."""
    if not names:
        return
    path.write_text("\n".join(sorted(names)) + "\n", encoding="utf-8")
    _names.cache_clear()


@functools.cache
def _names() -> frozenset[str] | None:
    try:
        text = NAMES_PATH.read_text(encoding="utf-8")
    except OSError:
        return None
    names = frozenset(line.strip() for line in text.splitlines() if line.strip())
    return names or None


def distributed(name: str) -> bool | None:
    """Whether a package of this name comes with the TeX distribution: True when it is on one
    of `SUPPORT`'s lists or in the names the image build wrote, False when the names are
    there and it is not among them (the instructor's own package), None when the names were
    not written (the repository, an image without TeX)."""
    if SUPPORT.packages.kind(name) is not None:
        return True
    names = _names()
    if names is None:
        return None
    return name in names
