# Addendum 4: the held-out outputs that exist, and how they were made

Written 2026-10-13 at about 16:15 local time, after Addendum 3 (no API credit). Registration and
Addenda 1 to 3 stand.

## What was run on the held-out sections

Only systems that need no model call:

1. **a11y-stem, headings task** (registration 7.2, 6.1: no model call, run once).
   `a11y-stem eval pipeline --task headings --set heldout --heldout-dir <dir with the
   registered answer key> --max-usd 0.01 --cache <work>/eval-cache --date 2026-10-13`,
   code commit `e9b5979f043b56b1bbb7b2f3742fd87a769e7e51` (the pipeline behaves as
   `293160e`: comment edits only; later commits touch the proof harness, tests and docs). It reads the same 15
   PDFs the parsers read (SHA-256 sets compared equal). Result file `headings.json`
   (sha256 `bcd5a5e250dcf6d31899eac6eb1fb32a45dc991d11c16648386df85d1194b598`), items
   `headings-items.jsonl` (`b6b8c30530455c81ffe93f0a0978859fb3fae2c30823c37b9e5492e01dfb61cf`).
   0 model calls, $0.
2. **Docling (defaults), MinerU (basic tier), PyMuPDF4LLM**, through `proof/parsers`
   (`parsers-bench proof`), once per section, a fresh process each, CPU only, 30-minute limit,
   `--no-wait` (the shared machine was never quiet: load average 4 to 12 during the runs, so
   times and memory are upper bounds and every run is marked as not started on a quiet
   machine). All 15 sections of each arm finished (`ok`).
3. **Docling with formula recognition**: started, stopped after one section by hand
   (chemistry 14.7, 11 pages, `ok`, 1,495 s = 136 s a page, 3.3 GB). At that speed the sections
   of 14 pages and more (7 of the 15) cannot finish in 30 minutes, and the whole arm would
   take about six hours. It was stopped to spare the machine; it is **not scored and not
   reported** beyond this line (Addendum 1, D2 is therefore not carried out).

Everything is in the repository under `proof/interim/heldout-2026-10/` (107 files, tree
hash `6273d2adc6aa0f5b7063cb70de2df74805892435795b3181bf11da062281d7d9`: sha256 over
the sorted `path NUL sha256(file) newline` lines), the report is
`proof/interim/INTERIM-heldout-2026-10.md` (sha256
`a480c9ca86441b504e052b070a093003b8f5c6a8925438cd0ccdf02b7e0f54e0`), rebuilt by
`make proof-interim`. Scoring is the same code the proof stages use.

## What was not run

The a11y-stem document, equations and figures tasks, the chatbot arms and the figure judge:
they need paid model calls (Addendum 3). The family A comparison and the equation half of
family B cannot be made. The parsers' numbers are published as parser-only numbers, and the
heading comparison is the heading half of family B only (k = 8 as in Addendum 1, D3).

## Spend

The model calls of the failed attempts (Addendum 3) were refused or billed before the credit
ran out; the parsers and the headings task cost $0. The dev-sample rehearsal that preceded the
run (chatbot luna arm, 2 runs, on three Physics sections, and its figure judge) cost $0.127
and is in the committed development bundle.
