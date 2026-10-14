"""Unit names for the speech reading of a measurement ("5 m/s" -> "5 meters per second").

Pure string work, no MathML: `pipeline.speech` flattens the tokens that follow a number
into one string ("kg*m/s^2") and asks `unit_words` to read it. Reading is conservative: a
string is a unit only when every atom is a known unit symbol (optionally with one SI
prefix) and the division is unambiguous; anything else returns None and the caller reads
the tokens letter by letter as before. `pipeline.speech` only offers a string that follows
a number and is upright (`mathvariant="normal"` or text), so an italic variable `m` is
never read as meters.
"""

# ruff: noqa: RUF001

from __future__ import annotations

import re

# Symbol -> singular name. Case matters (N newton, n nano; M mega, m milli).
UNITS = {
    "m": "meter", "s": "second", "g": "gram", "N": "newton", "J": "joule", "W": "watt",
    "Pa": "pascal", "Hz": "hertz", "V": "volt", "A": "ampere", "C": "coulomb",
    "K": "kelvin", "mol": "mole", "L": "liter", "cd": "candela", "h": "hour",
    "min": "minute", "eV": "electron volt", "T": "tesla", "F": "farad", "H": "henry",
    "Wb": "weber", "S": "siemens", "Ω": "ohm", "Bq": "becquerel", "Gy": "gray",
    "Sv": "sievert", "lx": "lux", "rad": "radian", "u": "atomic mass unit",
    "atm": "atmosphere", "cal": "calorie", "dB": "decibel", "lb": "pound",
    "ft": "foot", "AU": "astronomical unit", "°C": "degree Celsius",
    "°F": "degree Fahrenheit",
}  # fmt: skip
PLURAL_SAME = {"hertz", "siemens", "lux", "kelvin"}
PLURAL_OF = {"foot": "feet"}
# Units that take an SI prefix (kg, mm, MHz, mA); "min", "h", "cd", "atm" and the like do not.
PREFIXABLE = {
    "m", "s", "g", "N", "J", "W", "Pa", "Hz", "V", "A", "C", "K", "mol", "L", "T", "F",
    "H", "Wb", "S", "Ω", "eV", "Bq", "Gy", "Sv", "lx", "cal", "dB",
}  # fmt: skip
PREFIXES = {
    "k": "kilo", "M": "mega", "G": "giga", "T": "tera", "m": "milli", "μ": "micro",
    "µ": "micro", "n": "nano", "p": "pico", "c": "centi", "f": "femto",
}  # fmt: skip

_TOKEN = re.compile(r"°[CF]|[A-Za-zμµΩ]+|\d+|[*/()^-]|\s+")


def _atom_name(symbol: str) -> str | None:
    if symbol in UNITS:
        return UNITS[symbol]
    prefix, rest = symbol[:1], symbol[1:]
    if prefix in PREFIXES and rest in PREFIXABLE:
        return PREFIXES[prefix] + UNITS[rest]
    return None


def _tokens(text: str) -> list[str] | None:
    text = text.replace("·", "*").replace("⋅", "*").replace("−", "-").replace("∕", "/")
    tokens: list[str] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None:
            return None
        pos = match.end()
        tokens.append("*" if match.group().isspace() else match.group())
    return tokens


class _Parser:
    """term := factor ["^" ["-"] int]; product := term (("*" | "/") term)*. A "/" divides
    by the next term only, and anything after it (`J/kg K`) is ambiguous, so the whole
    string is then not read as a unit."""

    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def parse(self) -> list[tuple[str, int]] | None:
        parts = self._product()
        return parts if parts is not None and self.pos == len(self.tokens) else None

    def _product(self) -> list[tuple[str, int]] | None:
        term = self._term()
        if term is None:
            return None
        parts = list(term)
        divided = False
        while (tok := self.peek()) not in {None, ")"}:
            if tok not in {"*", "/"} or divided:
                return None  # a term after a divisor (`J/kg K`, `a/b/c`) is ambiguous
            self.pos += 1
            term = self._term()
            if term is None:
                return None
            if tok == "/":
                divided = True
                term = [(name, -exp) for name, exp in term]
            parts += term
        return parts

    def _term(self) -> list[tuple[str, int]] | None:
        tok = self.peek()
        if tok is None:
            return None
        if tok == "(":
            self.pos += 1
            inner = self._product()
            if inner is None or self.peek() != ")":
                return None
            self.pos += 1
            factor = inner
        else:
            name = _atom_name(tok)
            if name is None:
                return None
            self.pos += 1
            factor = [(name, 1)]
        power = 1
        if self.peek() == "^":
            self.pos += 1
            negative = self.peek() == "-"
            if negative:
                self.pos += 1
            digits = self.peek()
            if digits is None or not digits.isdigit():
                return None
            self.pos += 1
            power = -int(digits) if negative else int(digits)
        if power == 0:
            return None
        return [(name, exp * power) for name, exp in factor]


def _plural(name: str) -> str:
    head, _, last = name.rpartition(" ")
    if last in PLURAL_SAME:
        return name
    if last in PLURAL_OF:
        return f"{head} {PLURAL_OF[last]}".strip()
    if name.startswith("degree "):
        return "degrees " + name.removeprefix("degree ")
    return name + "s"


def _power(name: str, exp: int) -> str:
    return {1: name, 2: f"{name} squared", 3: f"{name} cubed"}.get(
        exp, f"{name} to the power {exp}"
    )


def unit_words(text: str, count: str | None = None) -> str | None:
    """The reading of a unit string after a number `count` ("1" reads singular), or None
    when `text` is not wholly a unit: "kg*m/s^2" -> "kilogram meters per second squared"."""
    tokens = _tokens(text.strip())
    if not tokens:
        return None
    parts = _Parser(tokens).parse()
    if not parts:
        return None
    merged: dict[str, int] = {}
    for name, exp in parts:
        merged[name] = merged.get(name, 0) + exp
    top = [(n, e) for n, e in merged.items() if e > 0]
    bottom = [(n, -e) for n, e in merged.items() if e < 0]
    plural = (count or "").strip() != "1"
    words: list[str] = []
    for index, (name, exp) in enumerate(top):
        last = index == len(top) - 1
        reading = _plural(name) if plural and last else name
        words.append(_power(reading, exp))
    if bottom:
        words.append("per")
        words += [_power(name, exp) for name, exp in bottom]
    return " ".join(words)
