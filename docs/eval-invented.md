# Invented-content rate

How much of a system's output says something the source PDF does not. It works on any
output: a11y-stem HTML, a chatbot's HTML, a PDF parser's Markdown. Code:
`src/readablestem/eval/invented/`; command: `a11y-stem eval invented ...`.

The rules are the table in section 7.5 of the held-out pre-registration. This page says how
each rule is read where that table leaves room, what was added next to it, what it found on
the development material, and what it cannot see. The numbers below are from development
material only (OpenStax Physics 2.2-2.3 and eight small test documents). They are not
held-out numbers and are not evidence about how a system does on other documents.

## What counts as invented

The reference R is the PDF's own text layer (PyMuPDF words, every page, including the
running footer and attribution the print adds), folded to lower case, NFKC, ligatures and
quotes folded, runs of `[a-z0-9]`. A unit of the output is *unsupported* when:

| Type | Unit | Unsupported when | Deterministic |
|---|---|---|---|
| Prose sentence | a sentence of 7+ words in prose outside maths, headings, tables, figure descriptions | fewer than 25% of its word 4-grams are in R | yes |
| Number | each number in prose and headings, outside maths | it is not among R's numbers (minus signs, thousands separators, 0.50 = 0.5) | yes |
| Heading | each output heading | no run of up to 3 consecutive lines of R matches it, difflib ratio 0.85 | yes |
| Equation, aligned to a gold equation | | its letters, digits and Greek letters hold a symbol more often than the gold equation does | yes |
| Equation, no gold partner, 3+ symbols | | no window of R's symbol stream of the same length matches, difflib ratio 0.8 | yes |
| Table cell | each non-empty cell | a number in it is not in R, or 3+ words and under half its word bigrams are in R | yes |
| Figure description | each alt text, `<figure>` text, long description | the image-aware judge sets `factual_error` | no: a model, on a seeded sample |

Everything above the last row makes no model call. The last row reuses the figure judge
(`eval/judge.py`, prompt `judge_figure`): it sees the description, the OpenStax alt text when
the gold has one, the caption and the image, and never which system wrote the description.
Its rate is agreement with a model reading the image, not accuracy; `eval calibrate`
measures the judge against human labels.

### Readings where the table leaves room

- **Maths is cut out of a sentence.** A sentence's 4-grams are taken inside the stretches
  between maths spans, so no 4-gram spans a gap. A sentence with no 4-gram inside any
  stretch falls back to the 4-grams of its words run together. (The earlier chatbot
  analysis read the flattened text of a MathML equation as a sentence: 134 of its 918 flagged
  "sentences" were equations. See the comparison below.)
- **Numbers** are read from every prose block and heading, not only from sentences of 7 words.
- **TeX** in a chatbot's HTML is converted with a11y-stem's own cleaner and converter
  (`equation_step.clean_latex`, `_convert`) and macros the page defines are expanded. A span
  that does not convert is counted as *unchecked*, never as correct.
- **Alignment** of output equations to gold equations is the document task's (weighted LCS on
  the MathML signature, threshold 0.5, reading order). With no gold directory every equation
  is "no gold partner".
- **Text a11y-stem wrote itself** (elements with class `a11y-stem`: the "About this version"
  section, notices, labels) is scored like any other text and counted apart. Every table has
  two views: all text (the headline for an output shown to a reader, because a reader reads
  the notes too) and without the tool's own text.
- **Figure descriptions with a data table** (cells of a table inside a description) are their
  own type, `description_cell`, so a figure's table is not scored as a document table.

### Added next to the rule: a kept equation number

A chatbot often keeps the number the print sets beside an equation ("2.1") inside the
equation. The gold equation has no such number, so the aligned-equation rule flags a symbol
that is in the PDF. The rule as registered is unchanged and stays the primary number. Next
to it, the report gives the same rate "less a kept equation number": an equation whose only
added symbols are the digits of a trailing `(n)`, `(n.m)` or `n.m` that R prints
(`score.equation_tag`). On the development runs this removed all 8 flagged equations of one
arm (sol, expert prompt: 36.4% as registered, 0.0% without kept numbers) and none in the
others. It could hide a real trailing number that the PDF also prints elsewhere; the audit's
labels for the aligned stratum measure that. If the registered rule is replaced by this
one, that needs a v2 of the pre-registration before the run.

### Intervals

Rates are pooled by sums over sections (never means of section rates). Two intervals: the
cluster bootstrap by section (resample sections with replacement, B = 10,000, NumPy
`default_rng(0)`) and the Wilson interval, which treats every unit as independent and is
narrower when units in a section are alike. The cluster interval needs 2 or more sections
that have units of that type; with fewer, it prints `n/a` (a resample of one section would
give a zero-width interval that means nothing). A paired difference between two systems
resamples the same sections for both. Figure-description rates carry the Wilson interval
only.

## Run it

```
# one output (no model call, no key); --units writes the text of every unit to the JSON
a11y-stem eval invented score --pdf section.pdf --output out.html --system a11y-stem \
    --section 5-4 --run 1 --gold data/gold/... --pages <page slugs> --units --out rep.json

# pool reports into rates with intervals; --compare A B gives a paired difference
a11y-stem eval invented summary rep-*.json --out summary.json

# figure descriptions: a seeded sample, image-aware judge, hard spend cap
a11y-stem eval invented judge --output out.html --gold ... --pages ... --images <cache> \
    --sample 30 --max-usd 0.5 --out judge.json

# the audit: a blind sheet and its key, then precision and recall from the labelled sheet
a11y-stem eval invented audit-sheet --reports rep-*.json --pdf-dir pdfs/ --sheet s.csv --key k.json
a11y-stem eval invented audit-score --key k.json --sheet s.csv --out audit.json

# plant inventions by hand and see whether they are found (the development check)
a11y-stem eval invented selfcheck --pdf p.pdf --output out.html --plan plan.json

# the SHA-256 of the rules' code and thresholds
a11y-stem eval invented fingerprint
```

Real model calls need `--max-usd` (or `--fake`) and the usual key. A report carries the
SHA-256 of the package's code and the thresholds (`fingerprint`), so "the code is frozen
before the run" is one comparison. Reports hold counts, rates, hashes and ids only; the
text of flagged units is written only with `--units`, to the file you name, never to a log.

## The audit

The rules are blunt, so the pre-registration measures them. Per system and type, up to 50
flagged units and up to 50 unflagged units are sampled (seeded) onto a sheet with no system
name and no hint of which the rule flagged, in a seeded random order, with the nearest line
of the PDF beside each unit. A person writes `invented`, `supported` or `unsure`. From the
labels: precision (invented among flagged), the miss rate (invented among unflagged), recall
and the audited invented rate from both, with Wilson intervals for precision and miss rate
and a bootstrap for recall and the audited rate. The raw rate and the audited numbers are
published together. Nothing but the person's labels is used: there is no model labeller.
The sheet and key commands ran on development outputs (611 units); no labels exist, so there
is no precision or recall to report yet.

## Development check, 2026-10-13

Data: saved chatbot runs from earlier development work, kept outside this repository (gpt-6-luna and gpt-6.1-sol, three
prompts, 8 input PDFs: Physics 2.2-2.3 and 7 small authored documents, 40 PDF pages; some
arms have 2 or 3 runs), and a11y-stem (pipeline 0.4.0, commit 293160e) run once on the same
8 PDFs (default flags).
The numbers are in `data/eval/invented-dev-2026-10-13.json` (counts only, no text), with the
fingerprint of the code that produced them; `eval invented fingerprint` must print the same
value for them to be reproduced.

### a11y-stem against the chatbots (units unsupported / units, all text)

| System | Sentences | Numbers | Headings | Equations, aligned | Equations, no gold partner | Table cells |
|---|---|---|---|---|---|---|
| a11y-stem, all text | 45 / 436 | 0 / 274 | 8 / 132 | 0 / 22 | 0 / 59 | none |
| a11y-stem, without its own text | 0 / 391 | 0 / 274 | 0 / 124 | 0 / 22 | 0 / 59 | none |
| luna, expert prompt | 30 / 419 | 5 / 288 | 1 / 121 | 0 / 21 | 1 / 65 | 0 / 104 |
| luna, naive prompt | 77 / 269 | 1 / 249 | 20 / 78 | none | 0 / 43 | 0 / 107 |
| luna, short WCAG prompt | 61 / 248 | 1 / 174 | 16 / 88 | 5 / 17 | 4 / 67 | 0 / 95 |
| sol, expert prompt | 38 / 425 | 0 / 210 | 0 / 119 | 8 / 22 (0 / 22 without kept numbers) | 25 / 248 | 1 / 95 |
| sol, naive prompt | 595 / 983 | 16 / 673 | 125 / 234 | none | 0 / 45 | 78 / 360 |
| sol, short WCAG prompt | 211 / 1,017 | 6 / 579 | 93 / 263 | 12 / 59 | 17 / 104 | 26 / 227 |

Read with care. Gold equations exist only for Physics 2.2-2.3, so "aligned" counts come
from one document and have no cluster interval ("n/a": fewer than 2 sections with units).
Some arms ran twice or three times on a document; units are pooled. The sentence rate of the
naive and WCAG-prompt arms is mostly text they added (study aids, "In words:" readings of
equations, accessibility notes, a contents list), which is what the metric is for, but a
share of it is faithful paraphrase that the rule counts as unsupported (a known false
positive). The audit, not this table, says what share. Wilson intervals for the sentence
rate are, for example, a11y-stem all text 7.8 to 13.5%, sol naive 57.4 to 63.5%; the cluster
intervals over 8 sections are far wider (a11y-stem 2.8 to 51.0%, sol naive 12.3 to 63.6%)
because the rate depends heavily on the document. With 8 documents this development set
cannot rank the arms.

What a11y-stem's 45 flagged sentences and 8 flagged headings are: every one is text the
tool wrote itself (the "About this version" section: 4 fixed sentences, 1 heading; and
notices such as "This table is shown as an image of the original..."). Text copied from the
PDF: 0 of 391 sentences, 0 of 274 numbers, 0 of 124 headings, 0 of 22 aligned equations
(0 symbols added in 339), 0 of 59 other equations (47 more had under 3 symbols and were not
checked). The Wilson 95% upper bounds on the content-only sentence and heading rates are
1.0% and 3.0%.

### Inventions planted by hand

`data/eval/invented-plants-physics-2-2-2-3.json` holds inventions written by hand: 20
sentences, 6 headings, 5 equations, 6 numbers changed in copied sentences, 6 words changed
in copied sentences, and a symbol added to each of 5 real equations. They were planted in
the sol/expert run on Physics 2.2-2.3 (62 units flagged before planting):

```
a11y-stem eval invented selfcheck --pdf long_2-2_2-3.pdf --output sol-expert.html \
    --plan data/eval/invented-plants-physics-2-2-2-3.json --gold data/gold/openstax/physics \
    --pages 2-2-speed-and-velocity,2-3-position-vs-time-graphs
```

| Planted | Number | Found | Recall |
|---|---|---|---|
| sentence | 20 | 20 | 100% |
| heading | 6 | 6 | 100% |
| equation (no gold partner) | 5 | 5 | 100% |
| number changed in a copied sentence | 6 | 6 | 100% |
| word changed in a copied sentence | 6 | 0 | 0% |
| symbol added to a real equation | 5 | 5 | 100% |

No unit other than the planted ones became flagged. The same 20 sentences, 6 headings and 5
equations planted in a11y-stem's output on the same document were found 31 of 31. These are
easy cases written by the person who built the rules: they show the rules work as
specified, not how often they are right on a system's own mistakes. Two numbers and one
heading first planted coincided with text the PDF prints; they were replaced before the
figures above (the check reports such numbers as `numbers_the_pdf_prints`).

The 0 of 6 is the blind spot, and it happens outside the plan too. One of the sol outputs
(short WCAG prompt) reads "A scalar quantity is fully described by its magnitude and
direction, while a vector needs only magnitude": the PDF's sentence with scalar and vector
swapped. Every 4-gram is in R, so the sentence is not flagged.

### Figure descriptions

| Descriptions | Judged | Factual error | Wilson 95% | Judge cost |
|---|---|---|---|---|
| a11y-stem, Physics 2.2-2.3 | 9 of 9 | 0 | 0 to 29.9% | $0.0377 |
| sol/expert chatbot, same document (7 of 9 had an image) | 7 of 7 | 0 | 0 to 35.4% | $0.0227 |
| a11y-stem with 4 of 9 alt texts replaced by wrong ones (a bar chart for a map, a sailboat for a jet car, a rising line made falling, a falling one made rising) | 9 of 9 | 4 (the 4 planted) | 18.9 to 73.3% | $0.0207 |

About $0.003 per judged description (gpt-6.1-sol, list price). 30 descriptions per system
cost about $0.10. The judge was not calibrated against human labels for this check; the
held-out run does that (`eval calibrate`, kappa with a bootstrap interval).

### Against the earlier rule

The earlier chatbot analysis (`analyze.invented_sentences`, PDF text from `pdftotext`) flagged
918 "sentences" in the 45 main runs; this rule flags 547. 518 are flagged by both, 400 only
by the earlier rule, 29 only by this one. Of the 400, 134 are mostly one- and two-character
tokens (a flattened equation, now checked as an equation), and the rest include the
print's attribution lines (in R now) and figure descriptions (now their own type). Of the 29,
many are sentences about a figure with the symbols cut out ("The horizontal axis points right
and is labeled ;"), which are descriptions the chatbot wrote as ordinary paragraphs.

### Parser Markdown

The Markdown reader was run on the Docling (default and formula), MinerU and PyMuPDF4LLM
output for 3 development pages: no prose, number or heading flagged (they copy the text
layer), MinerU 5 equations and Docling with formulas 3 equations checked, none flagged.
Docling's default setting keeps equations as pictures, so it has none to check.

## What it cannot see

- A word changed inside a sentence that keeps a quarter of its 4-grams (measured above: 0 of
  6 planted; one real example). A changed number is caught; a changed word, a swapped
  scalar and vector, a flipped "increasing" are not.
- A sign flipped in an equation that has no gold partner and still matches R's symbol
  stream at ratio 0.8. Aligned equations count symbols, so a changed sign is not an added
  symbol either; only an added letter, digit or Greek letter is.
- Faithful paraphrase and re-ordering (counted as unsupported), and an equation read out in
  words ("In words: velocity equals ...") which says what the PDF says in other words.
- Numbers read off a graph. R holds what is printed; an axis value that exists only in the
  picture is unsupported in R, and a correct description of a figure is never supported by
  R. That is why figures use the judge.
- A correct claim the PDF does not state (a true fact the chatbot adds) is invented by
  definition here. The metric measures fidelity to the source, not truth.
- TeX that does not convert and MathML that does not parse are counted as unchecked, so a
  high unchecked count means the equation rates describe fewer equations.
- Table cells are checked by number and wording only; whether a cell is in the right row or
  column is the table task's job.

## Files

- `src/readablestem/eval/invented/`: `text.py` (R and the normalisations), `read.py` (HTML and
  Markdown into units), `score.py` (the rules), `pool.py` (rates and intervals),
  `figures.py` (the sampled judge), `audit.py` (sheet, key, precision and recall),
  `plant.py` (planted inventions), `report.py`, `cli.py`.
- `data/eval/invented-plants-physics-2-2-2-3.json`: the inventions planted by hand.
- `data/eval/invented-dev-2026-10-13.json`: the development numbers above (counts only).
- `tests/test_eval_invented_*.py`: offline tests of each part.
