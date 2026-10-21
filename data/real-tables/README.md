# Real-document table set

Five openly licensed lecture notes, textbooks and tutorials with tables, used for the
real-document part of the tables gate (docs/eval.md "Real documents": detection precision and
full-extent recall of `readablestem eval pipeline --task tables --real-dir data/real-tables`).
It stands in for the private set of lab-manual PDFs the research used, which are copyrighted
and cannot be committed.

## Pre-registration (written before detection was run on any of these files)

Floors (the gate's, docs/eval.md): precision >= 0.95 and full-extent recall >= 0.85, taken
as point estimates over all labelled pages of all five documents. The Wilson intervals are
reported next to them: with about 25 labelled tables they are wide, so a pass is evidence,
not proof.

Documents (all arXiv, all licensed CC BY 4.0 according to the arXiv OAI-PMH record; none is
in the development, fresh or held-out manifests of the real-world paper corpus):

| File | arXiv | What it is |
|---|---|---|
| 2401.00649.pdf | 2401.00649 | Peng Ding, "Linear Model and Extensions": a statistics textbook from lecture notes (433 pages; the labelled pages are a subset) |
| 2412.19752.pdf | 2412.19752 | Nicolas Curien, "A random walk among random graphs": lecture notes |
| 2505.03022.pdf | 2505.03022 | Simon Rudkin, "An Introduction to Topological Data Analysis Ball Mapper in Python": a tutorial with code boxes and figure grids |
| 2409.08224.pdf | 2409.08224 | R. C. Subramaniam, N. Borse, A. Bralin, J. W. Morphew, C. M. Rebello and N. S. Rebello, "Investigating the Design-Science Connection in a multi-week Engineering Design (ED)-based introductory physics laboratory task": physics laboratory course study with lab schedules and coding tables |
| 2407.13734.pdf | 2407.13734 | M. Uehara, Y. Zhao, T. Biancalani and S. Levine, "Understanding Reinforcement Learning-Based Fine-Tuning of Diffusion Models: A Tutorial and Review" |

What counts as a table: every `tabular`-family environment in the LaTeX source that has at
least two columns of content, ruled or not (the notation lists of the lecture notes count).
Not tables: the single-column boxes (biographies), figure grids (a `tabular` of images) and
the code boxes. The pages of the figure grids and code boxes are in the set on purpose: they
are what the detector has to leave alone. The LaTeX source gave the truth (which `tabular`,
where it begins and ends); each box was then fitted to the words and rules of the PDF page.
A box is the table's body, from its header row to its last row, without its caption or its
notes (the matching rule tolerates a caption: a prediction counts when it shares at least half
of the smaller box with a label).

Pages: every page that carries a labelled table and its two neighbours (one each side); for the
28-page tutorial all pages. The pages not listed in `pages` are not part of the score. Each
file here holds only those pages (PyMuPDF `select`, page frames and contents unchanged), so a
page number in `labels.json` is the page's index in this file; `source_pages` in
`labels.json` gives its index in the arXiv PDF.

## Licence and attribution

The five works are (c) their authors and licensed CC BY 4.0
(<https://creativecommons.org/licenses/by/4.0/>). The files here are extracts: pages were
selected from the arXiv PDFs and nothing else was changed. Originals:
<https://arxiv.org/abs/2401.00649>, <https://arxiv.org/abs/2412.19752>,
<https://arxiv.org/abs/2505.03022>, <https://arxiv.org/abs/2409.08224>,
<https://arxiv.org/abs/2407.13734>.
