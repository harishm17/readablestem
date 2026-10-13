# Held-out results that need no model call (interim)

**What this is.** The two parts of the held-out run that need no model call: a11y-stem's heading detection (the headings task, run once) and the open parsers (CPU, defaults, one process per section, 30-minute limit). a11y-stem's equation, figure and whole-document runs and the chatbot arms need paid model calls, which could not be made (the API account had no credit), so **they do not exist and nothing about them is claimed here**. No number below compares a11y-stem's equation transcription with anything. Held-out answer key: registered, manifest `e9e7e429a5ce20ebc36c4c313ea46a8754d8b47cad6d359ec5dfb7f14d899ceb`; 15 sections, 242 Letter pages, 509 gold headings, 1,369 gold equations. Inputs are OpenStax retired first editions (CC BY 4.0), attribution in each PDF.

**Reading the numbers.** `value [cluster 95% interval by section]`: cluster bootstrap by section, B = 10,000, seed 0; with 15 sections it is approximate and tends to be a little narrow. Parser timings were taken on a shared machine whose load average was above 1.5 for most runs (the harness was told not to wait for a quiet one): they are upper bounds. Docling in default settings and PyMuPDF4LLM write no LaTeX by design, so their equation numbers are 0 by construction: that says what the tools are built for, not how well they read maths. A perfect transcription is not scored 1.0 by this scorer (a hand-written correct sample of 40 gold equations scored 28 of 40; `proof/scorer-readiness/`).

### Headings: a11y-stem against the parsers

**Headings (preregistration 7.2)**

| Metric | a11y-stem (headings task) | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|---|
| heading F1 | 0.9613 [0.9474, 0.9772] | 0.9104 [0.8418, 0.9576] | 0.9228 [0.8937, 0.9547] | 0.8297 [0.7384, 0.9185] |
| heading precision | 0.9467 [0.9272, 0.9703] | 0.8668 [0.7599, 0.9444] | 0.9558 [0.9359, 0.9741] | 0.8265 [0.7284, 0.9258] |
| heading recall | 0.9764 [0.9564, 0.9935] | 0.9587 [0.9338, 0.9805] | 0.8919 [0.8480, 0.9452] | 0.8330 [0.6753, 0.9706] |
| heading level_accuracy (among matched) | 1.0000 [1.0000, 1.0000] | 0.0307 [0.0238, 0.0421] | 0.1652 [0.0522, 0.3262] | 1.0000 [1.0000, 1.0000] |

**Counts**

| Count | a11y-stem (headings task) | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|---|
| heading F1 | 497/525 pred, 497/509 gold | 488/563 pred, 488/509 gold | 454/475 pred, 454/509 gold | 424/513 pred, 424/509 gold |
| heading precision | 497/525 | 488/563 | 454/475 | 424/513 |
| heading recall | 497/509 | 488/509 | 454/509 | 424/509 |
| heading level_accuracy (among matched) | 497/497 | 15/488 | 75/454 | 424/424 |

**Heading F1: a11y-stem against each parser**

| Measure | Against | a11y-stem | Other | Difference (a11y-stem minus other) | Cluster 99.375% (k = 8): the claim | Cluster 95% | Result |
|---|---|---|---|---|---|---|---|
| heading F1 | docling | 0.9613 | 0.9104 | 0.0509 | [-0.0052, 0.1481] | [0.0043, 0.1173] | **no difference this set can detect** |
| heading F1 | mineru-basic | 0.9613 | 0.9228 | 0.0386 | [0.0047, 0.0789] | [0.0120, 0.0670] | **a11y-stem is better** |
| heading F1 | pymupdf4llm | 0.9613 | 0.8297 | 0.1316 | [0.0292, 0.2706] | [0.0509, 0.2229] | **a11y-stem is better** |

Paired cluster bootstrap by section. k = 8: the registration's rule of 2 comparisons per parser arm with four arms (Addendum 1, D3), although only the heading half can be made. "X is better" is written only when the Bonferroni interval excludes 0. Heading level is compared relative to the section's top level; Docling and MinerU write every heading at one level in their Markdown export, which is a property of that export.

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

**Invented content (7.5)**

| Metric | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|
| invented prose sentences | 0.0086 [0.0009, 0.0207] | 0.0078 [0.0005, 0.0188] | 0.0299 [0.0150, 0.0465] |
| invented numbers | 0.0138 [0.0038, 0.0237] | 0.0009 [0.0000, 0.0024] | 0.0224 [0.0067, 0.0424] |
| headings the PDF does not support | 0.0000 [0.0000, 0.0000] | 0.0000 [0.0000, 0.0000] | 0.0000 [0.0000, 0.0000] |
| aligned equations with an added symbol | n/a | 0.2643 [0.2049, 0.3209] | n/a |
| equations with no gold partner and no match in the PDF | n/a | 0.1397 [0.0386, 0.2593] | n/a |

**Coverage (7.6)**

| Metric | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|
| coverage: PDF word 6-grams found in the output | 0.7237 [0.6487, 0.7897] | 0.6742 [0.6004, 0.7418] | 0.7999 [0.7610, 0.8368] |

**Figures (7.3): a parser writes no description, so every figure fails**

| Metric | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|
| figure judge_pass_rate (no description = fail) | 0.0000 [0.0000, 0.0000] | 0.0000 [0.0000, 0.0000] | 0.0000 [0.0000, 0.0000] |
| figures with no description | 1.0000 [1.0000, 1.0000] | 1.0000 [1.0000, 1.0000] | 1.0000 [1.0000, 1.0000] |

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
| invented prose sentences | 18/2,090 | 16/2,046 | 73/2,439 |
| invented numbers | 42/3,054 | 2/2,160 | 116/5,179 |
| headings the PDF does not support | 0/563 | 0/475 | 0/513 |
| aligned equations with an added symbol | 0/0 | 171/647 | 0/0 |
| equations with no gold partner and no match in the PDF | 0/0 | 57/408 | 0/0 |
| coverage: PDF word 6-grams found in the output | 32,906/45,467 | 30,656/45,467 | 36,369/45,467 |

**Wilson 95% (items treated as independent)**

| Wilson 95% (items treated as independent) | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|
| end_to_end_exact_lenient (all gold equations) | [0.0000, 0.0028] | [0.1547, 0.1948] | [0.0000, 0.0028] |
| end_to_end_exact_lenient, without G1 equations | [0.0000, 0.0029] | [0.1604, 0.2018] | [0.0000, 0.0029] |
| matched_exact_lenient (aligned pairs only) | n/a | [0.3316, 0.4057] | n/a |
| detection_recall | [0.0000, 0.0028] | [0.4463, 0.4991] | [0.0000, 0.0028] |
| detection_precision | n/a | [0.5203, 0.5770] | n/a |
| end_to_end_exact_lenient, allowlist off (descriptive row) | [0.0000, 0.0028] | [0.1519, 0.1918] | [0.0000, 0.0028] |
| invented prose sentences | [0.0055, 0.0136] | [0.0048, 0.0127] | [0.0239, 0.0375] |
| invented numbers | [0.0102, 0.0185] | [0.0003, 0.0034] | [0.0187, 0.0268] |
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
