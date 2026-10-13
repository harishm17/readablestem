"""Known-correct LaTeX for the 40 seeded gold equations (scorer readiness check, PREREG 7.1 b).

Written by hand from the gold MathML, not by any system under test. Not checked against the
rendered gold by the author yet (that is his step)."""

import json
import sys
from pathlib import Path

LATEX = [
    r"\frac{v u'}{c^2}",
    r"\gamma",
    r"L_0",
    r"v",
    r"E = \frac{\text{hc}}{\lambda} .",
    r"u = \frac{v + u'}{1 + \frac{v u'}{c^2}}",
    r"A",
    r"\text{10}^{-5}",
    r"\text{CH}_3\text{CO}_2{}^{\text{−}}:",
    r"\text{n}{\left(\text{OH}^{\text{−}}\right)}_0 = 0.100\ M \times \text{X mL} \times \left(\frac{\text{1 L}}{\text{1000 mL}}\right)",
    r"K_{\text{b}} = \frac{x^{\text{2}}}{0.0500\ M} ,",
    r"v = \frac{c}{\lambda} \text{ so } \frac{3.00 \times 10^{8}\text{m/s}}{\frac{\text{499 nm} \times \text{1 m}}{10^{9}\text{nm}}} = 6.01 \times 10^{14}\text{Hz}",
    r"d_{x^2 \text{−} y^2}",
    r"E = h n u \text{ so } 6.63 \times 10^{-34}\text{J}\text{·}\text{s} \times 6.01 \times 10^{14}\text{Hz} = 3.99 \times 10^{-19}\text{Joules/ion}",
    r"\text{CH}_3\text{CO}_2\text{H} + \text{OH}^{\text{−}} \longrightarrow \text{CH}_3\text{CO}_2{}^{\text{−}} + \text{H}_2\text{O}",
    r"[\text{H}_3\text{O}^{+}] = \frac{\text{n}(\text{H}^{+})}{V} = \frac{\text{0.002500 mol} \times \left(\frac{\text{1000 mL}}{\text{1 L}}\right)}{\text{25.00 mL}} = 0.1\ M",
    r"i",
    r"\left(4+3i\right)(2-5i).",
    r"60°,",
    r"-2+3i",
    r"43°.",
    r"x > 9",
    r"x = 1",
    r"A",
    r"\frac{1}{6}",
    r"\frac{P(A \text{ AND } B)}{P(A)}",
    r"\frac{P(A \text{ AND } B)}{P(B)}",
    r"\frac{2}{4}",
    r"\frac{2}{6}",
    r"\frac{2}{6}",
    r"\frac{4}{6}",
    r"\mu = \frac{nr}{r+b} = \frac{(4)(6)}{6+5} = 2.18",
    r"r\left(s-18\right)",
    r"6\left(x+7\right).",
    r"20\left(\frac{3}{5}q\right)",
    r"-7\left(8-15y\right).",
    r"\left\{\begin{array}{l}\text{−}x+y=2\\2x+y=-4\end{array}\right. .",
    r"a, a+\left(\text{−}a\right)=0.",
    r"D_x",
    r"\left(4,-3\right),",
]

sample = json.loads(Path(sys.argv[1]).read_text())
assert len(sample) == len(LATEX) == 40
with Path(sys.argv[2]).open("w") as fh:
    for row, latex in zip(sample, LATEX, strict=True):
        fh.write(json.dumps({"id": row["id"], "latex": latex}, ensure_ascii=False) + "\n")
