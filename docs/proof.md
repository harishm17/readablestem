# One-command reproduction (`make proof`)

`make proof` (or `uv run a11y-stem proof`) turns the recorded run in `proof/bundles/` into
`PROOF.md`: every table, with its intervals, its limits and the licence notices. It needs no
API key, no network and no money, and it writes the same bytes on every machine that has
the dependency versions in `uv.lock`.

`PROOF.md` states what a machine can measure about a11y-stem's output on a set of OpenStax
sections. It does not measure conformance with WCAG or any other standard, and nothing in it
is a claim that a document is accessible. a11y-stem assists a person who makes course
material accessible.

## Quick start

```
git clone <this repository> && cd a11y-stem
make proof            # replay: uses the recorded run; no key, no network, no cost
make proof-check      # only check the pins and the hashes of the recorded run
make proof-list       # the proof sets in proof/sets/
```

`uv` and Python 3.12 or newer are the only requirements (`uv sync` runs by itself on the
first `make`). A clean machine can use the container instead (see "Container" below).

## What a proof set is

A proof set is one JSON file in `proof/sets/` (`proof/sets/index.json` names the default). It
holds:

- the OpenStax sections (book slug and page slug) and how to rebuild their answer key from
  the OpenStax archive (`gold fetch`), with the retrieval date, whether retired editions are
  allowed, and optionally the archive release to read;
- the SHA-256 of the answer key's `manifest.json`, which lists the SHA-256 of every answer
  file: a changed or extra answer file stops the run;
- how often each task is repeated (`document`, `equations`, `headings`, `figures`);
- for a held-out set, the pre-registration file and its SHA-256, and the decisions that must
  be made before a live run (`signoff`);
- where the recorded run lives (`bundle`), the limits to print, and a suggested spend cap.

The development sample (`smoke-physics-2`, three Physics sections the pipeline was built on)
is the only set whose bundle is committed in this branch. It shows the command works end to
end. It is not a held-out result, and its report says so on top.

## Two modes

### Replay (default)

```
pins -> recorded run intact -> code identity -> replay every (task, repeat)
     -> compare with the recorded reports -> stages -> PROOF.md
```

The recorded run (the bundle) holds everything the outside world told the pipeline: each
model answer, each second-check verdict, each axe-core result, the PDFs that were printed,
and the identity of every equation and figure picture. Replay runs the real pipeline and
the real scoring code on those, so the layout analysis, the checks, the review triage, the
HTML assembly and the metrics are computed again from scratch. Then it compares every
metric and every cost with the numbers written when the run was recorded. If any differ,
the code in this checkout is not the code that made the recording, and the run stops
(exit code 4). `--allow-drift` writes the report anyway, with a banner that says so.

Replay refuses to run when:

| Situation | Exit code |
|---|---|
| a pin does not match (answer key, pre-registration copy, bundle hashes, bundle for another set) | 3 |
| the pipeline version, a prompt version, a model name or a setting that changes results differs from the recording | 4 |
| the replayed numbers differ from the recorded ones | 4 |
| the pipeline asks for a model answer the bundle does not hold | 5 |
| bad arguments, an unknown set, a missing cap for a live run | 2 |
| `--require-complete` and a part of the proof is not computed | 1 |

A replay never invents an answer: a request with no recorded answer is counted and stops
the run.

### Live

```
make proof-live MAX_USD=2 ENV_FILE=.env RECORD=proof/bundles/smoke-physics-2
```

Live needs `OPENAI_API_KEY` (read from the environment, or from `ENV_FILE` through
`uv run --env-file`; it is never printed or recorded), a hard spend cap `--max-usd`, and
Chromium for printing the PDFs and for axe-core:

```
uv sync --group browser && uv run playwright install --with-deps chromium
sudo apt-get install -y fonts-noto-core fonts-liberation     # the fonts the print uses
```

A live run:

1. checks the pins; refuses a held-out set unless the checkout is clean, the pinned
   decisions are confirmed with `--confirm-signoff`, and the client is the real one;
2. fetches the answer key if the checkout has no committed copy, prints every section to
   PDF and builds every input before the first model call (a failed print or download stops
   the run with nothing spent);
3. runs every (task, repeat) once with the real models behind one spend cap, with
   `store=False` and no content in the logs, recording each answer;
4. calls the `record` hook of each stage (the chatbot and the parsers are recorded
   afterwards, with `--add-systems`);
5. writes the bundle (to `--record`, by default outside the repository), then replays it, so
   `PROOF.md` is always what `make proof` writes from that bundle.

A run that hits the cap stops with exit code 1 and writes no bundle: a budget stop is never
scored as if the document had been converted. With `--keep-finished` the runs that had
finished are kept instead (the one the cap stopped is dropped whole), the bundle says it is
incomplete, and `PROOF.md` opens with a banner saying how many planned runs are missing. `--fake` swaps in the offline fake client to
check the plumbing; its report carries a banner and a held-out set refuses it.

A new live recording will not give the same numbers as the committed one. Model output
varies between runs; this is why the held-out set repeats its tasks and shows each run.

### Checking the sources

```
make proof-sources
```

fetches the answer key from OpenStax again and compares it with the pinned hash, then prints
the PDFs again and compares page counts and the text layer with the recorded PDFs (bytes
differ: Chromium stamps a creation date and fonts differ between machines). Scoring always
uses the recorded PDFs. This needs the network and Chromium.

## The recorded run (`proof/bundles/<set>/`)

| Path | Content |
|---|---|
| `bundle.json` | set, date, code identity (pipeline and prompt versions, settings, commit), runs, PDFs, spend, and the SHA-256 of every other file |
| `answers/<task>.run<N>.jsonl` | model answers, second-check verdicts and axe-core results of one run |
| `reports/<task>.run<N>.json` | that run's report without per-item rows: the numbers a replay must reproduce |
| `media/pdfs/` | the exact PDF every system was given |
| `media/*.jsonl` | per equation and figure: picture identity, size, text layer, context |
| `systems/<arm>/` | other systems' outputs in the form `a11y_stem.proof.intermediate` defines |
| `gold/` | optional copy of the answer key (CC BY, with `ATTRIBUTION.md`) |

It holds content derived from CC BY OpenStax sections and nothing else: no keys, host names
or user data. Course material from other sources never goes into a bundle.

## The other systems and the other parts

The a11y-stem arm is in the bundle as model answers. Everything else in `PROOF.md` is
computed from files the bundle holds, by stages (`src/a11y_stem/proof/stages.py`):

| Stage | Module | What it reads |
|---|---|---|
| invented-content rate | `proof/invented.py` | a11y-stem's HTML (rebuilt by the replay) and the outputs below, against each PDF's text layer; rules in `eval/invented/` ([eval-invented.md](eval-invented.md)) |
| comparison with the chatbot recipe | `proof/comparison.py` | `systems/chatbot-*/` |
| comparison with open PDF parsers | `proof/parsers.py` | `systems/<parser>/` |
| human labels and judge agreement | `proof/labels.py` | not written: those labels are the author's, and none exist yet; the stage prints "not computed" |

`proof/systems.py` is the shared scorer: each output file is read by one reader
(`eval.invented.read`), its maths converted the way a11y-stem converts its own model's LaTeX,
aligned to the gold equations by the document task's code, and its headings matched by the
headings task's code. The stages print the claims of preregistration section 8.3 (family A:
the chatbot, 5 comparisons; family B: each parser, 2 comparisons each, Bonferroni-corrected)
and every single-system number.

### Recording the held-out set

The held-out set (`heldout-2026-10`, registered in `proof/PREREGISTRATION.md`) is recorded in
three steps, each with its own spend cap, by one script:

```
ENV_FILE=/path/to/.env proof/record-heldout.sh     # a11y-stem $12, chatbot arms and judge $10
```

It needs API credit, a clean checkout, Chromium and `make -C proof/parsers setup`, and nothing
else may call the API while it runs (an empty balance or a rate limit makes calls fail, and a
failed call is not a system's answer). Each step can be interrupted and started again:
`--resume` reads back the a11y-stem runs that finished, and the chatbot and parser steps skip
what they already recorded. Copy the result to `proof/bundles/heldout-2026-10/`, make that
set the default in `proof/sets/index.json`, and `make proof` writes `PROOF.md` from it.

### Recording the other systems

They are recorded into a finished bundle, each with its own cap, so a failure in one never
loses another:

```
# the chatbot recipe: one cap for the calls and the figure judge together
a11y-stem proof --set heldout-2026-10 --add-systems chatbot --max-usd 10
# the open parsers (CPU, hours, resumable; needs `make -C proof/parsers setup` once)
a11y-stem proof --set heldout-2026-10 --add-systems parsers
```

`--arms` limits them. The chatbot recipe repeats `niche/chatbot-runs` of the research
(`proof/chatbot.py`: the PDF as a file and a 150 dpi image of every page, one prompt, no
system instructions, `store=False`, at most 64,000 output tokens). A reply that was cut off
or empty is a failure of the section and is not asked again. The parsers run in
`proof/parsers` (a separate uv project, one pinned environment per tool); a crash, an empty
output or a 30-minute limit also scores the section as found nothing. Parser outputs are the
tools' Markdown exports (images in them cut to a marker). After `--add-systems` the bundle's
manifest is rewritten; a system that is already in it is never overwritten.

## Wording rules

The report builder refuses to write a file in which the words compliant, compliance,
verified, certified, guarantee or "WCAG conformance" appear outside a marked quotation. The
tool assists; the tables report agreement with an answer key and what a machine can detect.

## Container

`proof/Dockerfile` has two targets. `replay` (the default) holds the locked dependencies and
the repository, and needs no browser. `full` adds Chromium and the print fonts for
`--verify-sources` and `--live`.

```
docker build -f proof/Dockerfile -t a11y-stem-proof .
docker run --rm -v "$PWD/out:/out" a11y-stem-proof a11y-stem proof --out /out/PROOF.md

docker build -f proof/Dockerfile --target full -t a11y-stem-proof:full .
docker run --rm --env OPENAI_API_KEY -v "$PWD/out:/out" a11y-stem-proof:full \
    a11y-stem proof --live --max-usd 2 --record /out/bundle --out /out/PROOF.md
```

The container runs as a non-root user and keeps its working files in `/tmp`.

A dev container needs nothing more than a Python 3.12 image with `uv` installed (for
example the `ghcr.io/astral-sh/uv:python3.12-bookworm` image); then run `make proof`.

## Files

- `src/a11y_stem/cli/proof.py`: the command.
- `src/a11y_stem/proof/`: `registry` (sets and pins), `bundle`, `replay`, `runner`,
  `engine` (the flow), `stages`, `a11y_tables`, `report`, `document` (PROOF.md), `stats`,
  `goldinfo`, `sources`, `intermediate`; the other systems: `chatbot`, `judging`, `systems`,
  `systems_stage`, `systems_tables`, `invented`, `comparison`, `parsers`.
- `proof/sets/`, `proof/bundles/`: the sets and their recorded runs.
- `tests/test_proof_*.py`: offline tests of all of the above.
