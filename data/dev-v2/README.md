# Extended development set (dev-v2)

Backlog item T2-0 (`a11y-research/IMPROVEMENTS.md`). The original development set
(`data/gold`) is 30 physics equations at the scorer's ceiling: it cannot show the failures
the held-out run found. This set holds the classes it cannot see, so that Tier 2 accuracy
work can be built and measured without touching held-out data. Full description, counts
and how to run it: `docs/eval.md`, section "Extended development set".

| Path | What it is | Licence |
|---|---|---|
| `dev/openstax/<book>/<section>.jsonl`, `_book.json` | gold for OpenStax sections and key-terms pages: equations (MathML), headings, tables, figures, plus prose sub/superscript runs, code blocks and glossary entries, every record with its `classes` | CC BY 4.0 (OpenStax) |
| `dev/authored/<document>/` | documents written for this project: source, PDF, `gold.jsonl` (LaTeX built by Tectonic, Word exported by LibreOffice, HTML printed by Chromium) | MIT |
| `dev/manifest.json` | every dev file with its SHA-256, records and class counts | MIT |
| `check.sealed.tar.gz`, `SEAL.json` | the sealed check part (see below) | as above |
| `selection.json` | the rules and the choice: pinned book versions, targets, split salt, the dev sections with their overlap with the held-out text | MIT |
| `ATTRIBUTION.md`, `summary.md` | generated: books, authors, licences; items per class | |

## Rules it was built under

- No section of the 15 registered held-out sections is used, and no section of a book that
  has one. Chosen sections are checked against the registered text: the share of a
  section's word 8-grams (visible text, MathML removed; the measure of
  `proof/selection/features.py`) found in the held-out text must be below 0.05.
  `readablestem gold devset verify --overlap` recomputes the numbers (it needs the held-out
  archive cache, `~/.cache/readablestem/heldout/openstax`).
- Only CC BY 4.0 OpenStax books (retired first editions are allowed, see
  `proof/DECISION-retired-editions-2026-10-13.md`) and MIT material written for this
  project. Every record carries its licence; `ATTRIBUTION.md` lists the books.
- No model is called to build it. OpenStax gold is the published MathML, headings and
  tables; authored gold is known by construction.

## The sealed check part

`check.sealed.tar.gz` holds a small second part in the same layout as `dev/`. It exists for a
quick sanity check before a merge, and is not to be read while building: its sections come
from a hash split of the candidates (selection.json, `check_rule`) and are not named in
the repository; its authored documents are fresh draws from the same generators.

- `SEAL.json` records the archive's SHA-256, size, record count, the hash of its record
  ids and the totals per class, and no item name.
- `eval pipeline --set dev-v2 --part check` checks the archive against the seal, extracts it
  under `~/.cache/readablestem/dev-v2/`, keeps its caches and report outside the repository,
  refuses `--items`, and the report holds totals only.
- `readablestem gold devset verify` fails if the archive is not the sealed one.

The protection is a hash and a discipline, not secrecy: anyone can unpack the archive, and
the builder `authored/build_authored.py` can regenerate the four check documents
(`--only latex-exercises-chk ...`), so running it for a `-chk` document opens the part as
well. The rule is to run the check part, read the totals, and not open it.

## Commands

```
uv run readablestem gold devset summary                 # items per class, dev and check
uv run readablestem gold devset verify [--overlap]      # files vs manifest, archive vs seal
uv run readablestem eval pipeline --task document --set dev-v2 --fake
uv run readablestem eval pipeline --task headings --set dev-v2 --part check --max-usd 5
uv run readablestem eval equations --set dev-v2 --self-check
uv run python data/dev-v2/authored/build_authored.py --out /tmp/pool     # authored documents
uv run readablestem gold devset build --authored-pool /tmp/pool --date 2026-10-14 --force
```
