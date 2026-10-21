r"""Brackets that carry a script, grouped for the LaTeX converter.

latex2mathml hangs a script on the one symbol before it, so `[A]_0` becomes `[`, `A`, and
`msub(], 0)`, and `Al_2(SO_4)_3` ends in `msub(), 3)`: the subscript belongs to a closing
bracket, not to the group it closes, and a screen reader says "close bracket sub 0". The
group is what the subscript belongs to (`msub(mrow([A]), 0)`, as published MathML has it, and
what `{[A]}_0` converts to), so a `[...]` or `(...)` that is followed by `^` or `_` is
wrapped in braces first. `[Zn^{2+}]`, `[0, 1]^2` and `(a + b)^2` are grouped the same way.
`\left[ ... \right]_0` is already a group and is left alone; so is a bracket with no script
after it, a half-open interval (the brackets do not match), `\sqrt[3]{x}` and a bracket that
is escaped (`\{`, `\[`). Pure string work, no dependencies.
"""

from __future__ import annotations

_OPEN = {"(": ")", "[": "]"}
_CLOSE = {")", "]"}


def group_delimited_scripts(latex: str) -> str:
    """`latex` with each `(...)` and `[...]` followed by `^` or `_` wrapped in braces.
    Text with no such bracket comes back unchanged."""
    if "^" not in latex and "_" not in latex:
        return latex
    inserts: list[tuple[int, str]] = []
    # (character, index, brace depth) of the brackets opened and not yet closed
    stack: list[tuple[str, int, int]] = []
    depth = 0
    i, n = 0, len(latex)
    while i < n:
        c = latex[i]
        if c == "\\":
            j = i + 1
            while j < n and latex[j].isascii() and latex[j].isalpha():
                j += 1
            name = latex[i + 1 : j]
            if j == i + 1:
                i += 2  # an escaped character: \{ \[ \, ...
                continue
            i = j
            if name in ("left", "right"):
                i += 1  # the delimiter that follows belongs to \left / \right
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            stack = [item for item in stack if item[2] <= depth]
        elif c in _OPEN:
            stack.append((c, i, depth))
        elif c in _CLOSE:
            if stack and _OPEN[stack[-1][0]] == c and stack[-1][2] == depth:
                _, start, _ = stack.pop()
                k = i + 1
                while k < n and latex[k] == " ":
                    k += 1
                if k < n and latex[k] in "^_":
                    inserts.append((start, "{"))
                    inserts.append((i + 1, "}"))
            else:
                stack = [item for item in stack if item[2] < depth]  # a mismatched pair
                if stack and _OPEN[stack[-1][0]] != c:
                    stack.clear()
        i += 1
    if not inserts:
        return latex
    out: list[str] = []
    pos = 0
    for at, text in sorted(inserts, key=lambda p: (p[0], p[1] == "{")):
        out.append(latex[pos:at])
        out.append(text)
        pos = at
    out.append(latex[pos:])
    return "".join(out)
