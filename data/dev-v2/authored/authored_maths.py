"""Mathematics content generators: exercises, systems, piecewise functions, determinants,
matrices, worked derivations and fraction sentences.

Every generator takes a seeded `random.Random` and the variant ("dev" or "check"). The two
variants draw their coefficients from disjoint number pools, so the same template gives a
different equation in each. Classes are set by construction.
"""

# ruff: noqa: E501  (content tables: long literal lines are the data)

from __future__ import annotations

import re
from dataclasses import dataclass, field
from math import gcd

# Coefficient pools: no number is in both.
NUMS = {"dev": [2, 3, 5, 7, 9, 11], "check": [4, 6, 8, 10, 12, 13]}
# Small offsets and constants: also disjoint.
KONST = {"dev": [1, 3, 5, 7, 9, 13, 15], "check": [2, 4, 6, 8, 10, 14, 16]}
NAMES = {
    "dev": ["flour", "sugar", "rice", "milk", "paint", "juice"],
    "check": ["butter", "honey", "water", "cement", "sand", "oil"],
}

MATH_RE = re.compile("\u27e6(.+?)\u27e7")


@dataclass
class EqSpec:
    latex: str
    display: str  # block | inline
    classes: list[str]
    context: str
    anchor: str | None
    exercise_number: str | None = None


@dataclass
class Item:
    """One exercise or paragraph: LaTeX text and the equations inside it, in order."""

    tex: str
    eqs: list[EqSpec] = field(default_factory=list)
    number: int | None = None


# ----------------------------------------------------------------- small formatting helpers


def poly(terms: list[tuple[int, str]]) -> str:
    """`[(3,'x^2'),(-5,'x'),(2,'')]` -> `3x^2 - 5x + 2`."""
    out = ""
    for c, v in terms:
        if c == 0:
            continue
        mag = abs(c)
        body = ("" if mag == 1 and v else str(mag)) + v
        if not out:
            out = ("-" if c < 0 else "") + body
        else:
            out += (" - " if c < 0 else " + ") + body
    return out or "0"


def signed(c: int) -> str:
    return f"+ {c}" if c >= 0 else f"- {-c}"


def det2(a, b, c, d):
    return a * d - b * c


def det3(m):
    (a, b, c), (d, e, f), (g, h, i) = m
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


def tex_matrix(rows, env="bmatrix"):
    body = " \\\\ ".join(" & ".join(str(x) for x in r) for r in rows)
    return f"\\begin{{{env}}} {body} \\end{{{env}}}"


def tex_array(rows, spec):
    body = " \\\\ ".join(" & ".join(str(x) for x in r) for r in rows)
    return f"\\begin{{array}}{{{spec}}} {body} \\end{{array}}"


def split_math(tex: str) -> tuple[str, list[str]]:
    """Template text with recorded math in U+27E6..U+27E7 -> (LaTeX with $..$, [math])."""
    pieces = MATH_RE.findall(tex)
    return MATH_RE.sub(lambda m: "$" + m.group(1) + "$", tex), pieces


def plain_context(tex: str) -> str:
    out = tex.replace("\u27e6", "").replace("\u27e7", "").replace("$", "")
    out = out.replace("~", " ")
    return " ".join(out.split())


def prose_anchor(tex: str) -> str | None:
    """A run of plain words from `tex` (outside math) to find its page: the first chunk of
    at least 12 characters, else the longest."""
    t = re.sub(r"\\\[.*?\\\]", "|", tex, flags=re.S)
    t = re.sub(r"\u27e6.+?\u27e7|\$.+?\$", "|", t)
    chunks = [re.sub(r"\\[A-Za-z]+\{?|[{}\\~]", " ", c).strip() for c in t.split("|")]
    chunks = [c for c in chunks if len(c) >= 4]
    for c in chunks:
        if len(c) >= 12:
            return c
    return max(chunks, key=len) if chunks else None


def inline_item(tex: str, number: int | None = None, opens: bool = False) -> Item:
    """An item whose recorded math sits inline; builds the EqSpecs from the markers."""
    _, pieces = split_math(tex)
    ctx = plain_context(tex)
    anchor = prose_anchor(tex)
    eqs = []
    for k, latex in enumerate(pieces):
        classes = []
        if "\\frac" in latex:
            classes.append("inline_fraction")
        ex = None
        if opens and k == 0 and tex.startswith("\u27e6"):
            classes.append("numbered_exercise_eq")
            ex = str(number)
        eqs.append(EqSpec(latex, "inline", classes, ctx, anchor if k == 0 else "=", ex))
    return Item(tex, eqs, number)


# ----------------------------------------------------------------- exercise templates


def _quad(rng, v):
    p, r = rng.choice(NUMS[v]), rng.choice([1, 1, 2, 3])
    q, s = rng.choice([1, 2, 3, 4, 5]), rng.choice([1, 2, 3, 4, 6])
    # (p x - q)(r x - s)
    a, b, c = p * r, -(p * s + q * r), q * s
    return poly([(a, "x^2"), (b, "x"), (c, "")]), a


def _open_quadratic(rng, v):
    expr, _ = _quad(rng, v)
    return f"0 = {expr}", "Solve the equation for $x$ by factoring."


def _open_rational(rng, v):
    a, b, c = rng.choice(NUMS[v]), rng.choice(KONST[v]), rng.choice(KONST[v])
    return (
        f"y = \\frac{{{a}x + {b}}}{{x - {c}}}",
        "State the domain and the vertical asymptote of this function.",
    )


def _open_line(rng, v):
    m, k = rng.choice(NUMS[v]), rng.choice(KONST[v])
    sign = rng.choice([1, -1])
    return (
        f"y = {poly([(sign * m, 'x'), (-k, '')])}",
        "Find the $x$-intercept of the line and state its slope.",
    )


def _open_intercepts(rng, v):
    a, b = rng.choice(NUMS[v]), rng.choice(KONST[v])
    return (
        f"\\frac{{x}}{{{a}}} + \\frac{{y}}{{{b}}} = 1",
        "Find the points where the line crosses each axis.",
    )


def _open_radical(rng, v):
    a, b = rng.choice(KONST[v]), rng.choice(NUMS[v])
    return (
        f"\\sqrt{{x + {a}}} = {b}",
        "Solve the equation and check the answer for extraneous solutions.",
    )


def _open_abs(rng, v):
    a, b = rng.choice(KONST[v]), rng.choice(NUMS[v])
    return f"|x - {a}| < {b}", "Write the solution set in interval notation."


def _open_exp(rng, v):
    base, k, a = rng.choice([2, 3, 5]), rng.choice([2, 3, 4]), rng.choice(KONST[v])
    return (
        f"{base}^{{x - {a}}} = {base**k}",
        "Solve for $x$ by writing both sides with the same base.",
    )


def _open_log(rng, v):
    base, k, a = rng.choice([2, 3, 5]), rng.choice([2, 3]), rng.choice(NUMS[v])
    return f"\\log_{{{base}}}(x + {a}) = {k}", "Find $x$ and state the domain of the logarithm."


def _open_cubic(rng, v):
    a, b = rng.choice(NUMS[v]), rng.choice(KONST[v])
    return (
        f"f(x) = {a}x^3 - {b}x",
        "Find $f'(x)$ and the points where the tangent line is horizontal.",
    )


def _open_circle(rng, v):
    h, k, r = rng.choice(NUMS[v]), rng.choice(KONST[v]), rng.choice([3, 4, 5, 6])
    return (
        f"(x - {h})^2 + (y + {k})^2 = {r * r}",
        "State the centre and the radius of this circle.",
    )


def _open_trig(rng, v):
    p = rng.choice(NUMS[v])
    q = p + rng.choice([1, 2, 3, 5])
    return (
        f"\\sin\\theta = \\frac{{{p}}}{{{q}}}",
        "Find $\\cos\\theta$ for an acute angle $\\theta$.",
    )


def _open_arith(rng, v):
    a1, d = rng.choice(KONST[v]), rng.choice(NUMS[v])
    return f"a_n = {a1} + (n - 1) \\cdot {d}", "Find the 20th term of this arithmetic sequence."


def _open_proportion(rng, v):
    a, b, c = rng.choice(NUMS[v]), rng.choice(KONST[v]), rng.choice([4, 5, 6, 8])
    return f"\\frac{{{a}}}{{x}} = \\frac{{{b}}}{{{c}}}", "Solve the proportion for $x$."


def _open_inequality(rng, v):
    a, b, c, d = rng.choice(NUMS[v]), rng.choice(KONST[v]), 1, rng.choice(KONST[v])
    return (
        f"{a}x + {b} \\le {c + a + 1}x - {d}",
        "Solve the inequality and show the solution on a number line.",
    )


def _open_derivative(rng, v):
    a, b = rng.choice(NUMS[v]), rng.choice(KONST[v])
    return (
        f"\\frac{{d}}{{dx}}\\left({a}x^3 - {b}x^2\\right)",
        "Evaluate this derivative at $x = 2$.",
    )


def _open_integral(rng, v):
    a, b, n = rng.choice(NUMS[v]), rng.choice(KONST[v]), rng.choice([2, 3, 4])
    return f"\\int_0^{n} ({a}x + {b})\\,dx", "Evaluate the definite integral."


def _open_partial(rng, v):
    a, b, c = rng.choice(NUMS[v]), rng.choice(NUMS[v]), rng.choice(KONST[v])
    return (
        f"\\frac{{{a}}}{{x - 1}} + \\frac{{{b}}}{{x + 1}} = {c}",
        "Solve for $x$, giving exact values.",
    )


def _open_sum(rng, v):
    a, b, n = rng.choice(NUMS[v]), rng.choice(KONST[v]), rng.choice([8, 10, 12])
    return (
        f"\\sum_{{k=1}}^{{{n}}} ({a}k + {b})",
        "Evaluate the sum using the formulas for $\\sum k$.",
    )


OPENING = [
    _open_quadratic,
    _open_rational,
    _open_line,
    _open_intercepts,
    _open_radical,
    _open_abs,
    _open_exp,
    _open_log,
    _open_cubic,
    _open_circle,
    _open_trig,
    _open_arith,
    _open_proportion,
    _open_inequality,
    _open_derivative,
    _open_integral,
    _open_partial,
    _open_sum,
]


def opening_exercise(rng, v, number: int, index: int) -> Item:
    latex, instr = OPENING[index % len(OPENING)](rng, v)
    return inline_item(f"\u27e6{latex}\u27e7. {instr}", number, opens=True)


# ---- prose-first exercises


def _pf_simplify(rng, v):
    a = rng.choice(KONST[v])
    return f"Simplify \u27e6\\frac{{x^2 - {a * a}}}{{x + {a}}}\u27e7 for $x \\neq -{a}$."


def _pf_slope(rng, v):
    a, b, c, d = (
        rng.choice(KONST[v]),
        rng.choice(NUMS[v]),
        rng.choice(KONST[v]),
        rng.choice(NUMS[v]),
    )
    c += a + 1
    return (
        f"A line passes through $({a}, {b})$ and $({c}, {d})$. Compute the slope "
        f"\u27e6\\frac{{{d} - {b}}}{{{c} - {a}}}\u27e7 and write the line in point-slope form."
    )


def _pf_add(rng, v):
    while True:
        a, b, c, d = (
            rng.choice(NUMS[v]),
            rng.choice([2, 3, 5]),
            rng.choice(KONST[v]),
            rng.choice([4, 6, 7]),
        )
        if a % b and c % d and gcd(a, b) == 1 and gcd(c, d) == 1:
            break
    return (
        f"Add \u27e6\\frac{{{a}}}{{{b}}} + \\frac{{{c}}}{{{d}}}\u27e7 and write the result "
        "as a single fraction in lowest terms."
    )


def _pf_eval(rng, v):
    a, b, c = rng.choice(NUMS[v]), rng.choice(KONST[v]), rng.choice([2, 3, 4])
    return (
        f"Let $f(x) = {a}x^2 - {b}$. Compute \u27e6f({c}) - f(-{c})\u27e7 and explain why "
        "the answer is what you would expect."
    )


def _pf_expand(rng, v):
    a, b = rng.choice(NUMS[v]), rng.choice(KONST[v])
    return f"Expand \u27e6(x + {a})^2 - (x - {b})^2\u27e7 and collect like terms."


def _pf_split(rng, v):
    a, b, c = rng.choice(NUMS[v]), rng.choice(KONST[v]), rng.choice([4, 6, 8])
    return (
        f"Rewrite \u27e6\\frac{{{a}x + {b}}}{{{c}}}\u27e7 as the sum of two fractions "
        "and simplify each one."
    )


def _pf_recipe(rng, v):
    p, q = rng.choice(NUMS[v]), rng.choice([4, 5, 6, 8])
    p = min(p, q - 1)
    n = rng.choice([6, 9, 12])
    stuff = rng.choice(NAMES[v])
    return (
        f"A recipe uses \u27e6\\frac{{{p}}}{{{q}}}\u27e7 cup of {stuff} for each batch. "
        f"How much {stuff} is needed for {n} batches?"
    )


def _pf_two_step(rng, v):
    a, b = rng.choice(NUMS[v]), rng.choice(KONST[v])
    return (
        f"Solve \u27e6\\frac{{x}}{{{a}}} - \\frac{{x}}{{{a + 1}}} = {b}\u27e7 for $x$ "
        "and check your answer by substitution."
    )


PROSE_FIRST = [
    _pf_simplify,
    _pf_slope,
    _pf_add,
    _pf_eval,
    _pf_expand,
    _pf_split,
    _pf_recipe,
    _pf_two_step,
]


def prose_first_exercise(rng, v, number: int, index: int) -> Item:
    return inline_item(PROSE_FIRST[index % len(PROSE_FIRST)](rng, v), number)


# ----------------------------------------------------------------- systems of equations


def system_latex(rng, v, size: int = 2) -> str:
    """A braced system with an integer solution."""
    pool = NUMS[v]
    while True:
        sol = [rng.randint(-3, 5) for _ in range(size)]
        m = [[rng.choice(pool) * rng.choice([1, 1, -1]) for _ in range(size)] for _ in range(size)]
        for row in m:
            if size == 3 and rng.random() < 0.5:
                row[rng.randrange(size)] = 0
        det = det2(m[0][0], m[0][1], m[1][0], m[1][1]) if size == 2 else det3(m)
        if det != 0 and any(any(row) for row in m) and all(any(row) for row in m):
            break
    names = ["x", "y", "z"][:size]
    rows = []
    for row in m:
        rhs = sum(c * s for c, s in zip(row, sol, strict=True))
        lhs = poly([(c, n) for c, n in zip(row, names, strict=True)])
        rows.append(f"{lhs} = {rhs}")
    body = " \\\\ ".join(rows)
    return f"\\left\\{{ \\begin{{array}}{{l}} {body} \\end{{array}} \\right."


def system_item(rng, v, number: int | None, lead: str, size: int = 2) -> Item:
    latex = system_latex(rng, v, size)
    tex = f"{lead}\n\\[\n  {latex}\n\\]"
    eq = EqSpec(latex, "block", ["system_braced"], lead, prose_anchor(lead))
    return Item(tex, [eq], number)


SYSTEM_LEADS = {
    "dev": [
        "Solve the system of equations by elimination:",
        "Find every pair of numbers that satisfies the system:",
        "Use substitution to solve the following system:",
        "Determine the values of the unknowns in the system:",
    ],
    "check": [
        "Find the solution of the linear system below:",
        "Solve by adding multiples of one equation to another:",
        "Which values satisfy all the equations in the system?",
        "Eliminate one unknown and solve the system:",
    ],
}


# ----------------------------------------------------------------- piecewise functions


def piecewise_latex(rng, v) -> str:
    n = rng.randrange(6)
    a, b, c, d = (rng.choice(NUMS[v]) for _ in range(4))
    k1, k2 = rng.choice(KONST[v]), rng.choice(KONST[v])
    if n == 0:
        return (
            f"f(x) = \\begin{{cases}} {a}x + {k1} & \\text{{if }} x < {k2} \\\\ "
            f"x^2 - {b} & \\text{{if }} {k2} \\le x < {k2 + c} \\\\ {d} & \\text{{otherwise}} "
            "\\end{cases}"
        )
    if n == 1:
        return (
            f"g(t) = \\begin{{cases}} {a}t & \\text{{for }} 0 \\le t \\le {k1} \\\\ "
            f"{a * k1} & \\text{{for }} {k1} < t \\le {k1 + b} \\\\ "
            f"{a * k1} - {c}(t - {k1 + b}) & \\text{{for }} t > {k1 + b} \\end{{cases}}"
        )
    if n == 2:
        return (
            f"h(x) = \\begin{{cases}} x + {k1} & \\text{{when }} x \\ge 0 \\\\ "
            f"{k1} - {a}x & \\text{{when }} x < 0 \\end{{cases}}"
        )
    if n == 3:
        return (
            f"s(n) = \\begin{{cases}} {a} & \\text{{if }} n \\text{{ is even}} \\\\ "
            f"-{b} & \\text{{if }} n \\text{{ is odd}} \\end{{cases}}"
        )
    if n == 4:
        return (
            f"P(w) = \\begin{{cases}} {a} & \\text{{if }} 0 < w \\le {k1} \\\\ "
            f"{a} + {c}(w - {k1}) & \\text{{if }} {k1} < w \\le {k1 + b} \\\\ "
            "\\text{no service} & \\text{otherwise} \\end{cases}"
        )
    return (
        f"u(x) = \\begin{{cases}} {a}x - {k1} & \\text{{if }} x > {k2} \\text{{ and }} x \\ne {k2 + b} \\\\ "
        f"{c} & \\text{{if }} x = {k2 + b} \\\\ 0 & \\text{{elsewhere}} \\end{{cases}}"
    )


PIECEWISE_LEADS = {
    "dev": ["A function is defined in pieces:", "A function is given by cases:"],
    "check": [
        "Consider the following function defined by cases:",
        "A rule changes with the input:",
    ],
}


def piecewise_item(rng, v, number: int | None, lead: str, after: str) -> Item:
    latex = piecewise_latex(rng, v)
    tex = f"{lead}\n\\[\n  {latex}\n\\]\n{after}"
    eq = EqSpec(latex, "block", ["piecewise_words"], lead, prose_anchor(lead))
    return Item(tex, [eq], number)


# ----------------------------------------------------------------- inline fraction sentences

FRACTION_TEMPLATES = [
    "Dividing both sides by {a} gives \u27e6x = \\frac{{{b}}}{{{a}}}\u27e7.",
    "A tank that is \u27e6\\frac{{{p}}}{{{q}}}\u27e7 full holds {n} litres, so the whole tank "
    "holds \u27e6\\frac{{{q}}}{{{p}}} \\cdot {n}\u27e7 litres.",
    "The expression \u27e6\\frac{{{p}}}{{{q}}}x + {b}\u27e7 is linear, and its slope is "
    "\u27e6\\frac{{{p}}}{{{q}}}\u27e7.",
    "Multiplying \u27e6\\frac{{x}}{{{a}}}\u27e7 by {a} removes the denominator and leaves "
    "the single letter x.",
    "On a map drawn to scale \u27e6\\frac{{1}}{{{k}}}\u27e7, one centimetre stands for {k} "
    "centimetres on the ground.",
    "Writing the ratio of the sides as \u27e6\\frac{{{p}}}{{{q}}}\u27e7 shows that the "
    "triangles are similar.",
    "The average rate of change over the interval is \u27e6\\frac{{f({b}) - f({a})}}{{{b} - "
    "{a}}}\u27e7.",
    "If a pump fills \u27e6\\frac{{1}}{{{a}}}\u27e7 of a pool each hour, it needs {a} hours to "
    "fill the whole pool.",
    "Two pipes together fill \u27e6\\frac{{1}}{{{a}}} + \\frac{{1}}{{{b}}}\u27e7 of the pool "
    "in one hour.",
    "The probability of drawing a red card is \u27e6\\frac{{{p}}}{{{q}}}\u27e7 when {p} of "
    "the {q} cards are red.",
    "Because \u27e6\\frac{{{p}x}}{{{q}}}\u27e7 equals \u27e6\\frac{{{p}}}{{{q}}}x\u27e7, the "
    "factor can be moved in front of the letter.",
    "The common denominator of \u27e6\\frac{{1}}{{{a}}}\u27e7 and \u27e6\\frac{{1}}{{{b}}}\u27e7 "
    "is their product when the numbers share no factor.",
    "A speed of \u27e6\\frac{{{a}}}{{{b}}}\u27e7 metres per second is the same as {a} metres "
    "every {b} seconds.",
    "To compare \u27e6\\frac{{{p}}}{{{q}}}\u27e7 with \u27e6\\frac{{{a}}}{{{b}}}\u27e7, "
    "cross-multiply and compare the two products.",
    "The reciprocal of \u27e6\\frac{{{p}}}{{{q}}}\u27e7 is obtained by swapping the numerator "
    "and the denominator.",
    "The mean of {n} equal parts of a total T is \u27e6\\frac{{T}}{{{n}}}\u27e7.",
]


def fraction_sentences(rng, v, count: int) -> list[Item]:
    items = []
    order = list(range(len(FRACTION_TEMPLATES)))
    k = 0
    while len(items) < count:
        if k % len(order) == 0:
            rng.shuffle(order)
        tmpl = FRACTION_TEMPLATES[order[k % len(order)]]
        k += 1
        p = rng.choice(NUMS[v])
        q = p + rng.choice([1, 2, 3, 5])
        if gcd(p, q) != 1:
            continue
        a, b = rng.sample(NUMS[v], 2)
        text = tmpl.format(
            p=p, q=q, a=a, b=b, n=rng.choice(KONST[v]) + 3, k=rng.choice(NUMS[v]) * 100
        )
        items.append(inline_item(text))
    return items


# ----------------------------------------------------------------- determinants and matrices


def _entries(rng, v, rows, cols, lo=-4):
    pool = NUMS[v] + KONST[v]
    return [
        [
            rng.choice(pool) * rng.choice([1, 1, -1]) if rng.random() < 0.8 else rng.randint(lo, 3)
            for _ in range(cols)
        ]
        for _ in range(rows)
    ]


def determinant_latex(rng, v, style: int) -> tuple[str, str]:
    """(latex, lead sentence)."""
    k1, k2 = rng.choice(NUMS[v]), rng.choice(NUMS[v])
    if style % 8 == 0:
        m = _entries(rng, v, 2, 2)
        return (
            f"\\det A = {tex_matrix(m, 'vmatrix')} = {det2(m[0][0], m[0][1], m[1][0], m[1][1])}",
            "The determinant of a two by two matrix is the product of the diagonal entries "
            "minus the product of the others:",
        )
    if style % 8 == 1:
        m = _entries(rng, v, 3, 3)
        return (
            f"\\det B = {tex_matrix(m, 'vmatrix')} = {det3(m)}",
            "Expanding along the first row gives the determinant of a three by three matrix:",
        )
    if style % 8 == 2:
        return (
            f"{tex_matrix([[f'{k1}a', 'b'], [f'{k2}c', 'd']], 'vmatrix')} = {k1}ad - {k2}bc",
            "Scaling entries scales the terms of the determinant:",
        )
    if style % 8 == 3:
        a, b = rng.choice(KONST[v]), rng.choice(NUMS[v])
        return (
            f"{tex_matrix([[f'x - {a}', b], [1, f'x + {a}']], 'vmatrix')} = 0",
            "The characteristic condition below is a quadratic equation in x:",
        )
    if style % 8 == 4:
        m = _entries(rng, v, 2, 2)
        r = [rng.randint(1, 6), rng.randint(1, 6)]
        mx = [[r[0], m[0][1]], [r[1], m[1][1]]]
        my = [[m[0][0], r[0]], [m[1][0], r[1]]]
        return (
            f"x = \\frac{{{tex_matrix(mx, 'vmatrix')}}}{{{tex_matrix(m, 'vmatrix')}}}, \\quad "
            f"y = \\frac{{{tex_matrix(my, 'vmatrix')}}}{{{tex_matrix(m, 'vmatrix')}}}",
            "Cramer's rule writes each unknown as a ratio of determinants:",
        )
    if style % 8 == 5:
        m = _entries(rng, v, 2, 2)
        return (
            f"\\left| {tex_array(m, 'cc')} \\right| = {det2(m[0][0], m[0][1], m[1][0], m[1][1])}",
            "Vertical bars around an array also denote the determinant:",
        )
    if style % 8 == 6:
        m = _entries(rng, v, 3, 3)
        m[1] = [0, rng.choice(KONST[v]), 0]
        return (
            f"\\Delta = {tex_matrix(m, 'vmatrix')} = {det3(m)}",
            "A row with a single non-zero entry makes the expansion short:",
        )
    m = _entries(rng, v, 3, 3)
    m[2] = [0, 0, rng.choice(NUMS[v])]
    m[1][0] = 0
    return (
        f"\\det C = {tex_matrix(m, 'vmatrix')} = {m[0][0] * m[1][1] * m[2][2]}",
        "The determinant of a triangular matrix is the product of its diagonal:",
    )


def matrix_latex(rng, v, style: int) -> tuple[str, str]:
    k1, k2 = rng.choice(NUMS[v]), rng.choice(NUMS[v])
    s = style % 9
    if s == 0:
        m = _entries(rng, v, 2, 3)
        t = [list(r) for r in zip(*m, strict=True)]
        return (
            f"A = {tex_matrix(m, 'bmatrix')}, \\qquad A^{{T}} = {tex_matrix(t, 'bmatrix')}",
            "Transposing a matrix swaps its rows and columns:",
        )
    if s == 1:
        m = _entries(rng, v, 2, 2)
        x = [rng.randint(-2, 4), rng.randint(-2, 4)]
        r = [m[0][0] * x[0] + m[0][1] * x[1], m[1][0] * x[0] + m[1][1] * x[1]]
        return (
            f"{tex_matrix(m, 'pmatrix')}{tex_matrix([[x[0]], [x[1]]], 'pmatrix')} = "
            f"{tex_matrix([[r[0]], [r[1]]], 'pmatrix')}",
            "A matrix times a column vector gives another column vector:",
        )
    if s == 2:
        m = _entries(rng, v, 2, 2)
        d = det2(m[0][0], m[0][1], m[1][0], m[1][1]) or 1
        inv = [[m[1][1], -m[0][1]], [-m[1][0], m[0][0]]]
        return (
            f"A^{{-1}} = \\frac{{1}}{{{d}}} {tex_matrix(inv, 'bmatrix')}",
            "The inverse of a two by two matrix uses the determinant and swaps entries:",
        )
    if s == 3:
        m = _entries(rng, v, 2, 2)
        r = [rng.randint(1, 20), rng.randint(1, 20)]
        aug = [[m[0][0], m[0][1], r[0]], [m[1][0], m[1][1], r[1]]]
        return (
            f"\\left[ {tex_array(aug, 'cc|c')} \\right]",
            "The augmented matrix of a system keeps the right-hand side beside the coefficients:",
        )
    if s == 4:
        m = _entries(rng, v, 3, 3)
        r = [rng.randint(1, 20) for _ in range(3)]
        aug = [[*row, r[i]] for i, row in enumerate(m)]
        return (
            f"\\left[ {tex_array(aug, 'ccc|c')} \\right]",
            "A three by three system gives an augmented matrix with one extra column:",
        )
    if s == 5:
        a, b = rng.randint(-3, 5), rng.randint(-3, 5)
        m = _entries(rng, v, 2, 2)
        r = [m[0][0] * a + m[0][1] * b, m[1][0] * a + m[1][1] * b]
        before = [[m[0][0], m[0][1], r[0]], [m[1][0], m[1][1], r[1]]]
        after = [[1, 0, a], [0, 1, b]]
        return (
            f"\\left[ {tex_array(before, 'cc|c')} \\right] \\longrightarrow "
            f"\\left[ {tex_array(after, 'cc|c')} \\right]",
            "Row operations turn the left block into the identity and read off the solution:",
        )
    if s == 6:
        m1, m2 = _entries(rng, v, 2, 2), _entries(rng, v, 2, 2)
        r = [[m1[i][j] * k1 + m2[i][j] * k2 for j in range(2)] for i in range(2)]
        return (
            f"{k1}{tex_matrix(m1, 'bmatrix')} + {k2}{tex_matrix(m2, 'bmatrix')} = "
            f"{tex_matrix(r, 'bmatrix')}",
            "Scalar multiples of matrices are added entry by entry:",
        )
    if s == 7:
        x = [rng.choice(KONST[v]), -rng.choice(NUMS[v]), rng.randint(1, 6)]
        return (
            f"\\mathbf{{v}} = {tex_matrix([[c] for c in x], 'pmatrix')}",
            "A vector in three dimensions is written as a column:",
        )
    return (
        f"{k1}I = {tex_matrix([[k1, 0], [0, k1]], 'bmatrix')}",
        "A scalar multiple of the identity matrix has the same number down the diagonal:",
    )


def block_item(latex: str, lead: str, classes: list[str]) -> Item:
    tex = f"{lead}\n\\[\n  {latex}\n\\]"
    return Item(tex, [EqSpec(latex, "block", classes, lead, prose_anchor(lead))])


# ----------------------------------------------------------------- worked derivations (align*)


def derivation_latex(rng, v, style: int) -> tuple[str, str]:
    a, b = rng.choice(NUMS[v]), rng.choice(KONST[v])
    s = style % 8
    if s == 0:
        x = rng.randint(2, 9)
        c = a * x + b
        return (
            f"\\begin{{align*}} {a}x + {b} &= {c} \\\\ {a}x &= {c - b} \\\\ x &= {x} \\end{{align*}}",
            "Isolating the unknown takes three short steps:",
        )
    if s == 1:
        return (
            f"\\begin{{align*}} (x + {a})^2 &= (x + {a})(x + {a}) \\\\ "
            f"&= x^2 + {a}x + {a}x + {a * a} \\\\ &= x^2 + {2 * a}x + {a * a} \\end{{align*}}",
            "Expanding a square by hand shows where the middle term comes from:",
        )
    if s == 2:
        bb = 2 * a
        return (
            f"\\begin{{align*}} x^2 + {bb}x + {b} &= 0 \\\\ (x + {a})^2 &= {a * a - b} \\\\ "
            f"x + {a} &= \\pm\\sqrt{{{a * a - b}}} \\\\ x &= -{a} \\pm \\sqrt{{{a * a - b}}} "
            "\\end{align*}",
            "Completing the square solves the quadratic in four steps:",
        )
    if s == 3:
        return (
            f"\\begin{{align*}} x^2 - {a * a} &= x^2 - {a}^2 \\\\ &= (x - {a})(x + {a}) "
            "\\end{align*}",
            "A difference of squares factors at once:",
        )
    if s == 4:
        return (
            f"\\begin{{align*}} \\frac{{d}}{{dx}}\\left({a}x^3 + {b}x\\right) &= {a} \\cdot 3x^2 + {b} \\\\ "
            f"&= {3 * a}x^2 + {b} \\end{{align*}}",
            "The power rule is applied term by term:",
        )
    if s == 5:
        x, y = rng.randint(1, 6), rng.randint(1, 6)
        return (
            f"\\begin{{align*}} {a}x + y &= {a * x + y} \\\\ x - y &= {x - y} \\\\ "
            f"{a + 1}x &= {(a + 1) * x} \\\\ x &= {x} \\end{{align*}}",
            "Adding the two equations of a system removes y:",
        )
    if s == 6:
        k = rng.randint(2, 6)
        return (
            f"\\begin{{align*}} 2^{{x + {k}}} &= {2 ** (k + 3)} \\\\ 2^{{x + {k}}} &= 2^{{{k + 3}}} \\\\ "
            f"x + {k} &= {k + 3} \\\\ x &= 3 \\end{{align*}}",
            "Matching bases turns an exponential equation into a linear one:",
        )
    n = rng.choice([8, 10, 12])
    return (
        f"\\begin{{align*}} \\sum_{{k=1}}^{{{n}}} ({a}k + {b}) &= {a}\\sum_{{k=1}}^{{{n}}} k + {b} \\cdot {n} \\\\ "
        f"&= {a} \\cdot {n * (n + 1) // 2} + {b * n} \\\\ &= {a * n * (n + 1) // 2 + b * n} \\end{{align*}}",
        "A sum splits into a multiple of a known sum and a constant:",
    )
