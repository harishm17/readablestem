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
96 are identical before and after. The 25 that differ are the chemistry sections (21 prints), the
authored chemistry document, the Word lecture (it has formulas and ions in its sentences), one
lecture slide whose reaction line was a heading and is now an equation, and a space after
"Simplify:" in the two prints of an algebra section. No physics, algebra, statistics, slide or
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

## What it did on the development sets

Twelve documents of dev-v2: the eleven *Chemistry: Atoms First* sections (70 pages, 335 gold
equations) and the authored chemistry document (9 pages, 108 gold equations), 443 gold
equations on 79 PDF pages. Before is commit `c190b3e` (this branch's base), after is this work;
the same models (claude-opus-5-5, claude-haiku-5-5), thresholds and settings, the equation prompt
with its new "Chemistry" section being the one change to what the model is asked. Every model
answer was recorded once and the "after" numbers were scored again from the recording with the
final converter (`--answers`, `--answers-offline`: no model call for a scoring change). Tables
are off, as in production. Scored by the repository's scorers (`scripts/dev_buckets.py`) and by
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
  group, an arrow as an operator). Both scorers compare the shape of the MathML, so they call the
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

## Speech, checked by hand

24 chemistry equations of the development gold (a seeded draw: 8 OpenStax-shaped MathML of every
kind the sections have, and authored ones in the converter's older shape), read by
`mathml_to_speech`. What was found, and fixed, as the list was read: an ion saved with its charge
on the lowercase letter read "capital Z n to the power 2 plus" and `2Ag⁺` read "2 amperes g"
(`speech_chem._split_pair`); `HSO₃⁻` read "H S O 3 superscript minus" (OpenStax puts the charge on
an empty base); a concentration in brackets read "open bracket N O close bracket" next to
"the concentration of C l 2"; `2e⁻` read "2e to the power minus"; the cell-notation bars read
"box drawings light vertical". All are in `tests/test_speech_chem.py`. Left as they are:

- a state symbol with conditions, `(aq, 1 M)`, reads "open parenthesis a q comma 1 capital M
  close parenthesis" (the state is not picked out of it);
- a standard potential `E°(Cu²⁺/Cu)` reads as maths with a chemical subscript ("capital E sub
  C u 2 plus /Cu end sub degrees"), clumsy and correct;
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

## What does not work yet

- **A reaction with a label over the arrow** (`CaCO₃(s) →Δ CaO(s)`) set as TeX does: the label is a
  small row above the arrow and the text layer starts a line with it, so the reaction is cut in
  three (3 equations of the authored document). OpenStax's MathML sets the label in the same
  line and works.
- **Formulas that the PDF set at one size** and **formulas in a table cell** (the cell text is
  flat; docs/tables.md).
- **Two-part arrow labels** (`->[above][below]`), `<=>>`, a precipitate mark, a state symbol with
  a concentration in `\ce`: refused, the picture stays with a decision.
- **The primary and symbols scorers reward the spelling of the gold**, so a change that is right
  for a reader (the group a bracket's script belongs to, an arrow as an operator) can lower them
  on gold made by the older converter; `scripts/dev_chem.py` has a score that does not.
- **Real screen readers were not run.** The reading is the review screen's string; what NVDA with
  MathCAT or VoiceOver says about this MathML (`mtext` element symbols in OpenStax's shape,
  grouped scripts) is the manual test of docs/mathml-alttext.md and was not done.

## Spend (list price, from the answer files)

| What | USD |
|---|---|
| Part 1 baseline, the 12 documents (batch: half this was paid) | 1.347 |
| Part 1 after: the authored document (live, with the shadow second check, 0.174 of it unneeded) and the 11 OpenStax sections | 1.503 |
| **Part 1 total** (cap 6.00; 3.0 with every replay free) | **2.850** |

The development set only; the held-out sections and the sealed check part were not run or read
for this work (the recorded held-out results were read to describe the problem and nothing was
tuned on them).
