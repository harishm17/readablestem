# Fresh real-document table set (held out)

Seven openly licensed arXiv papers with tables, used as the held-out part of the real-document
table check (`readablestem eval pipeline --task tables --mode local --real-dir
data/real-tables-heldout`; docs/tables.md, "Recall on real documents"). `data/real-tables` is the
development set the detector was tuned on; nothing here was run through the table detector, or
looked at for tuning, before this README, the files and `labels.json` were committed.

## Pre-registration (written before detection was run on any of these files)

Floors, the project's own (docs/eval.md "Real documents"): precision >= 0.95 and full-extent
recall >= 0.85, point estimates over all labelled pages of all seven documents, scored by the
same code as the development set (`eval/tables.score_real_document`: a prediction is true when it
shares at least half of the smaller box with a label; a label is found in full when a prediction
covers 90% of it and at least 75% of that prediction lies in it). The Wilson 95% intervals are
reported next to them. The run is made **once**, with the code of the commit that follows this
one, and reported whatever it shows; the code is not tuned on these documents afterwards.

How the documents were chosen (`scripts/tables_heldout.py select`, `selection.json` has every
document looked at and why each skipped one was skipped):

1. Candidates are the records of the real-world corpus pool (docs/eval-realworld.md: arXiv OAI
   records with their licence; only CC BY 4.0, CC BY 3.0 and CC0 are in it) that nothing has used:
   not visited by the real-world builder, in none of its dev, held-out or fresh manifests, not in
   `data/real-tables`.
2. Kept when the title says lecture, tutorial, primer, introduction, introductory, course notes,
   notes on, a guide, guide to, handbook, textbook, for beginners or first look (35 records; the
   pool is mathematics-heavy and has few lecture notes among the records nothing had used).
3. Taken in the order of SHA-256(`tables-heldout-2026-10-21:` + arXiv id). For each: download the
   PDF and the source (one request every three seconds), and accept the first eight that pass the
   gates: a main TeX file, 6 to 250 PDF pages, and at least three data tables found in the PDF's
   words by `gold/tabulars.py`. The walk stopped at seven accepted when the candidates ran out
   (28 skipped: 22 with fewer than three tables found, 4 whose source is itself a PDF, 1 with no
   main file, 1 not downloadable). An earlier walk with a wider word list (it matched "survey",
   "overview", "notes", "guide") accepted eight papers that were mostly research articles; it was
   replaced before any detection was run, and none of those papers is here.

What counts as a table, as in `data/real-tables`: every `tabular`-family environment of the
LaTeX source with at least two columns of content, ruled or not; not a grid of images, a code box
or a figure. The source gave the truth (which `tabular`, and which words are in it); the box of
each was fitted to the words of the PDF page (`gold/tabulars.py`, an alignment of the cells' words
with the page's words in content-stream order). Then **every page was looked at** (58 pages, with
the labelled boxes drawn on them) and the boxes corrected by hand where they were wrong. The
alignment of words had put two boxes on a graph whose node labels repeat the words of a table
(2312.15334 page 3 and 2305.15484 page 3; the tables were on 2312.15334 page 3 and 2305.15484
page 4) and had missed a table of check marks (2311.16525 page 2); those three tables were boxed by
their printed extent. No other box was changed, and nothing has been changed since: there has been
no detection run.

Pages: every page that carries a labelled table and its two neighbours (one each side), at most 30
per paper. The PDF files here hold only those pages (PyMuPDF `insert_pdf`), so a page number in
`labels.json` is the page's index in its file; `source_pages` gives its index in the arXiv PDF.
Raster images were downsampled to 72 dpi where they were larger (`Document.rewrite_images`) to keep
the repository small; text, vector graphics and page frames are unchanged.

| File | arXiv | Pages | Tables | Authors, title |
|---|---|---|---|---|
| 2302.07336.pdf | 2302.07336 | 15 | 12 | M. Vignal, G. Geschwind, B. Pollard, R. Henderson, M. D. Caballero, H. J. Lewandowski, "Survey of physics reasoning on uncertainty concepts in experiments: an assessment of measurement uncertainty for introductory physics labs" |
| 2312.15334.pdf | 2312.15334 | 5 | 4 | G. Kortemeyer, W. Bauer, "Cheat sites and artificial intelligence usage in online introductory physics courses: what is the extent and what effect does it have on assessments?" |
| 2405.19435.pdf | 2405.19435 | 8 | 6 | H. N. Archer, M. N. Simon, C. Mead, E. E. Prather, M. Brunkhorst, D. Hunsley, "Bringing Lecture-Tutorials Online: An Analysis of A New Strategy to Teach Planet Formation in the Undergraduate Classroom" |
| 2305.15484.pdf | 2305.15484 | 5 | 4 | G. Kortemeyer, C. Kortemeyer, W. Bauer, "Taking introductory physics in studio, lecture, or online format: what difference does it make in subsequent courses, and for whom?" |
| 2504.13112.pdf | 2504.13112 | 11 | 4 | G. G. Chrysos, Y. Wu, R. Pascanu, P. Torr, V. Cevher, "Hadamard product in deep learning: Introduction, Advances and Challenges" |
| 2311.16525.pdf | 2311.16525 | 7 | 5 | S. Swarnkar, R. Roy, T. Kaur, D. Blair, "On the effectiveness of the early introduction of modern physics in school curriculum: the case of the structure of atom versus wave-particle duality" |
| 2304.11221.pdf | 2304.11221 | 7 | 4 | G. Kortemeyer, "Can an AI-tool grade assignments in an introductory physics course?" |

58 pages, 39 labelled tables. Most are physics-education studies and one is a survey of a deep
learning technique, not lecture notes: that is what the pool offered under the rule above, and
it is a fair test of tables in real papers (long text cells, check-mark grids, regression tables,
wide tables set in two columns).

`labels.json` SHA-256: `d599366e67b3a2a7a333369a544bf84f7b7094db636494c7cb997f043bfe5231` (`eval` prints it with the result).

## Licence and attribution

The seven works are (c) their authors and licensed CC BY 4.0
(<https://creativecommons.org/licenses/by/4.0/>) according to the arXiv OAI-PMH record. The files
here are extracts: pages selected from the arXiv PDFs, raster images downsampled to 72 dpi where
larger. Originals: `https://arxiv.org/abs/<arXiv id>` for each id above.
