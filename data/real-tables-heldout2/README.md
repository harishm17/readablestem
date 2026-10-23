# Second fresh real-document table set (held out)

Ten openly licensed arXiv documents with tables, for a second measurement of table detection on
documents no earlier measurement or tuning has seen (`readablestem eval pipeline --task tables --mode
local --real-dir data/real-tables-heldout2`; docs/tables.md, "Second held-out measurement").
`data/real-tables` and `data/real-tables-heldout` are development sets now; nothing in this
directory was run through the table detector, or looked at for tuning, before this file, the PDFs,
`labels.json` and `REGISTRATION.sha256` were committed.

## Pre-registration (written before detection was run on any of these files)

**The question.** Does the table detector of `fix/tables-round2` find real tables in documents from
statistics, computer science, economics and engineering at the project's floors, and does it do
better on them than `main` at the branch point?

**Floors**, the project's own (docs/eval.md "Real documents"): precision >= 0.95 and full-extent
recall >= 0.85, point estimates over all labelled pages of all ten documents, scored by the same
code as the other sets (`eval/tables.score_real_document`: a prediction is true when it shares at
least half of the smaller box with a label; a label is found in full when a prediction covers 90%
of it and at least 75% of that prediction lies in it). Wilson 95% intervals are reported next to
them.

**The comparison.** Two arms, each run once, on the same files, free and without a model:

- `main` at the branch point, `8555f6c` (Merge pull request #25);
- `fix/tables-round2` at `433b729` (the last commit that changes `src/`; the commit that adds this
  directory changes no `src/` file).

The result is reported whatever it shows. Any change to the detector made after reading the
result is development work, is labelled so, and its numbers are not held-out numbers. The registered
run's per-table outcomes are committed (`result-<date>.json`) before any such change.

**How the documents were chosen** (`scripts/tables_heldout2.py`; `selection.json` has every
document visited and why each skipped one was skipped; `pool-windows.json` the harvest):

1. Candidates are arXiv records under CC BY 4.0, CC BY 3.0 or CC0 (read from the OAI-PMH record) of
   the real-world corpus pool (docs/eval-realworld.md) and of 20 further low-volume sets and windows
   (`WINDOWS` in the script, fixed before any record was looked at): statistics, computer science,
   economics, biology, engineering and optimisation sets, 21,615 records. Papers of `physics.ed-ph`
   are left out (the first set was made of them).
2. Every paper any earlier work looked at is left out (330 ids: the real-world manifests and
   candidate logs, the real-world cache, `data/real-tables`, `data/real-tables-heldout` and its
   selection walk; SHA-256 of the sorted ids joined by newlines
   `719f3ad85a948cda246c7db46424beb92f26cb61ee53b0b800f771f3961d49d0`).
3. Each remaining paper gets a stratum from its primary category (statistics, computer science,
   economics, biology, engineering, mathematics) and a tier: *teaching* when lecture, tutorial,
   primer, introduction to, introductory, handout, textbook, handbook, "a guide", "guide to",
   pedagogical, teaching, "a gentle", "this course" or "course on" (the full pattern is `TEACHING`)
   occurs in the title, the arXiv comments or the first 800 characters of the abstract; *other*
   otherwise. 278 teaching candidates, 25,381 others.
4. Walk the teaching tier, then the other tier only if the first gave too few: one paper from each
   stratum in turn (statistics, computer science, economics, biology, engineering, mathematics),
   each stratum in the order of SHA-256(`tables-heldout2-2026-10-22:` + arXiv id). For each: download
   the PDF and the source (one request every three seconds) and accept it when it has a main TeX
   file, 6 to 250 PDF pages and at least three data tables found in the PDF's words by
   `gold/tabulars.py`. Stop at ten accepted. The walk stopped at ten inside the teaching tier (32
   visited: 10 accepted, 17 with fewer than three tables found, 5 whose source is itself a PDF); the
   other tier was not used, and the biology and mathematics strata gave no document.

The teaching words are matched in the abstract too, so several accepted documents are surveys,
reviews or research articles that say "introduction", "guide" or "tutorial" somewhere, not lecture
notes: 2202.07104 (review), 2407.05381 (survey), 2401.05900 (survey), 2503.10293 (review),
2106.10091, 2504.14964, 2411.14542 and 2002.00539 (research articles); 2212.00756 (tutorial and
overview) and 2304.04752 (a practitioner's guide) are the closest to teaching material. That is
what the rule offered. The set is not lecture notes.

**What counts as a table**, as in the other sets: every `tabular`-family environment of the LaTeX
source with at least two columns of content, ruled or not; not a grid of images, a code box or a
figure. The source gave the truth (which `tabular`, and which words are in it); a box was fitted to
the words of the PDF page (`gold/tabulars.py`). Then **every page was looked at** (100 pages, with
the proposed boxes drawn on them) and the boxes corrected by hand where they were wrong; the log
is `label-corrections.json` (every box that changed, from and to). In summary: boxes were grown to
the outer rules just above and below a table and to the sides of those rules (within 14 pt and
12 pt); the six small tables of 2304.04752 pages 7 and 8 (proposals held a header row or nothing),
Table 6 of 2202.07104 (a table of check marks, not placed), Tables 2 and 3 of 2411.14542 (cut, not
placed) and one table of 2002.00539 (cut) were boxed by their printed rules; the author block of
2002.00539 (a `tabular` of names) was removed.
No detection run was used for any of this.

Pages: every page that carries a labelled table and its two neighbours (one each side), at most 30
per paper. The PDF files here hold only those pages (PyMuPDF `insert_pdf`), so a page number in
`labels.json` is the page's index in its file; `source_pages` gives its index in the arXiv PDF.

| File | Stratum | Licence | arXiv pages | Pages here | Tables | Title |
|---|---|---|---|---|---|---|
| 2202.07104.pdf | computer science | CC BY 4.0 | 59 | 13 | 6 | The Hitchhiker's Guide to Fused Twins: A Review of Access to Digital Twins in situ in Smart Cities |
| 2212.00756.pdf | engineering | CC BY 4.0 | 55 | 8 | 3 | Mechanism Design Theory in Control Engineering: A Tutorial and Overview of Applications in Communication, Power Grid, Transportation, and Security Systems |
| 2304.04752.pdf | statistics | CC BY 4.0 | 60 | 16 | 12 | A Practitioner's Guide to Bayesian Inference in Pharmacometrics using Pumas |
| 2407.05381.pdf | computer science | CC BY 4.0 | 24 | 7 | 3 | Online Matching: A Brief Survey |
| 2106.10091.pdf | economics | CC BY 4.0 | 18 | 5 | 4 | Introductory Economics: Gender, Majors, and Future Performance |
| 2504.14964.pdf | computer science | CC BY 4.0 | 15 | 8 | 3 | Evaluating Code Generation of LLMs in Advanced Computer Science Problems |
| 2503.10293.pdf | engineering | CC BY 4.0 | 68 | 10 | 4 | The Cahn-Hilliard-Navier-Stokes Framework for Multiphase Fluid Flows: Laminar, Turbulent, and Active |
| 2401.05900.pdf | engineering | CC BY 4.0 | 41 | 22 | 13 | Near-Field Communications: A Comprehensive Survey |
| 2411.14542.pdf | statistics | CC BY 4.0 | 31 | 4 | 4 | Combining missing data imputation and internal validation in clinical risk prediction models |
| 2002.00539.pdf | computer science | CC BY 4.0 | 10 | 7 | 6 | Evolving Neural Networks through a Reverse Encoding Tree |

100 pages, 58 labelled tables. Authors are named in the arXiv records
(`https://arxiv.org/abs/<arXiv id>`).

`labels.json` SHA-256 and those of the other registered files are in `REGISTRATION.sha256`
(`sha256sum -c REGISTRATION.sha256` from this directory).

## Licence and attribution

All ten works are (c) their authors and licensed CC BY 4.0
(<https://creativecommons.org/licenses/by/4.0/>) according to the arXiv OAI-PMH record. The files
here are extracts: pages selected from the arXiv PDFs. Originals: `https://arxiv.org/abs/<arXiv id>`
for each id above.
