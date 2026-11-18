"""The one pattern for a printed "Table N" / "Figure N" caption line.

The table step (`pipeline/tables.py`), the HTML check for a caption left in the text
(`pipeline/html_checks.py`) and the figure step (`pipeline/layout/figures.py`) all ask the
same question of a line of text, so they share the answer: the number is Arabic ("4", "2.3",
"4a") or Roman ("IV", as APS and IEEE print it), and the line is a caption only when the
number is followed by a separator, a capital letter or the end of the line, so "Table 2 shows
that ..." is not one.
"""

from __future__ import annotations

import re

_NUMBER = (
    r"\s*(?:\d+[A-Za-z]?(?:[.\-\u2013]\d+[A-Za-z]?)*|[IVXLC]{1,6}\b)"
    r"(?:\s*[.:|\u2013\u2014-]\s*|\s+(?=[A-Z(\"'\u201c])|\s*$)"
)

# "Table 2.3 Speeds", "TABLE 4: ...", "Tab. 1 ...", "TABLE IV. ...".
TABLE_CAPTION = re.compile(r"^(?:Table|TABLE|Tab\.|TAB\.)" + _NUMBER)

# "Figure 2.3 Velocity...", "Fig. 4: ...". Beamer captions have no number: "Figure: Velocity ...".
FIGURE_CAPTION = re.compile(
    r"^(?:Figure|FIGURE|Fig\.|FIG\.)" + _NUMBER + r"|^(?:Figure|Fig\.):\s+(?=\S)"
)
