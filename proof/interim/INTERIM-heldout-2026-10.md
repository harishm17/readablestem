# Held-out results that need no model call (interim)

**What this is.** The two parts of the held-out run that need no model call: ReadableSTEM's heading detection (the headings task, run once) and the open parsers (CPU, one process per section, 30-minute limit). ReadableSTEM's equation, figure and whole-document runs and the chatbot arms need paid model calls, which could not be made (the API account had no credit), so **they do not exist and nothing about them is claimed here**. No number below compares ReadableSTEM's equation transcription with anything. Held-out answer key: registered, manifest `9be8c5650fe492d0c5312199b3040aa6ea741595d5ba97df4b68e816455c9aa3`; 15 sections, 242 Letter pages, 509 gold headings, 1,369 gold equations. Inputs are OpenStax retired first editions (CC BY 4.0), attribution in each PDF; figures inside them keep the credits and licences OpenStax gives them.

**The PDFs are printed by this project's own stylesheet.** Every system was given the same PDF bytes, but they were not the publisher's PDFs: the sections were printed from OpenStax's HTML by `readablestem.eval.render` (its `PRINT_CSS`, Liberation Sans, bordered boxes and bold labels for examples, solutions and notes). That stylesheet was written for ReadableSTEM's own tests, and the headings rules were developed on pages it printed. A result that depends on how a box is drawn may not carry over to a publisher's PDF; see the heading tables below.

**Reading the numbers.** `value [cluster 95% interval by section]`: cluster bootstrap by section, B = 10,000, seed 0; with 15 sections it is approximate and tends to be a little narrow. Parser timings were taken on a shared machine whose load average was above 1.5 for most runs (the harness was told not to wait for a quiet one): they are upper bounds. A perfect transcription is not scored 1.0 by this scorer (a hand-written correct sample of 40 gold equations scored 28 of 40; `proof/scorer-readiness/`), and the scorer also charges a tool for how it writes LaTeX; the descriptive row below shows how much.

**Which settings.** Docling ran in its default settings, where a formula is not decoded and no LaTeX is written. The registration also planned a Docling arm with formula recognition switched on (Addendum 1, D2: 30 minutes per section, a section that does not finish scores as found nothing). It was started, stopped by hand after one section (11 pages, 1,495 s) and **not carried out** (Addendum 4), so there is no Docling formula number and Docling's 0.0000 on equations says only that its defaults write none. MinerU ran at its `basic` tier on CPU. Its own documentation names `standard` as the default tier (also CPU-capable), so **mineru-basic is not MinerU at its default settings.** Any number for MinerU here is for the basic tier. PyMuPDF4LLM ran with its layout model and defaults.

**The invented-content rows are not audited.** They count units (sentences, numbers) that the PDF's text layer does not contain. The rule's precision and recall have not been measured (the blind audit of Preregistration 7.5 has not been done), it was validated on three physics pages only, and on Markdown from a parser it counts things that are not invented content: see the table 'What the flagged units are' below. Read the rows as a text-layer mismatch, not as an invention rate.

### Headings: ReadableSTEM against the parsers

**Headings (preregistration 7.2)**

| Metric | ReadableSTEM (headings task) | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|---|
| heading F1 | 0.9613 [0.9474, 0.9772] | 0.9104 [0.8418, 0.9576] | 0.9228 [0.8937, 0.9547] | 0.8297 [0.7384, 0.9185] |
| heading precision | 0.9467 [0.9272, 0.9703] | 0.8668 [0.7599, 0.9444] | 0.9558 [0.9359, 0.9741] | 0.8265 [0.7284, 0.9258] |
| heading recall | 0.9764 [0.9564, 0.9935] | 0.9587 [0.9338, 0.9805] | 0.8919 [0.8480, 0.9452] | 0.8330 [0.6753, 0.9706] |
| heading level_accuracy (among matched) | 1.0000 [1.0000, 1.0000] | 0.0307 [0.0238, 0.0421] | 0.1652 [0.0522, 0.3262] | 1.0000 [1.0000, 1.0000] |

**Counts**

| Count | ReadableSTEM (headings task) | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|---|
| heading F1 | 497/525 pred, 497/509 gold | 488/563 pred, 488/509 gold | 454/475 pred, 454/509 gold | 424/513 pred, 424/509 gold |
| heading precision | 497/525 | 488/563 | 454/475 | 424/513 |
| heading recall | 497/509 | 488/509 | 454/509 | 424/509 |
| heading level_accuracy (among matched) | 497/497 | 15/488 | 75/454 | 424/424 |

**Heading F1: ReadableSTEM against each parser**

| Measure | Against | ReadableSTEM | Other | Difference (ReadableSTEM minus other) | Cluster 99.375% (k = 8): the claim | Cluster 95% | Sign-flip p | Result |
|---|---|---|---|---|---|---|---|---|
| heading F1 | docling | 0.9613 | 0.9104 | 0.0509 | [-0.0052, 0.1481] | [0.0043, 0.1173] | 0.0186 | **no difference this set can detect** |
| heading F1 | mineru-basic | 0.9613 | 0.9228 | 0.0386 | [0.0047, 0.0789] | [0.0120, 0.0670] | 0.0073 | **ReadableSTEM is higher by point estimate: the registered interval excludes 0, the sign-flip test does not (p = 0.0073)** |
| heading F1 | pymupdf4llm | 0.9613 | 0.8297 | 0.1316 | [0.0292, 0.2706] | [0.0509, 0.2229] | 0.0088 | **ReadableSTEM is higher by point estimate: the registered interval excludes 0, the sign-flip test does not (p = 0.0088)** |

Paired cluster bootstrap by section. k = 8: the registration's rule of 2 comparisons per parser arm with four arms (Addendum 1, D3), although only the heading half can be made. The registered rule writes "X is better" when the Bonferroni interval excludes 0; this report adds the sign-flip test and writes "higher by point estimate" when the two do not agree. Heading level is compared relative to the section's top level; Docling and MinerU write every heading at one level in their Markdown export, which is a property of that export. Sign-flip p: two-sided p-value of a paired sign-flip permutation test on the per-section differences, a robustness check that is not part of the registered rule; the Bonferroni threshold for it is 0.00625. With fewer than 10 sections no verdict is written (a bootstrap over so few sections only returns the sections' own extremes), and a "better" that the sign-flip test does not support is written as "higher by point estimate".

**Heading recall: free-standing headings and headings inside a box (descriptive)**

| Where the gold heading sits | ReadableSTEM | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|---|
| Free-standing (no enclosing box) | 0.9636 (106/110) | 1.0000 (110/110) | 0.9727 (107/110) | 0.9727 (107/110) |
| Inside a box (example, solution, note, exercise, ...) | 0.9799 (391/399) | 0.9474 (378/399) | 0.8697 (347/399) | 0.7945 (317/399) |

Recall = gold headings found / gold headings, pooled over the 15 sections, no interval. This split was made after the results were known; it shows where the difference in recall in the registered table comes from.

**Extra headings that are only a number, and heading F1 without them (descriptive)**

| System | Headings that match no gold heading | of them only a number (an exercise number, say) | Heading F1 (registered row) | Heading F1 if those are not counted as written |
|---|---|---|---|---|
| ReadableSTEM | 28 | 0 | 0.9613 | 0.9613 |
| docling | 75 | 41 | 0.9104 | 0.9467 |
| mineru-basic | 21 | 1 | 0.9228 | 0.9237 |
| pymupdf4llm | 89 | 62 | 0.8297 | 0.8833 |

Docling and PyMuPDF4LLM write a bare exercise number such as `142.` as a heading; the gold has none. The last column is a sensitivity check made after the results were known, not a rule.

### Review load and heading flags (ReadableSTEM)

**ReadableSTEM's review screen on headings (headings task, registered metrics 7.4)**

| Measure | Value |
|---|---|
| PDF pages | 242 |
| Required decisions (one per group of fixes) | 29 |
| Required fixes (several can share one decision) | 115 |
| **Required decisions per PDF page** | **0.1198** |
| Optional notes per PDF page | 0.4711 |
| Wrong headings (predicted, not in the answer key) | 28 |
| Wrong headings a required decision pointed at | 0 of 28 |
| Required decisions that pointed at a wrong heading | 0 of 29 |
| **Heading flags: precision** | **0.0000** |
| **Heading flags: recall** | **0.0000** |
| Wrong headings not asked about (wrong_not_required) | 28 |
| Precision of headings left accepted / sent to review | 0.9317 (n = 410) / 1.0000 (n = 115) |

Flag precision is the share of required decisions that point at a wrong heading; recall is the share of wrong headings a person is asked about. All 28 wrong headings were left in the page as accepted headings (28 accepted, optional note). On this set the review screen caught none of the heading errors the answer key shows. On the development sample (PROOF.md, document task) the same two measures were precision 0.5556 and recall 0.7778; that sample is the one the rules were tuned on, so it is not evidence for the held-out case. The 115 headings sent to review were all right: the screen asks about repeated labels, which were not where the errors were.

### Parsers on equations, invented content, coverage

**Equations (7.1)**

| Metric | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|
| end_to_end_exact_lenient (all gold equations) | 0.0000 [0.0000, 0.0000] | 0.1738 [0.1081, 0.2177] | 0.0000 [0.0000, 0.0000] |
| end_to_end_exact_lenient, without G1 equations | 0.0000 [0.0000, 0.0000] | 0.1802 [0.1148, 0.2235] | 0.0000 [0.0000, 0.0000] |
| unflagged_wrong_per_100 | 0.00 [0.00, 0.00] | 27.61 [22.15, 34.74] | 0.00 [0.00, 0.00] |
| matched_exact_lenient (aligned pairs only) | n/a | 0.3679 [0.2400, 0.4420] | n/a |
| detection_recall | 0.0000 [0.0000, 0.0000] | 0.4726 [0.3810, 0.5529] | 0.0000 [0.0000, 0.0000] |
| detection_precision | n/a | 0.5488 [0.3798, 0.6914] | n/a |
| end_to_end_exact_lenient, allowlist off (descriptive row) | 0.0000 [0.0000, 0.0000] | 0.1709 [0.1070, 0.2132] | 0.0000 [0.0000, 0.0000] |
| end_to_end_exact_lenient, markup normalised (descriptive row) | 0.0000 [0.0000, 0.0000] | 0.1972 [0.1325, 0.2405] | 0.0000 [0.0000, 0.0000] |

**Invented content (7.5): text-layer mismatch, rule unaudited**

| Metric | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|
| invented prose sentences (text-layer mismatch, rule unaudited) | 0.0086 [0.0009, 0.0207] | 0.0078 [0.0005, 0.0188] | 0.0299 [0.0150, 0.0465] |
| invented numbers (text-layer mismatch, rule unaudited) | 0.0138 [0.0038, 0.0237] | 0.0009 [0.0000, 0.0024] | 0.0224 [0.0067, 0.0424] |
| headings the PDF does not support | 0.0000 [0.0000, 0.0000] | 0.0000 [0.0000, 0.0000] | 0.0000 [0.0000, 0.0000] |
| aligned equations with an added symbol | n/a | 0.2643 [0.2049, 0.3209] | n/a |
| equations with no gold partner and no match in the PDF | n/a | 0.1397 [0.0386, 0.2593] | n/a |
| prose sentences not in the text layer, HTML comments removed (descriptive row) | 0.0086 [0.0009, 0.0207] | 0.0078 [0.0005, 0.0188] | 0.0163 [0.0088, 0.0249] |

**What the flagged units are (descriptive, computed from the same units)**

| Parser | Flagged numbers | of them negative (a minus sign) | of them with a sign, bracket, operator or spaced digits in context |
|---|---|---|---|
| docling | 42 | 13 | 35 |
| mineru-basic | 2 | 0 | 2 |
| pymupdf4llm | 116 | 25 | 107 |

`prose sentences not in the text layer, HTML comments removed` takes the Markdown reader's view with `<!-- ... -->` removed first: PyMuPDF4LLM wraps a boxed region in `<!-- Start of picture text -->` and its words otherwise count as prose. A negative flagged number is mostly an exponent: the PDF's text layer holds `10−14` with no space, which the number rule reads as 14, and a Markdown `10 −14` reads as -14. The last column is a loose pattern on the unit's context, not a classification by a person. The registered rows above are unchanged.

**Equations the tool wrote as TeX or MathML (the answer key has 1,369)**

| Parser | Written |
|---|---|
| docling | 0 |
| mineru-basic | 1,179 |
| pymupdf4llm | 0 |

**Counts behind the rates**

| Count | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|
| end_to_end_exact_lenient (all gold equations) | 0/1,369 | 238/1,369 | 0/1,369 |
| end_to_end_exact_lenient, without G1 equations | 0/1,321 | 238/1,321 | 0/1,321 |
| unflagged_wrong_per_100 | 0/1,369 | 378/1,369 | 0/1,369 |
| matched_exact_lenient (aligned pairs only) | 0/0 | 238/647 | 0/0 |
| detection_recall | 0/1,369 | 647/1,369 | 0/1,369 |
| detection_precision | 0/0 | 647/1,179 | 0/0 |
| end_to_end_exact_lenient, allowlist off (descriptive row) | 0/1,369 | 234/1,369 | 0/1,369 |
| end_to_end_exact_lenient, markup normalised (descriptive row) | 0/1,369 | 270/1,369 | 0/1,369 |
| invented prose sentences (text-layer mismatch, rule unaudited) | 18/2,090 | 16/2,046 | 73/2,439 |
| invented numbers (text-layer mismatch, rule unaudited) | 42/3,054 | 2/2,160 | 116/5,179 |
| headings the PDF does not support | 0/563 | 0/475 | 0/513 |
| aligned equations with an added symbol | 0/0 | 171/647 | 0/0 |
| equations with no gold partner and no match in the PDF | 0/0 | 57/408 | 0/0 |
| prose sentences not in the text layer, HTML comments removed (descriptive row) | 18/2,090 | 16/2,046 | 39/2,400 |
| coverage: PDF word 6-grams found in the output | 32,906/45,467 | 30,656/45,467 | 36,369/45,467 |

**Coverage (7.6)**

| Metric | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|
| coverage: PDF word 6-grams found in the output | 0.7237 [0.6487, 0.7897] | 0.6742 [0.6004, 0.7418] | 0.7999 [0.7610, 0.8368] |

**Figures (7.3): a parser writes no description, so every figure fails**

| Metric | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|
| figure judge_pass_rate (no description = fail) | 0.0000 [0.0000, 0.0000] | 0.0000 [0.0000, 0.0000] | 0.0000 [0.0000, 0.0000] |
| figures with no description | 1.0000 [1.0000, 1.0000] | 1.0000 [1.0000, 1.0000] | 1.0000 [1.0000, 1.0000] |

**Wilson 95% (items treated as independent)**

| Wilson 95% (items treated as independent) | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|
| end_to_end_exact_lenient (all gold equations) | [0.0000, 0.0028] | [0.1547, 0.1948] | [0.0000, 0.0028] |
| end_to_end_exact_lenient, without G1 equations | [0.0000, 0.0029] | [0.1604, 0.2018] | [0.0000, 0.0029] |
| matched_exact_lenient (aligned pairs only) | n/a | [0.3316, 0.4057] | n/a |
| detection_recall | [0.0000, 0.0028] | [0.4463, 0.4991] | [0.0000, 0.0028] |
| detection_precision | n/a | [0.5203, 0.5770] | n/a |
| end_to_end_exact_lenient, allowlist off (descriptive row) | [0.0000, 0.0028] | [0.1519, 0.1918] | [0.0000, 0.0028] |
| end_to_end_exact_lenient, markup normalised (descriptive row) | [0.0000, 0.0028] | [0.1770, 0.2191] | [0.0000, 0.0028] |
| invented prose sentences (text-layer mismatch, rule unaudited) | [0.0055, 0.0136] | [0.0048, 0.0127] | [0.0239, 0.0375] |
| invented numbers (text-layer mismatch, rule unaudited) | [0.0102, 0.0185] | [0.0003, 0.0034] | [0.0187, 0.0268] |
| headings the PDF does not support | [0.0000, 0.0068] | [0.0000, 0.0080] | [0.0000, 0.0074] |
| aligned equations with an added symbol | n/a | [0.2318, 0.2996] | n/a |
| equations with no gold partner and no match in the PDF | n/a | [0.1094, 0.1767] | n/a |

**Sections that did not convert**

| System | Outputs | Expected | ok | truncated | empty | timeout | crashed | not run |
|---|---|---|---|---|---|---|---|---|
| docling | 15 | 15 | 15 | 0 | 0 | 0 | 0 | 0 |
| mineru-basic | 15 | 15 | 15 | 0 | 0 | 0 | 0 | 0 |
| pymupdf4llm | 15 | 15 | 15 | 0 | 0 | 0 | 0 | 0 |

A section that is not ok scores as found nothing: its gold items stay in every denominator.

**Time and memory per PDF page (CPU, shared machine, upper bounds)**

| System | Total API cost | Cost/page median | p95 | min | max | Seconds/page median | p95 | min | max | Peak memory (MB) |
|---|---|---|---|---|---|---|---|---|---|---|
| docling | $0 | n/a | n/a | n/a | n/a | 3.67345 | 7.93241 | 2.19993 | 8.67304 | 3327 |
| mineru-basic | $0 | n/a | n/a | n/a | n/a | 4.28371 | 13.79948 | 2.17400 | 15.34236 | 4235 |
| pymupdf4llm | $0 | n/a | n/a | n/a | n/a | 0.34993 | 0.44153 | 0.26852 | 0.45325 | 385 |

### What these numbers leave out

- ReadableSTEM's equations, figures and whole documents, and the chatbot arms, were not run: there is no held-out comparison on equations.
- Docling with formula recognition was not run to the registered rule; MinerU was run at the `basic` tier, not its documented default (`standard`). A one-off run of the `standard` tier on the same 15 PDFs, made after these results were known (not registered, outputs not committed), scored 266 of 1,369 equations exactly under the same scorer (0.194) and the same heading F1 (0.9228).
- The PDFs are printed by this project's stylesheet, not the publisher's.
- Heading F1 is the only comparison with a result. It rests on 15 sections of 5 books. A sign-flip test on the section differences is above the Bonferroni threshold for MinerU and PyMuPDF4LLM, so those two are differences in point estimate that the registered interval only just separates from 0.
- On free-standing headings ReadableSTEM is not ahead (0.964 recall against 0.973 to 1.000 for the parsers); its lead is in headings inside boxes, and a part of Docling's and PyMuPDF4LLM's deficit is bare exercise numbers that they write as headings (tables above).
- ReadableSTEM's review screen asked about none of the 28 wrong headings (table above).
- The invented-content rows are a text-layer mismatch from an unaudited rule, and are not comparable between a Markdown parser and HTML.
- The parser model weights are not pinned or hashed in the run records (`proof/parsers/WEIGHTS.md` lists what the cache held afterwards), and the runs were not offline.
- No screen-reader testing, no scans, no handwriting.
