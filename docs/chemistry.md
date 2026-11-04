# Chemistry (2026-10-21)

How formulas, ions, state symbols, reactions and concentrations are found in a PDF, written
as MathML, and read aloud; what it did on the development sets, and what it does not do yet.
Code: `pipeline/chem.py` (detection and `\ce`), `pipeline/layout.py` (`_chem_words`,
`_with_chemistry`, `_reaction_row`), `pipeline/speech_chem.py`, `core/latex_groups.py`,
`pipeline/equation_step.py` (`expand_ce`, `read_latex`), `models/prompts/equation.md` ("Chemistry").
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
| `\ce{...}` to plain LaTeX, a tested subset, the rest refused | `chem.ce_to_latex`, `chem.convert_ce_commands`, `chem.expand_ce`, `equation_step.read_latex` |
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
96 are identical before and after. The 25 that differ are the chemistry material (21 files: the section prints, the authored chemistry document and a fixture), the
Word lecture (it has formulas and ions in its sentences), one
lecture slide whose reaction line was a heading and is now an equation, and a space after
"Simplify:" in the two prints of an algebra section. No physics, algebra, statistics, slide or
Word text gained a formula that is not one.

### Spans: lists, half-reactions, `E°cell` and arrow labels (2026-11-03)

The span errors of the development run (18 gold equations: 4 extra, 13 partial, 1 split; "Outcome
classes" below) had four layout causes, fixed in `pipeline/layout.py` (the chemistry functions) and
the detection half of `pipeline/chem.py`; the rules are in docs/layout.md ("Chemistry in running
text and on display lines").

| Cause | What the layout did | Now |
|---|---|---|
| `CO3 2−, F−` and `(a) A ⇌ B; (b) C ⇌ D` | one inline run, or one display line, for the whole list | one equation per ion; one inline equation per reaction, list marker left out |
| `E°cell`, `E°cathode`, `ΔS°298` | the degree sign sat inside the lowered label in the text layer (`E c°ell`), the inline equation was cut in two (`E c`, `ell`), and two degree signs of one row were inserted as one block (`E c° °athode`) | the label is whole and the degree sign follows it; the sign is part of the inline run after a symbol |
| `anode (oxidation):` set apart from its reaction | the block was cut after the label | the label joins its reaction (12 glyph sizes, labels that name a half-reaction only) |
| a label over an arrow (`\xrightarrow{high temperature}`, one over and one under, a long arrow) | only a label of 12 characters or less, and only with a one-dash arrow, was joined; the label counted in the row's size and made the subscripts of the formulas "text" | any script-size label over or under an arrow is part of the reaction, read after the arrow in words of its own (`chem.Word.over_arrow`); the arrow word may have up to 12 dashes |

What was left: the rule under a half-reaction block (`Overall:` is below a `¯` row; the gold has
one equation, the layout two: 16.3 and 16.4, 5 equations), `x = …` lines cut at the edge and the
cut of `H2O(g) → H2O(l)` (the crop, equations plan T4), the degree sign of `ΔG°` set at full size,
and the equations in the table cells.

Measured, no model call (2026-11-03, branch `chem/layout-spans`; the recording of the
2026-10-26 development run, `--answers` with `--answers-offline`, `FEATURE_TABLES` and
`EQUATION_LINE_CONTEXT` on, the 12 chemistry documents, 443 gold):

- **Identity** (`scripts/layout_identity.py`, 143 PDFs): 135 are identical and 8 differ: the two
  prints each of 12.3, 14.1, 16.3 and 16.4 of OpenStax Chemistry. Nothing that is not chemistry
  changes (a first version split `W1, W2` of a paper into two equations; letters in an italic
  face are variables, not element symbols).
- **Replay**: before the change the recording answers 760 of 812 requests and reproduces the
  recorded classes (291 chemically right against the recording's 293; the 52 unanswered
  requests are 2 equations); after it 690 of 812, because the 90 requests of a changed crop or
  line have no answer. Span errors (extra / partial / split) on what is answered: 18 (4 / 13 / 1) to 9
  (1 / 8 / 0). 24 gold equations moved to "not read" for the missing answer: 10 were span errors
  or "undetected: other", 14 were right before and are the equations whose text hint changed
  (`E c°ell` to `E cell°`). They are read again by the paid re-record (equations plan C10), not
  by this change.
- **At the layout level** (the element's text layer against the gold, no reading needed): of the
  23 gold equations that were a span error or "undetected: other", 6 now have an element with
  the gold's symbols (`E°cell` twice, `ΔS°298`, `CO3 2−`, the two reactions of 14.1) and 4 more
  are closer (the `Anode … Overall` blocks of 16.4: text similarity 0.73 to 0.96, 0.97 to 0.99).

## Writing it: `\ce`

The equation prompt asks for mhchem for chemical species (`\ce{H2O}`, `\ce{SO4^2-}`,
`\ce{2 H2 + O2 <=> 2 H2O}`) and for ordinary LaTeX around it. The model's answer is written out
as plain LaTeX before any check (`equation_step.read_latex`: `expand_ce`, then `clean_latex`); the
LaTeX allowlist, the converter, the MathML checks and the text-layer cross-check all see the
converted reading. `clean_latex` itself does not convert: the proof applies it to the chatbot
arms' recorded LaTeX, whose numbers must not move.

`read_latex` is the one entry on every path that takes LaTeX from a model or a person: the
equation step, the table step (cells), the image-page reader and its second reading
(`image_page`, `image_page_check`), the page-math spans (`page_math`, mathematics only), the
table HTML's words-and-formulas cells, and the review screen's edit box for an equation and for a
table cell (`web.edit_check`). Each stores the converted LaTeX, so an edited `\ce{H2O}` is
saved, shown and exported as `\text{H}_2\text{O}`. A `\ce` outside the subset stays as it is and
fails the LaTeX check with the code `unsupported_ce` (check `latex.allowed_commands`; any other
unknown command is still `unknown_command`). The edit box says "The chemistry in \ce{...} is
outside what this tool can read: write the formula with ordinary LaTeX, or check it for a typo."
and nothing is saved. The proof's arms (`proof/systems.py`) and the gold side of the eval
(`gold/arxiv_gold.py`) do not expand it, on purpose (recorded numbers must not move).

| Supported | Examples |
|---|---|
| species of element symbols with counts | `H2O`, `C6H12O6`, `CuSO4.5H2O` |
| brackets and parentheses with counts | `Ca(OH)2`, `Al2(SO4)3`, `[Cu(NH3)4]^2+` |
| charges | `Na+`, `OH-`, `SO4^2-`, `Fe^3+`, `Fe^{3+}`, `Fe+++`, `e-` |
| state symbols | `(aq)`, `(s)`, `(l)`, `(g)`, with a condition: `(aq, 0.10 M)`, `(g, 1 atm)` (a number, then `M mM m atm bar kPa Pa mmHg torr` or no unit) |
| the excited state, on a nuclide only | `^{60}_{28}Ni^*` (a bare `Ni^*` is refused: speech reads the star only on a nuclide) |
| gas and precipitate marks | `CO2 ^`, `AgCl v` (after a species, set apart by a space; written `\uparrow`, `\downarrow`) |
| coefficients | `2`, `0.5`, `1/2` |
| arrows | `->`, `-->`, `<-`, `<--`, `<->`, `<=>`, with a label `->[\Delta]`, `->[heat]`, or two (`->[above][below]`; text, numbers, `\text{...}`, `^\circ`) |
| bonds | `-` (between atoms), `=`, `#` |
| nuclides | `^{14}_{6}C`, `^{0}_{-1}e`, `_{6}^{14}C` |
| particles | `e-`, `n`, `p` |

Not supported, and refused (`CeError`, then the LaTeX check says `unknown_command`, the cheap
model's answer goes to the main model, and a reading that still has it is a failed fix with a
required decision, the picture kept): `<=>>` and `<<=>`, a mark that does not follow a species
(on a coefficient, an arrow, or two in a row, glued to the formula, or followed by a number), a star on a bare species, `$...$` in the body, a
condition with a unit that is not listed or a second condition, a three-part arrow label,
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
are read: the converter's (one `mi` per element symbol, see "MathML shape" below) and
OpenStax's (`mtext` for `HSO` or `2e`, an empty-based charge). A state is read as `(` `aq` `)` or as
one `mtext (aq)`, a nuclide as `mmultiscripts` or as an empty base with its numbers.

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
| Zn²⁺(aq, 0.10 M) | Z n 2 plus aqueous, 0.10 molar |
| E°(Cu²⁺/Cu) | capital E degrees open parenthesis C u 2 plus slash C u close parenthesis |
| ⁶⁰₂₈Ni* → ⁶⁰₂₈Ni + γ | nickel 60, atomic number 28, excited state reacts to form nickel 60, atomic number 28 plus gamma |
| [Cu(NH₃)₄]²⁺ | open bracket C u open parenthesis N H 3 close parenthesis 4 close bracket 2 plus |
| ¹⁴₆C | carbon 14, atomic number 6 |

Element symbols are spelled ("N a", not "Na"), counts are numbers with no "sub" (a coefficient and
a count are told apart by the symbol between them, as a teacher says "two H two O"), a charge is
its number and its sign, a `+` after a charge or a state symbol follows a comma so it is not read
as a second charge. Names are not substituted ("sulfate"): a reading must not say more than the
page. A concentration is read only for a bracket around a formula; `[A]` stays "open bracket A
close bracket" because `[3]` and `[x]` are not concentrations.

## What it did on the development sets

Twelve documents of dev-v2: the eleven *Chemistry: Atoms First* sections (70 pages, 335 gold
equations) and the authored chemistry document (9 pages, 108 gold equations), 443 gold
equations on 79 PDF pages. Before is commit `c190b3e` (this branch's base), after is this work;
the same models (claude-opus-5-5, claude-haiku-5-5), thresholds and settings, the equation prompt
with its new "Chemistry" section being the one change to what the model is asked. Every model
answer was recorded once and the "after" numbers were scored again from the recording with the
final converter (`--answers`, `--answers-offline`: no model call for a scoring change). Tables
are off in that run (production has them on since 2026-10-21, docs/tables.md). Scored by the repository's scorers (`scripts/dev_buckets.py`) and by
`scripts/dev_chem.py`.

| 12 documents, 443 gold equations | before | after |
|---|---|---|
| primary `exact_lenient`, end to end | 0.093 | 0.108 |
| symbols scorer with credit (secondary) | 0.370 | 0.363 |
| detection recall | 0.634 | 0.720 |
| detection recall, gold class `chem_formula` / `chem_charge` / `chem_equation` / `nuclide` | 60% / 57% / 59% / 84% | 78% / 73% / 71% / 84% |
| detection precision (gold-aligned predictions) | 0.530 | 0.474 |
| predictions the gold has no equation for | 249 | 354 |
| required decisions per PDF page | 1.405 | 1.392 |
| equation flags: precision / recall | 0.585 / 0.205 | 0.590 / 0.146 |
| cost per PDF page | $0.0171 | $0.0169 |
| prose chemistry script runs (gold, 205) read as `<sub>`/`<sup>` / as inline math | 157 / 21 | 3 / 176 |

By the source of the gold, with `content`, the score that does not depend on how the gold spells
a formula (`scripts/dev_chem.py`: the text of the MathML token by token, hyphen, minus and en dash
one thing, every arrow one thing, the element that holds it and the upright style ignored):

| | OpenStax sections, 335 gold | authored document, 108 gold |
|---|---|---|
| | before / after | before / after |
| aligned to a predicted equation | 210 (62.7%) / 211 (63.0%) | 71 (65.7%) / 108 (100%) |
| primary | 31 (9.3%) / 44 (13.1%) | 10 (9.3%) / 4 (3.7%) |
| symbols scorer, with credit | 154 (46.0%) / 151 (45.1%) | 10 (9.3%) / 10 (9.3%) |
| **content right** | 168 (50.1%) / 166 (49.6%) | 63 (58.3%) / **104 (96.3%)** |
| content wrong, and a person is asked | 14 / 16 (of 42 / 45 wrong) | 7 / 3 (of 8 / 4 wrong) |

What the numbers say, and what they do not:

- **The authored document is where the work shows**: its formulas and ions in sentences and its
  reactions on lines of their own were `H<sub>2</sub>O` and a paragraph with an arrow cut out;
  every one of the 108 is now an equation and 104 read right. Its primary and symbols scores do
  not rise, and the primary falls: its gold was made by the converter as it was (one upright
  `mi` per letter, a bracket's subscript on the bracket, an arrow as an `mi`) and the model's
  chemistry is now written the OpenStax way (an `mtext` per run of element symbols, scripts on the
  group, an arrow as an operator; since pipeline 0.5.2 one `mi` per symbol, see "MathML
  shape" below). Both scorers compare the shape of the MathML, so they call the
  better MathML wrong against a gold that has the old shape; `content` compares what is written.
- **On the OpenStax sections the system was already near what these scores can see**: their
  equations are set in a maths font, so layout already found them, and the model already wrote
  `\text{...}` as OpenStax does. Content right is 50.1% before and 49.6% after, within the noise
  of 335 equations. The primary rises by 13 equations, from the arrow being an operator and the
  charge shapes, and 4 equations of a rate law lose it because OpenStax writes `[A]^m` with the
  exponent on the bracket and the grouping fix puts it on the group (the grouping is what a
  reader needs, section "Writing it").
- **What is left on the OpenStax sections is not chemistry in sentences**: of the 335, 102 are
  formulas in the cells of the tables, 92 of them undetected (a table is one element, and with
  tables off a picture), which is 74% of what is undetected. 32 more are lone `×` signs the gold
  counts as equations. That is Part 2 of this work (docs/tables.md, "Tables on").
- **New predictions the gold does not count**: 105 more equations (a formula in a sentence is a
  prose script run in the gold, which `prose_classes` scores: 155 more of its 205 chemistry runs
  are now inline maths). A random sample of 70 of the 125 new unmatched predictions was read by
  hand: all 70 are chemical formulas, ions or concentrations (two have a span a little off:
  `CH3CH2OCOCH3 = 0` takes the relation, `NaHCO3(s) ∆` takes the heat mark). The equation prompt
  and layout do not make it more costly: the formulas are read by the cheap model.
- **Review load is flat** (1.405 to 1.392 required decisions per page) because the cheap model's
  formula reads pass the text-layer checks. Equations wrong in the symbols and nobody asked per
  100 gold rise from 21.0 to 30.5 on the symbols scorer, which is the authored document's 37 new
  aligned equations that the scorer calls wrong for their spelling; on `content` the equations
  that are wrong and not asked are 28 / 1 (OpenStax / authored) before and 29 / 1 after.
- The model answers in `\ce` for 192 of 268 equations of the authored document (72%); 297 of
  301 distinct `\ce` answers of the dev sections convert, the 4 left are a two-part arrow label,
  `HE` (the gold's own typo), a nuclide with no symbol and a cut-off answer: each a failed fix with
  a required decision.

`scripts/dev_chem.py` and the commands to repeat this (they need an API key and about $3 at list
price; the replays are free):

```
uv run readablestem eval pipeline --task document --set dev-v2 --provider anthropic --max-usd 2.5 \
  --answers A.jsonl --sections chemistry-atoms-first latex-chemistry --items after.jsonl --out after.json
uv run python scripts/dev_chem.py --gold data/dev-v2/dev before=before.jsonl after=after.jsonl
uv run python scripts/dev_buckets.py --gold data/dev-v2/dev before=before.jsonl after=after.jsonl
```

Four readings added after listening to what the review screen said for the development
chemistry set (`tests/test_speech_chem_fixes.py`):

- A state with conditions, `(aq, 0.10 M)` or `(g, 1 atm, 298 K)`, reads the state and then each
  condition: "aqueous, 0.10 molar", "gas, 1 atmosphere, 298 kelvin". Only a number with a unit
  is read this way; any other condition leaves the markup read as it was. `M` is "molar" only
  here, inside a state.
- A `/` between two species (`Cu²⁺/Cu`, `Cl₂/Cl⁻`) is a redox couple and reads "slash". A `/`
  after a bracket or a closing parenthesis, or between italic letters, is still "divided by".
- A half-reaction table (species, an arrow in a cell of its own, species) reads each row with
  the arrow between the species ("reacts to form"), not "right arrow" in a cell alone.
- `↑` after a species in a reaction reads "gas given off" and `↓` "precipitate" (elsewhere "up
  arrow", "down arrow").
- An asterisk after a nuclide (`⁶⁰₂₈Ni*`) reads ", excited state". An asterisk after another
  species is not guessed at.
- In a chemistry row, a unit after a decimal number reads as a unit (`0.0592 V`: V is also
  vanadium), and `mol`, `L⁻¹`, `s⁻¹` side by side read as one rate unit ("moles per liter
  second"). A whole number before a symbol stays a coefficient (`2 K`).

## Speech, checked by hand

24 chemistry equations of the development gold (a seeded draw: 8 OpenStax-shaped MathML of every
kind the sections have, and authored ones in the converter's older shape), read by
`mathml_to_speech`. What was found, and fixed, as the list was read: an ion saved with its charge
on the lowercase letter read "capital Z n to the power 2 plus" and `2Ag⁺` read "2 amperes g"
(`speech_chem._split_pair`); `HSO₃⁻` read "H S O 3 superscript minus" (OpenStax puts the charge on
an empty base); a concentration in brackets read "open bracket N O close bracket" next to
"the concentration of C l 2"; `2e⁻` read "2e to the power minus"; the cell-notation bars read
"box drawings light vertical". All are in `tests/test_speech_chem.py`. Left as they are:

- an aligned block of half-reactions reads "row 1: row 1:" (a label row of the table reading,
  not chemistry);
- two-letter symbols are spelled "C u"; the ion names ("sulfate") are never said.

Twenty of the 24, as read (the rest are in the tests):

| Page | Spoken |
|---|---|
| ¹³₂₈Al, ²₄He, ⁷₃Li | aluminum 28, atomic number 13; helium 4, atomic number 2; lithium 7, atomic number 3 |
| H₂SO₄ | H 2 S O 4 |
| Fe₂O₃ | F e 2 O 3 |
| Cu²⁺(aq) + 2e⁻ ⟶ Cu(s) | C u 2 plus aqueous, plus 2 e minus reacts to form C u solid |
| 2H⁺(aq) + 2e⁻ ⟶ H₂(g) | 2 H plus aqueous, plus 2 e minus reacts to form H 2 gas |
| Cl₂(g) + 2e⁻ ⟶ 2Cl⁻(aq) | C l 2 gas, plus 2 e minus reacts to form 2 C l minus aqueous |
| Mg(OH)₂(s) + 2HCl(aq) ⟶ MgCl₂(aq) + 2H₂O(l) | M g open parenthesis O H close parenthesis 2 solid, plus 2 H C l aqueous, reacts to form M g C l 2 aqueous, plus 2 H 2 O liquid |
| N₂(g) + 3H₂(g) ⇌ 2NH₃(g) | N 2 gas, plus 3 H 2 gas, is in equilibrium with 2 N H 3 gas |
| 2H₂O₂(aq) ⟶ 2H₂O(l) + O₂(g) | 2 H 2 O 2 aqueous, reacts to form 2 H 2 O liquid, plus O 2 gas |
| ZnCO₃(s) →(Δ) ZnO(s) + CO₂(g) | Z n C O 3 solid, reacts, with heat, to form Z n O solid, plus C O 2 gas |
| Zn(s) + Cu²⁺(aq) ⟶ Zn²⁺(aq) + Cu(s) | Z n solid, plus C u 2 plus aqueous, reacts to form Z n 2 plus aqueous, plus C u solid |
| Cu(s) + 2Ag⁺(aq) ⟶ Cu²⁺(aq) + 2Ag(s) | C u solid, plus 2 A g plus aqueous, reacts to form C u 2 plus aqueous, plus 2 A g solid |
| [H₃O⁺] = [OH⁻] = 1.0 × 10⁻⁷ M | the concentration of H 3 O plus equals the concentration of O H minus equals … |
| rate = k[OCl⁻]²[I⁻] | rate equals k the concentration of O C l minus squared the concentration of I minus |
| HSO₃⁻(aq) + OH⁻(aq) ⇌ SO₃²⁻(aq) + H₂O(l) | H S O 3 minus aqueous, plus O H minus aqueous, is in equilibrium with S O 3 2 minus aqueous, plus H 2 O liquid |
| Pt(s) \| H₂(g, 1 atm) \| H⁺(aq, 1 M) ‖ Ag⁺(aq, 1 M) \| Ag(s) | P t solid, vertical bar H 2 open parenthesis g comma 1 atm close parenthesis vertical bar H plus open parenthesis a q comma 1 capital M close parenthesis double vertical bar … |
| [Cu(NH₃)₄]²⁺ | open bracket C u open parenthesis N H 3 close parenthesis 4 close bracket 2 plus |
| Na⁺ + Cl⁻ → NaCl | N a plus, plus C l minus reacts to form N a C l |

## What a screen reader says (MathCAT and SRE)

The speech above is ours. A student's screen reader reads the MathML in the page: NVDA 2026.1
and JAWS with MathCAT, MathJax and ChromeVox with Speech Rule Engine. `scripts/screenreader_speech.py`
runs our MathML and the gold's through both and reports where they differ, with a flag for
whether MathCAT recognised the input as chemistry (it marks `data-chem-formula` or
`data-chem-equation`; `docs/eval.md`, "Speech under screen-reader engines", has the commands and
what the numbers do not show). Build the engines once:

```
scripts/speech_oracles/build.sh     # MathCAT v0.7.7 from source (rustup into ~/.cargo, no sudo),
                                    # SRE 4.1.5 from npm; into .local/speech-oracles/
uv run python scripts/screenreader_speech.py --items after.jsonl --gold data/dev-v2/dev \
  --sections chemistry-atoms-first latex-chemistry
```

What it found on the development chemistry rows of the recorded run of 2026-10-26, before the shape
below (443 gold equations, 318 with a proposal; `--items` of that run, no model call): MathCAT
recognised 114 of the 443 gold equations as chemistry (the authored document's one-`mi`-per-letter
shape, not OpenStax's `mtext`), and 90 of our 94 proposals for those. A formula it recognises is
spelled letter by letter ("cap h, sub 2 cap o"); `intent` does not change that (checked, "MathML
shape"), it makes MathCAT treat the expression as chemistry. A state written `<mtext>(aq)</mtext>`
was spoken "(aq)", not "aqueous": MathCAT splits a state out of an `mtext` only when the text is
shorter than four characters, so `(s)` works and `(aq)` does not. 63 of our 318 proposals had it,
and none of the 16 gold readings that say "aqueous" was matched by ours. That is the shape, not the
symbols, and the scorers do not see it.

## MathML shape (pipeline 0.5.2)

A student's screen reader follows the MathML, so the shape decides what is said. The converter
(`validate.safe_latex_to_mathml`, the one place MathML is made from LaTeX for equations, table
cells, page maths, review edits and the notation memory) now rewrites a converted tree in two
steps: `pipeline/mathml_shape.py` for all maths and `pipeline/chem_mathml.py` for chemistry.
MathCAT's chemistry rules (`src/chemistry.rs` of the version the oracle builds) decide the
chemistry shape:

| Written | Shape | Why |
|---|---|---|
| an element symbol | one `mi` per symbol (`<mi>Na</mi>`); `NaCl` is two. Beside other maths a one-letter symbol keeps `mathvariant="normal"` | MathCAT marks an element `mi` or `mtext`; one token per symbol is what its own repairs aim for. With `intent` on the root no style is needed, and Speech Rule Engine reads an explicit one aloud ("normal H") |
| a count or a charge | on the last symbol of the species (`msub`, `msup`, `msubsup`); `SO4^2-` is `S` then `msubsup(O, 4, 2−)` | the shape MathCAT writes itself for sulfate |
| a state `(aq)` `(s)` `(l)` `(g)` after a species | `mo (`, `mi aq`, `mo )` | an `mtext` of four characters is not taken apart (`(aq)` read "(aq)"), one of three is |
| a nuclide of an element, or the electron | `mmultiscripts(C, mprescripts, 6, 14)` | an empty-based `msubsup` is read "C with 2 prescripts"; MathCAT checks the numbers against the element. A neutron, proton, positron, alpha, beta or gamma, and an excited nuclide (`Ni^*`), keep the converter's shape: MathCAT has no rule for them (it reads the star "super times" and then no longer takes the nuclide as one) |
| an arrow | `mo`; a labelled arrow is the `mo` and its label in a `mover` | the `mstyle` and `mpadded` around `\xrightarrow` made MathCAT fail with "Pattern match/replacement failure" and say nothing, once the symbols are `mi` |
| a formula or reaction on its own | `intent=":chemical-formula"` or `":chemical-equation"` on the `math` element | MathCAT reads `intent` only there; a small reaction (`H2 -> O2`) is under its own likelihood threshold without it |

The forms of the `\ce` subset ("Writing it: `\ce`") in this shape: a state with a condition
(`(aq, 0.10 M)`) stays one `mtext` because MathCAT has no rule for a condition (it reads the text;
our own reading says "aqueous, 0.10 molar") and counts as evidence like a plain state; the gas
and precipitate marks are the operators `↑` and `↓` after the species (MathCAT reads them
"up arrow", "down arrow" and keeps the equation chemistry; our reading says "gas given off",
"precipitate"); the excited nuclide is as above. Every one of them reads through both our speech
(`tests/test_mathml_shape.py`) and MathCAT (`tests/test_screenreader_shape.py`).

A bare word of capitals is not evidence by itself, because `ON`, `IS`, `SO`, `IN`, `OK`, `PH`,
`NY`, `FBI`, `CPU`, `HIV` and `V_{\text{IN}}` are words and acronyms as often as formulas: a
multi-symbol `mtext` needs a lowercase letter (`NaCl`, `HCl`, `NaOH`), or a count or charge, or a
state, or a coefficient in front of it, or a bracket pair with a count on it (`Ca(OH)_2`, where `OH`
is then a group), or a reaction arrow with such a species somewhere in it; an arrow between two
bare words does not count. An `mi` word that is not upright (`\operatorname{Co}`, `\mathit{Na}`)
is not a symbol. `tests/test_chem_mathml.py` keeps a list of words, acronyms and units that must
convert exactly as before.

A row is chemistry only with evidence: a symbol with a count or a charge, a nuclide prefix, two
symbols side by side, a state after a species, or an arrow with symbols on both sides. A lone
`\text{H}` (hydrogen or a variable), `\text{No}`, `\text{Pa}` or `\mathrm{d}x` is left as it was.
Chemistry beside other maths (`rate = k[NO2]^m`, `K_w = [H3O+][OH-]`, `ΔG°`) is reshaped but gets no
`intent`: MathCAT would read the whole expression as one formula, and an `intent` on a part of a
row is ignored. The two `intent` values are the only ones any allowlist accepts, and only on
`math` (`core/mathml_intent.py`; `validate.check_mathml`, `safe_html.sanitize_mathml` and
`web.mathml.sanitize_mathml` refuse anything else; `tests/test_chem_mathml.py`). `intent` does not
change how a formula is spelled aloud ("cap h, sub 2 cap o" either way); it makes MathCAT take the
expression as chemistry, with the state words and arrow phrases that follow.

For all maths: `^\circ` after a symbol is the degree sign U+00B0 (U+2218 stays between two things,
`f \circ g`); `\overline` and `\underline` use the macron and the low line, not U+2015, which
Speech Rule Engine reads as "quotation dash". Matrices (`pmatrix`, `bmatrix`, `vmatrix`), `cases`,
`aligned`, `\lim` and `\sum` were already read as matrices, determinants, cases, lines and limits
by both engines (checked, `tests/test_screenreader_shape.py`), so they are unchanged.

The review screen and `alttext` read the new shape the same words as before
(`tests/test_mathml_shape.py`). Review carry compares an equation's LaTeX and display, not its
MathML or reading (`web/carry.py`), so an accepted equation of an earlier upload is still carried
when the MathML is made another way; the fingerprint is the picture's hash and does not change.
A carried accept keeps the new fix's own equation (its MathML shape and reading) and copies only the
decision; an edit is the person's words and is copied whole. The step cache key holds the pipeline
version, so a re-converted file asks again. Where the shape goes: the student file and the review
screen draw an `intent` equation's symbols upright by one CSS rule each (`assemble.py`,
`web/static/app.css`); the EPUB export drops `intent` (it is not in the EPUB 3 MathML schema:
epubcheck 5.4.0 reports RSC-005) and keeps everything else; Canvas and the Common Cartridge
exports carry the MathML as it is.

### Measured (offline, $0)

The recorded dev-v2 run of 2026-10-26 (`~/.cache/a11y-stem/round4/on.jsonl`, flags on; 1,809 gold
equations in 8 sets, 1,564 with a proposal): the proposals' LaTeX converted by the converter before
and after, read by both engines (`scripts/screenreader_speech.py --pairs`). "Equal" is the same
words as the gold's reading.

| set (gold equations) | MathCAT ClearSpeak | MathCAT SimpleSpeak | SRE clearspeak | SRE mathspeak |
|---|---|---|---|---|
| chemistry (443) | 133 to 136 | 133 to 136 | 61 to 35 | 83 to 45 |
| OpenStax algebra (1,120) | 736 to 736 | 727 to 727 | 517 to 517 | 456 to 456 |
| authored exercises, slides, matrices, Word images, HTML slides (246) | 221 to 222 | 221 to 222 | 221 to 220 | 206 to 205 |

**Which spelling of the symbol.** Four were measured on the same run (chemistry set; "normal said"
is the number of the 318 chemistry proposals whose Speech Rule Engine reading says "normal"):

| symbol written as | MathCAT ClearSpeak equal | SRE clearspeak equal | SRE mathspeak equal | "normal" said | recognised as chemistry |
|---|---|---|---|---|---|
| `mi` with `mathvariant="normal"` always (first version) | 132 | 44 | 50 | 199 | 155 of 155 |
| `mi` with no style at all | 132 | 33 | 51 | 6 | 155 of 155 |
| `mathvariant="normal"` on multi-letter symbols only (Na, Cl) | 132 | 33 | 51 | 6 | 155 of 155 |
| **no style when the whole equation is chemistry, `normal` beside other maths (kept, with the stricter evidence below)** | 136 | 35 | 45 | 64 | 153 of 153 |

MathCAT's reading and recognition are the same in all four, so the style is a choice about the
other readers. Speech Rule Engine says the `mathvariant` aloud ("normal H"), so the first version
cost every single-letter symbol; with no style it reads "H sub 2 O". "Equal to the gold" is lower
for the unstyled forms because the gold writes `mtext` and Speech Rule Engine reads an `mtext` and
an `mi` differently, not because the reading is worse. The unstyled `mi` has a cost: an italic
single letter is a variable to our own chemistry reading (`pipeline/speech_chem.py`), which needs
the upright style to tell the `H` of water from an enthalpy `H`. So the style is dropped only where
`intent` says the whole equation is chemistry (the review screen's reading puts it back from
`intent`, `speech._restore_upright_symbols`) and kept on a symbol beside other maths (a rate law, an
equilibrium constant), which is the 64 (the first three rows were measured before the evidence rule below was tightened). The standalone file draws a symbol of an `intent` equation
upright with one rule (`math[intent] mi { text-transform: none }`; Chromium draws it the same as
`mathvariant="normal"`, checked; Firefox and Safari were not); Canvas has no stylesheet, so a Canvas
page draws those symbols italic.

- **Recognised as chemistry.** Of the correct dev outputs that are a formula or a reaction on their
  own (exact on the chem scorer in the recorded run, `intent` written; n=153), MathCAT recognised
  136 before (0.889, 95% Wilson 0.829 to 0.929) and recognises all 153 now (1.000, 0.976 to 1.000).
  Of all 293 correct chemistry outputs, 141 before and 158 now; the rest are chemistry beside other
  maths (rate laws, thermodynamics, cell notation) that MathCAT does not call chemistry whatever
  the shape. `tests/test_screenreader_shape.py` pins a list of 36 formulas and reactions at 95% or
  better when the engines are built.
- **States.** Where the gold's reading has a state word ("aqueous", "solid", "liquid", "gas"; 36 of
  the 318 proposals' equations), ours had it in 34 before and has it in 36 now. Ours read a state
  literally (`(aq)`, "of s") in 69 of 318 before and in 16 now: a state written with conditions
  (`(aq, 1 M)`, outside the `\ce` subset), or in a table cell.
- **Errors.** Neither engine fails on any of the 1,564 proposals.
- **A neutron, proton, positron, alpha, beta or gamma with prefix numbers keeps the converter's
  shape** (an empty base holding the numbers, then the symbol). MathCAT 0.7.7 has a nuclide rule
  for elements and for the electron (`{}^0_{-1}e`) only, and reads `mmultiscripts` of the others no
  better, so they were not changed (no regression). In a reaction they are still read "n pre sub 0
  and pre super 1" while its other nuclides are read as nuclides; a neutron or a positron alone
  has no `intent`. 9 of the 37 correct all-chemistry outputs that hold a nuclide are in that case.
- **"Equal to the gold" rises by 3 on chemistry** (133 to 136: 16 gained, 13 lost; one more in
  the slides set) and the SRE columns fall. The gold is not the right reading: of the 13 lost under
  MathCAT, 10 are where the gold's `mtext` is a word ("NO sub 2", "OH", "CO", "CH sub 3 OH") and ours
  is spelled letter by letter ("cap n cap o sub 2"), and 3 are nuclear equations the gold's shape
  does not let MathCAT take as chemistry ("Be pre sub 4 and pre super 9") and ours is read as
  nuclides ("super 9, sub 4, cap b e"). The 16 gained are the states. Algebra and the authored sets
  do not move: what is left there is the gold's own reading ("negative" for a minus in a number,
  matrices as "array of", a trailing "period"), where ours is the better reading.
- **Speech Rule Engine on the rest.** A symbol with a count followed by another is read "H sub 2 of
  O" (a function of O) where an `mtext` is not; an invisible times between symbols removes some of
  the "of" but lowers both engines' equality, so it was not kept. Nuclides are read "left sub 8 left
  super 16" where the gold's empty-based `msubsup` is "sub 8 to the 16th power". One `mtext` per
  symbol was tried and is not better (SRE equal 37 and 62, MathCAT 135). VoiceOver and JAWS have
  their own engines and were not measured; the shapes are the standard MathML ones.
- **Scorers** (`scripts/dev_converter_effect.py --shape-only`, same run). The chem scorer is
  unchanged at 293 of 443. The symbols scorer is unchanged at 1,289 of 1,809 (its "before" is
  1,289, not the 1,271 it gave before the change below). The primary (`exact_lenient`) goes 827 to
  827; the non-chemistry sets change by 0 equations.
  - The symbols scorer first lost 1,271 to 1,196, all in chemistry sets. That was an artefact, not
    a change in content: it keeps a letter's style as an atom (`normal:H`), so an upright `mi`
    symbol read as a different symbol from the gold's `mtext` one (75 OpenStax and 11 authored
    equations, for example `\text{NH}_4^+`, `\text{H}_2\text{O}\text{(s)} \to \text{H}_2\text{O}\text{(l)}`,
    `\text{Ni}^{2+}\text{(aq)} + 2\text{e}^- \to \text{Ni}\text{(s)}`; the same symbols in the same
    order). `equations_symbols` now calls an upright element symbol, and the electron `e`, the same
    as an `mtext` one; an upright `d` is still not a plain `d`. On the old converter's output that is
    +18 equations on this run (chemistry sets and one slides document only, 0 elsewhere), on the new
    output +62, so the two sides are alike.
  - The primary scorer loses 10 and gains 10. The 10 gained are the states. The 10 lost are rate
    laws and the like where the gold's `mtext` is a packed word ("CH", "NO") against our one symbol
    per token or where a symbol keeps `normal` beside other maths, and one authored equation whose
    gold has the old converter's shape. That is the packing gap the primary scorer documents
    (docs/eval.md); it is left as registered, so the registered held-out numbers stay
    reproducible. The primary does read `mmultiscripts` as the answer keys' prefix scripts (our
    shape only: no recorded gold has one), which keeps the nuclides equal. Use the chem scorer for
    chemistry.

## Side effect on maths that is not chemistry

The conversion repair that hangs a script on the group before it (`(a+b)^2`, `[A]_0`) is for
every equation, not only chemistry, and so are the arrow and the element-symbol repairs. Checked
without a model call (`scripts/dev_converter_effect.py`): every recorded answer of the tables-off
runs is converted by the converter as it was (the three repairs off) and as it is, and scored
against the gold. The equation prompt was put back to the recorded one for these replays, so every
request is answered from the recordings.

| set | primary before to after | symbols |
|---|---|---|
| `data/gold` (30 equations) | 0.767 to 0.767 | 0.967 to 0.967 |
| dev-v2 maths written by people (OpenStax College Algebra 9.6, Elementary Algebra 5.2 and 8.5; 360 gold) | 0.569 to 0.658 (205 to 237 of 360) | unchanged |
| of which 9.6 Binomial Theorem | 94 to 126 of 161 | unchanged |
| dev-v2 authored LaTeX (exercises, matrices; 147 gold) | 136 to 132 | unchanged |
| real-world arXiv dev (25 papers, 2,350 gold), the gold as stored | 0.4987 to 0.4877 (**-1.11 points**) | 0.5209 to 0.5209 |
| the same, the gold converted again from its LaTeX by the same converter | 0.5017 to 0.5043 (+0.26) | 0.5196 to 0.5187 (-0.09, 2 equations) |

Where the gold is MathML written by people the change helps (binomials, 32 equations: OpenStax
groups `(a+b)^n`, as the converter now does) and moves nothing else. Where the gold was made *by
the old converter* it can only lose: the authored LaTeX documents and the arXiv gold
(`gold/arxiv_gold.to_mathml` calls `safe_latex_to_mathml`, so that "gold and a prediction are made
by the same converter"). Their stored MathML keeps the old shape, `msup(")", 2)` and an arrow as an
`mi`; the four authored equations and the 26 arXiv equations that fall are right on both readings
of their LaTeX (`(x-5)^{2}+(y+13)^{2}=36` against the gold's `(x - 5)^2 + ...`). Re-making the gold
with the converter it is compared to gives +0.26 and -0.09 points on the real-world set: no
regression, which is why the change was not narrowed to chemical species (that would also lose the
binomials). Two consequences for the merge: the real-world dev result of its own branch (0.562
primary) reads 0.550 on the same answers with this converter, and its held-out gold (registered,
hashed) cannot be re-made, so a comparison there has to convert the gold's LaTeX again when it
scores (the script above does it); and the space layout now puts after an inline equation that TeX
leaves no space glyph before (`ions` after `SO42-`) changes the context text of the equations in
script-heavy paragraphs: 400 of the 2,750 recorded real-world dev requests were asked again
(about $0.23).

## Accuracy: why the primary score barely moved (2026-10-21)

The chemistry work above moved the primary score (`exact_lenient`, end to end) of the twelve
development chemistry documents from 0.093 to 0.108 and left the registered held-out chemistry at
0.041, for ReadableSTEM and for the chatbots alike, while the symbols score was 0.65 against 0.13.
The question: is the primary number real, or the way it is measured? Method: the recorded
development answers (no model call), every one of the 443 gold equations of the twelve documents
put in one outcome class (`scripts/dev_chem_classes.py`), and the scorer's verdict checked against
what the equation says. Branch `fix/chemistry-accuracy`; the replay of the recording at its base
commit reproduces 0.108 exactly (785 of 785 requests answered from the file).

### The finding: mostly a scorer artefact

The primary scorer compares the shape of the MathML. The chemistry gold has two shapes, and a
reading that says the same chemistry in a third is wrong on the primary:

| Gold | Shape | Example: gold against the reading |
|---|---|---|
| OpenStax (335 equations, written by people) | an `mtext` per run of element symbols, `(` `aq` `)` as three tokens, `°` (U+00B0), a charge on an empty base `msup({}, +)`, numbers split in two `mn` (`0.2` `57`) | gold `H2O(l)` as `msub(mtext H, 2)`, `mtext O`, `mo (`, `mtext l`, `mo )`; the reading has `mtext (l)` and `∘` for the degree |
| authored (108, made by the repository's converter from LaTeX) | one upright `mi` per letter, the arrow as an `mi` | gold `\mathrm{H_2O}` is `mi H`, `msub`, `mi O`; the reading `\text{H}_2\text{O}` is `mtext H`, `mtext O` |

The forms that make a chemically identical reading wrong on the primary, with how many aligned
equations each class holds (the classes are cumulative and in this order; each is a scorer that
already existed, then the new one):

| Wrong on the primary, right by... | What differs | n (of 319 aligned) |
|---|---|---|
| stream v2 | tokens packed otherwise (`(s)` one token or three; `kg/L` one token or two) | 75 |
| the symbols scorer | scripts grouped otherwise (`[A]_0` on the bracket or on its closing bracket; a charge on `msup({}, +)` or `msubsup`); a table cut into cells otherwise | 39 |
| the **chem scorer** (new) | upright against italic against `mtext` against bold letters (all 97 of the authored document's, 33 of OpenStax's); `→` against `⟶`; `│ ║` bars against `\| \|\|`; `∆` against `Δ`; `0.2` `57` against `0.257`; `96,485` against `96{,}485`; a closing parenthesis of the sentence inside the gold equation (18 gold equations, `[Al(H2O)6]^{3+})`) | 130 |

The chemistry scorer is `eval/equations_chem.py` (`exact_chem` and `pred_mathml` in the document
task's pairs, a labelled secondary beside the primary, like the symbols scorer; the primary, the
gate and the committed reports are unchanged, and it is computed for chemistry gold only). It
starts from the symbols stream (scripts as markers, in reading order, tables flattened) and
forgives only the spellings in the table above; a different count, charge, element, state symbol,
coefficient or arrow kind, a script moved between sub and super, and a missing or extra part are
all wrong (`tests/test_equations_chem.py`, 44 tests: 15 pairs of near formulas that must stay
wrong). Evidence that it is not too generous:

- on the authored document the gold has its LaTeX source. For the 100 equations of the document
  that the chem scorer (or an earlier rung) calls right and the primary calls wrong, the reading's
  LaTeX and the gold's LaTeX are the same string once `\text`, `\mathrm`, spacing and the arrow
  command are removed: 100 of 100;
- every equation the existing `content` score of `scripts/dev_chem.py` calls right the chem scorer
  calls right (0 exceptions); the chem scorer is stricter than `content` where it matters (it
  keeps sub against super) and more lenient only for the three forms above (bars, numbers,
  the sentence's parenthesis), 16 equations, all read by hand;
- everything the symbols scorer calls right, it calls right.

### Outcome classes, all 443 gold equations of the twelve documents, ReadableSTEM

Three columns: before the chemistry work (`c190b3e`), the base of this branch, this branch.

| Class | before | base | this branch |
|---|---|---|---|
| **right on the primary** | 41 (9.3%) | 48 (10.8%) | 48 (10.8%) |
| scorer artefact: tokens (right on stream v2) | 75 | 74 | 75 |
| scorer artefact: scripts (right on the symbols scorer) | 48 | 39 | 39 |
| scorer artefact: chem (right on the chem scorer) | 83 | 125 | 130 |
| **chemically right, all four** | 247 (55.8%) | 286 (64.6%) | **292 (65.9%)** |
| input: the printed page lacks what the gold has | 7 | 7 | 7 |
| not read (a failed fix, the picture kept) | 3 | 5 | 2 |
| span: the reading holds the gold's symbols and more | 4 | 4 | 4 |
| span: the reading is a part of the gold's symbols | 16 | 14 | 11 |
| span: the gold is written as several pieces | 1 | 1 | 1 |
| wrong reading (same span, other symbols) | 4 | 3 | 3 |
| undetected: formula in a table cell | 95 | 94 | 94 |
| undetected: lone `×` (the gold counts the sign as an equation) | 22 | 22 | 22 |
| undetected: other | 44 | 7 | 7 |
| gold | 443 | 443 | 443 |

By source of the gold, this branch: OpenStax 335 = 44 right + 141 artefacts (69 tokens, 39 scripts,
33 chem) + 150 not right (7 input, 2 not read, 4+10+1 span, 3 wrong reading, 94 table, 22 `×`, 7
other); authored 108 = 4 right + 103 artefacts (6 + 97) + 1 span.

What it says, as measured:

1. **The primary number is mostly a measure of markup.** 65.9% of the gold equations are written
   right by ReadableSTEM (89 to 92% of the 319 it found and aligned: 292 of 319) while the primary
   says 10.8%. On the authored document 107 of 108 are right in chemistry (before the work: 63)
   and the primary says 4. The chemistry work did raise the content (before 55.8%, now 65.9%), and
   the primary could not see it: it moved by 7 equations because the OpenStax shape (arrow as an
   operator) happens to match the primary's rule, and it moved the authored document the wrong
   way (10 to 4) because the gold of that document has the old converter's shape.
2. **What is really missing is detection, not reading.** 124 of the 443 (28%) are not found at
   all: 94 are formulas in the cells of tables (tables were off in that run; production has them on since 2026-10-21, docs/tables.md),
   22 are a lone `×` that the gold counts as an equation and the page prints in a sentence (not
   chemistry, nothing to read: no equation to find; a reader hears "times" in the sentence), and 7
   are not paired: 2 are a reaction line that the table element under it holds (17-3), 5 are
   neighbours merged into one predicted equation or lost in the alignment (the `(a)` of an
   `(a) ...; (b) ...` line, derivation steps).
3. **When the system finds an equation it mostly reads it right**: of 319 aligned, 292 chemically
   right, 3 wrong readings (read by hand: `E°cell` read as `Ecell` because the crop starts at
   `ell`; `ΔG° = −56.2 kJ/mol` read without its left part and with the next equation merged in;
   `average mass` read as `average`), 2 failed fixes, 7 the page cannot give (below), and 16 span
   problems.
4. **7 equations no reader of the PDF can get**: the printed page shows `²H` where the gold has
   `₁²H` (the print of OpenStax's MathML dropped the empty-base subscript in running text; the
   subscript is not in the PDF: checked on the page picture and the glyphs). The gold is not wrong
   (the book has it); the input is. Left as they are, in their own class.

### Against the chatbots

The chatbot recipe was recorded on five of the chemistry sections (Haiku) and two (Opus)
(`scripts/dev_chatbot.py`; `docs/eval-page-math.md`). The same classes, on the same sections; the
proof's converter cannot read `\ce{...}`, which the chatbots write, so each chatbot is scored as
recorded and with its `\ce` written out as ReadableSTEM does (`--expand-ce`; what the chatbot wrote,
not what the proof's converter could read).

| 5 sections, 217 gold | ReadableSTEM | Haiku chatbot | Haiku, `\ce` written out |
|---|---|---|---|
| primary right | 19 (8.8%) | 22 (10.1%) | 23 (10.6%) |
| scorer artefacts (tokens, scripts, chem) | 64 | 85 | 86 |
| **chemically right** | **83 (38.2%)** | **107 (49.3%)** | **109 (50.2%)** |
| input: the page lacks it | 7 | 0 | 0 |
| span (extra, partial, split) | 7 | 3 | 4 |
| wrong reading | 1 | 11 | 12 |
| undetected: table cell | 94 | 42 | 42 |
| undetected: lone `×` | 18 | 18 | 18 |
| undetected: other / unreadable | 7 | 36 | 32 |

| 2 sections, 93 gold | ReadableSTEM | Opus chatbot | Opus, `\ce` written out |
|---|---|---|---|
| primary right | 1 | 0 | 1 |
| scorer artefacts | 40 | 5 | 68 |
| **chemically right** | **41 (44.1%)** | **5 (5.4%)** | **69 (74.2%)** |
| span (extra, partial) | 5 | 0 | 0 |
| wrong reading | 0 | 0 | 2 |
| undetected: table cell | 37 | 37 | 0 |
| undetected: other / unreadable | 1 | 42 | 13 |

Read as: on the primary score all three are at 0 to 11% on these sections, for the same reason.
The chatbots' registered chemistry score (0.041) is that and a second thing: the proof's converter
refuses `\ce` (Opus: 0 of 36 and 5 of 57 aligned as recorded, 27 and 44 with `\ce` written out).
On chemical content the chatbots are ahead of ReadableSTEM by 11 points (Haiku) and 30 (Opus) on
these sections, and the whole difference is the table cells they read as equations (52 and 37
more) and equations the layout does not find; ReadableSTEM's readings that it finds are wrong
less often (1 against 11 wrong readings). Two to five sections: indicative only.

### What was fixed

| Real error | Fix | Equations on the development run |
|---|---|---|
| a reaction with a label over its arrow (`\xrightarrow{\Delta}` in TeX) cut in three: `CaCO3(s) ∆`, `−→`, the products | `layout._label_over_arrow` joins the line that ends in a script-size label with the line that starts at the arrow under it; `chem.ARROW_LABELS` (`∆`, `Δ`, `heat`, `light`) are part of a reaction | 3 (authored: CaCO3, KClO3, NaHCO3); the one with the electron capture (a reading cut as `+ e⁻ → ⁷₃`) is not fixed |
| the arrow label over **and under** the arrow, `\ce{->[above][below]}`, refused (electrolysis of NaCl, 600 °C) | `chem._arrow_label` reads two labels and `\text{...}`, `^\circ`; written as `\xrightarrow[below]{above}` | 1 (OpenStax 18.2) |
| the phase boundaries of a cell, `\middle\|` with no `\left` (cell notation), made the converter refuse the whole equation | `chem.tidy_cell_notation`, in `expand_ce` | 2 (OpenStax 16.3) |

Layout check (`scripts/layout_identity.py`, no model call): 125 of the 126 PDFs of the repository's
fixtures, development material and development pages give the same elements before and after; the
one that differs is the authored chemistry document (329 to 317 elements).

Measured by replay: the recorded answers answer 769 of 773 requests (the four others are live,
$0.009); chemically right 286 to 292 of 443 (65.9%), the primary 48 to 48, symbols 161 to 162,
`not read` 5 to 2, partial span 14 to 11.

Replay checks and tests: `tests/test_chem_accuracy.py` (label above and below, three-part and
unreadable labels still refused, `\middle` in cell notation, with `\left` left alone),
`tests/test_layout_chemistry.py` (the three labelled reactions are one display line each),
`tests/test_equations_chem.py`. No prompt changed (same hashes), so the recorded answers and
`latest-*` stay valid.

### What was looked at and not changed

- **The degree sign.** The reading writes `^\circ` as `∘` (U+2218); OpenStax has `°` (U+00B0) in
  170 of its gold equations. Mapping it would make 10 more OpenStax chemistry equations right on
  the primary (44 to 54), and `speech.py` already says "degrees" for both. It was not made then: the
  converter is shared with every equation, and the real-world gold (made by the same converter,
  stored with `∘`) cannot be re-made, so it would lower a registered number to raise another.
  It was made in pipeline 0.5.2 ("MathML shape" below), because Speech Rule Engine says `∘` as
  "composed with"; the cost is bounded, the real-world dev gold has a ring in a superscript in 2 of
  its 2,350 equations and the held-out gold in 0 of 1,826.
- **State symbols as three tokens** (`(` `l` `)`, as OpenStax) instead of the one `mtext (l)` the
  reading has: 7 more primary matches on OpenStax (54 to 61), no difference to the review
  screen's reading; a screen reader does differ (MathCAT says "(aq)" for `mtext (aq)`), and
  pipeline 0.5.2 writes three tokens for that reason.
- **The rule under a half-reaction block** (OpenStax 16.3, 16.4: five equations): the gold is one
  equation with the "overall" line under the rule, the layout makes the lines above and the line
  below two equations. Each is read right; the gold's grain differs.
- **Lists in a sentence** (`(a) ...; (b) ...`, `CO3^2-, F^-`): the gold is one equation per item, the
  layout one span for the line (3 equations).
- **The unknown symbol `HE`** (OpenStax 20.2 prints the book's typo for He): `\ce` refuses a
  symbol that is not an element; kept, a failed fix with a decision.
- **Formulas in table cells** (94) and **a reaction line the table element took in** (7) are the
  table work of `docs/tables.md`, not touched here.

### Held-out

`docs/eval-chemistry-heldout.md` (pre-registered before the run; five sections of OpenStax
Chemistry, 377 gold equations, not used before; one run). The registered held-out set's chemistry
sections were not re-run or re-scored for this work.

| 377 gold equations | baseline (`f206326`) | this branch |
|---|---|---|
| primary `exact_lenient`, end to end | 0.135 | 0.135 |
| chemically right (chem scorer or an earlier rung) | 0.552 (208) | 0.560 (211) |
| symbols scorer | 0.531 | 0.536 |
| of the aligned, chemically right | 81.6% | 82.7% |
| not found: lone `×` / table cell / other | 66 / 39 / 16 | 66 / 39 / 16 |

The same shape as the development sets (primary 0.135, chemically right 0.56); the fixes add 3
equations because the sections have no TeX label over an arrow.

### Numbers under both scorers

See the table above: primary 48 of 443 = 0.108 and chemically right 292 of 443 = 0.659 on the
development run for this branch, against 0.108 and 0.646 at its base and 0.093 and 0.558 before the
chemistry work. Where this document says "primary" it is `exact_lenient` as registered; "chemically
right" is `exact_chem` or an earlier rung of the ladder (stream v2, symbols), which are the same
scorers as the existing secondary ones.


## What does not work yet

- **A reaction with a label over the arrow** (`CaCO₃(s) →Δ CaO(s)`) set as TeX does is one line now
  (section "Accuracy"); a label that is not `∆`, `Δ`, `heat` or `light`, or one that is taller than
  a script, still cuts it, and so does an electron-capture line (`+ e⁻ → ⁷₃Li`) of the authored
  document.
- **Formulas that the PDF set at one size** and **formulas in a table cell** (the cell text is
  flat; docs/tables.md).
- **Three-part arrow labels** and `<=>>`: refused, the picture stays with a decision. A two-part label is read aloud as "right arrow
  with 600 degrees Celsius below and electrolysis above", not as "reacts, with ..., to form".
- **The primary and symbols scorers reward the spelling of the gold**, so a change that is right
  for a reader (the group a bracket's script belongs to, an arrow as an operator) can lower them
  on gold made by the older converter. `eval/equations_chem.py` is the score for chemistry that
  does not ("Accuracy"); the primary stays as registered.
- **The prompt's one `\mathrm` example** (a state symbol with a concentration) is older than the
  `\text` shape the converter now writes, and the table prompt (`models/prompts/table.md`) does
  not teach `\ce`, though `table_step` expands it. A candidate that fixed both (and added states
  with conditions, marks, the excited nuclide, half-reactions, couples and cell notation) was
  re-recorded on the dev sets and did not move chemically right (-0.5 points on 385 gold
  equations), so it is not in the prompts: `docs/eval-chemistry-round2.md`.
- **Real screen readers were not run.** MathCAT and Speech Rule Engine were, built from source
  ("MathML shape"); what NVDA, JAWS and VoiceOver say with their own settings and what VoiceOver
  does with `intent` and `mmultiscripts` is the manual test of docs/mathml-alttext.md and was not
  done.
- **MathCAT reads some nuclear particles as maths** (a neutron, proton or positron with prefix
  numbers, an excited `*`), and Speech Rule Engine says "normal" before a one-letter symbol
  beside other maths ("MathML shape"; an equation that is all chemistry has no style to say):
  both are in the engines, not in the markup we can change.

## Spend (list price, from the answer files)

| What | USD |
|---|---|
| Part 1 baseline, the 12 documents (batch: half this was paid) | 1.347 |
| Part 1 after: the authored document (live, with the shadow second check, 0.174 of it unneeded) and the 11 OpenStax sections | 1.503 |
| **Part 1 total** (cap 6.00; 3.0 with every replay free) | **2.850** |
| The side-effect check: replays only | 0 |
| `latest-equations.json` and `latest-document.json` re-recorded for the new equation prompt (the CI gate was stale); the figure answers replayed | 0.145 |

The development set only; the held-out sections and the sealed check part were not run or read
for this work (the recorded held-out results were read to describe the problem and nothing was
tuned on them).
