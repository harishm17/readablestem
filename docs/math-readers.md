# Reader B: a second reading of a handwritten expression

Code: `src/readablestem/models/math_readers/`; tests: `tests/test_math_readers.py`,
`tests/test_math_readers_launch.py`, `tests/test_math_reader_local.py`. Status: the adapters
exist and **one pipeline step calls one of them**: `pipeline/image_page_reconcile.py`, when
`IMAGE_PAGES_SECOND_READER` names it (off by default; "Using a reader in the pipeline" below).
All three readers are off, and nothing else reads their output.

A reader takes one expression crop (a PNG) and returns LaTeX candidates, best first, each with
the confidence the model reported about itself (not calibrated, and not comparable between
readers). The purpose is a reading that does not make the same mistakes as the page pass's, so
that where the two differ the tool can ask a person instead of trusting either.

## The protocol

`MathReader.read(ReadRequest(image_png)) -> ReadResult(candidates, call)`.

- `ReadRequest` accepts only PNG data up to 4 MiB (`ValueError` otherwise, before any
  reservation). Cut the crop tight and render it large.
- `ReadResult.call` is a content-free `ReaderCall`: reader name, model id, an `Outcome` code
  (`ok`, `refused`, `invalid_output`, `timeout`, `api_error`), sends, tokens, dollars, latency.
- A reader never raises for a provider error, a refusal, a timeout or unreadable output; it
  returns no candidates and the outcome says why. It raises `BudgetExceeded` when a spend
  reservation is refused (nothing is sent) and `ConfigError` or `UnknownModelError` when it is
  built without its key or with an unpriced model.
- Candidates are cleaned (math delimiters and control characters removed, at most 2,000
  characters, at most five, distinct).

## The three readers

| Reader | Setting | Key | What makes it differ from the page pass |
|---|---|---|---|
| `claude` | `MATH_READER_CLAUDE_ENABLED`, `MATH_READER_CLAUDE_MODEL` (default `claude-sonnet-5-5`), `MATH_READER_CLAUDE_EFFORT` | `ANTHROPIC_API_KEY` | Another model than the page pass's, its own instructions (`prompt.CLAUDE_INSTRUCTIONS`: structure first, each symbol settled before the whole, the pairs handwriting confuses), and different input: one tight high-resolution crop with no page picture and no document text |
| `openai` | `MATH_READER_OPENAI_ENABLED`, `MATH_READER_OPENAI_MODEL` (default `gpt-6-luna`) | `OPENAI_API_KEY` | A different model family; sent with `store: false` like every OpenAI request here |
| `local` | `MATH_READER_LOCAL_ENABLED`, `MATH_READER_LOCAL_DIR`, `MATH_READER_LOCAL_THREADS` (default 2), `MATH_READER_LOCAL_TIME_S` (default 20) | none | A small image-to-LaTeX model that is not a language model and has no prompt (TexTeller, below); runs in this process, so nothing is sent anywhere. See "The local reader" |

Both are built by hand over HTTP (`transport.py`) rather than through the step clients, which
are shaped for the pipeline (a `Step`, the job's call cap, the server-side refusal fallback).
So a Claude refusal is a refusal here: `stop_reason == "refusal"` is `refused`. The two
instruction texts are files, `models/prompts/math_reader.md` (OpenAI) and
`models/prompts/math_reader_claude.md` (Claude), loaded by `prompt.py`: a step now uses a reader,
so the eval guard (`scripts/eval_guard.py`) asks for `data/eval/latest-image-pages.json` when
either changes. They moved there with their text unchanged.

## Spend, timeouts, retries

The send loop (`engine.py`) follows `models/openai_client.py`:

- The worst case of one send is reserved before it (`models.pricing.estimate_max_cost_usd`
  for the actual image) and settled at the usage the reply states. `count_call=False`: a reader
  is an optional extra reading, so it holds dollars under every cap but does not use up the
  job's call cap.
- `MATH_READER_MAX_USD_PER_JOB` (default $0.50) is a sub-cap for every reader of one job
  together, inside the job's own caps (`engine.ReaderBudget`, shared by the readers
  `build_readers` makes). Past it a read raises `BudgetExceeded("reader_usd")` before the job's
  budget is touched: the caller treats that as "no second reading for this expression", never
  as a failed job (the message code is `budget_reader_usd`, `web/messages.py`; it is only ever a note
  on a fix that stands: a failed fix with a `budget_` reason stops the whole job in `pipeline/run.py`),
  so second readings cannot use up the dollars the conversion needs.
- Each send is bounded by `MODEL_TIMEOUT_S`. Connection errors, timeouts and HTTP 408, 409,
  429 and 5xx are resent up to `MODEL_MAX_RETRIES` times, each resend reserving and settling on
  its own, with the same backoff and `retry-after` rule; a 429 pauses the gate of the provider ("anthropic"
  or "openai"), which the step clients of that provider share. A refused resend returns the failure already had.
- Charge per send: the reply's usage; an HTTP error or a connection that never left, $0; a
  timeout or a connection that broke after sending, the whole reservation.
- Logs carry the reader name, status, outcome, counts and dollars. Never the crop, the
  LaTeX, an error body or a key (the Anthropic key travels in `x-api-key`, the OpenAI key in
  `authorization`, neither in a URL).

## Turning one on

The `local` reader is not one of these: it sends nothing, so it is not in
`Settings.math_readers_on`, adds no recipient and does not change the privacy page or the
production check (`tests/test_math_reader_local.py`). The two below do.

Each reader sends a crop of the instructor's handwriting to a model provider. That can change
who the deployment's recipients are: with `MODEL_PROVIDER=openai`, the Claude reader makes
Anthropic a new recipient, and the other way round for the OpenAI reader.

One list says who receives what: `web/recipients.py` (`recipients(settings)`: the model
provider, each reader that is on, the host, tracing when on). The privacy page
(`privacy.html` and `_recipients.html`), the "Where it is processed" sentence and the account
deletion notes are all written from it, so the page names a reader's company as soon as the
reader is on. Before one is turned on in production:

1. Update the "Where it goes" row of `docs/vendor-information.md` (keep the sheet under 900
   words, `tests/test_web_record.py`) and the table in `docs/security.md`; read the rendered
   `/privacy` page, and check the provider's retention terms for the key used.
2. A new provider (not Anthropic or OpenAI) needs a name in `web/recipients.py` and a branch
   in `_recipients.html`.
3. The guard: a production start is refused while a reader is on and the privacy page,
   rendered with the production settings and stripped to text (`web.recipients.notice_text`),
   does not name every company that receives pictures. `readablestem ops launch-check` lists
   the same, and now also reads the rendered page for the model provider, analytics, tables,
   notation and Microsoft items rather than the template's source.

The page's wording about handwriting ("not converted") stays true until a later change uses a
reader's output. The pipeline now does for a reader that sends nothing (`local`); a reader that
sends a crop to a provider is only used where a caller hands it to `convert_pdf(readers=...)`
(see below), and the checklist above applies before one is turned on.

## The local reader

`local_reader.LocalReader` runs TexTeller (OleehyO/TexTeller, an image-to-LaTeX transformer of
about 300 million parameters) with the ONNX Runtime the app already uses for its table model.
There is no new Python dependency (NumPy, Pillow and onnxruntime are in the lock) and no
network access at run time. It returns one candidate with no confidence, costs $0, and reports
`reader="local"`, `model="texteller"` in `ReaderCall`. `build_readers` makes it when
`MATH_READER_LOCAL_ENABLED=true` and raises `ConfigError` without `MATH_READER_LOCAL_DIR`.

**Why this one.** On a 74-expression practice set (10 composed pages of MathWriting test ink,
2026-11-03, not in this repository) a reader that disagrees with the page pass is a better flag
than the page pass's own "required" decisions. Of the 21 expressions the page pass read wrong
(spoken form not equal to the gold), its own required review caught 10 and flagged 17 right
readings; disagreement with TexTeller on the clean crops caught 17 and flagged 10 right ones
(8 of the 11 wrong readings that had no review at all). One set, 74 expressions, one run: a
signal to confirm on a larger set, not a result.

**Licence (read 2026-11-03; not legal advice).**

| Part | Licence | Source |
|---|---|---|
| TexTeller code | Apache-2.0 | `LICENSE` of github.com/OleehyO/TexTeller |
| TexTeller weights (huggingface.co/OleehyO/TexTeller at the revision pinned in `local_weights.py`; the `TexTeller_en` variant too) | Apache-2.0 | the model cards' `license:` field and the README badge |
| Training data: Tex80M (80 million pairs: LaTeX-rendered formulas and a handwritten subset), and the older 550K set the first versions used | Apache-2.0 (card of `OleehyO/latex-formulas-80M`); OpenRAIL (card of `OleehyO/latex-formulas`) | the dataset cards |

The weights licence allows use in a free public service, so the reader was built. It does not
settle two things. First, the handwritten part of the training data is, in the authors' words,
"collected entirely from existing open source work, which includes all test sets". The public
handwritten-formula sets are mostly non-commercial or research-only (MathWriting is CC BY-NC-SA
4.0), and whether that carries over to a model trained on them is not answered by the licence
text. Second, the model has probably seen the MathWriting test ink the practice set is made
of, so 0.66 may be too high. The variant that was trained from scratch with the benchmark test
sets removed (`TexTeller_en`) scores 0.554 on the same 74 clean crops; as a flag it catches 16
of the 21 and flags 17 right readings. Whether to turn the reader on in production is the
owner's decision after reading both points.

**Measurements** (a 16-core box under load average 3 to 7, ONNX Runtime 1.31.0, the 74 clean
crops of the practice set, one run each; time per crop includes the greedy one-beam decode and
the image preparation):

| Files | On disk | Resident, peak | Address space, peak | Time per crop | Exact (`exact_lenient`) | As a flag: wrong readings caught of 21, right ones flagged |
|---|---|---|---|---|---|---|
| float32, PyTorch (`texteller` package, 8 threads) | 1.2 GB | not measured | not measured | 1.6 s | 0.662 (49) | 17, 10 |
| float32 ONNX, 3 files, 2 threads | 2.1 GB | 3.0 GB | 4.2 GB | 1.7 s (p95 2.1) | 0.649 (48) | 17, 10 |
| float32 ONNX, one merged decoder, 2 threads (6 crops) | 1.2 GB | 2.3 GB | 3.5 GB | 1.8 s | not scored | not scored |
| **int8, 2 threads (what the build makes)** | **0.6 GB** | **1.05 GB** | **2.3 GB** | **1.07 s (p95 1.21)** | 0.554 (41) | 18, 15 |
| int8, 1 thread | 0.6 GB | 1.05 GB | 2.1 GB | 1.94 s (p95 2.12) | same files | same files |
| float32 encoder, int8 decoder, 2 threads | 0.9 GB | 1.3 GB | 2.6 GB | 1.9 s | 0.622 (46) | 16, 9 |
| int8 encoder, float32 decoder, 2 threads | 1.3 GB | 2.6 GB | 3.8 GB | 1.9 s | 0.608 (45) | 17, 13 |

Loading takes 3 s (int8) to 6 s (float32) and happens on the first read. The int8 rewrite
costs 8 of 74 exact readings (0.649 to 0.554) and adds false alarms as a flag (15 right
readings flagged instead of 10) while still catching 18 of 21: a worse reader and a still
useful flag. Per-channel int8 broke the model (0 of 74 right) and is not used. The float32
files need 3 GB resident and 4.2 GB of address space, which cannot fit the conversion child's
cap (`CONVERSION_MEMORY_MB`, default 3072), so the image ships only int8. The mixed
(float32 encoder) option would give 0.622 but peaks at 2.6 GB of address space on its own, which
leaves no room under the cap next to PyMuPDF and the table model. Measured as the child runs
it (PyMuPDF imported, the table model loaded, a 3072 MB address-space cap, `MALLOC_ARENA_MAX=2`),
the int8 reader read five crops at about 1 s each with a peak address space of 2.07 GB and
1.1 GB resident. The experiment's scan-degraded crops cost the float32 model 13 points (0.527);
the int8 files were not run on them.

**The image.** `docker build --build-arg INSTALL_MATH_READER=true .` (on Railway, the service
variable `INSTALL_MATH_READER=true`) adds a build stage that downloads the five files at the
pinned revision, refuses any whose SHA-256 or size differs, rewrites the three ONNX files with
int8 weights and puts them in `/app/math-reader` (the image grows by about 0.6 GB; the image
sets `MATH_READER_LOCAL_DIR` to it). `onnx` and `onnxruntime` are installed in that stage only.
Without the argument the directory is empty and the stage costs nothing. On Cloud Run
`gcloud builds submit --tag` cannot pass build arguments: change the `ARG` default instead, as
for LibreOffice (`docs/deploy.md`). The checksums pin the
float32 sources; the int8 files are made at build and are not pinned. Without Docker:
`python -m readablestem.models.math_readers.local_weights --dir .local/math-reader` (needs
`onnx`; `--from DIR` uses float32 files you already have, `--fp32` keeps them). The model tests
run against real files when `LOCAL_READER_TEST_DIR` points at that directory and skip otherwise.

**How it runs in the pipeline** (`IMAGE_PAGES_SECOND_READER=local`): inside the conversion
child, which already has the address-space cap, the CPU limit and the job's time limit, and
which exits after one job, so the model is loaded lazily on the first handwritten expression
(3 s, 1 GB resident) and is gone with the child. Threads are capped at 2
(`MATH_READER_LOCAL_THREADS`). Each read has its own time budget (`MATH_READER_LOCAL_TIME_S`,
20 s, about twenty times a normal read); the caller should also stop asking once a share of the
job's time limit is spent. A read that runs out is `timeout` with no candidates, which the
caller treats as "no second reading", as with the dollar sub-cap. A page of 20 expressions costs
about 20 s of wall time on two threads. The service's memory allowance must go from 1 GB to
2 GB while a child holds the loaded reader (`docs/deploy-railway.md`). The caller stops asking once
`IMAGE_PAGES_SECOND_READER_TIME_S` (default 120 s) of one job is spent: the expressions after it get
a note and no second reading.

**Limits of the evidence.** 74 expressions from one body of ink, one run each, clean crops:
the intervals are about 0.11 either side. The reader was not tried on camera photos or real
instructor pages, and the crops it gets in production are cut by this tool's own layout step,
which the practice set's are not. Four of the 21 wrong readings were misread the same way by
both readers, so a second reading cannot catch them.

## Using a reader in the pipeline (`pipeline/image_page_reconcile.py`)

`IMAGE_PAGES_SECOND_READER` is `off` (default), `local`, `claude` or `openai`, and the reader it
names must also be enabled (`MATH_READER_LOCAL_ENABLED` and so on; `Settings` refuses a start
otherwise). With it on and a page read from an image (`FEATURE_IMAGE_PAGES`), `convert_pdf` reads
the crop of each handwritten **display** equation with that reader after every other step,
compares the result with the reading on the fix (`math_canonical.compare`: the same notation, or
the same words spoken, is agreement; a letter style that differs is a difference) and records one
note on the fix, a `Check` named `image_page.second_reader`:

| Code | When | Shown to the reviewer | Else |
|---|---|---|---|
| `second_reader_agrees` | the two readings agree | no (a passed check; the record keeps it) | the item stays a required decision: handwritten maths is always asked (`enforce_maths_required`), and two readers of the same ink can be wrong the same way, so agreement is not acceptance |
| `second_reader_differs` | they differ | yes: "A second reader looked at the picture of this equation and read it differently ..." | where the difference is at most 3 stretches of at most 6 LaTeX tokens, `Element.alternatives` is filled from `reading_alternatives.from_reading` (only options that convert, at most 3 each): the review card's choices (docs/web.md) |
| `second_reader_unavailable` | no answer: timeout, refusal, invalid output, an error, a reading that does not pass the LaTeX checks, the job's time share for second readings spent, or a spend limit of the job's own | yes | nothing else changes |
| `budget_reader_usd` | the readers' own dollar share (`MATH_READER_MAX_USD_PER_JOB`) is spent | yes | nothing else changes; no more reads are made |

**A reader's spend limit never stops the job.** The notes are checks on a fix that stands. A failed
fix whose reason starts `budget_` is read by `pipeline.run._budget_scope` as "the job's money is
gone" and stops every other step, so `budget_reader_usd` is only ever a check code and never a
`Fix.reason` (`tests/test_image_page_reconcile.py`, the sub-cap test, runs a real `ClaudeReader`
over a sub-cap too small for one read and checks the job goes on). A reader that raises, for any
reason, costs that one expression its second reading; a `ConfigError` (a missing key or model file)
stops the asking and notes the rest.

What it reads: display equations of an image page (`IMAGE_PAGE_REASON`) with a crop, a reading on
the fix and no `single_reading_crop_failed` note. Inline formulas have no crop of their own and are
not read. The choices are made against the fix's own LaTeX, which is the string
`ReadingAlternatives.latex` carries.

**Which reader runs where.** The conversion child has no keys and no network, so only `local` is
built there (`image_page_reconcile.default_readers`). `claude` and `openai` are used where a
caller passes `convert_pdf(readers=build_readers(...))` (the CLI, the evals, tests): in the hosted
service they would need the parent to read the crop for the child, which is not built, so with
them chosen and no readers passed nothing is read. Their dollars go through the job's budget
(`ReaderBudget`) and are added to the document's cost, not to its call count.

The counts are logged without content (`second_reader_done`: reader, agreed, differed, with
choices, unavailable, over budget, dollars) and traced as a `second_readings` span.
