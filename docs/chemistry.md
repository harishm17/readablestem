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
96 are identical before and after. The 25 that differ are the chemistry material (21 files: the section prints, the authored chemistry document and a fixture), the
Word lecture (it has formulas and ions in its sentences), one
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
| arrows | `->`, `-->`, `<-`, `<--`, `<->`, `<=>`, with a label `->[\Delta]`, `->[heat]`, or two (`->[above][below]`; text, numbers, `\text{...}`, `^\circ`) |
| bonds | `-` (between atoms), `=`, `#` |
| nuclides | `^{14}_{6}C`, `^{0}_{-1}e`, `_{6}^{14}C` |
| particles | `e-`, `n`, `p` |

Not supported, and refused (`CeError`, then the LaTeX check says `unknown_command`, the cheap
model's answer goes to the main model, and a reading that still has it is a failed fix with a
required decision, the picture kept): `<=>>` and `<<=>`, a gas or precipitate mark `^` / `v`,
`$...$` in the body, a concentration inside a state symbol `(aq, 0.10 M)`, a three-part arrow label,
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
   all: 94 are formulas in the cells of tables (tables are off, as in production; docs/tables.md),
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
  the primary (44 to 54), and `speech.py` already says "degrees" for both. It was not made: the
  converter is shared with every equation, and the real-world gold (made by the same converter,
  stored with `∘`) cannot be re-made, so
  it would lower a registered number to raise another. Open: decide with the real-world measure
  (docs/chemistry.md, "Side effect on maths that is not chemistry", has the method).
- **State symbols as three tokens** (`(` `l` `)`, as OpenStax) instead of the one `mtext (l)` the
  reading has: 7 more primary matches on OpenStax (54 to 61), no difference to a reader.
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
- **Three-part arrow labels**, `<=>>`, a precipitate mark, a state symbol with a concentration in
  `\ce`: refused, the picture stays with a decision. A two-part label is read aloud as "right arrow
  with 600 degrees Celsius below and electrolysis above", not as "reacts, with ..., to form".
- **The primary and symbols scorers reward the spelling of the gold**, so a change that is right
  for a reader (the group a bracket's script belongs to, an arrow as an operator) can lower them
  on gold made by the older converter. `eval/equations_chem.py` is the score for chemistry that
  does not ("Accuracy"); the primary stays as registered.
- **The prompt's one `\mathrm` example** (a state symbol with a concentration) is older than the
  `\text` shape the converter now writes; changing it changes the prompt's version and with it
  every recorded answer, so it waits for the next re-recording. The table prompt
  (`models/prompts/table.md`) does not teach `\ce` either, and `table_step` does not expand it.
- **Real screen readers were not run.** The reading is the review screen's string; what NVDA with
  MathCAT or VoiceOver says about this MathML (`mtext` element symbols in OpenStax's shape,
  grouped scripts) is the manual test of docs/mathml-alttext.md and was not done.

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
