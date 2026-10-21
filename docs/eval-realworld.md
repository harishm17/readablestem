# A real-world evaluation set: arXiv papers, with the author's LaTeX as the answer key (2026-10-21)

Every number in [eval.md](eval.md), [PROOF.md](../PROOF.md) and the other eval pages comes from
Chromium prints of OpenStax HTML: the text layer of those PDFs is clean (one font, no ligature
trouble, every glyph has a Unicode value), which flatters a pipeline that reads the text layer. Real course
material is LaTeX (and Word) PDFs with math fonts, ligatures, accents drawn from several glyphs, figures
with their own text, tables full of mathematics, and glyphs with no ToUnicode map. This page is
the first measurement on that kind of PDF: how it was built, what it can and cannot say, what ReadableSTEM
and the chatbot recipe scored on it, in a development part and in a held-out part that was
registered before any system ran on it.

**The short version.** On 15 held-out papers (71 pages, 1,826 gold equations; registered before any system
ran), ReadableSTEM writes 0.583 of the equations exactly right end to end (95% interval 0.447 to 0.688), the
chatbot recipe on Opus 0.641 (0.451 to 0.782) and on Haiku 0.641 (0.460 to 0.776). The paired differences on the
registered test are -0.058 against Opus (97.5% interval -0.146 to +0.040, sign-flip p = 0.16) and -0.058 against
Haiku (-0.114 to +0.021, p = 0.63): **no difference this set can detect**, with ReadableSTEM lower by point estimate. The
Opus arm had one paper answered with a refusal (scored as found nothing, as registered); without that paper the
difference is -0.087 (97.5% -0.160 to -0.005, p = 0.063; not registered). On the 25 development papers ReadableSTEM
is lower than Haiku by 0.125 (95% -0.166 to -0.083). So the clean-text-layer hypothesis is not what separates them here:
ReadableSTEM scores 0.58 on real LaTeX PDFs against 0.60 on the re-measured OpenStax sections (a different mix of equations, so not
like for like), and the chatbots' 0.64 is no worse than theirs on OpenStax (0.62 and 0.60). What costs ReadableSTEM
is where the equations are and how they are cut: mathematics in tables and figures (it finds 11% of the table
equations, the chatbots 23% to 31%), the edges of inline spans (8.7% of the gold, against 2% for the chatbots), and
short inline expressions in running text that it does not detect. In running text, where 82% of the gold is, it is 2 to 4
points behind (0.671 against 0.695 and 0.709). It is cheaper than Opus ($0.030 against $0.083 per page) and 13 times the cost of Haiku
($0.0023), and it asks a person about 5.6 things per page. Spend: $13.43 of the $20 cap for the registered runs, $15.39 with the page math experiments of the last section (development only: the page pass lifts the development primary score by 2.5 points).

## What the set is

**Papers and pages.** 40 arXiv papers, under CC BY 4.0 (the licence is read from the arXiv record;
CC BY 3.0 and CC0 would also be accepted, every other licence is skipped), in math-heavy
categories (math.PR, math.NA, math.HO, stat.TH, stat.ME, stat.ML, cs.LG, cs.DS, econ.EM, q-bio.QM,
physics.class-ph, ed-ph, flu-dyn, data-an, cond-mat.stat-mech), half of them lecture-note or tutorial
style by title (a heuristic: some "tutorials" are research papers). The document is the PDF arXiv
serves (compiled by the authors), cut to 4 to 6 chosen pages; the source is used for gold only.

| | Dev | Held-out |
|---|---|---|
| Papers | 25 | 15 |
| PDF pages | 114 | 71 |
| Gold equations | 2,350 | 1,826 |
| of which display / inline | 197 / 2,153 | 87 / 1,739 |
| single-symbol equations (one token) | 608 | 456 |
| typed in running text / a table / a figure / an algorithm | 1,908 / 223 / 177 / 42 | 1,500 / 237 / 69 / 20 |
| Gold equations per page | 20.6 | 25.7 |

Inline mathematics is 92% to 95% of the gold: a research paper's page is mostly `$x$`, `$n \to \infty$`
and `$\mathcal{O}(n^2)$` in running text, with a display equation every few pages. That is the opposite
of the OpenStax sets (where display equations are a third to a half), and it is why the report also gives
the score by display and inline.

**How the papers were chosen** (`src/readablestem/gold/arxiv_corpus.py`, `scripts/realworld_select.py`,
`scripts/realworld_build.py`; deterministic given the cached OAI records):

1. The arXiv OAI-PMH `ListRecords` of 15 sets over a window of months each (the record carries the licence, so
   no paper is fetched before its licence is known): 10,527 records with an accepted licence after de-duplicating
   cross-lists (about 35% to 45% of the records; recent arXiv papers are often CC BY), 10,165 inside the page range.
2. A seeded order (seed `realworld-2026-10-21`, SHA-256 of seed, set and id; sets in turn; tutorial and
   research alternating). The first 40 candidates that pass the gates are the set, and a candidate that fails a
   gate is skipped, never replaced by choice. 129 were visited: 40 accepted, 59 had fewer than 4 usable pages, 12 had
   more than 10% unconvertible equations, 9 no source files, 6 a page count outside 8 to 120, 2 a source that is itself a
   PDF, 1 an http error (`data/realworld/candidates.json`).
3. Pages: 4 to 6 per paper, drawn by hash among the pages where every equation is known (below).
4. Split: 15 held-out and 25 development, by hash, in proportion to style. The held-out split was registered
   (`data/realworld/PREREGISTRATION.md`, hashed in `REGISTRATION.sha256`) before any system had run on any
   part, and an addendum (sizing of the Opus arm) before any held-out run.

The corpus, the PDFs and the gold are kept outside the repository (`~/.cache/readablestem/realworld`), as
the held-out cache is; the repository has the scripts, the manifests (ids, titles, licences, chosen pages, SHA-256 of the PDF,
the source archive, the cut PDF and each gold file), the registration and the results.
`uv run python scripts/realworld_build.py verify` rebuilds the gold of every paper from the cached downloads and
checks it against the manifests.

## How the gold is made

No model is called and no TeX engine is needed (`src/readablestem/gold/arxiv_tex.py`,
`arxiv_gold.py`; tests in `tests/test_arxiv_tex.py` with small fixture sources):

1. **Flatten and expand.** `%` comments are dropped, `\input` and `\include` are followed, and the author's macros are
   read from the whole source (`\newcommand`, `\renewcommand`, `\providecommand`, `\def` with `#n` parameters,
   `\DeclareMathOperator`, `\let`, argument-free `\newenvironment`) and expanded, nested, until nothing changes. This is what lets
   `\be ... \ee` and `\R` and `\norm{x}` become the real thing. A recursive definition, a macro that grows without bound or a
   `\def` with a delimited parameter is recorded as a failure.
2. **Extract.** `equation`, `align`, `gather`, `multline`, `eqnarray`, `flalign`, `alignat`, `displaymath` and the starred
   forms, `$$..$$`, `\[..\]`, `\(..\)`, `$..$` and `math`, in order, with `\$` and `\verb` respected. Verbatim and listings are
   skipped. Floats (`figure`, `table`, `algorithm`) and pictures (`tikzpicture`) are extracted too but kept apart, because they are
   printed somewhere else than they are typed.
3. **Split.** An `align`, `eqnarray` or `flalign` with several numbered rows is one gold equation per numbered row (a
   run of unnumbered rows belongs to the next numbered row), a starred one is a single equation (as an `aligned` block),
   `gather` is one equation per row and `multline` one equation. This follows the layout stage's own rule (a derivation is one
   equation, two numbered rows stay apart; docs/layout.md). It is a convention, and the pipeline and the chatbot may
   write an alignment the other way (see "Limits").
4. **Place.** Prose words (four letters or more, maths removed) of the source are aligned to the words of the PDF
   with word 3-grams that occur once in both and a longest increasing subsequence. An equation lies between the nearest anchors
   before and after it; it is *placed* when both are on the same page and has an interval of pages otherwise. A float or a
   picture is placed by its own words, and when it has none it blocks the pages it may be on.
5. **Convert.** The same code the pipeline uses for its own LaTeX: `equation_step.clean_latex`, then
   `validate.safe_latex_to_mathml` (latex2mathml), so that gold and a prediction in LaTeX are compared like for like. A few
   author-level commands the converter does not know (`\ensuremath`, `\textcolor`, `\mathop`, `\arg`, `\limits`) are rewritten first.
   An equation that comes out with an unknown command, an unresolved `\ref` or `\cite`, or a converter error is *unconvertible*.
6. **Pick pages.** A page is usable when every equation printed on it is placed on that page (none has an interval that includes it),
   at least 90% of the page's words are in the source's prose (so nothing on the page comes from a file the extractor did not
   read), it has at least 5 equations, none of them unconvertible, and it is not page 1 (title, author lines and abstract math
   are in the preamble). Pages are then drawn by hash, 4 to 6 per paper.

The scorers are the document task's own (`eval/pipeline_eval.document_page_item`): gold and prediction are aligned in reading
order by the weighted LCS of the OpenStax runs, an equation is `exact_lenient` when the normalised MathML trees are equal, the symbols
scorer (`eval/equations_symbols.py`) credits splits and merges, and the report keys are those of docs/eval-page-math.md.

## Limits

- **The pages are easier than the papers.** A page with an equation the converter cannot convert (a command latex2mathml does not know, a bracket it
  cannot pair, an equation over the pipeline's 2,000-character LaTeX limit), with a `\ref` in a `\text`, or whose equations the anchors
  cannot place is not used. 59 of the 129 visited papers had fewer than 4 usable pages. A paper that is mostly
  hard mathematics is under-represented.
- **Gold boundaries are the author's math mode, not the visual formula.** An author who types `$R=$ $6.371\times10^6$ m` or
  `$^{21}$Mg` or `$m_n$ = 6` or `$F = q (\vec v \times \vec B$)` has the gold stop where the dollar sign stops. A reader sees the whole `R = 6.371 x 10^6 m`.
  A system that writes the whole formula is scored wrong on the `span` bucket. It applies to every arm, but more to a
  system that cuts by looking (ReadableSTEM's `span` bucket is 8.6% of the gold, the chatbots' 3.5%).
- **Alignments are one convention.** See step 3. A paper has 78 (dev) and 18 (held-out) gold equations from alignments
  out of 2,350 and 1,826: too few to say how a system treats them (the report gives the numbers anyway).
- **Mathematics inside embedded figures is not in the gold** (axis labels of a PDF figure are in the PDF text layer, not in the
  source). A system that reads them writes an equation with no gold partner (counted as unmatched, and as unsupported or not
  by the PDF's text). Math in a `tikzpicture` or in `\caption` is in the gold.
- **The style label is a heuristic** and the sets are only as varied as the sets, windows and seed.
- **No Word and no scans.** These are LaTeX PDFs only.
- **Float order.** Equations in tables and figures are placed on the right page, but inside a page they are ordered by where the
  float was typed, not where it is printed; the aligner's order tolerance absorbs most of it (a table's cells are
  adjacent), but it is a possible source of lost matches. The ReadableSTEM misses in tables below are real misses (it wrote no
  equation for those cells), not alignment artefacts (checked on one paper).
- **One run per arm**, no estimate of run-to-run variation (0.007 on the primary score on OpenStax), and
  15 held-out papers.

## How the comparison was made

| Arm | What ran |
|---|---|
| ReadableSTEM | `feat/page-math` at `c190b3e` with this branch's scripts only: document task, provider anthropic, `claude-opus-5-5` (effort medium) as main and `claude-haiku-5-5` (low) as cheap model, the page math pass off, the Claude second check off, every other setting at its default; `scripts/realworld_run.py rs` |
| Chatbot, Haiku | `claude-haiku-5-5`, the PDF, a 150 dpi PNG of every page and the Purdue sentence verbatim ("Convert to accessible HTML compliant with WCAG 2.2 AA with complete MathJax headers and remove [cite]."), no system prompt, default effort; `proof.chatbot.record_arms` unchanged |
| Chatbot, Opus | the same on `claude-opus-5-5` |

One run of each, every model answer recorded (`--answers`, the chatbots' reply files) and replayed for scoring. The same
cut PDF is the input of the three. The scorers are the document task's. The registered metrics are in
`data/realworld/PREREGISTRATION.md`: primary `exact_lenient` end to end; secondary symbols-credited;
detection recall and precision; wrong equations nobody is asked about, per 100 gold; invented (unsupported) equations;
cost per page. Intervals are 95% cluster bootstraps over papers (10,000 resamples). Two paired tests were registered
(ReadableSTEM minus each chatbot on the primary, 97.5% intervals, sign-flip p below 0.025 to say "better" or "worse").
Reproduce a table: `uv run python scripts/realworld_report.py --part dev|heldout`.

## Development results (25 papers, 114 pages, 2,350 gold equations)

Development numbers: the extractor, the report and the budget were built looking at these papers; nothing was tuned on
the systems. Opus ran on the first 6 papers of a registered order (27 pages, $2.37), because it costs $0.088 a page.

| | ReadableSTEM | Chatbot, Haiku | Chatbot, Opus (6 papers) | ReadableSTEM on those 6 | Haiku on those 6 |
|---|---|---|---|---|---|
| Papers / gold equations | 25 / 2,350 | 25 / 2,350 | 6 / 546 | 6 / 546 | 6 / 546 |
| **exact_lenient (primary)** | 0.562 [0.494, 0.629] | 0.687 [0.615, 0.751] | 0.793 [0.676, 0.858] | 0.593 [0.392, 0.710] | 0.771 [0.614, 0.848] |
| symbols, credited (secondary) | 0.588 [0.514, 0.659] | 0.713 [0.640, 0.778] | 0.810 [0.700, 0.872] | 0.614 | 0.793 |
| detection recall | 0.737 [0.658, 0.808] | 0.838 [0.785, 0.883] | 0.868 [0.784, 0.925] | 0.720 | 0.866 |
| detection precision | 0.861 [0.831, 0.892] | 0.874 [0.828, 0.916] | 0.865 [0.753, 0.935] | 0.889 | 0.915 |
| wrong, not asked, per 100 gold | 11.2 [7.8, 14.9] | 15.1 [10.6, 20.3] | 7.5 [4.8, 12.8] | 9.3 | 9.5 |
| unsupported equations, aligned (an added symbol) | 0.063 [0.033, 0.106] | 0.050 [0.025, 0.085] | 0.034 [0.009, 0.079] | | |
| unsupported equations, no gold partner | 0.121 [0.049, 0.210] | 0.162 [0.067, 0.307] | 0.000 | | |
| cost per PDF page | $0.0235 | $0.0022 | $0.0878 | | |
| required decisions per PDF page (all kinds) | 4.54 | 0 | 0 | | |

Paired differences on the development papers (descriptive): ReadableSTEM minus Haiku on the primary is -0.125 (95% -0.166 to -0.083,
25 papers, sign-flip p < 0.001); minus Opus, on the 6 papers, -0.200 (-0.335 to -0.127, p = 0.031). The "unsupported" rows are
the invented-content rules of docs/eval-invented.md applied to every arm's HTML (`scripts/realworld_invented.py`; Addendum 2 of
the registration says why the document task's own field cannot be used for a chatbot); ReadableSTEM's registered value
(`text_layer_supported`) is 0.047 per prediction.

**Where the equations are lost** (per gold equation; the buckets of `scripts/dev_buckets.py`, as in docs/eval-page-math.md):

| Bucket | ReadableSTEM | Haiku | Opus (6 papers) |
|---|---|---|---|
| correct (`exact_lenient`) | 0.562 | 0.687 | 0.793 |
| scorer: the symbols are right, the primary scorer says no | 0.018 | 0.020 | 0.016 |
| span: aligned, symbols differ at an end only | 0.086 | 0.035 | 0.035 |
| recognition: aligned, symbols differ inside | 0.065 | 0.091 | 0.024 |
| no proposal | 0.005 | 0.006 | 0.000 |
| undetected: no aligned prediction | 0.263 | 0.162 | 0.132 |

ReadableSTEM's 619 undetected equations: 352 (57%) are typed in a table (208) or a figure (144), 126 are a single symbol in running
text, 86 are two or three symbols, 34 a longer inline expression and 21 a display equation. Haiku's 381: 190 in tables and figures, 81 single
symbols. ReadableSTEM's 203 span failures are 138 cut too long, 59 too short and 6 both. By where the equation was typed (`exact_lenient`):

| Typed in | gold | ReadableSTEM | Haiku | Opus (6 papers) |
|---|---|---|---|---|
| running text | 1,908 | 0.659 | 0.755 | 0.829 |
| table | 223 | 0.058 | 0.363 | 0.671 |
| figure (caption or panel) | 177 | 0.122 (detected 0.163) | 0.372 | 0.531 |
| algorithm | 42 | 0.571 | 0.571 | 0.944 |
| display / inline | 197 / 2,153 | 0.503 / 0.567 | 0.508 / 0.703 | 0.381 / 0.810 |
| research / tutorial paper | 976 / 1,374 | 0.499 / 0.606 | 0.648 / 0.715 | 0.736 / 0.813 |

(The Opus column is 546 equations of 6 papers, 82 of them in tables; the other columns are all 25 papers.) **Ten failures of
ReadableSTEM** (development; gold LaTeX is what the author typed; "text layer" is what PyMuPDF reads from the PDF for the region):

| # | Where | Gold | ReadableSTEM wrote | What it shows |
|---|---|---|---|---|
| 1 | 2408.09889 p7, a table of fitted parameters | `\eta_\textbf{r}` (and 47 other cells) | nothing | math in table cells is not an equation element: 47 of this paper's 48 float equations missed |
| 2 | 2501.09629 p29, a figure panel | `G(m\|q)=G(q)-G(m,q)` | nothing | text inside a figure |
| 3 | 2501.09629 p50 | `L` | nothing | a single symbol |
| 4 | 2505.11658 p2 | `6.371 \times 10^{6}` | `R = 6.371 \times 10^{6}\ \text{m}` | the author typed `R =` and `m` outside the dollars; the page shows one formula (a boundary convention; Haiku wrote the same) |
| 5 | 2503.07628 p6 | `\boldsymbol{g}: \Gamma_N \to \mathbb{R}^2` | `\Gamma_N \rightarrow \mathbb{R}^2` | the span starts after the bold g (text layer `ΓN →ℝ2`) |
| 6 | 2408.09889 p6 | `\mathcal{O}(n_\text{live}^{-\frac{1}{2}})` | `\mathcal{O}(n_{\text{live}}^{-\frac{1}{2}})\ \text{([26])}` | the citation after the formula is absorbed (flagged as a required decision) |
| 7 | 2404.15495 p7 | `k=1,...,T` | `\lambda_1\ (k=1,\ldots,T)` | the span takes in the neighbouring symbol |
| 8 | 2412.08797 p13, display | `\widetilde{\mathcal{P}}^{**}\equiv\max_{...}\widetilde{\mathcal{P}}(\nu,\chi)=...` | nothing (a required decision) | the accent glyph has no Unicode value: text layer `�𝒫∗∗≡maxν∈(0,1)...` |
| 9 | 2504.03499 p21, two-line display | `\begin{aligned} &\bm z_{t+1}=\arg\max ... \\ & r_t(\bm z)=... \end{aligned}` | nothing (a required decision); Haiku wrote only the second line | a display with an unmapped glyph; a derivation cut by line |
| 10 | 2505.08251 p2 | `p_{\text{out}} = p_{ij}` | `p_{out} = p_{ij}` | the reading is right to a reader; the primary scorer says no (the symbols scorer says yes: bucket "scorer") |

## Held-out results (15 papers, 71 pages, 1,826 gold equations; registered, run once)

Run 2026-10-21 after `PREREGISTRATION.md` and Addendum 1 were committed and hashed. Nothing was changed between the
first held-out number and the end (Addendum 2, written after the results were read, adds a metric reading and says so). ReadableSTEM $2.10,
Haiku $0.16, Opus $5.86; no job failed; one Opus reply was a refusal.

| 15 held-out papers | ReadableSTEM | Chatbot, Opus | Chatbot, Haiku |
|---|---|---|---|
| **exact_lenient, end to end (primary)** | 0.583 [0.447, 0.688] | 0.641 [0.451, 0.782] | 0.641 [0.460, 0.776] |
| symbols, credited (secondary) | 0.620 [0.480, 0.728] | 0.690 [0.499, 0.833] | 0.691 [0.504, 0.829] |
| detection recall | 0.780 [0.620, 0.898] | 0.752 [0.550, 0.899] | 0.765 [0.587, 0.892] |
| detection precision | 0.859 [0.831, 0.899] | 0.801 [0.734, 0.871] | 0.913 [0.854, 0.965] |
| wrong equations nobody is asked about, per 100 gold | 10.0 [6.2, 14.5] | 11.1 [7.2, 16.2] | 12.3 [8.4, 16.8] |
| unsupported equations, aligned (an added symbol) | 0.049 [0.028, 0.085] | 0.034 [0.012, 0.069] | 0.029 [0.008, 0.062] |
| unsupported equations, no gold partner (3+ symbols) | 0.141 [0.049, 0.231] | 0.415 [0.250, 0.526] | 0.103 [0.000, 0.268] |
| cost per PDF page | $0.0296 [0.0195, 0.0405] | $0.0825 [0.0727, 0.0917] | $0.0023 [0.0021, 0.0026] |
| required decisions per PDF page (all kinds; of which on aligned equations) | 5.56 [3.58, 7.62]; 3.27 | 0 | 0 |

**The registered tests** (paired, 15 papers; interval 97.5%, Bonferroni k = 2; sign-flip p):

| | Estimate | 95% | 97.5% | p | Verdict by the registered rule |
|---|---|---|---|---|---|
| ReadableSTEM minus Opus, primary | -0.058 | [-0.136, +0.027] | [-0.146, +0.040] | 0.163 | no difference this set can detect; lower by point estimate |
| ReadableSTEM minus Haiku, primary | -0.058 | [-0.108, +0.010] | [-0.114, +0.021] | 0.626 | no difference this set can detect; lower by point estimate |

Descriptive differences (95%): symbols-credited -0.070 [-0.166, +0.020] against Opus and -0.070 [-0.142, +0.013] against Haiku; detection recall
+0.028 and +0.016; wrong-not-asked -1.0 and -2.3 per 100 (all intervals include 0). **Sensitivity, not registered:** without 2405.01155,
where Opus returned a refusal and so scored 0 on its 176 equations (14 papers, all arms): ReadableSTEM 0.623 [0.504, 0.706], Opus 0.710 [0.581, 0.804],
Haiku 0.688 [0.518, 0.799]; the difference to Opus is -0.087 (95% -0.151 to -0.017; 97.5% -0.160 to -0.005; sign-flip p = 0.063, so
still "no difference" by the registered rule) and to Haiku -0.065 (97.5% -0.121 to +0.022, p = 0.61).

**Where the equations are lost** (held-out):

| Bucket | ReadableSTEM | Opus | Haiku |
|---|---|---|---|
| correct | 0.583 | 0.641 | 0.641 |
| scorer | 0.031 | 0.035 | 0.033 |
| span | 0.087 | 0.020 | 0.018 |
| recognition | 0.054 | 0.054 | 0.071 |
| no proposal | 0.026 | 0.002 | 0.002 |
| undetected | 0.220 | 0.248 | 0.235 |

| Typed in (exact_lenient; detected) | gold | ReadableSTEM | Opus | Haiku |
|---|---|---|---|---|
| running text | 1,500 | 0.671; 0.887 | 0.695; 0.807 | 0.709; 0.837 |
| table | 223 | 0.072; 0.112 | 0.220; 0.305 | 0.157; 0.229 |
| sideways table | 14 | 0.214; 0.786 | 0.643; 1.000 | 0.643; 1.000 |
| figure | 69 | 0.493; 0.623 | 0.812; 0.913 | 0.725; 0.841 |
| algorithm | 20 | 0.300; 0.750 | 0.700; 0.900 | 0.650; 0.900 |
| display / inline | 87 / 1,739 | 0.333 / 0.596 | 0.241 / 0.661 | 0.345 / 0.656 |
| single symbol / longer | 456 / 1,370 | 0.605 / 0.576 | 0.671 / 0.631 | 0.636 / 0.643 |
| research / tutorial paper | 1,148 / 678 | 0.622 / 0.518 | 0.645 / 0.636 | 0.709 / 0.527 |

ReadableSTEM's 401 undetected: 227 (57%) are typed in a table or a figure (198 table, 26 figure, 3 sideways table), 109 are single symbols in running
text, 32 are two or three symbols, 23 longer inline expressions, 10 display equations. Its 158 span failures are 77 cut too long and 80 too short
(1 both); the chatbots have 37 (Opus) and 32 (Haiku). Opus's 453 undetected include 155 table equations and 121 single symbols, and the 176
equations of the refused paper. On 42% to 52% of ReadableSTEM's span-failure equations (Haiku) and 31% to 44% (Opus) the chatbot is not exact either, which
says that a good part of the span bucket is the author's boundary convention or a hard case, not an error only the pipeline makes.

ReadableSTEM's registered unsupported value (the document task's `text_layer_supported`, per prediction) is 0.045 [0.028, 0.062] (development 0.047).

Review screen (ReadableSTEM only): of the aligned equations that are wrong in the symbols, 57.6% were a required decision
(development 38.0%), and 75.4% of the required decisions on aligned equations were wrong in the symbols (development 58.3%).

## What this says

- **ReadableSTEM against the chatbots on real LaTeX PDFs.** On the registered test, ReadableSTEM's primary score is 5.8 points
  below both chatbots', and the set cannot tell that from no difference (97.5% intervals extend from -0.15 to +0.04). The
  development part says the same with the sign clearer (25 papers: 0.562 against Haiku's 0.687). The ordering is the one of the
  OpenStax proof (the chatbot ahead on equations by 0.03 to 0.12), not a collapse of ReadableSTEM on real PDFs: its primary
  score is 0.56 to 0.58 here against 0.50 (registered 2026-10-14) and 0.60 (re-measured, not blind) on OpenStax. The two sets differ so much in
  what the equations are (here 92% to 95% inline, one in five typed in a table, figure or algorithm) that no like-for-like claim is made.
- **Haiku at $0.0023 a page is as accurate as Opus at $0.083 on the held-out papers** (0.641 each; on the six development papers they share, Opus is 2
  points ahead, 0.793 against 0.771). ReadableSTEM costs $0.030, 13 times Haiku and 36% of Opus, and adds a review screen that asks 5.6 questions a page. The
  screen is more useful than on OpenStax (75% of its required equation decisions are wrong equations, and it catches 58% of the symbol-wrong
  ones) and the wrong equations nobody is asked about, 10.0 per 100, are fewer than the chatbots' 11.1 and 12.3, but not detectably.
- **Invented equations.** Aligned equations with an added symbol are 3% to 5% for every arm. Equations that have no gold partner and no match in
  the PDF's text are rarer for ReadableSTEM (14%) and Haiku (10%) than for Opus (42%, of 118 equations; on the development papers 0 of 26). These are
  small counts, and "no match in the text layer" includes equations whose glyphs are missing from it.
- **The text layer matters where glyphs have no Unicode value.** 9.6% (development) and 4.8% (held-out) of the equations ReadableSTEM aligned had at least
  one U+FFFD in their text layer (accents and calligraphic letters from a math font). It got 62% and 41% of those exactly right, against 78% and 76% of the
  others, and "no proposal" (a required decision with no equation) is 2.6% of the held-out gold, against 0.2% for the chatbots, which read the page image.

**The three failure modes of ReadableSTEM on real PDFs**, by the number of equations they cost (held-out, development in brackets):

1. **Mathematics inside tables and figures.** 227 (352) of its 401 (619) undetected equations, 57% in both parts. It writes an equation for 11% (7%) of the table
   equations and 62% (16%) of the figure ones; its exact rate in a table is 0.07 (0.06). Math in a table cell is not an equation element
   of the pipeline (it is the table step's text), and text inside a figure is not read as math. The chatbots lose here too (Opus 0.22, Haiku 0.16 exact in tables) but much less.
2. **The edges of inline spans.** 8.7% (8.6%) of the gold, 4 to 5 times the chatbots' (1.8% to 3.5%): the formula is cut at the end of its glyph run, so a
   neighbouring symbol, a unit or a citation comes in (`([26])`, `R =`, a leading `\lambda_1`), or a leading symbol or a closing bracket stays out
   (`\boldsymbol{g}:`, `(\mathbb{R}^d)`). Part of this is the author's math boundary (`R =` and `m` outside the dollars), which the scorer cannot tell from a
   pipeline error; the rest is real.
3. **Short inline expressions in running text are not found.** 109 (126) single symbols, 32 (86) of two or three symbols, 23 (34) longer inline
   expressions: `i < j`, `\times 10^{-11}`, a lone `L`, `k`. That is 9% (10.5%) of the gold. It is a choice as much as a failure (a single italic letter
   in a sentence is also a letter), but the answer key counts them and a screen reader reads them as text, not as a variable.

   Across them: **no proposal for display equations whose glyphs have no Unicode value** (2.6% of the held-out gold).

## The page math pass on the real-world development set

ADR 0006's page math pass (`FEATURE_PAGE_MATH`: one Haiku call per page places the equations, the reconciliation policy of
`page_math_reconcile.py` lays them over layout, and each crop's reading is compared with the page's) did nothing measurable on
OpenStax, whose text layer is clean ([eval-page-math.md](eval-page-math.md)). The failure modes above (span edges, short
inline math not found, glyphs without Unicode) are what it was built for, so it was run on the 25 development papers
(114 pages, 2,350 gold equations). Development only: the held-out part is not used. Each run's model answers are recorded; a later run starts from
the answers of the one before, so a request already answered costs nothing the second time. `scripts/realworld_compare.py` makes the paired tables
(95% cluster bootstrap over the 25 papers, sign-flip test).

| Dev, 25 papers | Flag off | Page pass, as committed | + output limit 12,288 | + no agreement lift |
|---|---|---|---|---|
| **exact_lenient (primary)** | 0.5617 | 0.5809 (+0.019 [+0.009, +0.031], p = 0.001) | **0.5864 (+0.025 [+0.011, +0.041], p = 0.003)** | 0.5821 |
| symbols, credited | 0.5877 | 0.6089 (+0.021 [+0.011, +0.033]) | 0.6162 (+0.029 [+0.015, +0.045]) | 0.6119 |
| detection recall | 0.7366 | 0.7489 (+0.012 [+0.004, +0.022]) | 0.7519 (+0.015 [-0.003, +0.034]) | 0.7519 |
| detection precision | 0.8608 | 0.8674 | 0.8813 (+0.021 [-0.003, +0.040]) | 0.8813 |
| wrong, not asked, per 100 gold | 11.2 | 11.7 | 12.3 (+1.1 [0.0, +2.4]) | 11.7 |
| required decisions per PDF page (all kinds) | 4.54 | 3.96 | 3.35 (-1.19 [-1.69, -0.72]) | 3.76 |
| equation flags: precision / recall (symbols-wrong) | 0.583 / 0.380 | 0.571 / 0.323 | 0.557 / 0.275 | 0.540 / 0.330 |
| unsupported equations, aligned / no partner | 0.063 / 0.121 | 0.060 / 0.112 | 0.061 / 0.081 | |
| cost per PDF page | $0.0235 | $0.0250 | $0.0234 (page pass $0.0028) | $0.0251 |
| pages where the page pass answered nothing | | 25 of 112 | 2 of 112 | 2 of 112 |

**What the committed pass loses, and the fix.** On 25 of the 112 pages that have text the page pass returned nothing: the
answer ran into its output limit (`MAX_OUTPUT_TOKENS` 4,096, 6,144 with the thinking allowance; every failed call stopped at
6,144 tokens, `invalid_output`). Those are the dense pages (a real paper page has 25 to 50 inline expressions, the OpenStax pages the pass was built on have far fewer),
that is, the ones where it would help. The limit is now the setting `PAGE_MATH_MAX_OUTPUT_TOKENS` (default 4,096, unchanged); at 12,288 two pages fail, the page pass costs $0.0028 a page
($0.32 for the 112 pages; Haiku's output is cheap) and the whole conversion costs what it did without the pass ($0.0234 against $0.0235 a page), because
agreeing readings are accepted without a second look. All numbers in the last two columns are with the limit raised.

**Which buckets moved** (per gold equation, flag off to page pass at 12,288):

| Bucket | Off | On | Change |
|---|---|---|---|
| correct | 0.562 | 0.586 | +0.025 |
| span (symbols differ at an end) | 0.086 | 0.075 | -0.011 (cut too long 138 to 129, too short 59 to 46, both 6 to 1) |
| recognition | 0.065 | 0.064 | -0.001 |
| undetected | 0.263 | 0.248 | -0.015: single symbols in text 126 to 111, 2 to 3 symbols 86 to 76, display equations 21 to 11, longer inline 34 to 33, **tables 208 to 209, figures 144 to 143** |
| no proposal | 0.005 | 0.005 | 0 |
| scorer | 0.018 | 0.022 | +0.003 |

By stratum (exact_lenient): display 0.503 to 0.558, align-like 0.385 to 0.462, running text 0.659 to 0.688, single symbols 0.548 to 0.587, research papers
0.499 to 0.534; **tables 0.058 to 0.049 and figures 0.122 to 0.145: the pass does not touch what the real-world set showed is lost most**
(equations in tables are not paragraph text, and the pass's spans are laid over paragraph rows and display regions only). Of the gold equations: 103 went from wrong to exact, 45 from exact to wrong
(net +58 of 2,350): undetected to correct 46, span to correct 39, recognition to correct 16, scorer to correct 2; correct to span 23, to undetected 14, to recognition 6, to scorer 2.
Of the aligned pairs, 961 sit on a page-pass span (77% exact) and 725 on a layout run the pass did not confirm (79%).

**Ten that it helped** (gold; flag off; page pass): `a`, nothing, `a` (a lone variable in a figure label); `N_i > 0`, `\text{(i) } N_i > 0` (the item number taken in), `N_{i} > 0`;
`K(t)`, `K(t+\Delta t)` (a neighbour taken in), `K(t)`; `m`, nothing, `m`; `S, M \rightarrow \infty`, `M \rightarrow \infty` (the left half lost), whole;
`P(f)/P_{\phi=0\%}(f)`, `P_{\phi=0^\circ}` (the `%` read as a degree sign from the glyphs), right; `\mathbf{A}`, nothing, found; `0\sim 20\%`, `0 \sim 20` (percent sign left out), whole;
`\bm g_{t+1}`, `\mathbf{g}_{t+1}`, `\boldsymbol{g}_{t+1}`; `\theta_{\max} )`, `\theta_\text{max})`, `\theta_{\max})`.
**Five that it hurt:** `t=1, 2, 3` in a table cell, right, now `t = 1, 2, 3, \underline{w}_{t}` (the span runs on into the next cell: twice); `z_i = z_j`, found,
now not found (the page pass read the line as part of a `cases` block); a long `\langle N\rangle = ... \langle N^2\rangle` display, right, now misread inside; `(W^\top W)^{-1}`, right,
now `\left(W^{\top}W\right)^{-1}` (the symbols scorer calls both right; the primary does not).

**The cost: fewer questions, and fewer of the wrong ones are asked about.** The pass lowers required decisions per page by 26% (4.54 to 3.35) because two agreeing
readings are accepted. But both readings share the page pass's span, so agreement says the symbols are right and nothing about where the span
ends, and the equation flags' recall of symbol-wrong equations falls from 0.38 to 0.275; the wrong equations nobody is asked about rise from 11.2 to 12.3 per 100
(+1.1, 95% 0.0 to +2.4, so not clear). Of the wrong equations that are accepted without a decision with the pass on, 163 of 289 are ones where the two readings agreed. The setting
`PAGE_MATH_AGREEMENT_LIFT` (default true) turns the confidence lift off: the last column. It brings decisions back to 3.76 a page and recall to 0.33, takes the unflagged-wrong back to
11.7, and costs $0.0017 a page and 0.4 points of primary score (the main model's second reads change some right answers; p = 0.03). It is a dial between the decisions asked and the errors not asked about,
not an improvement in accuracy; the default is left as it was.

**Policy changes considered.**
- *Page-pass inline spans overriding layout's runs when the quote verifies* is already how it works: a layout run that touches a page span is replaced by it
  (`layout._with_page_spans`), and a span is dropped only when its LaTeX and the text it quotes disagree in their symbols (4 of 1,566 spans on dev). Nothing to change; the 14 equations that
  went from found to not found are not runs dropped as look-alikes (checked by replaying three papers: none was).
- *Page-pass-only displays where the glyphs have no Unicode value* is already allowed (a block span stands where layout found no display or only part of one, and its crop
  is transcribed). The dev equations without a proposal are not a Unicode problem: 7 of the 12 are rejected by the LaTeX allowlist (`unknown_command`: `\arg` (6 of the 17
  distinct proposals the allowlist refused), `\substack`, `\succsim`, `\colon`, `\coloneqq`, `\triangleq`, `\textmu`), a defect of the converter's command list, not of the pass. A rewrite
  of `\arg` to `\operatorname{arg}` and an allowance for the others would recover roughly 7 to 9 gold equations of 2,350 (0.3 to 0.4 point), with or without the page pass. Not done here (outside the flag).
- *The output limit* is the policy change that mattered, above.
- *Not tried:* putting equations in tables and figures on the pass's reading list (the pass is given paragraph rows; the 57% of undetected equations that are typed in tables and figures
  are untouched), and splitting a very dense page in two.

**Spend:** $0.978 (pass as committed) + $0.898 (limit 12,288) + $0.096 (no lift) = **$1.97** of the $4 + $3 allowed; the whole work is now $15.39 of the $20 cap.
No run was repeated; the invented-content rates come from replaying the recorded answers offline.

**Recommendation.** Turn the flag on for production, **with `PAGE_MATH_MAX_OUTPUT_TOKENS=12288`** (the default of 4,096 loses a fifth of dense pages and half of the gain). On real LaTeX PDFs it adds about 2.5 points of
exact equations (paired interval +1.1 to +4.1 on 25 papers, positive in the symbols score and in recall), raises display equations by 5.5 points, and cuts the review load by a quarter at no cost per page. Against that,
the review screen asks about fewer of the wrong equations (recall 0.38 to 0.28; 1.1 more wrong equations per 100 are not asked about, an interval touching 0), it does nothing for tables and figures, and
on a clean text layer (OpenStax) it measured no effect. The evidence is development-only (the held-out part is spent for tuning, and ADR 0006 asks for a fresh held-out set before the flag
is default-on), so the safe form of the recommendation is: on, with the higher limit, for the beta; check it on a fresh set of arXiv papers (about 18 more would put a +2.5 point difference at
roughly 80% power, from the paired standard deviation implied by the interval here) when the next real-world budget allows; and decide `PAGE_MATH_AGREEMENT_LIFT` by how much the review screen should be trusted to find errors.

## What it does not say

The registered difference is not significant, and one run per arm gives no run-to-run noise. The papers are 15 (and 25) of ten fields,
with pages chosen so that every equation on them is known; mathematics in embedded figures is not in the gold; the author's math boundaries
are the gold's (a limit for every arm, larger for the one that cuts by looking); the style label is a heuristic; and nothing here says whether a wrong equation
changes the meaning, whether the page is acceptable to a person, or anything about Word files, scans, chemistry or tables as
tables. The Opus arm's refusal on one paper may be a property of that paper's content or of that call, and it was not asked again.

## Spend (hard cap $20)

`scripts/realworld_run.py tally` (from the recorded answers; `data/realworld/spend-log.jsonl` has one line per start with the estimate each run was
given and its cap; the first start of the Haiku development arm, which a connection error stopped, is in the tally and not in the log):

| | Development | Held-out |
|---|---|---|
| ReadableSTEM | $2.674 (114 pages, $0.0235 a page) | $2.105 (71 pages, $0.0296) |
| Chatbot, Haiku | $0.255 (114 pages) | $0.165 (71 pages) |
| Chatbot, Opus | $2.372 (27 pages, $0.0878) | $5.856 (71 pages, $0.0825) |
| Total | $5.301 | $8.126 |
| Page math experiments (development, [below](#the-page-math-pass-on-the-real-world-development-set)) | $1.97 | |
| **Whole work** | | **$15.39** |

No run was repeated. The offline replays (ReadableSTEM's HTML for the invented-content rules, the reports) cost nothing.

## Files and how to rerun

| What | Where |
|---|---|
| Harvest, order, build | `scripts/realworld_select.py`, `scripts/realworld_build.py` (`build`, `verify`), `src/readablestem/gold/arxiv_corpus.py`, `arxiv_tex.py`, `arxiv_gold.py` |
| Runs | `scripts/realworld_run.py` (`estimate`, `rs`, `chat`, `chat-items`, `tally`); media: `src/readablestem/eval/realworld_media.py` |
| Reports | `scripts/realworld_report.py`, `scripts/realworld_invented.py`; results `data/realworld/results-*.json`, `invented-*.json` |
| Registration | `data/realworld/PREREGISTRATION.md`, `-ADDENDUM-1.md`, `-ADDENDUM-2.md`, `REGISTRATION.sha256`, `scripts/realworld_register.py` |
| Manifests and the candidate log | `data/realworld/manifest-dev.json`, `manifest-heldout.json`, `pool.json`, `order-*.json`, `candidates.json`, `spend-log.jsonl` |
| Tests | `tests/test_arxiv_tex.py`, fixtures in `tests/fixtures/arxiv_tex/` |
| Corpus, gold, PDFs, recorded answers (not in the repository) | `~/.cache/readablestem/realworld/{papers,gold,runs}` |

```
uv run python scripts/realworld_select.py harvest
uv run python scripts/realworld_select.py order --seed realworld-2026-10-21
uv run python scripts/realworld_build.py build --order data/realworld/order-realworld-2026-10-21.json --target 40 --held-out 15
uv run python scripts/realworld_build.py verify          # gold equals the manifests
uv run --env-file .env python scripts/realworld_run.py rs --part dev --max-usd 4.2
uv run --env-file .env python scripts/realworld_run.py chat --part dev --arm chatbot-haiku-purdue --max-usd 1.2
uv run python scripts/realworld_report.py --part dev --examples 12
```

