"""Chemistry, nuclide and prose-with-scripts generators.

Chemical equations are built from species lists and balanced by solving the atom and charge
conservation equations, so every equation in the gold is balanced. Nuclear equations are
checked for conservation of mass number and atomic number.
"""

# ruff: noqa: E501  (content tables: long literal lines are the data)

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from math import gcd, log10

SYMBOLS = [
    "H",
    "He",
    "Li",
    "Be",
    "B",
    "C",
    "N",
    "O",
    "F",
    "Ne",
    "Na",
    "Mg",
    "Al",
    "Si",
    "P",
    "S",
    "Cl",
    "Ar",
    "K",
    "Ca",
    "Sc",
    "Ti",
    "V",
    "Cr",
    "Mn",
    "Fe",
    "Co",
    "Ni",
    "Cu",
    "Zn",
    "Ga",
    "Ge",
    "As",
    "Se",
    "Br",
    "Kr",
    "Rb",
    "Sr",
    "Y",
    "Zr",
    "Nb",
    "Mo",
    "Tc",
    "Ru",
    "Rh",
    "Pd",
    "Ag",
    "Cd",
    "In",
    "Sn",
    "Sb",
    "Te",
    "I",
    "Xe",
    "Cs",
    "Ba",
    "La",
    "Ce",
    "Pr",
    "Nd",
    "Pm",
    "Sm",
    "Eu",
    "Gd",
    "Tb",
    "Dy",
    "Ho",
    "Er",
    "Tm",
    "Yb",
    "Lu",
    "Hf",
    "Ta",
    "W",
    "Re",
    "Os",
    "Ir",
    "Pt",
    "Au",
    "Hg",
    "Tl",
    "Pb",
    "Bi",
    "Po",
    "At",
    "Rn",
    "Fr",
    "Ra",
    "Ac",
    "Th",
    "Pa",
    "U",
    "Np",
    "Pu",
    "Am",
    "Cm",
]
Z_OF = {s: i + 1 for i, s in enumerate(SYMBOLS)}


@dataclass
class ChemEq:
    latex: str
    classes: list[str]
    lead: str


# ----------------------------------------------------------------- formulas and balancing


def parse_formula(formula: str) -> Counter:
    if formula == "e":
        return Counter()
    stack = [Counter()]
    i = 0
    while i < len(formula):
        ch = formula[i]
        if ch == "(":
            stack.append(Counter())
            i += 1
        elif ch == ")":
            i += 1
            m = re.match(r"\d+", formula[i:])
            n = int(m.group()) if m else 1
            i += len(m.group()) if m else 0
            top = stack.pop()
            for k, c in top.items():
                stack[-1][k] += c * n
        else:
            m = re.match(r"[A-Z][a-z]?", formula[i:])
            if not m:
                raise ValueError(formula)
            el = m.group()
            i += len(el)
            m2 = re.match(r"\d+", formula[i:])
            n = int(m2.group()) if m2 else 1
            i += len(m2.group()) if m2 else 0
            stack[-1][el] += n
    return stack[0]


Species = tuple[str, int, str | None]  # formula, charge, state


def balance(reactants: list[Species], products: list[Species]) -> list[int]:
    """Smallest positive integer coefficients that conserve every atom and the charge."""
    species = reactants + products
    elements = sorted({e for f, _, _ in species for e in parse_formula(f)})
    rows = []
    for e in elements:
        rows.append(
            [
                Fraction(parse_formula(f)[e] * (1 if k < len(reactants) else -1))
                for k, (f, _, _) in enumerate(species)
            ]
        )
    rows.append(
        [Fraction(c * (1 if k < len(reactants) else -1)) for k, (_, c, _) in enumerate(species)]
    )
    n = len(species)
    # reduced row echelon form
    pivots = []
    r = 0
    for c in range(n):
        piv = next((i for i in range(r, len(rows)) if rows[i][c] != 0), None)
        if piv is None:
            continue
        rows[r], rows[piv] = rows[piv], rows[r]
        rows[r] = [x / rows[r][c] for x in rows[r]]
        for i in range(len(rows)):
            if i != r and rows[i][c] != 0:
                f = rows[i][c]
                rows[i] = [a - f * b for a, b in zip(rows[i], rows[r], strict=True)]
        pivots.append(c)
        r += 1
    free = [c for c in range(n) if c not in pivots]
    if len(free) != 1:
        raise ValueError(f"cannot balance uniquely: {species}")
    f = free[0]
    sol = [Fraction(0)] * n
    sol[f] = Fraction(1)
    for i, c in enumerate(pivots):
        sol[c] = -rows[i][f]
    den = 1
    for x in sol:
        den = den * x.denominator // gcd(den, x.denominator)
    ints = [int(x * den) for x in sol]
    g = 0
    for x in ints:
        g = gcd(g, abs(x))
    ints = [x // g for x in ints]
    if all(x < 0 for x in ints):
        ints = [-x for x in ints]
    if not all(x > 0 for x in ints):
        raise ValueError(f"no positive balance: {species} -> {ints}")
    return ints


def formula_tex(formula: str) -> str:
    """`Ca(OH)2` -> `Ca(OH)_2`, `C6H12O6` -> `C_6H_{12}O_6`."""

    def sub(m: re.Match) -> str:
        d = m.group(2)
        return m.group(1) + ("_" + d if len(d) == 1 else "_{" + d + "}")

    return re.sub(r"([A-Za-z)])(\d+)", sub, formula)


def charge_tex(charge: int) -> str:
    if charge == 0:
        return ""
    mag = abs(charge)
    sign = "+" if charge > 0 else "-"
    return "^" + (sign if mag == 1 else "{" + str(mag) + sign + "}")


def species_tex(sp: Species, coef: int = 1) -> str:
    formula, charge, state = sp
    inner = "e" if formula == "e" else formula_tex(formula)
    body = inner + charge_tex(charge) + (f"({state})" if state else "")
    return (str(coef) + "\\," if coef > 1 else "") + "\\mathrm{" + body + "}"


def ionic_formula(cat: str, cc: int, an: str, ac: int) -> str:
    g = gcd(cc, abs(ac))
    ncat, nan = abs(ac) // g, cc // g
    poly = len(re.findall("[A-Z]", an)) > 1 or bool(re.search(r"\d", an))
    cat_part = cat + (str(ncat) if ncat > 1 else "")
    an_part = (f"({an}){nan}" if poly else f"{an}{nan}") if nan > 1 else an
    return cat_part + an_part


def equation(
    reactants: list[Species], products: list[Species], arrow: str = "\\longrightarrow"
) -> tuple[str, list[int]]:
    coefs = balance(reactants, products)
    left = " + ".join(
        species_tex(s, c) for s, c in zip(reactants, coefs[: len(reactants)], strict=True)
    )
    right = " + ".join(
        species_tex(s, c) for s, c in zip(products, coefs[len(reactants) :], strict=True)
    )
    return f"{left} {arrow} {right}", coefs


def chem_eq(
    reactants: list[Species], products: list[Species], lead: str, arrow="\\longrightarrow"
) -> ChemEq:
    latex, _ = equation(reactants, products, arrow)
    classes = ["chem_equation", "chem_formula"]
    if any(s[1] for s in reactants + products):
        classes.append("chem_charge")
    return ChemEq(latex, classes, lead)


# ----------------------------------------------------------------- reaction pools (per variant)

PRECIPITATION = {
    "dev": [
        ("Ag", 1, "Cl", -1),
        ("Pb", 2, "I", -1),
        ("Ba", 2, "SO4", -2),
        ("Ca", 2, "CO3", -2),
        ("Fe", 3, "OH", -1),
        ("Cu", 2, "S", -2),
        ("Ag", 1, "CrO4", -2),
        ("Mg", 2, "OH", -1),
        ("Ca", 2, "PO4", -3),
    ],
    "check": [
        ("Ag", 1, "Br", -1),
        ("Pb", 2, "SO4", -2),
        ("Ba", 2, "CrO4", -2),
        ("Sr", 2, "CO3", -2),
        ("Cr", 3, "OH", -1),
        ("Cd", 2, "S", -2),
        ("Ag", 1, "I", -1),
        ("Ni", 2, "OH", -1),
        ("Zn", 2, "CO3", -2),
    ],
}
NEUTRALISATION = {  # (anion, anion charge, cation, cation charge, salt state)
    "dev": [
        ("Cl", -1, "Na", 1, "aq"),
        ("SO4", -2, "K", 1, "aq"),
        ("NO3", -1, "Ca", 2, "aq"),
        ("PO4", -3, "Ba", 2, "s"),
        ("Cl", -1, "Al", 3, "aq"),
    ],
    "check": [
        ("Br", -1, "Li", 1, "aq"),
        ("I", -1, "Mg", 2, "aq"),
        ("ClO4", -1, "Sr", 2, "aq"),
        ("SO4", -2, "Mg", 2, "aq"),
        ("CO3", -2, "Li", 1, "aq"),
    ],
}
COMBUSTION = {  # (fuel, state)
    "dev": [
        ("CH4", "g"),
        ("C3H8", "g"),
        ("C5H12", "l"),
        ("C2H4", "g"),
        ("C2H6O", "l"),
        ("C6H12O6", "s"),
    ],
    "check": [
        ("C2H6", "g"),
        ("C4H8", "g"),
        ("C8H18", "l"),
        ("CH4O", "l"),
        ("C3H8O", "l"),
        ("C12H22O11", "s"),
    ],
}
METAL_ACID = {  # (metal, charge, acid anion, anion charge)
    "dev": [("Mg", 2, "Cl", -1), ("Zn", 2, "Cl", -1), ("Al", 3, "SO4", -2), ("Fe", 2, "Cl", -1)],
    "check": [("Zn", 2, "SO4", -2), ("Mg", 2, "SO4", -2), ("Al", 3, "Cl", -1), ("Ni", 2, "Cl", -1)],
}
DISPLACEMENT = {  # (metal, charge, ion metal, ion charge)
    "dev": [
        ("Zn", 2, "Cu", 2),
        ("Mg", 2, "Fe", 2),
        ("Al", 3, "Cu", 2),
        ("Cu", 2, "Ag", 1),
        ("Zn", 2, "Ag", 1),
    ],
    "check": [
        ("Mg", 2, "Cu", 2),
        ("Zn", 2, "Pb", 2),
        ("Fe", 2, "Ag", 1),
        ("Al", 3, "Ni", 2),
        ("Zn", 2, "Ni", 2),
    ],
}
WEAK_ACIDS = {  # (acid formula, acid charge, base formula)
    "dev": [
        ("HF", 0, "F"),
        ("HNO2", 0, "NO2"),
        ("CH3COOH", 0, "CH3COO"),
        ("HCN", 0, "CN"),
        ("HClO", 0, "ClO"),
    ],
    "check": [
        ("HCO3", -1, "CO3"),
        ("HSO4", -1, "SO4"),
        ("H2PO4", -1, "HPO4"),
        ("NH4", 1, "NH3"),
        ("HSO3", -1, "SO3"),
    ],
}
WEAK_BASES = {  # (base formula, base charge, conjugate acid formula)
    "dev": [("NH3", 0, "NH4"), ("CH5N", 0, "CH6N")],
    "check": [("C5H5N", 0, "C5H6N"), ("CO3", -2, "HCO3")],
}
REDOX = {
    "dev": [
        ([("Cu", 2, "aq"), ("e", -1, None)], [("Cu", 0, "s")]),
        ([("Fe", 3, "aq"), ("e", -1, None)], [("Fe", 2, "aq")]),
        ([("MnO4", -1, "aq"), ("H", 1, "aq"), ("e", -1, None)], [("Mn", 2, "aq"), ("H2O", 0, "l")]),
        ([("Zn", 0, "s")], [("Zn", 2, "aq"), ("e", -1, None)]),
        ([("Cl", -1, "aq")], [("Cl2", 0, "g"), ("e", -1, None)]),
        ([("Al", 3, "aq"), ("e", -1, None)], [("Al", 0, "s")]),
        ([("O2", 0, "g"), ("H", 1, "aq"), ("e", -1, None)], [("H2O", 0, "l")]),
        (
            [("Cr2O7", -2, "aq"), ("H", 1, "aq"), ("e", -1, None)],
            [("Cr", 3, "aq"), ("H2O", 0, "l")],
        ),
    ],
    "check": [
        ([("Ag", 1, "aq"), ("e", -1, None)], [("Ag", 0, "s")]),
        ([("Sn", 4, "aq"), ("e", -1, None)], [("Sn", 2, "aq")]),
        ([("I2", 0, "s"), ("e", -1, None)], [("I", -1, "aq")]),
        ([("Fe", 0, "s")], [("Fe", 2, "aq"), ("e", -1, None)]),
        ([("Br2", 0, "l"), ("e", -1, None)], [("Br", -1, "aq")]),
        (
            [("PbO2", 0, "s"), ("H", 1, "aq"), ("SO4", -2, "aq"), ("e", -1, None)],
            [("PbSO4", 0, "s"), ("H2O", 0, "l")],
        ),
        ([("NO3", -1, "aq"), ("H", 1, "aq"), ("e", -1, None)], [("NO", 0, "g"), ("H2O", 0, "l")]),
        ([("H2O2", 0, "aq"), ("H", 1, "aq"), ("e", -1, None)], [("H2O", 0, "l")]),
    ],
}
DECOMPOSITION = {
    "dev": [
        ([("CaCO3", 0, "s")], [("CaO", 0, "s"), ("CO2", 0, "g")], True),
        ([("KClO3", 0, "s")], [("KCl", 0, "s"), ("O2", 0, "g")], True),
        ([("H2O2", 0, "aq")], [("H2O", 0, "l"), ("O2", 0, "g")], False),
        ([("NaHCO3", 0, "s")], [("Na2CO3", 0, "s"), ("H2O", 0, "g"), ("CO2", 0, "g")], True),
    ],
    "check": [
        ([("MgCO3", 0, "s")], [("MgO", 0, "s"), ("CO2", 0, "g")], True),
        ([("Ag2O", 0, "s")], [("Ag", 0, "s"), ("O2", 0, "g")], True),
        ([("HgO", 0, "s")], [("Hg", 0, "l"), ("O2", 0, "g")], True),
        ([("Cu(OH)2", 0, "s")], [("CuO", 0, "s"), ("H2O", 0, "g")], True),
    ],
}
EQUILIBRIA = {
    "dev": [
        ([("N2", 0, "g"), ("H2", 0, "g")], [("NH3", 0, "g")]),
        ([("N2O4", 0, "g")], [("NO2", 0, "g")]),
        ([("SO2", 0, "g"), ("O2", 0, "g")], [("SO3", 0, "g")]),
        ([("H2", 0, "g"), ("I2", 0, "g")], [("HI", 0, "g")]),
    ],
    "check": [
        ([("CO", 0, "g"), ("H2", 0, "g")], [("CH4O", 0, "g")]),
        ([("NO", 0, "g"), ("O2", 0, "g")], [("NO2", 0, "g")]),
        ([("CO", 0, "g"), ("H2O", 0, "g")], [("CO2", 0, "g"), ("H2", 0, "g")]),
        ([("PCl5", 0, "g")], [("PCl3", 0, "g"), ("Cl2", 0, "g")]),
    ],
}

LEADS = {
    "precipitation": {
        "dev": "Mixing two clear solutions can form a solid. The net ionic equations below leave out the spectator ions:",
        "check": "A precipitate appears when the ions meet in solution. Each net ionic equation is balanced for atoms and charge:",
    },
    "neutralisation": {
        "dev": "An acid and a base give a salt and water. The coefficients are chosen so that every atom is conserved:",
        "check": "Neutralisation reactions between acids and hydroxides are balanced in the same way:",
    },
    "combustion": {
        "dev": "Complete combustion of a fuel in oxygen produces carbon dioxide and water:",
        "check": "The burning of the following fuels in a plentiful supply of oxygen is written as:",
    },
    "metal_acid": {
        "dev": "Active metals dissolve in acids and release hydrogen gas:",
        "check": "Hydrogen gas is also released when these metals react with dilute acid:",
    },
    "displacement": {
        "dev": "A more reactive metal displaces a less reactive one from solution:",
        "check": "Displacement reactions move electrons from one metal to the ions of another:",
    },
    "weak": {
        "dev": "Weak acids and bases ionise only partly, which is why equilibrium arrows are used:",
        "check": "Partial ionisation in water is shown with a double arrow, and the charges must balance:",
    },
    "redox": {
        "dev": "Half-equations show the electrons that are gained or lost:",
        "check": "Each half-reaction below is balanced for atoms and for charge:",
    },
    "decomposition": {
        "dev": "Heating can break a compound down into simpler substances:",
        "check": "Some compounds break down when they are heated or catalysed:",
    },
    "equilibria": {
        "dev": "Many gas-phase reactions reach a dynamic equilibrium instead of going to completion:",
        "check": "The following reversible reactions are written with a double arrow:",
    },
}


def chem_equations(v: str) -> list[tuple[str, list[ChemEq]]]:
    """Groups of balanced equations: (group name, equations)."""
    groups: list[tuple[str, list[ChemEq]]] = []

    def grp(name: str, eqs: list[ChemEq]) -> None:
        groups.append((name, eqs))

    lead = {k: x[v] for k, x in LEADS.items()}
    eqs = []
    for cat, cc, an, ac in PRECIPITATION[v]:
        salt = ionic_formula(cat, cc, an, ac)
        eqs.append(
            chem_eq([(cat, cc, "aq"), (an, ac, "aq")], [(salt, 0, "s")], lead["precipitation"])
        )
    grp("precipitation", eqs)

    eqs = []
    for an, ac, cat, cc, state in NEUTRALISATION[v]:
        acid = "H" + (str(-ac) if -ac > 1 else "") + an
        base = ionic_formula(cat, cc, "OH", -1)
        salt = ionic_formula(cat, cc, an, ac)
        eqs.append(
            chem_eq(
                [(acid, 0, "aq"), (base, 0, "aq")],
                [(salt, 0, state), ("H2O", 0, "l")],
                lead["neutralisation"],
            )
        )
    grp("neutralisation", eqs)

    eqs = [
        chem_eq(
            [(fuel, 0, st), ("O2", 0, "g")], [("CO2", 0, "g"), ("H2O", 0, "l")], lead["combustion"]
        )
        for fuel, st in COMBUSTION[v]
    ]
    grp("combustion", eqs)

    eqs = []
    for metal, mc, an, ac in METAL_ACID[v]:
        acid = "H" + (str(-ac) if -ac > 1 else "") + an
        salt = ionic_formula(metal, mc, an, ac)
        eqs.append(
            chem_eq(
                [(metal, 0, "s"), (acid, 0, "aq")],
                [(salt, 0, "aq"), ("H2", 0, "g")],
                lead["metal_acid"],
            )
        )
    grp("metal_acid", eqs)

    eqs = [
        chem_eq(
            [(m, 0, "s"), (n, nc, "aq")],
            [(m, mc, "aq"), (n, 0, "s")],
            lead["displacement"],
        )
        for m, mc, n, nc in DISPLACEMENT[v]
    ]
    grp("displacement", eqs)

    eqs = []
    for acid, ca, base in WEAK_ACIDS[v]:
        eqs.append(
            chem_eq(
                [(acid, ca, "aq"), ("H2O", 0, "l")],
                [("H3O", 1, "aq"), (base, ca - 1, "aq")],
                lead["weak"],
                "\\rightleftharpoons",
            )
        )
    for base, cb, conj in WEAK_BASES[v]:
        eqs.append(
            chem_eq(
                [(base, cb, "aq"), ("H2O", 0, "l")],
                [(conj, cb + 1, "aq"), ("OH", -1, "aq")],
                lead["weak"],
                "\\rightleftharpoons",
            )
        )
    grp("weak", eqs)

    eqs = [chem_eq(r, p, lead["redox"]) for r, p in REDOX[v]]
    grp("redox", eqs)

    eqs = []
    for r, p, heat in DECOMPOSITION[v]:
        eqs.append(
            chem_eq(
                r,
                p,
                lead["decomposition"],
                "\\xrightarrow{\\Delta}" if heat else "\\longrightarrow",
            )
        )
    grp("decomposition", eqs)

    eqs = [chem_eq(r, p, lead["equilibria"], "\\rightleftharpoons") for r, p in EQUILIBRIA[v]]
    grp("equilibria", eqs)
    return groups


# ----------------------------------------------------------------- formulas and ions in sentences

COMPOUNDS = {
    "dev": [
        ("water", "H2O"),
        ("calcium hydroxide", "Ca(OH)2"),
        ("glucose", "C6H12O6"),
        ("ammonia", "NH3"),
        ("sulfuric acid", "H2SO4"),
        ("ethanol", "C2H6O"),
        ("aluminium sulfate", "Al2(SO4)3"),
        ("sodium bicarbonate", "NaHCO3"),
        ("methane", "CH4"),
        ("iron(III) oxide", "Fe2O3"),
    ],
    "check": [
        ("carbon dioxide", "CO2"),
        ("sodium sulfate", "Na2SO4"),
        ("hydrogen sulfide", "H2S"),
        ("calcium carbonate", "CaCO3"),
        ("ammonium nitrate", "NH4NO3"),
        ("sucrose", "C12H22O11"),
        ("magnesium hydroxide", "Mg(OH)2"),
        ("potassium permanganate", "KMnO4"),
        ("nitric acid", "HNO3"),
        ("propane", "C3H8"),
    ],
}
IONS = {
    "dev": [
        ("sodium", "Na", 1),
        ("chloride", "Cl", -1),
        ("calcium", "Ca", 2),
        ("sulfate", "SO4", -2),
        ("iron(III)", "Fe", 3),
        ("ammonium", "NH4", 1),
        ("hydroxide", "OH", -1),
        ("phosphate", "PO4", -3),
        ("copper(II)", "Cu", 2),
        ("nitrate", "NO3", -1),
    ],
    "check": [
        ("potassium", "K", 1),
        ("magnesium", "Mg", 2),
        ("aluminium", "Al", 3),
        ("carbonate", "CO3", -2),
        ("bromide", "Br", -1),
        ("hydronium", "H3O", 1),
        ("fluoride", "F", -1),
        ("zinc", "Zn", 2),
        ("hydrogen carbonate", "HCO3", -1),
        ("iron(II)", "Fe", 2),
    ],
}


def compound_sentences(v: str) -> list[tuple[str, str]]:
    """(sentence with the formula in ⟦⟧, latex of the formula) for inline chemistry."""
    out = []
    for name, f in COMPOUNDS[v]:
        out.append((f"The formula of {name} is ⟦\\mathrm{{{formula_tex(f)}}}⟧.", f))
    return out


def ion_sentences(v: str) -> list[str]:
    out = []
    for name, f, c in IONS[v]:
        latex = "\\mathrm{" + formula_tex(f) + charge_tex(c) + "}"
        kind = "cation" if c > 0 else "anion"
        out.append(f"The {name} {kind} is written ⟦{latex}⟧ and carries a charge of {abs(c)}.")
    return out


# ----------------------------------------------------------------- nuclides


def nuc(a: int, z: int, sym: str) -> str:
    return f"{{}}^{{{a}}}_{{{z}}}\\mathrm{{{sym}}}"


N = nuc(1, 0, "n")
P = nuc(1, 1, "H")
ALPHA = nuc(4, 2, "He")
BETA = "{}^{0}_{-1}\\mathrm{e}"
POSITRON = "{}^{0}_{1}\\mathrm{e}"
NU = "\\nu_{e}"
ANTINU = "\\bar{\\nu}_{e}"

ALPHA_PARENTS = {
    "dev": [
        ("U", 238),
        ("Ra", 226),
        ("Po", 210),
        ("Pu", 239),
        ("Rn", 222),
        ("Th", 232),
        ("Am", 241),
    ],
    "check": [
        ("U", 235),
        ("Th", 230),
        ("U", 234),
        ("Po", 218),
        ("Cm", 244),
        ("Pu", 238),
        ("Ra", 224),
    ],
}
BETA_PARENTS = {
    "dev": [
        ("C", 14),
        ("H", 3),
        ("Co", 60),
        ("Sr", 90),
        ("I", 131),
        ("Cs", 137),
        ("P", 32),
        ("Pb", 210),
    ],
    "check": [
        ("Na", 24),
        ("S", 35),
        ("Fe", 59),
        ("Tc", 99),
        ("Bi", 214),
        ("Pb", 214),
        ("Au", 198),
        ("Ca", 45),
    ],
}
POSITRON_PARENTS = {
    "dev": [("C", 11), ("N", 13), ("O", 15), ("F", 18), ("Na", 22)],
    "check": [("K", 38), ("Ga", 68), ("Rb", 82), ("I", 124), ("Ne", 19)],
}
CAPTURE_PARENTS = {
    "dev": [("Be", 7), ("Ar", 37), ("Fe", 55), ("Cr", 51)],
    "check": [("Ga", 67), ("Tl", 201), ("V", 49), ("Zn", 65)],
}
GAMMA = {"dev": [("Ni", 60, 28), ("Sr", 87, 38)], "check": [("Ba", 137, 56), ("Pb", 207, 82)]}
OTHER_NUCLEAR = {
    "dev": [
        (
            "Fission of uranium-235 after neutron capture:",
            [(235, 92, "U"), (1, 0, "n")],
            [(141, 56, "Ba"), (92, 36, "Kr"), (1, 0, "n", 3)],
        ),
        ("Deuterium and tritium fuse:", [(2, 1, "H"), (3, 1, "H")], [(4, 2, "He"), (1, 0, "n")]),
        (
            "Nitrogen bombarded with alpha particles gives oxygen:",
            [(14, 7, "N"), (4, 2, "He")],
            [(17, 8, "O"), (1, 1, "H")],
        ),
        (
            "The first artificial radioactive isotope was made from aluminium:",
            [(27, 13, "Al"), (4, 2, "He")],
            [(30, 15, "P"), (1, 0, "n")],
        ),
    ],
    "check": [
        (
            "Another fission channel of uranium-235:",
            [(235, 92, "U"), (1, 0, "n")],
            [(90, 38, "Sr"), (143, 54, "Xe"), (1, 0, "n", 3)],
        ),
        (
            "Two deuterium nuclei can also fuse:",
            [(2, 1, "H"), (2, 1, "H")],
            [(3, 2, "He"), (1, 0, "n")],
        ),
        (
            "Beryllium bombarded with alpha particles releases neutrons:",
            [(9, 4, "Be"), (4, 2, "He")],
            [(12, 6, "C"), (1, 0, "n")],
        ),
        (
            "Cosmic-ray neutrons make carbon-14 in the atmosphere:",
            [(14, 7, "N"), (1, 0, "n")],
            [(14, 6, "C"), (1, 1, "H")],
        ),
    ],
}


def _terms(side: list[tuple]) -> tuple[str, int, int]:
    parts, a_sum, z_sum = [], 0, 0
    for t in side:
        a, z, sym = t[0], t[1], t[2]
        k = t[3] if len(t) > 3 else 1
        a_sum += a * k
        z_sum += z * k
        parts.append((str(k) + "\\," if k > 1 else "") + nuc(a, z, sym))
    return " + ".join(parts), a_sum, z_sum


def nuclear_equations(v: str) -> list[tuple[str, str]]:
    """(lead sentence, latex) of decay and reaction equations, conservation checked."""
    out = []

    def lead(kind: str, a: int, sym: str) -> str:
        return {
            "alpha": f"Alpha decay of {sym}-{a}:",
            "beta": f"Beta decay of {sym}-{a}:",
            "positron": f"Positron emission by {sym}-{a}:",
            "capture": f"Electron capture in {sym}-{a}:",
        }[kind]

    for sym, a in ALPHA_PARENTS[v]:
        z = Z_OF[sym]
        d = SYMBOLS[z - 3]
        out.append(
            (
                lead("alpha", a, sym),
                f"{nuc(a, z, sym)} \\longrightarrow {nuc(a - 4, z - 2, d)} + {ALPHA}",
            )
        )
    for sym, a in BETA_PARENTS[v]:
        z = Z_OF[sym]
        d = SYMBOLS[z]
        out.append(
            (
                lead("beta", a, sym),
                f"{nuc(a, z, sym)} \\longrightarrow {nuc(a, z + 1, d)} + {BETA} + {ANTINU}",
            )
        )
    for sym, a in POSITRON_PARENTS[v]:
        z = Z_OF[sym]
        d = SYMBOLS[z - 2]
        out.append(
            (
                lead("positron", a, sym),
                f"{nuc(a, z, sym)} \\longrightarrow {nuc(a, z - 1, d)} + {POSITRON} + {NU}",
            )
        )
    for sym, a in CAPTURE_PARENTS[v]:
        z = Z_OF[sym]
        d = SYMBOLS[z - 2]
        out.append(
            (
                lead("capture", a, sym),
                f"{nuc(a, z, sym)} + {BETA} \\longrightarrow {nuc(a, z - 1, d)} + {NU}",
            )
        )
    for sym, a, z in GAMMA[v]:
        out.append(
            (
                f"Gamma emission from an excited {sym}-{a} nucleus:",
                f"{nuc(a, z, sym)}^{{*}} \\longrightarrow {nuc(a, z, sym)} + \\gamma",
            )
        )
    for text, left, right in OTHER_NUCLEAR[v]:
        lt, la, lz = _terms(left)
        rt, ra, rz = _terms(right)
        if (la, lz) != (ra, rz):
            raise ValueError(f"nuclear equation not conserved: {text}")
        out.append((text, f"{lt} \\longrightarrow {rt}"))
    return out


def nuclide_sentences(v: str) -> list[str]:
    """Inline nuclide sentences with the nuclide in ⟦⟧."""
    picks = {
        "dev": [("C", 14), ("U", 238), ("Co", 60), ("I", 131), ("Fe", 56), ("O", 16)],
        "check": [("N", 15), ("Th", 232), ("Sr", 88), ("Cu", 63), ("Ca", 40), ("Pb", 208)],
    }[v]
    out = []
    for sym, a in picks:
        z = Z_OF[sym]
        out.append(
            f"The nuclide ⟦{nuc(a, z, sym)}⟧ has {z} proton{'s' if z != 1 else ''} and {a - z} neutron{'s' if a - z != 1 else ''}."
        )
    return out


# ----------------------------------------------------------------- prose with scripts

MATERIALS = {
    "dev": [
        ("aluminium", 2.70),
        ("iron", 7.87),
        ("copper", 8.96),
        ("ice", 0.92),
        ("granite", 2.75),
        ("brass", 8.50),
    ],
    "check": [
        ("lead", 11.34),
        ("zinc", 7.14),
        ("nickel", 8.91),
        ("ethanol", 0.79),
        ("silver", 10.49),
        ("glass", 2.50),
    ],
}
SPECIFIC_HEAT = {
    "dev": [("water", 4.18), ("aluminium", 0.90), ("copper", 0.39)],
    "check": [("ethanol", 2.44), ("iron", 0.45), ("lead", 0.13)],
}
SOLUTES = {
    "dev": ["sodium chloride", "glucose", "hydrochloric acid", "potassium nitrate"],
    "check": ["sucrose", "sodium hydroxide", "magnesium sulfate", "acetic acid"],
}


def cf(formula: str) -> str:
    """Formula markup: `Ca(OH)2` -> `Ca(OH)_{2|c}`."""
    return re.sub(r"([A-Za-z)])(\d+)", lambda m: f"{m.group(1)}_{{{m.group(2)}|c}}", formula)


def ion_markup(formula: str, charge: int) -> str:
    mag = abs(charge)
    sign = "+" if charge > 0 else "-"
    return cf(formula) + "^{" + ("" if mag == 1 else str(mag)) + sign + "|c}"


def unit_sentences(rng, v: str, count: int) -> list[str]:
    mats = MATERIALS[v]
    out = []
    makers = []

    def u1():
        name, d = rng.choice(mats)
        vol = rng.choice([12, 25, 40, 60, 85])
        return (
            f"The density of {name} is {d:.2f} g/cm^{{3|u}}, so a block of volume {vol} cm^{{3|u}} "
            f"has a mass of {d * vol:.0f} g."
        )

    def u2():
        a, t = rng.choice([1.5, 2.0, 2.5, 3.0, 3.5, 4.5]), rng.choice([4, 5, 6, 8, 10])
        return (
            f"A cart accelerates at {a} m/s^{{2|u}} for {t} s and reaches a speed of {a * t:g} m/s."
        )

    def u3():
        p = rng.choice([1.0, 1.1, 1.2, 1.3, 2.0, 2.5])
        return (
            f"At sea level the density of air is about 1.2 kg m^{{-3|u}}, and a barometer reading "
            f"corresponds to a pressure of {p} times 10^{{5|o}} N/m^{{2|u}}."
        )

    def u4():
        a, t = rng.choice([0.5, 1.2, 2.0, 3.5]), rng.choice([2, 4, 5, 10])
        return (
            f"The plate has an area of {a} m^{{2|u}} and a thickness of {t} cm, so its volume is "
            f"{a * t / 100:g} m^{{3|u}}."
        )

    def u5():
        c, s = rng.choice([0.10, 0.25, 0.50, 1.0, 2.0]), rng.choice(SOLUTES[v])
        return (
            f"The solution contains {c} mol dm^{{-3|u}} of {s}, so 250 cm^{{3|u}} of it holds "
            f"{c / 4:g} mol."
        )

    def u6():
        i = rng.choice([340, 480, 610, 780, 950, 1020])
        return (
            f"The intensity of sunlight at the ground is roughly {i} W m^{{-2|u}} on a clear day."
        )

    def u7():
        name, c = rng.choice(SPECIFIC_HEAT[v])
        return f"The specific heat capacity of {name} is {c} J g^{{-1|u}} K^{{-1|u}}."

    def u8():
        vol, a = rng.choice([2.5, 6.0, 11.0, 30.0]), rng.choice([12, 40, 85, 150])
        return f"The reservoir holds {vol} km^{{3|u}} of water spread over {a} km^{{2|u}}."

    def u9():
        f, a = rng.choice([400, 900, 1500, 2500]), rng.choice([2.0, 5.0, 8.0, 10.0])
        return (
            f"A steel wire with a cross-section of {a} mm^{{2|u}} carries a load of {f} N, "
            f"which is a stress of {f / a:g} N/mm^{{2|u}}."
        )

    def u10():
        q = rng.choice([0.4, 0.8, 1.5, 2.2])
        return f"The pump delivers {q} m^{{3|u}}/s, which is {q * 1000:g} dm^{{3|u}} every second."

    def u11():
        name, _ = rng.choice(mats)
        x = rng.choice([0.5, 1.2, 4.0, 9.0])
        return f"The measured viscosity of the {name} melt is {x} kg m^{{-1|u}} s^{{-1|u}}."

    def u12():
        g = rng.choice([9.78, 9.80, 9.81, 9.83])
        return f"Near the surface of the earth, free fall has an acceleration of {g} m s^{{-2|u}}."

    makers = [u1, u2, u3, u4, u5, u6, u7, u8, u9, u10, u11, u12]
    order = []
    while len(out) < count:
        if not order:
            order = list(range(len(makers)))
            rng.shuffle(order)
        out.append(makers[order.pop()]())
    return out


def chem_sentences(rng, v: str, count: int) -> list[str]:
    comps = COMPOUNDS[v]
    ions = IONS[v]
    cations = [(n, f, c) for n, f, c in ions if c > 0]
    anions = [(n, f, c) for n, f, c in ions if c < 0]
    makers = []

    def c1():
        n, f = rng.choice(comps)
        return f"{n[0].upper() + n[1:]} has the formula {cf(f)} and is stored in a sealed bottle."

    def c2():
        (n1, f1), (n2, f2) = rng.sample(comps, 2)
        return (
            f"{n1[0].upper() + n1[1:]}, {cf(f1)}, is made of molecules or ions that differ from those of "
            f"{n2}, {cf(f2)}."
        )

    def c3():
        (n, f, c), (n2, f2, c2_) = rng.choice(cations), rng.choice(anions)
        return (
            f"When the salt of {n} and {n2} dissolves in water it releases {ion_markup(f, c)} and "
            f"{ion_markup(f2, c2_)} ions."
        )

    def c4():
        n, f, c = rng.choice(cations)
        return (
            f"The {n} ion, {ion_markup(f, c)}, is formed when the neutral species loses "
            f"{'one electron' if c == 1 else str(c) + ' electrons'}."
        )

    def c5():
        n, f, c = rng.choice(anions)
        return (
            f"A bottle labelled {n} holds the ion {ion_markup(f, c)}, which is surrounded by water "
            "molecules."
        )

    def c6():
        (_, f1), (_, f2), (_, f3) = rng.sample(comps, 3)
        return (
            f"In the reaction {cf(f1)} reacts with {cf(f2)} to give {cf(f3)}, the mass of the "
            "products equals the mass of the reactants."
        )

    def c7():
        n, f = rng.choice(comps)
        return f"One mole of {n}, {cf(f)}, contains 6.02 times 10^{{23|o}} formula units."

    def c8():
        n, f = rng.choice(comps)
        _, f2, c = rng.choice(cations)
        return (
            f"A solution of {n} ({cf(f)}) was mixed with a solution containing {ion_markup(f2, c)} "
            "ions."
        )

    makers = [c1, c2, c3, c4, c5, c6, c8]
    out, order = [], []
    while len(out) < count:
        if not order:
            order = list(range(len(makers)))
            rng.shuffle(order)
        out.append(makers[order.pop()]())
    return out


HALF_LIVES = {
    "dev": [
        ("C", 14, "beta emission", "5730 years"),
        ("I", 131, "beta emission", "8.0 days"),
        ("Co", 60, "beta emission", "5.27 years"),
        ("U", 238, "alpha emission", "4.5 billion years"),
        ("Ra", 226, "alpha emission", "1600 years"),
        ("Po", 210, "alpha emission", "138 days"),
        ("H", 3, "beta emission", "12.3 years"),
        ("Sr", 90, "beta emission", "28.8 years"),
        ("Cs", 137, "beta emission", "30.2 years"),
        ("P", 32, "beta emission", "14.3 days"),
        ("Rn", 222, "alpha emission", "3.8 days"),
        ("Am", 241, "alpha emission", "432 years"),
    ],
    "check": [
        ("Na", 24, "beta emission", "15 hours"),
        ("S", 35, "beta emission", "87 days"),
        ("Fe", 59, "beta emission", "44.5 days"),
        ("U", 235, "alpha emission", "704 million years"),
        ("Po", 218, "alpha emission", "3.1 minutes"),
        ("Cm", 244, "alpha emission", "18.1 years"),
        ("Pu", 238, "alpha emission", "87.7 years"),
        ("Ra", 224, "alpha emission", "3.6 days"),
        ("Ca", 45, "beta emission", "163 days"),
        ("Y", 90, "beta emission", "64 hours"),
        ("F", 18, "positron emission", "110 minutes"),
        ("Ga", 67, "electron capture", "3.3 days"),
    ],
}
ELEMENT_NAMES = {
    "C": "carbon", "I": "iodine", "Co": "cobalt", "U": "uranium", "Ra": "radium", "Po": "polonium",
    "H": "hydrogen", "Sr": "strontium", "Cs": "caesium", "P": "phosphorus", "Rn": "radon",
    "Am": "americium", "Na": "sodium", "S": "sulfur", "Fe": "iron", "Cm": "curium", "Pu": "plutonium",
    "Ca": "calcium", "Y": "yttrium", "F": "fluorine", "Ga": "gallium",
}  # fmt: skip


def nuclide_prose(rng, v: str, count: int) -> list[str]:
    rows = list(HALF_LIVES[v])
    rng.shuffle(rows)
    out = []
    for k in range(count):
        sym, a, mode, t = rows[k % len(rows)]
        z = Z_OF[sym]
        name = ELEMENT_NAMES[sym]
        t_ = k % 4
        if t_ == 0:
            out.append(
                f"The nucleus ^{{{a}|n}}{sym} decays by {mode}, and its half-life *t*_{{1/2|o}} is {t}."
            )
        elif t_ == 1:
            out.append(
                f"{name[0].upper() + name[1:]}-{a}, written ^{{{a}|n}}{sym}, contains {z} proton{'s' if z != 1 else ''} and {a - z} neutron{'s' if a - z != 1 else ''}."
            )
        elif t_ == 2:
            other = a + rng.choice([1, 2, -1, -2])
            out.append(
                f"The isotopes ^{{{a}|n}}{sym} and ^{{{other}|n}}{sym} of {name} have the same "
                "chemical behaviour but different masses."
            )
        else:
            out.append(
                f"A sample of ^{{{a}|n}}{sym} loses half of its atoms every {t}, so the activity "
                "falls in a regular pattern."
            )
    return out


def other_sentences(rng, v: str, count: int) -> list[str]:
    base = {"dev": [2, 3, 5, 7, 9], "check": [4, 6, 8, 10, 12]}[v]
    makers = []

    def o1():
        a, b = rng.sample(base, 2)
        return f"The roots of the quadratic are *x*_{{1|o}} = {a} and *x*_{{2|o}} = {b}."

    def o2():
        d, a1 = rng.choice(base), rng.choice(base)
        return (
            f"Each term satisfies *a*_{{n+1|o}} = *a*_{{n|o}} + {d}, so with *a*_{{1|o}} = {a1} the "
            f"5^{{th|o}} term is {a1 + 4 * d}."
        )

    def o3():
        k = rng.choice(base) + 10
        return f"A tolerance of 10^{{-3|o}} is enough for the {k}^{{th|o}} iteration of the method."

    def o4():
        m = rng.choice(base) / 2
        return f"The mass of the sample is {m:g} times 10^{{-3|o}} kg."

    def o5():
        a, b = rng.choice(base), rng.choice(base)
        return (
            f"The initial velocity *v*_{{0|o}} is {a} m/s and the final velocity *v*_{{f|o}} is "
            f"{a + b} m/s."
        )

    def o6():
        n = rng.choice([200, 450, 3000, 70000])
        return f"Taking logarithms to base ten, log_{{10|o}} {n} is about {log10(n):.2f}."

    def o7():
        return "The square of the standard deviation, *s*^{2|o}, is called the variance."

    def o8():
        r = rng.choice(["0.91", "0.84", "0.97", "0.78"])
        return f"The coefficient of determination *R*^{{2|o}} equals {r} for this fit."

    def o9():
        a, b, c = rng.sample(base, 3)
        return f"The 1^{{st|o}}, 2^{{nd|o}} and 3^{{rd|o}} terms are {a}, {b} and {c}."

    def o10():
        e = rng.choice(base) * 3
        return f"The kinetic energy *E*_{{k|o}} of the cart is {e} J at the bottom of the ramp."

    def o11():
        t = rng.choice(base) + 1
        return f"The half-life *t*_{{1/2|o}} of the sample is {t} hours."

    def o12():
        return "The null hypothesis *H*_{0|o} is rejected when the p-value is below 0.05."

    def o13():
        return (
            "The point (*x*_{1|o}, *y*_{1|o}) lies on the line, and so does (*x*_{2|o}, *y*_{2|o})."
        )

    def o14():
        return "Expanding (*a* + *b*)^{2|o} gives *a*^{2|o} + 2*ab* + *b*^{2|o}."

    makers = [o1, o2, o3, o4, o5, o6, o7, o8, o9, o10, o11, o12, o13, o14]
    out, order = [], []
    while len(out) < count:
        if not order:
            order = list(range(len(makers)))
            rng.shuffle(order)
        out.append(makers[order.pop()]())
    return out


def group_paragraphs(sentences: list[str], per: int) -> list[str]:
    """Join sentences into paragraphs of about `per` sentences."""
    return [" ".join(sentences[i : i + per]) for i in range(0, len(sentences), per)]
