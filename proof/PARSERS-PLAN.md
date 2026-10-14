# Parser comparison plan (held-out OpenStax set), 2026-10-13

Status: **plan only, not frozen.** Nothing has run on the held-out set. The timings in §3
come from 3 development pages (OpenStax *Physics* 2.2, already in `data/gold`), so the
held-out sections are still unseen by every parser. No model calls were made ($0). Nothing
was committed, pushed or published. Not legal advice.

This is the "parser plan" that `PREREGISTRATION.md` §6.3 asks for (file read at SHA-256
`8127b143…`, version 1; version 2 differs only in wording and has `2c16815a…`). The pre-registration wins wherever the two overlap: the held-out set, primary
metrics, statistics, failure rules and the chatbot arms. This file adds what §6.3 leaves open:
- which parsers run, at which versions and settings;
- how each tool's output is mapped onto the shared intermediate form;
- a few parser-specific secondary measures, all descriptive;
- the licence position for publishing;
- the parser part of the claims (family B).

It is frozen (path and SHA-256 appended to `PREREG.sha256`) only after Harish's decisions
in §11, and before any held-out output exists. Otherwise, by §6.3, the parser comparison
does not run on this set.

Ask 3 of 4 (the reviewers' requests): "how does a11y-stem compare with the PDF parsers
people already use?" The answer is given on shared measures only. Most of these parsers produce
Markdown or JSON for LLM ingestion, not accessible HTML (§5). There is no overall score and no
"winner".

## 1. Arms

| Arm | System, pin | Settings | Decision |
|---|---|---|---|
| A0 | **a11y-stem**, as in PREREG §6.1 | production defaults | run by the main held-out run; the parser comparison reuses its outputs |
| P1 | **Docling 2.138.0** (MIT) | CLI defaults: OCR on (RapidOCR), TableFormer accurate, image mode `embedded`, formula enrichment **off** | run |
| P1f | Docling 2.138.0 + `--enrich-formula` | the one non-default switch, labelled as such | run, but see §7.3: at the measured ~136 s/page, the 30-minute section limit of PREREG §9 would cut 5-7 of the 15 sections. **Harish's call** (§11) |
| P2 | **MinerU 4.0.12** (Apache-2.0 + extra terms) | `mineru-kit parse --tier basic` (ONNX, CPU). This is MinerU's own recommended tier for "Linux w/o modern accelerator" | run |
| P3 | **PyMuPDF4LLM 1.28.2** (AGPL-3.0) | `pymupdf4llm.to_markdown(path)` defaults: the layout model is on because `pymupdf-layout` 1.28.2 is installed; images are not written | run |
| P4 | Marker 2.0.0 (code Apache-2.0; weights modified OpenRAIL-M) | CPU default = `fast` mode; equations are OCR'd by the Surya VLM through llama.cpp | **conditional, not run until Harish decides** (§6.1). Running it is "Use" of the weights, and the competing-service clause has no research exception |
| P5 | Nougat 0.1.17 (code MIT; default model `0.1.0-small`, CC BY 4.0) | defaults | **not run**: on CPU the first page took 19 min (§3), which means roughly 75 h for 242 pages, and every 26-29 page section would hit the 30-minute limit. Unmaintained since 2023 |
| P6 | olmOCR 0.4.27 (Apache-2.0) | 7B VLM; its README asks for an NVIDIA GPU with at least 12 GB | **not run**: infeasible on CPU. The box's NVIDIA driver is broken anyway (`nvidia-smi`: driver/library version mismatch) |
| P7 | Mathpix (paid API) | | **not run**: no Mathpix key on the box (only `OPENAI_API_KEY` exists in `.env`) |
| C1 | **Chatbot recipe, gpt-6.1-sol**, Purdue prompt | as PREREG §6.2 (R = 2), plus its "expert" prompt arm (R = 1) | run by the main held-out run; reused here |
| C2 | Chatbot recipe, **gpt-6-luna**, Purdue prompt | same script and inputs as C1 (`run_chat.py`, mode `pages`, `store=False`), R = 2 | **new arm, descriptive.** PREREG §6.2 names only sol, so C2 must be in the frozen plan (or a PREREG addendum) before the run |

**Shared measures** (PREREG §7 unless marked):
- equations: `end_to_end_exact_lenient` and `unflagged_wrong_per_100`;
- headings: F1;
- invented content;
- coverage;
- figures;
- cost and time.

**This plan's secondary, descriptive measures** (§4.4):
- the converter and normalisation rows;
- equation content retained in any form;
- reading order;
- images kept;
- tables;
- automated HTML checks.

## 2. The held-out set

As registered (PREREG §4.4):
- **15 sections**: *College Physics*, *Chemistry*, *Precalculus*, *Introductory Statistics*
  and *Intermediate Algebra*, 3 equation-density terciles each.
- **242 Letter pages** (6 to 29 per section).
- **1,369 gold equations** (163 block / 1,206 inline).
- **70 figures** (69 with an OpenStax alt).
- **27 tables**.
- **509 headings** (399 inside boxes).

Every arm gets the same 15 PDFs, byte for byte: the ones A0's document task prints
(PREREG §6). The adapters and settings were chosen on development material only: Physics
2.1-2.3, the repository's fixtures, and the 3 dev pages of §3. None was chosen on these
sections.

## 3. Measured today on 3 development pages

**Input.** Pages 2-4 of `chatbot-baseline/inputs/2-2-speed-and-velocity.pdf` (CC BY 4.0,
already dev gold), cut to `~/.cache/a11y-proof/parsers/in/dev3.pdf` (SHA-256 prefix
`e2924257f8c580aa`). By a11y-stem's layout the 3 pages hold:
- 6 display and 2 inline equations;
- 1 figure;
- 11 headings.

**Caveat on every timing:** the box was shared. Other jobs raised the load during most of
these runs (`uptime` at 10:50: 33.7 / 26.2 / 16.5 for 1 / 5 / 15 min, on 16 logical CPUs).
Treat the times as upper bounds and an order of magnitude; §7.4 re-times them on an idle box
before the run.

| Arm | Install footprint (measured) | Time on the 3 pages | Peak RSS | What came out |
|---|---|---|---|---|
| P1 Docling default | venv 1.4 GB (torch 2.14.1+cpu, transformers 5.19.0, docling-core 2.101.1, docling-ibm-models 4.0.3, docling-parse 7.22.3); weights: Heron layout 164 MB, TableFormer 342 MB, RapidOCR 62 MB | run 2: wall 12.7 s, of which conversion 5.5 s = **1.8 s/page**; the rest is start-up and model load. Run 1: 30.9 s, including a 10 MB OCR model download | 1.6 GB | 11 headings, **all `<h2>`**. 6 formula items, **none transcribed**: in the default HTML, 5 are a picture of the formula with `alt` = the PDF's flattened text (`alt="𝑣avg = distance time"`, fraction structure lost), and 1 inline item reads "Formula not decoded". The figure is an `<img>` with **no `alt`** |
| P1f Docling + `--enrich-formula` | + CodeFormulaV2 611 MB (CDLA-Permissive-2.0) | conversion 426 s, including an 18.5 s download: **about 136 s/page**; user CPU 1,416 s | 3.2 GB | 5 of 6 formulas decoded to LaTeX. Errors seen: `v_avg` read as `\nu`; one array with unbalanced braces and "tim" for "time"; `\intertext` for a two-line display |
| P2 MinerU basic | venv 634 MB (onnxruntime 1.31.0, no torch); weights 819 MB (PP-DocLayoutV2, PP-OCRv6, PP-FormulaNet plus-M, SLANet-plus; repo `MinerU-4_models_onnx` @ `358310b4`) | run 2: wall 17.3 s, inference 13.0 s = **4.3 s/page**. Run 1: 28.0 s / 21.4 s | 2.1 GB | Markdown. 5 display equations as `$$` LaTeX (with `\mathsf{d i s t a n c e}`, `\varDelta`, `aligned`) and 1 inline. 11 headings, **all `##`**. The figure is embedded as base64 with **empty alt** |
| P3 PyMuPDF4LLM | venv 252 MB (pymupdf 1.28.2, pymupdf-layout 1.28.2, onnxruntime 1.30.0) | conversion 1.2-1.3 s = **0.4 s/page**; wall 2.3-2.8 s | 0.35 GB | Markdown. 10 headings (6 `#`, 3 `##`, 1 `###`) wrapped in `**`. **The display equations of the first page are missing: blank lines where they were.** One equation came through as flattened text, with the fraction bars written as `<u>` underline (`𝑣avg = <u>distancetime</u> = ...`). Figure image dropped, caption kept |
| P5 Nougat | venv 1.5 GB (Python 3.11, torch 2.4.1+cpu, transformers 4.38.2, timm 0.5.4, albumentations 1.3.1); weights 959 MB in `~/.cache/torch/hub/nougat-0.1.0-small` | **1 of 3 pages in 1,141 s (19 min)**, then killed by the 1,200 s timeout; its own estimate was 38 min more. User CPU 1,052 s (about one core) | 2.5 GB | none: Nougat writes its `.mmd` only at the end of a document |
| P4 Marker (not run today) | earlier measurement, licence-deploy track 2026-10-08, `fast` mode with OCR **off**: venv 1.4 GB; surya_layout2 136 MB. The default CPU path also needs the `llama-server` binary (not installed here) and the surya-ocr-2 GGUF | then 1.3-1.8 s/page at 2 CPUs, with no equation OCR | 0.75-0.89 GB anon | HTML with LaTeX inside `<math>` tags, not MathML (Marker README) |
| C1 / C2 chatbot | `openai` SDK only (3.26.0 in a11y-stem's lock) | earlier runs (`niche/chatbot-runs/usage.jsonl`, 27-page upload): sol 228 s (8.4 s/page), **$0.0149/page**; luna 62 s (2.3 s/page), **$0.0007/page** | n/a | one HTML file with MathJax from a CDN; prose re-typed; no images (`chatbot-baseline/report.txt` §6) |

Also checked: which of the parsers' LaTeX a11y-stem's allowlist accepts.
- `\varDelta` (MinerU) and `\intertext` (Docling) fail `check_latex` (`unknown_command`), but
  latex2mathml converts them.
- `\mathsf`, `aligned` and `\nu` pass both.

Commands are in Appendix A. Outputs are in `~/.cache/a11y-proof/parsers/out/` (scratch; not
for publishing).

## 4. Mapping and measures

### 4.1 Intermediate form (PREREG §6.3)

One JSON per arm and section, written by the arm's adapter. Logs hold counts and codes only,
never content.

```
{arm, versions, settings, section, status: ok|crashed|timeout|empty|truncated, seconds,
 equations: [{order, display: block|inline, latex | mathml, raw}],
 headings:  [{order, level, text}],
 blocks:    [{order, kind: heading|para|list|caption|figure_text|table|equation, text}],
 figures:   [{order, caption_ref, image_kept: bool, text_alternative: str|null}],
 tables:    [{order, grid}],
 html_path: str|null}
```

### 4.2 Adapters: each reads the tool's most structured output, so that serialiser quirks are not scored

| Arm | Read from | Equations | Headings | Figures | Order |
|---|---|---|---|---|---|
| P1, P1f | `DoclingDocument` JSON (lossless), iterated in reading order; the HTML export is used only for §4.4 (h) | `FormulaItem.text` = LaTeX. When it is empty (not decoded), `orig` (the PDF text the default HTML puts in the formula picture's `alt`) feeds only "content retained". Inline when inside an inline group | `SectionHeaderItem.level` | `PictureItem`: image kept, no alt | body order |
| P2 | MinerU's structured output (`--format middle_json`, or the zip's content list), with Markdown as fallback | `equation` blocks and inline `$...$` | `title` blocks with their `text_level` | `image` blocks: embedded, alt empty | block order |
| P3 | `to_markdown` output via markdown-it-py | none emitted | `#` levels, `**` stripped | none (`write_images` off) | text order |
| P4 (if run) | Marker JSON blocks | LaTeX inside `<math>` | `SectionHeader` `<hN>` | `Picture` | block order |
| C2 | the reply's HTML, through `chatbot-baseline/extract.py` and `structure.py`, exactly as C1 | MathJax TeX | `h1`-`h6` | PREREG §7.3 rule | DOM order |

**LaTeX to MathML.** PREREG §7.1, unchanged: `equation_step.clean_latex`, then `_convert`
(the allowlist, then `safe_latex_to_mathml` with latex2mathml 3.81.1), then the document
alignment on the MathML signature. This is the same path for the parsers as for the
chatbot.

### 4.3 Primary and PREREG-secondary measures, as they apply to parsers

- **`end_to_end_exact_lenient`.** For P1 (default) and P3 it is 0 by construction: they emit no
  LaTeX. That is reported as "does not transcribe equations (by design)", not as a quality
  score, and they are left out of the equation claims (§4.6).
- **`unflagged_wrong_per_100`.** Parsers ask nothing, so every wrong equation is unflagged
  (PREREG). Same caveat for P1 and P3.
- **Heading F1, precision, recall, level accuracy.**
  - Flat outputs are scored as they are: P1 writes every heading as `<h2>`, P2 every heading
    as `##`.
  - The write-up says this flatness is a property of those exports.
  - Level accuracy is relative to the section's top level, so a flat output scores only on
    top-level headings.
- **Invented content (PREREG §7.5).** It applies unchanged. For the parsers it mostly measures
  OCR noise and reformatting, for example P3's `<u>` fraction bars and P1's text OCR'd from
  inside figures.
- **Coverage (PREREG §7.6).** It applies unchanged.
- **Figures (PREREG §7.3).** The defaults of P1, P2 and P3 emit no text alternative for
  pictures, so each figure is "no description": a judge fail by design.
  - No parser picture-description option is turned on: they are all off by default.
  - P1's formula pictures get the flattened text as `alt`, but those are equations, not
    figures.
- **Time and memory (PREREG §7.7).** Wall clock per page on the box, CPU only. Threads, CPU
  model and peak RSS are recorded (§7.4).

### 4.4 This plan's secondary measures (descriptive; never used for a claim)

**(a) Allowlist-free row.** The same LaTeX through `clean_latex` and `safe_latex_to_mathml`
only, skipping `check_latex`. This shows how much each arm loses to a11y-stem's allowlist, a
security rule of the product rather than a fault of the parser. Allowlist refusals are counted
per arm.

**(b) Normalised row.** Two rules, applied to every non-A0 arm's LaTeX before conversion:
1. a multi-letter word in `\mathrm`, `\mathsf`, `\textrm` or `\operatorname` becomes
   `\text{word}`, with inner spaces removed;
2. variant Greek capitals become their base letter (`\varDelta` to `\Delta`).

These are pure markup differences: the chatbot baseline found `\mathrm{avg}` against
`\text{avg}` to be one.

**(c) Converter row.** The same LaTeX converted with MathJax's tex2mml
(`niche/judge-blind/tex2mml.mjs`, MathJax version pinned in its `package-lock.json`). The
write-up mentions a ranking only when both converters give the same direction.

**(d) Equation content retained, in any form.** For arms that emit no LaTeX this is the only
fair equation measure.
- **Retained:** a gold equation with 3 or more symbols (letters, digits, Greek; NFKC,
  case-folded) is retained when some window of the arm's output symbol stream matches its
  symbol sequence with difflib ratio >= 0.8. The stream is all output text in reading order,
  `alt` and LaTeX included. This mirrors the "Equation, unaligned" rule of PREREG §7.5.
- **Silently dropped:** the rest. P3 dropped display equations on a dev page.
- **P1's formula pictures** count as retained. The write-up adds that a screen reader then
  hears the flattened text with no fraction structure, not MathML.

**(e) Reading order.**
- **Reference:** the gold block order (headings, paragraphs, list items, captions) from the
  section's printable HTML.
- **Matching:** each block is located in the output by its best word 6-gram match (at least
  half of its 6-grams found).
- **Order agreement:** the longest increasing subsequence of the matched positions, divided by
  the matched blocks. Kendall tau is secondary.
- Unmatched blocks belong to coverage, not to order.

**(f) Images kept.** Per gold figure (found through its caption number), whether the output
holds an image element there. On dev:
- P1 and P2 keep it;
- P3 drops it;
- the chatbots keep none.

**(g) Tables.** On the 27 gold tables, via `eval/tables.py`: detection by content, TEDS and
column-header exact.
- **Grids from:** Docling's `TableItem`, MinerU's table HTML, PyMuPDF4LLM's Markdown tables,
  the chatbots' `<table>`, and A0 with `FEATURE_TABLES` on. Off is production: a table kept
  as an image with a decision.
- **Why it is here:** this is where Docling (TableFormer) and MinerU (SLANet-plus) are
  strongest, so leaving tables out would flatter A0.
- **Counted apart:** `scope` and `<caption>`, which only A0 writes.

**(h) Automated HTML checks.** `html_checks.static_checks` and `axe_checks` run only on arms
that write HTML themselves: A0, Docling's HTML export, Marker, and the chatbots (offline: the
TeX that shows is what a no-script reader gets).
- Markdown-only arms (P2, P3) are "n/a": converting their Markdown would test the converter,
  not them.
- Zero violations is never presented as "accessible".

**(i) Automatic meaning-level pre-flag.** a11y-stem's text-layer checks
(`equation_step._evaluate` with confidence fixed at 0.95, as `score_baseline.py` did) are run
on every arm's aligned equations.
- This is a11y-stem's checker judging its competitors, so it is disclosed as such and is never
  a label.
- Harish's blind labels (§4.5) are the measure.

### 4.5 Labels for the parser arms (PREREG §7.8, set 1)

Parsers will have hundreds of wrong equations each: 1,369 gold equations, most of them inline.
Labelling them all is not feasible.

**Proposal:** for each parser arm that emits LaTeX (P1f, P2, P4 if run), a seeded sample of 50
of its wrong aligned equations.
- **Seed:** the PREREG label seed, plus the arm id.
- **Sheet:** mixed into the same blind sheet as everything else.
- **Reported:** the meaning-changing share, with a Wilson interval, described as a sample.
- **Effort:** 100-150 labels, about 1.5-2 h of Harish's time.

C2 is labelled like C1 (all wrong equations), or with the same 50-sample if Harish prefers.

### 4.6 Claims (family B of PREREG §8.3): proposal

PREREG §8.3 says "`end_to_end_exact_lenient` and heading F1 per parser (2 x number of
parsers)". Applied literally, that would let a11y-stem "beat" P1 and P3 on equations they do
not attempt. Proposed instead (needs a PREREG v2 or an addendum before the run):

| Measure | A0 against | Comparisons |
|---|---|---|
| `end_to_end_exact_lenient` | P2 MinerU; P1f Docling + formulas (if kept, and only on the sections it completed, with the timeouts reported) | 2 |
| heading F1 | P1 Docling, P2 MinerU, P3 PyMuPDF4LLM | 3 |
| (if Marker runs) both | P4 | +2 |

- **Without Marker,** k = 5, so a claim needs the 99% paired cluster-bootstrap interval of
  the difference to exclude 0 (the same level as family A). With Marker, k = 7 (99.29%).
- **P1 and P3 on equations,** C2, and every measure in §4.4 are descriptive.

## 5. What each system is not designed to do

| Arm | Built for | Not built for: do not read these as failures |
|---|---|---|
| a11y-stem | accessible HTML from STEM course PDFs, with a review screen | re-typing prose (it keeps the PDF's text); tables in production (behind a flag); scans; PPTX |
| Docling | lossless document conversion for gen-AI pipelines (DoclingDocument, Markdown, JSON). HTML is one export of that model | accessibility remediation: no alt on pictures, flat heading levels in the export, and formula recognition off by default (opt-in, and slow on CPU) |
| MinerU | high-quality extraction for LLM corpora, RAG and agent reading (Markdown, JSON), including Chinese | accessible HTML: empty alt, one heading level. Its tiers above `basic` want a GPU or a llama.cpp VLM |
| PyMuPDF4LLM | fast text-layer-to-Markdown for LLM and RAG ingestion | maths: no formula recognition, no LaTeX. Images off by default |
| Marker | Markdown, JSON and HTML for LLM pipelines; equations as LaTeX | MathML (LaTeX sits inside `<math>` for a MathJax-style renderer); accessibility markup |
| Nougat | arXiv-style papers to Mathpix Markdown | textbooks with figures and boxes. Known to repeat or hallucinate out of domain |
| olmOCR | linearising PDFs to text for LM training data (GPU) | HTML, accessibility |
| Mathpix | maths OCR to LaTeX, MMD and DOCX | (not run) |
| Chatbot recipe | a general assistant asked for accessible HTML | faithful transcription: it re-types the whole document and can change it (`niche/VERDICT-2026-10-10.md` §4) |

What the write-up says up front: the parsers solve a different problem, getting text into an
LLM. They are here because they are what an engineer reaches for first, and because equations,
headings and reading order are the hard part of both problems.

## 6. Licences: running locally, publishing numbers, publishing outputs

The inputs are OpenStax, CC BY 4.0. Any published output, crop or excerpt carries the
attribution. Numbers need none.

| Arm | Code / weights | Local benchmark | Publish numbers | Publish raw outputs |
|---|---|---|---|---|
| Docling 2.138.0 | MIT / Heron Apache-2.0; TableFormer CDLA-Permissive-2.0 + Apache-2.0; CodeFormulaV2 CDLA-Permissive-2.0; RapidOCR Apache-2.0 | yes | yes | yes, with attribution (no weights are redistributed) |
| MinerU 4.0.12 | "MinerU Open Source License": Apache-2.0, plus a separate commercial licence above 100M MAU or $20M monthly revenue, plus "if you provide online services to third parties based on MinerU" it must be stated. Weights: PaddlePaddle models, Apache-2.0 upstream (the ONNX repo carries no licence tag; its README says the original licences apply) | yes | yes; credit MinerU | yes, with attribution. The online-service clause matters only if the hosted app ever used MinerU |
| PyMuPDF4LLM 1.28.2 | AGPL-3.0 or Artifex commercial (ADR 0003 already accepts these terms) | yes | yes | yes |
| Marker 2.0.0 | Apache-2.0 / modified AI Pubs OpenRAIL-M (surya-ocr-2, surya_layout2; Marker's `MODEL_LICENSE` has the same terms) | **at risk** (§6.1) | **only after Harish decides, ideally with Datalab's written OK** | no: share-alike and attribution attach to the Output |
| Nougat 0.1.17 | MIT / small CC BY 4.0 (the default); base CC BY-NC 4.0 | (not run) | | |
| olmOCR | Apache-2.0 / Apache-2.0 | (not run) | | |
| Mathpix | proprietary API | (not run; read Mathpix's terms on publishing benchmarks first if a key ever appears) | | |
| Chatbot (OpenAI API) | API terms; output belongs to the user; `store=False` | yes | yes | yes, with the OpenStax attribution. The Purdue prompt is one public sentence, quoted with its source; no Purdue sample is used |

### 6.1 Why Marker is conditional

From the weights' licence (fetched today: `~/.cache/a11y-proof/lic/surya2_LICENSE.txt`;
`surya_layout2` and Marker's `MODEL_LICENSE` say the same):
- **Section 5: evaluating is "Use".** "Use may include creating any content with,
  fine-tuning, updating, running, training, **evaluating** and/or re-parametrizing the Model."
- **Restrictions 2(a) and 2(b):** no use if you, your employer or "the entity you are
  affiliated with" had more than $5M revenue or raised more than $5M, "except where Your Use is
  limited to personal use or research purposes". A university affiliation needs that
  exception. A published portfolio benchmark is arguably research.
- **Restriction 2(c) has no such exception:** no use "for any purpose if You ... provides or
  otherwise makes available any product or service that competes with any product or service
  offered by or made available by Licensor".
  - Datalab sells hosted PDF conversion to Markdown, HTML and JSON.
  - a11y-stem is a hosted PDF-to-HTML service whose beta opens Nov 9.
  - Whether that "competes" is a legal judgement this plan cannot make. A published table
    that ranks a11y-stem against Marker, next to the product, is the use most likely to be read
    as competitive.
- **Sections 7 and 8:** attribution is required "in connection with any Output", and
  share-alike applies to the Output.

Options for Harish:
- **(a)** Leave Marker out. Cite its own vendor-reported olmOCR-bench score (README: 76.0%
  balanced, 83.5% born-digital), labelled as vendor-reported on another benchmark.
- **(b)** Ask Datalab in writing for permission to run and publish a non-commercial benchmark,
  and run only after a yes. **Recommended**, with (a) as the fallback.
- **(c)** Decide that a11y-stem does not compete. Then run it, publish scores only with
  attribution and a licence link, and never publish Marker's outputs.

The 2026-10-08 licence-deploy track already ran Marker (fast mode, OCR off) for timing.
Publishing those timings raises the same question.

## 7. Protocol

### 7.1 Before the run

The following are frozen and hashed into the run report:
- this file, after §11's decisions;
- the adapters and their tests (on dev pages);
- one lockfile per arm;
- the §4.4 normalisation list;
- the sample seeds.

Then append this file's path and SHA-256 to `PREREG.sha256` (PREREG §6.3). Adapter changes
after the first held-out output are allowed only for crashes or parse errors, never for
scoring choices. Each one is reported as a deviation, with both numbers.

### 7.2 Run order

1. A0 and C1 as PREREG specifies. That run prints the 15 PDFs; record their SHA-256 and page
   counts.
2. Local arms one at a time on an idle box (§7.4): P3, P2, P1, then P1f.
3. C2 (luna, R = 2), under its own `--max-usd`.
4. Score every arm with the same command, then the blind labels, then the write-up.

### 7.3 Failures (PREREG §9)

**Section failures.** A crash, empty output or a **30-minute timeout** scores the section as
"found nothing", with every gold item missed. There are no reruns with other settings.
Infrastructure failures may be re-run at most twice, each attempt logged.

**P1f and the timeout.** At the 136 s/page measured under load:
- sections over about 13 pages time out: 7 of the 15, including all three Precalculus and all
  three Intermediate Algebra sections, which hold 1,001 of the 1,369 equations;
- on an idle box at perhaps half that time (about 68 s/page), the 5 sections of 26-29 pages
  would still be at or over the limit.

So under PREREG §9, P1f's equation numbers would mostly measure CPU speed. Choices for
Harish (§11):
- run P1f under the 30-minute rule and report it as such;
- fix in a PREREG v2, before the run, a longer limit for P1f only (120 min);
- drop P1f.

**Determinism.** Local arms are assumed deterministic. The dev pages are converted twice; if
the outputs differ, that arm runs R = 2 on the held-out set (PREREG §6.3).

### 7.4 Timing protocol

- **Idle box.** Start a timed arm only when the 1-minute load average is under 1.5. Record
  `uptime` before and after. Run nothing else.
- **Threads.** Tool defaults, with all 16 logical CPUs visible: Docling's `AcceleratorOptions`
  default of 4 threads, and onnxruntime's own default for MinerU and PyMuPDF4LLM.
- **Per user.** One fresh process per section, which is what one user waits; this is the
  number reported per page (PREREG §7.7).
- **Warm.** One extra warm process over all sections gives model-load time and conversion
  time per page.
- **Memory.** `/usr/bin/time` for peak RSS. Also record the CPU (Ryzen 7 5800H) and RAM
  (26 GB).

### 7.5 Budget for this plan's arms

| Item | Estimate for 242 pages |
|---|---|
| P3 | about 2 min |
| P1 default | about 8-10 min |
| P2 basic | about 20 min |
| P1f | at most 15 sections x 30 min = 7.5 h serial under the timeout; about 2-3 h with 3-4 sections in parallel (3.2 GB each), or about 9 h serial with no timeout |
| C2 luna, R = 2 | about $0.35 expected at $0.0007/page; worst case under $2. **Cap: $2**, or add it to the PREREG $50 cap |
| Labels (§4.5) | 100-150 equations, about 1.5-2 h of Harish |
| Adapters, tests and the scorer generalisation | about 1-1.5 days. PREREG §12 item 5 already asks for `score_baseline.py` to take any gold directory; the parser adapters plug into it |

### 7.6 Reproduction (ask 4)

Inside the PREREG's `make heldout-score` / `make heldout-run`, which run from saved outputs,
need no key and verify `PREREG.sha256` first. The parser part of a public a11y-stem
(MIT code, OpenStax inputs only):
- `bench/parsers/<arm>/pyproject.toml` with `uv.lock`, one environment per tool, so that
  a11y-stem's own dependencies do not change;
- `bench/parsers/adapters/`, with tests on dev pages;
- `make parsers`, which converts the 15 PDFs with every local arm and writes the intermediate
  JSON that `heldout-score` reads.

Marker stays out of the default target until §6.1 is settled. Raw outputs are not committed;
scores and per-item rows are (gold ids, and predicted LaTeX for OpenStax items, which are
CC BY).

## 8. Limits specific to the parser comparison (PREREG §13 covers the rest)

- **Home advantage.** a11y-stem was developed on prints from the same Chromium printer:
  Unicode maths alphanumerics in the text layer, untagged, raster figures. The held-out
  sections reduce that advantage but do not remove it. The parsers were trained on other
  material (arXiv, office documents, Chinese documents).
- **Converter bias.** Every arm's LaTeX goes through a11y-stem's allowlist and converter, which
  were tuned to a11y-stem's own prompt conventions (`array{rcl}`, `\mathbf`). Rows (a) to (c)
  of §4.4 bound the effect.
- **Exact match punishes markup.** `aligned` against a three-column `mtable`, or `\mathsf`
  words, can fail exact match while being correct; on dev, a perfect transcriber scores
  0.83. PREREG §7.1(b) measures the ceiling on this set.
- **Default settings.** They understate tools that people usually tune. Docling with formulas
  is shown; MinerU's VLM tiers and Marker's balanced mode (both want a GPU) are not.
- **Timings come from one CPU box,** not a cloud deployment.
- **Pre-flag bias.** a11y-stem's own checker pre-flags competitors' equations (§4.4 i):
  disclosed, and never a label.
- **No conformance claim.** a11y-stem assists people making course material accessible.

## 9. Not done here

- No held-out run, no adapters, no scoring code. This file is the plan.
- Marker not run (licence); olmOCR not run (GPU); Mathpix not run (no key).
- Nougat finished 1 of 3 dev pages before its 20-minute limit.
- MinerU's `standard` tier (the 1.2B VLM through llama.cpp on CPU) was not timed, because the
  box was under load.
- No timing was repeated on an idle box.
- This file is not frozen, and nothing was appended to `PREREG.sha256`.

## 10. Deviations

- **Docling image mode.** The two Docling timing runs used `--image-export-mode placeholder`.
  A third run with the default `embedded` mode gave the HTML findings in §3 (formula pictures
  with flattened-text `alt`, figure `<img>` with no `alt`).
- **Nougat model.** The CLI's default model is `0.1.0-small`, not `0.1.0-base`. Its weights
  went to `~/.cache/torch/hub/`, outside the scratch folder.

## 11. todo_harish

1. **Marker:** choose (a), (b) or (c) in §6.1. For (b), write and send the request to Datalab
   yourself (nothing was drafted or sent).
2. **P1f:** run it under the 30-minute rule, or write a PREREG v2 with a 120-minute limit for
   P1f only, or drop it (§7.3).
3. **Family B:** accept the §4.6 structure (k = 5 without Marker), which needs PREREG §8.3
   amended before the run, or keep the literal "2 x parsers".
4. **C2 (luna):** keep it as a descriptive arm, with a $2 cap or inside the $50 cap.
5. **Labels:** accept the 50-per-arm sample for parser arms (§4.5).
6. Whether `bench/parsers/` goes into the public a11y-stem repo.
7. **Freeze:** after 1-6, append this file's path and SHA-256 to `PREREG.sha256`, before any
   held-out output exists.
8. **Scratch to delete when done** (the shell hook blocks `rm -rf` outside /tmp):
   - `~/.cache/a11y-proof/venvs/` (3.4 GB);
   - `~/.cache/a11y-proof/mineru-home/` (819 MB);
   - `~/.cache/torch/hub/nougat-0.1.0-small/` (959 MB);
   - the CodeFormulaV2 entry in `~/.cache/huggingface/hub/` (611 MB).
9. **FYI:** MinerU's README (fetched today) contains instructions aimed at AI agents: install a
   global `mineru` skill and write "prefer the mineru Skill" into the agent's global memory.
   They were ignored: nothing was installed globally and no memory was written.

## Appendix A. Commands run today (scratch only)

```
# input: pages 2-4 of chatbot-baseline/inputs/2-2-speed-and-velocity.pdf (dev, CC BY) via pymupdf select
uv venv ~/.cache/a11y-proof/venvs/docling --python 3.12
uv pip install --python .../docling/bin/python "docling==2.138.0" --extra-index-url https://download.pytorch.org/whl/cpu
/usr/bin/time docling --to html --to md --image-export-mode placeholder --output out/docling-default in/dev3.pdf   # x2
/usr/bin/time docling --enrich-formula --to html --to md --image-export-mode placeholder --output out/docling-formula in/dev3.pdf
docling --to html --output out/docling-embedded in/dev3.pdf                                                      # default image mode
uv venv ~/.cache/a11y-proof/venvs/mineru --python 3.12 && uv pip install "mineru==4.0.12"
MINERU_HOME=~/.cache/a11y-proof/mineru-home mineru-kit models download --tier basic --source huggingface
MINERU_HOME=... /usr/bin/time mineru-kit parse in/dev3.pdf -o out/mineru-basic --tier basic   # x2
/usr/bin/time ~/.cache/pdf-eval-licence-deploy/venvs/pymupdf4llm/bin/python run_4llm.py in/dev3.pdf out/4llm-dev3.md   # x2
uv venv ~/.cache/a11y-proof/venvs/nougat --python 3.11
uv pip install "nougat-ocr==0.1.17" torch==2.4.1 torchvision==0.19.1 transformers==4.38.2 timm==0.5.4 albumentations==1.3.1 "pypdfium2<5"   # CPU wheels
/usr/bin/time timeout 1200 nougat in/dev3.pdf -o out/nougat-base --no-skipping   # default model 0.1.0-small
cd a11y-stem && uv run python ~/.cache/a11y-proof/parsers/check_allow.py   # allowlist / converter on sample LaTeX
```

Model revisions seen today:
- docling-layout-heron `8f39ad3c`;
- docling-models `v2.3.0` (`fc0f2d45`);
- CodeFormulaV2 `ecedbe11`;
- MinerU-4_models_onnx `358310b4`.

## Appendix B. Sources read

- `PDF-RECOMMENDATION.md` §1-2 and its challenge. Docling end to end on the dev gold, against
  `layout.py`:
  - heading precision 0.80 vs 0.877;
  - every heading at level 1;
  - display-equation recall 0.94 vs 1.00;
  - about 60 times slower.
- `pdf-eval-2026-10-09.tgz`:
  - `licence-deploy/results/licence_matrix.tsv`, `deploy_matrix.tsv`, `image_sizes.tsv`;
  - `landscape/sizes.tsv`;
  - `landscape/readme_olmocr.md`.
- `chatbot-baseline/report.txt`, `score_baseline.py`, `structure.py` and `extract.py`;
  `niche/chatbot-runs/usage.jsonl` and `scripts/run_chat.py`; `niche/VERDICT-2026-10-10.md`.
- `proof/PREREGISTRATION.md` (SHA-256 `8127b143…` for version 1, `2c16815a…` for version 2) and `proof/selection/`.
- a11y-stem `README.md`, `docs/eval.md`, `docs/design/beta-architecture.md` and `docs/steps.md`.
- Licences fetched today (`~/.cache/a11y-proof/lic/`):
  - MinerU `LICENSE.md` and `README.md`;
  - Marker `LICENSE`, `MODEL_LICENSE` and `README.md`;
  - the surya-ocr-2 and surya_layout2 `LICENSE` files;
  - Hugging Face licence tags for the Docling, MinerU, Surya, olmOCR and Nougat models;
  - PyPI metadata for docling, marker-pdf, surya-ocr, mineru, pymupdf4llm, olmocr,
    nougat-ocr and latex2mathml.
