# Chemistry (2026-10-21)

How formulas, ions, state symbols, reactions and concentrations are found in a PDF, written
as MathML, and read aloud; what it did on the development sets, and what it does not do yet.
Code: `pipeline/chem.py` (detection and `\ce`), `pipeline/layout.py` (`_chem_words`,
`_with_chemistry`, `_reaction_row`), `pipeline/speech_chem.py`, `core/latex_groups.py`,
`pipeline/equation_step.py` (`expand_ce`), `models/prompts/equation.md` ("Chemistry").
Development sets only: the registered held-out sections were not run or tuned on (the recorded
held-out results were read to describe the problem, below).

## The problem

Chemistry was the book where the system scored worst (primary `exact_lenient` 0.041 on the
registered held-out run, the same for the chatbots; `docs/remeasurement-2026-10-21.md`). The
analysis of that run (2026-10-20) found, for a student who listens to the page, five causes
that are about what the output says and not about the answer key:

1. formulas and ions in running text that was printed as plain letters with raised or lowered
   digits (`H3O+`, `Fe3+`, `SO4 2-`) were raised text, not mathematics, and a reaction on a line
   of its own (`Ag+(aq) + Cl-(aq) -> AgCl(s)`) was a paragraph with its arrow cut out as a one
   glyph equation;
2. a subscript after a closing bracket (`[A]_0`, `(SO4)3`) was hung on the bracket alone, so a
   reader said "close bracket sub 0";
3. a charge on a two-letter element symbol (`Na+`) hung on the lowercase letter alone (`N`, `msup(a, +)`);
4. reaction arrows and state symbols were read as symbols ("right arrow", "open parenthesis a q
   close parenthesis");
5. a chatbot (and the model, when asked) writes chemistry as mhchem, `\ce{...}`, which the strict
   converter could not read, so every such answer failed.

## What was built

| Part | Where |
|---|---|
| Detection of formulas, ions, state symbols, reactions in running text and on display lines | `pipeline/chem.py` (pure string rules), `layout._chem_words`, `_chem_runs`, `_with_chemistry`, `_reaction_row` |
| `\ce{...}` to plain LaTeX, a tested subset, the rest refused | `chem.ce_to_latex`, `chem.convert_ce_commands`, `equation_step.expand_ce` |
| The equation prompt asks for `\ce` | `models/prompts/equation.md` and `equation_notation.md`, section "Chemistry" |
| Subscripts and superscripts after `(...)` and `[...]` belong to the group | `core/latex_groups.group_delimited_scripts`, called by `validate.safe_latex_to_mathml` |
| A charge or a count hangs on the whole element symbol; a long arrow is an operator | `chem.group_element_symbols`, `chem.arrows_as_operators`, called by `safe_latex_to_mathml` |
| Speech | `pipeline/speech_chem.py`, hooks in `pipeline/speech.py` |
| Tests | `tests/test_chem_detect.py`, `test_chem_ce.py`, `test_layout_chemistry.py`, `test_speech_chem.py`, `test_steps_equation_chem.py` |

## Detection

A formula is mathematics, so it is marked as an inline equation even in the middle of a
sentence: `CO2 emissions` has an inline equation `CO2`. That is a decision, and it is cheap to
reverse (`layout._with_chemistry`): a reader who hears "C O 2" and moves on loses nothing, and one
who needs to step into the formula can. The rules are in docs/layout.md ("Chemistry in running
text and on display lines"); in short:

- The evidence has to be something only chemistry has: a **lowered count** on an element or a
  bracket (`H2O`, `Al2(SO4)3`), a **raised charge** (`Na+`, `SO4 2-`: digits then a sign, never a
  sign then digits, which is an exponent), or a **state symbol** (`(aq)`, `(s)`, `(l)`, `(g)`).
- Every letter must belong to an element symbol. `cm3`, `m2`, `mol-1`, `Kw`, `Nature1`, `NASA1`,
  `pH` and ordinary words are not formulas.
- **Scripts are required.** Plain letters and digits that the PDF set at one size (`H2O`) cannot
  be told from a word, so they are left alone; a Word document that writes `H2O` without a
  subscript is not helped.
- A reaction is an arrow with species on each side (`→ ⟶ ⇌ ⇄ ↔ ←`, and the `−→` TeX prints); the
  words around the arrow that are species, `+` and coefficients are part of it, and a word that
  is only elements and carries no evidence (`HCl`, `NaOH`) counts inside a reaction and nowhere
  else. A row that is one reaction is a display equation line.
- Brackets around a formula are kept (`[H3O+]`, a concentration); parentheses around the whole of
  it are not (`(NaHCO3)`).

Precision, measured. On the 121 PDFs of `scripts/layout_identity.py` (the repository's fixtures,
the development set, the authored material and the development pages printed into the cache),
96 are identical before and after. The 25 that differ are the chemistry sections and the
authored chemistry document, one lecture slide whose reaction line was a heading and is now an
equation, and two spaces in an algebra "Simplify:" line. No physics, algebra, statistics, slide or
Word text gained a formula that is not one.

## Writing it: `\ce`

The equation prompt asks for mhchem for chemical species (`\ce{H2O}`, `\ce{SO4^2-}`,
`\ce{2 H2 + O2 <=> 2 H2O}`) and for ordinary LaTeX around it. The model's answer is written out
as plain LaTeX before any check (`equation_step.expand_ce`, then `clean_latex`); the LaTeX
allowlist, the converter, the MathML checks and the text-layer cross-check all see the converted
reading. `clean_latex` itself does not convert: the proof applies it to the chatbot arms'
recorded LaTeX, whose numbers must not move.

| Supported | Examples |
|---|---|
| species of element symbols with counts | `H2O`, `C6H12O6`, `CuSO4.5H2O` |
| brackets and parentheses with counts | `Ca(OH)2`, `Al2(SO4)3`, `[Cu(NH3)4]^2+` |
| charges | `Na+`, `OH-`, `SO4^2-`, `Fe^3+`, `Fe^{3+}`, `Fe+++`, `e-` |
| state symbols | `(aq)`, `(s)`, `(l)`, `(g)` |
| coefficients | `2`, `0.5`, `1/2` |
| arrows | `->`, `-->`, `<-`, `<--`, `<->`, `<=>`, with a label `->[\Delta]`, `->[heat]` |
| bonds | `-` (between atoms), `=`, `#` |
| nuclides | `^{14}_{6}C`, `^{0}_{-1}e`, `_{6}^{14}C` |
| particles | `e-`, `n`, `p` |

Not supported, and refused (`CeError`, then the LaTeX check says `unknown_command`, the cheap
model's answer goes to the main model, and a reading that still has it is a failed fix with a
required decision, the picture kept): `<=>>` and `<<=>`, a gas or precipitate mark `^` / `v`,
`$...$` in the body, a concentration inside a state symbol `(aq, 0.10 M)`, a two-part arrow label,
anything that is not a species (words, a generic letter such as `A`). The prompt says what to do
instead: ordinary LaTeX with `\ce` only around the species inside it.

Three repairs at conversion make the MathML what a screen reader needs (docs/output.md,
"Chemistry"): a script after `(...)` or `[...]` is hung on the group, a charge or count after an
element symbol of two letters is hung on the symbol (`msup(mrow(N, a), +)`, not `N`, `msup(a, +)`),
and a long arrow is an operator and not an identifier.

## Reading it aloud

`pipeline/speech_chem.py`; the same strings are on the review screen and in `alttext` when
`MATHML_ALTTEXT` is on. A row of MathML counts as chemistry only with evidence (a lowered count or
a raised charge on an upright element symbol, a state symbol, two element symbols side by side, a
bracket around one species), so a variable that is set upright is not "H". Both shapes of MathML
are read: the converter's and OpenStax's (`mtext` for `HSO` or `2e`, an empty-based charge).

| Page | Spoken |
|---|---|
| H₂O | H 2 O |
| SO₄²⁻ | S O 4 2 minus |
| Fe³⁺ | F e 3 plus |
| Na⁺ + Cl⁻ → NaCl | N a plus, plus C l minus reacts to form N a C l |
| Ag⁺(aq) + Cl⁻(aq) → AgCl(s) | A g plus aqueous, plus C l minus aqueous, reacts to form A g C l solid |
| 2 H₂ + O₂ ⇌ 2 H₂O | 2 H 2 plus O 2 is in equilibrium with 2 H 2 O |
| CaCO₃(s) →(Δ) CaO(s) + CO₂(g) | C a C O 3 solid, reacts, with heat, to form C a O solid, plus C O 2 gas |
| [H₃O⁺] = [OH⁻] | the concentration of H 3 O plus equals the concentration of O H minus |
| [Cu(NH₃)₄]²⁺ | open bracket C u open parenthesis N H 3 close parenthesis 4 close bracket 2 plus |
| ¹⁴₆C | carbon 14, atomic number 6 |

Element symbols are spelled ("N a", not "Na"), counts are numbers with no "sub" (a coefficient and
a count are told apart by the symbol between them, as a teacher says "two H two O"), a charge is
its number and its sign, a `+` after a charge or a state symbol follows a comma so it is not read
as a second charge. Names are not substituted ("sulfate"): a reading must not say more than the
page. A concentration is read only for a bracket around a formula; `[A]` stays "open bracket A
close bracket" because `[3]` and `[x]` are not concentrations.
