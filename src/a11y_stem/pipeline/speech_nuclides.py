"""Names for nuclide notation in the speech reading ("^{14}_{6}C" -> "carbon 14, atomic
number 6"). Pure data and string work; `pipeline.speech` finds the notation in the MathML
(an empty base with a mass-number superscript and an atomic-number subscript, then the
element's letters) and asks this module for the words."""

# ruff: noqa: RUF001

from __future__ import annotations

ELEMENTS = {
    "H": "hydrogen", "He": "helium", "Li": "lithium", "Be": "beryllium", "B": "boron",
    "C": "carbon", "N": "nitrogen", "O": "oxygen", "F": "fluorine", "Ne": "neon",
    "Na": "sodium", "Mg": "magnesium", "Al": "aluminum", "Si": "silicon",
    "P": "phosphorus", "S": "sulfur", "Cl": "chlorine", "Ar": "argon", "K": "potassium",
    "Ca": "calcium", "Sc": "scandium", "Ti": "titanium", "V": "vanadium",
    "Cr": "chromium", "Mn": "manganese", "Fe": "iron", "Co": "cobalt", "Ni": "nickel",
    "Cu": "copper", "Zn": "zinc", "Ga": "gallium", "Ge": "germanium", "As": "arsenic",
    "Se": "selenium", "Br": "bromine", "Kr": "krypton", "Rb": "rubidium",
    "Sr": "strontium", "Y": "yttrium", "Zr": "zirconium", "Nb": "niobium",
    "Mo": "molybdenum", "Tc": "technetium", "Ru": "ruthenium", "Rh": "rhodium",
    "Pd": "palladium", "Ag": "silver", "Cd": "cadmium", "In": "indium", "Sn": "tin",
    "Sb": "antimony", "Te": "tellurium", "I": "iodine", "Xe": "xenon", "Cs": "cesium",
    "Ba": "barium", "La": "lanthanum", "Ce": "cerium", "Pr": "praseodymium",
    "Nd": "neodymium", "Pm": "promethium", "Sm": "samarium", "Eu": "europium",
    "Gd": "gadolinium", "Tb": "terbium", "Dy": "dysprosium", "Ho": "holmium",
    "Er": "erbium", "Tm": "thulium", "Yb": "ytterbium", "Lu": "lutetium", "Hf": "hafnium",
    "Ta": "tantalum", "W": "tungsten", "Re": "rhenium", "Os": "osmium", "Ir": "iridium",
    "Pt": "platinum", "Au": "gold", "Hg": "mercury", "Tl": "thallium", "Pb": "lead",
    "Bi": "bismuth", "Po": "polonium", "At": "astatine", "Rn": "radon", "Fr": "francium",
    "Ra": "radium", "Ac": "actinium", "Th": "thorium", "Pa": "protactinium",
    "U": "uranium", "Np": "neptunium", "Pu": "plutonium", "Am": "americium",
    "Cm": "curium", "Bk": "berkelium", "Cf": "californium", "Es": "einsteinium",
    "Fm": "fermium", "Md": "mendelevium", "No": "nobelium", "Lr": "lawrencium",
}  # fmt: skip

# Particles written with the same mass-number and charge scripts. The electron and the
# positron share the letter e; the sign of the atomic number tells them apart.
PARTICLES = {"n": "neutron", "p": "proton", "α": "alpha particle", "β": "beta particle",
             "γ": "gamma ray"}  # fmt: skip


def nuclide_words(symbol: str, mass: str | None, number: str | None) -> str | None:
    """The reading of a nuclide: `symbol` its letters, `mass` the mass number and `number`
    the atomic number as printed (a signed integer, either may be None). None when the
    letters are not an element or a particle, so the caller reads the markup plainly."""
    if mass is None and number is None:
        return None
    for value in (mass, number):
        if value is not None and not value.lstrip("+-−").isdigit():
            return None
    if symbol in ELEMENTS:
        head = ELEMENTS[symbol] + (f" {mass}" if mass else "")
        return head + (f", atomic number {number}" if number else "")
    if symbol == "e":
        sign = (number or "").replace("−", "-")
        if sign.lstrip("+-") not in {"1", ""}:
            return None
        name = "electron" if sign.startswith("-") or not sign else "positron"
        return _particle(name, mass, number)
    if symbol in PARTICLES:
        return _particle(PARTICLES[symbol], mass, number)
    return None


def _particle(name: str, mass: str | None, number: str | None) -> str:
    out = name
    if mass:
        out += f", mass number {mass}"
    if number:
        spoken = number.replace("-", "minus ").replace("−", "minus ").replace("+", "plus ")
        out += f", atomic number {spoken}"
    return out
