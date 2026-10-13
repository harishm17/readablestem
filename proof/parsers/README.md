# Parser comparison harness (optional)

Runners, adapters and scoring glue for comparing a11y-stem with PDF parsers people already
use, on OpenStax pages. It is a separate uv project: the app does not depend on it, and the
parsers (torch, onnxruntime, ...) live in their own environments, one per tool, outside this
repository.

**Status: plumbing only.** The harness has been run on two development pages (OpenStax
*Physics* 2.3, CC BY 4.0, from the demo PDF that is committed in this repository) to show that
the pieces fit. Nothing here has been run on the held-out set, and it must not be: that set is
frozen by `proof/PREREGISTRATION.md` in the research folder, which allows no system to run on
it before the frozen run. The numbers below are counts on two pages. They are not results, and
they say nothing about which tool is better.

a11y-stem assists people who make course material accessible. Nothing in this folder claims
that any output is compliant with, or verified against, a standard.

## One command

```
cd proof/parsers
make setup      # once: one environment per parser from its lock file, plus MinerU's weights
make plumbing   # two development pages -> every arm -> adapters -> scorers -> a summary
make test       # the harness tests (no parser needed; one integration test skips without one)
make lint       # ruff, ruff format --check, ty check
```

`make plumbing` converts each arm's two pages twice (the plan wants determinism checked, and
the first run of a tool can include downloads), adapts the last run, scores it, and writes
`plumbing-summary.json` (counts and codes only) plus per-arm `score.json` and
`score-items.json` under `~/.cache/a11y-proof/parsers-plumbing/latest/`. Run `uptime` first if
you read the times: a busy box makes every time an upper bound (the plan asks for a 1-minute
load average under 1.5 before a timed run).

## Layout

```
proof/parsers/
  pyproject.toml, uv.lock     the harness: a11y-stem (this checkout) + markdown-it-py
  arms/<tool>/pyproject.toml  the tool, pinned with ==, and its uv.lock (CPU torch for Docling)
  runners/                    the one script that has to run inside an arm's own environment
  src/parsers_bench/
    arms.py                   the registry: pins, settings, licence, what each is not built for
    measure.py                one fresh process: wall, CPU, peak memory, load average, host
    runners.py                the command per arm; writes native/, tool.log, run.json
    adapters/                 native output -> the shared intermediate form (one per tool)
    intermediate.py           the shared form
    devgold.py                gold cut to a few pages of a development section
    equations.py              LaTeX -> a11y-stem's converter -> the shared equation scorer
    invented.py, symbols.py   invented content (PREREG 7.5) and symbol-sequence matching
    score.py                  one summary per arm
    cli.py                    parsers-bench list | setup | run | adapt | plumbing
  tests/                      hand-made native outputs (invented text), no parser needed
```

Environments live in `~/.cache/a11y-proof/parsers-envs/<tool>` (override with `PARSERS_ENVS`),
MinerU's weights in `~/.cache/a11y-proof/mineru-home` (`PARSERS_MINERU_HOME`). Docling fetches
its weights into the Hugging Face cache on first use; run the plumbing once before timing
anything, then `--offline` keeps a timed run from touching the network.

## Install steps, by hand

`parsers-bench setup` does exactly this.

```
uv sync                                                    # the harness, in proof/parsers
cd arms/docling      && UV_PROJECT_ENVIRONMENT=~/.cache/a11y-proof/parsers-envs/docling      uv sync --frozen
cd arms/mineru       && UV_PROJECT_ENVIRONMENT=~/.cache/a11y-proof/parsers-envs/mineru       uv sync --frozen
cd arms/pymupdf4llm  && UV_PROJECT_ENVIRONMENT=~/.cache/a11y-proof/parsers-envs/pymupdf4llm  uv sync --frozen
MINERU_HOME=~/.cache/a11y-proof/mineru-home \
  ~/.cache/a11y-proof/parsers-envs/mineru/bin/mineru-kit models download --tier basic --source huggingface
```

Sizes on this box (Python 3.12, Linux x86-64, CPU only): Docling environment 1.4 GB, MinerU
432 MB, PyMuPDF4LLM 112 MB. Weights: Docling's layout, TableFormer and (for `docling-formula`)
CodeFormulaV2 models are fetched from Hugging Face on first use (the plan measured 164 MB, 342
MB and 611 MB); MinerU's `basic` tier is about 820 MB.

The locks pin what the plan measured: Docling 2.138.0 (docling-core 2.101.1, docling-ibm-models
4.0.3, docling-parse 7.22.3, torch 2.14.1+cpu, transformers 5.19.0, RapidOCR 3.10.0), MinerU
4.0.12 (docvortex 0.5.16, onnxruntime 1.31.0), PyMuPDF4LLM 1.28.2 with pymupdf and
pymupdf-layout 1.28.2. One difference from the plan's earlier measurement: the PyMuPDF4LLM lock
resolves onnxruntime 1.31.0, where the plan's scratch environment had 1.30.0.

## The arms

Ids follow `PARSERS-PLAN.md` in the research folder. Settings are tool defaults; the one
non-default switch is labelled.

| Arm | System, pin | Settings | Licence of code and models (package metadata and the plan's notes, 2026-10-13) |
|---|---|---|---|
| `docling` (P1) | Docling 2.138.0 | `docling --to json --to html --to md`; OCR on (RapidOCR); TableFormer accurate; image mode embedded (the CLI default); formula enrichment off | Docling, docling-core, docling-ibm-models: MIT. Layout model Apache-2.0; TableFormer CDLA-Permissive-2.0 and Apache-2.0; RapidOCR Apache-2.0 |
| `docling-formula` (P1f) | Docling 2.138.0 | as above plus `--enrich-formula` (CodeFormulaV2): the one non-default switch | as above; CodeFormulaV2 CDLA-Permissive-2.0 |
| `mineru-basic` (P2) | MinerU 4.0.12 | `mineru-kit parse --tier basic --format zip` (CPU, ONNX; MinerU's own recommendation for a machine without an accelerator) | "MinerU Open Source License" (Apache-2.0 plus extra terms: credit MinerU; a commercial licence above 100M MAU or $20M monthly revenue; say so when offering an online service based on MinerU). docvortex: MIT, Apache-2.0, BSD-3-Clause. Models: PaddlePaddle ONNX exports, Apache-2.0 upstream |
| `pymupdf4llm` (P3) | PyMuPDF4LLM 1.28.2 | `pymupdf4llm.to_markdown(path)`; the layout model is on because `pymupdf-layout` is installed; images not written | AGPL-3.0 or Artifex commercial. The `pymupdf-layout` wheel's metadata also carries a "Other/Proprietary" classifier; the app already depends on the same wheel (ADR 0003) |

Publishing numbers from all four is fine under these licences if the tools are credited.
Publishing their raw output is fine with the OpenStax attribution (the inputs are CC BY 4.0).
This is not legal advice.

### What each system is not built for

Read these as design differences, not failures. Most of these tools turn a PDF into Markdown or
JSON for feeding an LLM, not into accessible HTML.

| System | Built for | Not built for |
|---|---|---|
| Docling | lossless document conversion for gen-AI pipelines (DoclingDocument, Markdown, JSON; HTML is one export) | accessibility remediation: no alt text on pictures, every heading at one level in the HTML export, formulas kept as pictures (with the PDF text as alt) unless the opt-in enrichment is on |
| Docling with formulas | the same, with formula recognition | speed on CPU: about 136 s per page in the plan's measurement, which would put most 15-30 page sections past the 30-minute failure rule of the pre-registration |
| MinerU | high-quality extraction for LLM corpora and RAG (Markdown, JSON), including Chinese | accessible HTML: empty alt text on images, one heading level in Markdown; higher tiers want a GPU or a llama.cpp VLM |
| PyMuPDF4LLM | fast text-layer to Markdown for LLM and RAG ingestion | mathematics: no formula recognition and no LaTeX; images off by default |
| a11y-stem | accessible HTML from STEM course PDFs, with a review screen | re-typing prose (it keeps the PDF's text); tables in production (behind a flag); scans |

### Considered and not run (no runner on purpose)

| System | Why not |
|---|---|
| Marker 2.0.0 (P4) | Waits for Harish's decision (plan section 6.1). The weights' licence counts evaluating as use, and restriction 2(c) bars any use by someone offering a competing service, with no research exception; a11y-stem is a hosted PDF-to-HTML service. Not installed. |
| Nougat 0.1.17 (P5) | On CPU the first of three pages took 19 minutes; trained on arXiv papers, not textbooks; unmaintained since 2023. |
| olmOCR 0.4.27 (P6) | Needs an NVIDIA GPU with at least 12 GB; the box has no working GPU driver. |
| Mathpix (P7) | Paid API; no key on the box; its terms on publishing benchmarks are unread. |
| Chatbot recipe (C1, C2) | Its runs and scorer belong to the held-out task (they call a paid API). The intermediate form was written so that its HTML can be adapted into it. |

## From native output to the shared form

Each adapter reads the tool's most structured output, so that a serialiser's quirks are not
scored as the tool's. Every adapter ends in `adapters/builder.py`, so the rules for headings,
paragraphs, equations, figures and tables are written once. All items share one reading-order
counter. Maths is taken out of paragraph text and listed as equations.

| Tool | Read from | Equations | Headings | Figures |
|---|---|---|---|---|
| Docling | `DoclingDocument` JSON, body tree in order | `formula` items. LaTeX only if enrichment ran; otherwise no LaTeX, and the PDF text Docling puts in the picture's alt is kept as `raw`. Inline when inside an `inline` group | `section_header` at `level + 1` (what its HTML writes), `title` at 1 | `picture`; image kept when the JSON carries the crop; text alternative only from a description annotation (off by default). Words Docling OCRs inside a picture are `figure_text`, not prose |
| MinerU | `structured_content.json` in the zip (`markdown.md` as fallback) | `equation` blocks, and `$...$` in text read as inline equations. A trailing `\tag{...}` is left in | `paragraph_title`, `doc_title` at their `level` | `image`, `chart`; kept when there is an `image_source`; captions become caption blocks |
| PyMuPDF4LLM | the Markdown, read with markdown-it-py (CommonMark, tables, `$` maths) | none are written | `#` levels, `**` dropped | none (images off) |

A crashed, timed-out or empty run becomes a document with that status and no content, so the
scorer counts every gold item of the section as missed (PREREG section 9). Nothing is dropped.

## Scoring

Equations and headings go through a11y-stem's own evaluation code, with nothing re-implemented:
`eval.pipeline_eval.document_page_item` and `heading_page_item` (weighted alignment in reading
order, thresholds 0.5 and 0.85), `eval.equations.score_mathml`, `equation_step.clean_latex` and
`_convert`, `figure_details.numbers_in`.

- **Equations** (PREREG 7.1): `end_to_end_exact_lenient`, detection recall and precision,
  `unflagged_wrong_per_100` (parsers ask nothing, so every wrong aligned equation is unflagged;
  gold equations with the known scorer gap G1 are left out of the headline rate, and the
  plain rate is beside it). Four rows: `primary` (the one that counts), `allowlist_free` (skips
  a11y-stem's LaTeX allowlist), `normalised_plan` (the plan's two markup-only rewrites) and
  `normalised_plus` (those plus three proposed after reading the parsers' LaTeX on development
  pages: drop `\tag{...}`, join digits split by spaces, make a single upright letter text).
  Only `primary` is a measure; the others show how much of a score is a11y-stem's converter and
  markup, and each proposal can be struck before the plan is frozen. A tool that writes no
  LaTeX is reported as "does not transcribe equations (by design)", not as a quality score.
  "Retained in any form" asks whether a gold equation's symbols appear anywhere in the output
  text (difflib ratio at least 0.8 over a window), which is the only fair equation measure for
  tools that write none.
- **Headings** (PREREG 7.2): precision, recall, F1, level accuracy relative to each side's top
  level, and the distinct levels the tool wrote (Docling's HTML and MinerU's Markdown are flat).
- **Invented content** (PREREG 7.5): prose sentences (fewer than 25% of word 4-grams in the
  PDF's text layer), numbers, headings, aligned equations (added symbol) and unaligned
  equations (no window of the PDF's symbols matches), each with n, count and a Wilson interval.
  `invented.py` is this harness's reading of the 7.5 table, written so the adapters' output
  can be checked end to end. It is not the frozen invented-content builder of the held-out run;
  where they differ, the builder and the PREREG table win. Not implemented: figure
  descriptions (they need the image-aware judge) and table cells.
- **Figures**: counts only (figures on the pages by layout, kept as image, with a text
  alternative, no description). The judge-based measures are not run.
- **Cost and time**: API cost $0 and 0 calls for every parser; wall time per page and per
  fresh process (model load included), user CPU time, peak resident memory of the process
  tree, load average before and after, CPU model, logical CPUs and RAM.

### Dev gold

`devgold.py` cuts the section gold to the chosen pages by aligning each gold equation and
heading to what `layout.analyze` finds in the whole PDF. On the default pages (10-11 of the
demo PDF) that leaves 2 gold equations (one inline, one display), 6 headings and 1 figure,
with none unplaced. This is plumbing: the held-out run scores whole sections against
whole-section gold.

## Plumbing run, 2026-10-13

Command: `make plumbing` (that is, `uv run parsers-bench plumbing`) in `proof/parsers`; it
printed `real 18m13s`. Input: pages 10-11 of the committed demo PDF (OpenStax *Physics* 2.3,
CC BY 4.0, a development section). Gold on those two pages: 2 equations, 6 headings, 1
figure. Every arm exited `ok` and gave the same content in both of its runs
(`deterministic_across_runs: true`). API cost was $0 with 0 calls.

These are counts on two pages and say nothing about which tool is better.

| Arm | Equations written by the tool | Exact match (primary row) | Equation symbols found anywhere in the output | Headings matched (F1) | Distinct heading levels written | Invented, of items checked |
|---|---|---|---|---|---|---|
| `docling` | 0 (by design; 1 formula item kept as a picture) | 0 of 2 | 2 of 2 | 6 of 6 (0.857) | 1 | prose 0/20, numbers 0/9, headings 0/8 |
| `docling-formula` | 1 | 0 of 2 | 2 of 2 | 6 of 6 (0.857) | 1 | prose 0/20, numbers 0/9, headings 0/8, aligned equation 1/1 |
| `mineru-basic` | 5 (4 converted, 1 refused by the allowlist as `unknown_command`) | 0 of 2 (1 of 2 on the `normalised_plus` row) | 2 of 2 | 5 of 6 (0.769) | 1 | prose 0/21, numbers 0/11, headings 0/7, aligned equation 0/1, unaligned 0/3 |
| `pymupdf4llm` | 0 (by design) | 0 of 2 | 2 of 2 | 6 of 6 (0.857) | 3 | prose 0/21, numbers 1/26, headings 0/8 |

Time and memory, one fresh process over the two pages (model load included), CPU only
(AMD Ryzen 7 5800H, 16 logical CPUs, 27 GB). The 1-minute load average was 8.9 to 25 while
these ran, because other jobs shared the box and, for `docling-formula`, the arm's own
threads: every time is an upper bound and none is a measurement of the tool.

| Arm | Wall seconds, run 1 / run 2 | Peak resident memory (run 2) |
|---|---|---|
| `docling` | 22.5 / 19.1 | 1.6 GB |
| `docling-formula` | 146.4 / 738.0 | 2.1 GB |
| `mineru-basic` | 93.0 / 54.8 | 1.9 GB |
| `pymupdf4llm` | 6.2 / 5.7 | 0.38 GB |

What the plumbing showed, beyond the plan's earlier three-page look:
- Docling's default writes no LaTeX, so its equation rows are "does not transcribe by
  design", not a score. With `--enrich-formula` it found one formula item on these pages
  (the same one the default run keeps as a picture) and wrote LaTeX for it; that equation
  was aligned to a gold equation, did not match it, and was counted as an invented aligned
  equation (1 of 1). The other gold equation was not found as a formula item.
- MinerU writes every heading at one level in the structured output as well (`level` 2), and
  its equations carry markup a11y-stem's allowlist refuses (1 of 5) or that differs from
  the gold's (`\tag`, spaced digits): the `normalised_plus` row, which is a proposal and not
  a measure, gets 1 of 2.
- PyMuPDF4LLM dropped the figure image (images are off by default) and wrote one number
  that is not in the PDF's text layer (1 of 26; the page text is not reproduced here).
- 2 gold equations is too few to rank anything: a rate of 0 of 2 has a Wilson interval up to
  0.66.

## Not done, and limits

- No held-out run, by design. `PARSERS-PLAN.md` is not frozen; it needs Harish's decisions
  first (Marker, whether to run `docling-formula` under the 30-minute rule, family B, labels).
- The timings are from a shared box (see the load averages in each run) and are upper bounds.
  The plan's protocol (idle box, one fresh process per section, plus one warm process for model
  load versus conversion time) is not automated yet; `run` records the cold process only.
- Not implemented: the MathJax converter row, reading order, tables (TEDS), automated HTML
  checks, label sampling, the chatbot arms, the figure judge. All are secondary or belong to
  another task.
- Docling's and MinerU's own warnings and progress go to `tool.log` in scratch, which may hold
  file names and is never committed. Logs and summaries hold counts and codes, not page text.
- The plan said `bench/parsers/`; this folder is `proof/parsers/`, as asked.
- Two pages give wide intervals. A rate of 0 of 5 has a Wilson interval up to about 0.43.
