# The registered files, and how far they can be checked

The held-out set was fixed in a registration before any system ran on it. This page lists the
files that registration rests on, what you can check from this repository alone, and what you
cannot.

## Files

| File | What it is |
|---|---|
| [`PREREGISTRATION.md`](PREREGISTRATION.md) | the registration, **version 2** (SHA-256 `d4d3af03...`). It differs from version 1 (SHA-256 `8127b143...`) only in wording: the names of the people who asked for this evidence, an authorship line and paths on the author's machine were removed or made generic. Its sections, metrics, rules, thresholds and set are unchanged. Version 1 is not published; its hash is kept (see below) |
| `PREREGISTRATION-ADDENDUM-1.md` to `-5.md` | what was frozen later (the parser plan, prompts, caps), what happened to the first attempts, and what was run. Each is hashed in `PREREG-v1.sha256` (1 to 4) or `PREREG-after-review-v1.sha256` (5). They are unchanged, and they and the interim headings result cite version 1's hash |
| `PREREGISTRATION-ADDENDUM-6.md`, `-7.md` | Addendum 6 was written on 2026-10-13 before any model saw a held-out section: the model-dependent parts run on Claude because the OpenAI account had no credit, the configuration, the judges, the budget rules. Addendum 7 was written after the run and records what happened, with the deviations. Both cite version 2's hash and are hashed at the end of `PREREG-v2.sha256` (they are not in `PREREG-v1.sha256`, which is version 1's list as it was) |
| [`PARSERS-PLAN.md`](PARSERS-PLAN.md) | the parser plan. Addendum 1 cites version 1 of it (SHA-256 `3ab0e878...`); this is version 2 (`4689eeb3...`), which differs only in the removal of the requesters' names and the added note on the registration's version |
| [`DECISION-retired-editions-2026-10-13.md`](DECISION-retired-editions-2026-10-13.md) | the decision to use retired first editions (ADR 0001, question 2), dated before any system ran |
| [`selection/`](selection/) | how the 15 sections were chosen: `features.py` (properties of each section), `draw.py` (the seeded draw), `budget.py` (the page-count rules), their outputs (`features.json`, `draw.json`, `budget.json`), the logs, `rules-before-draw.sha256`, and the licence table. Three scripts (`selection/features.py`, `selection/scan_used.py`, `scorer-readiness/draw.py`) named directories on the author's machine in version 1; in version 2 only those lines differ (see "How the scripts were run") |
| [`scorer-readiness/`](scorer-readiness/) | the check of what the scorer gives a perfect transcriber (28 of 40) |
| `PREREG-v2.sha256` | the SHA-256 of all of the above and of the answer key's files, for version 2 (the files are the ones in this folder) |
| `PREREG-v1.sha256` | the same list with version 1's hashes (renamed from `PREREG.sha256`, contents unchanged). Five lines differ from version 2: `PREREGISTRATION.md`, `PARSERS-PLAN.md`, `selection/features.py`, `selection/scan_used.py` and `scorer-readiness/draw.py`. The other lines, including every answer-key file, are identical |
| `PREREG-after-review-v1.sha256` | the hash of Addendum 5 (renamed from `PREREG-after-review.sha256`, contents unchanged). The addendum is the same in both versions, so this line holds for both |

## What you can check from this repository

```
cd proof && sha256sum -c --ignore-missing PREREG-v2.sha256
```

All the files above must say `OK` (the lines for the answer key are skipped by
`--ignore-missing`; `make heldout-gold` fetches the key and `make heldout-verify` checks it
against the registered hashes). Then:

- `python3 proof/selection/draw.py proof/selection/features.json /tmp/draw.json` writes a file
  identical to `selection/draw.json`: the draw is a function of the seed text
  (`a11y-stem held-out OpenStax set, pre-registered 2026-10-13`), which is hashed, and of
  `features.json`;
- `selection/budget.json` lists the 15 sections that survived the page rules, and they are the
  sections in `sets/heldout-2026-10.json`;
- `tests/test_proof_registered.py` does the three checks above, and also checks that version 2
  differs from version 1 in the five files listed above only, and that reverting the documented
  lines of the three scripts gives back version 1's hashes (including the hash of
  `features.py` in `selection/rules-before-draw.sha256`, taken before the draw).

## Versions of the registration

Version 1 was written at 11:00 local time on 2026-10-13. Version 2 was written later the same day,
before any model-dependent result on the held-out sections existed (the first live attempts
were cut off by the account's credit limit and produced no results: Addendum 3). The two
differ only in wording, as the table above says. The measures taken without any model (the
headings task and the open parsers, Addenda 4 and 5, `interim/`) ran under version 1, and the
addenda and `interim/heldout-2026-10/a11y-headings/headings.json` cite version 1's hash
(`8127b143...`) and the parser plan's version-1 hash for that reason. They are left as they
were. The code, the set file `sets/heldout-2026-10.json` and the README pin version 2's hash.

Text inside the registration and the parser plan that says `PREREG.sha256` refers to the hash
file of the version in question (`PREREG-v1.sha256` or `PREREG-v2.sha256`).

## How the scripts were run

- `selection/features.py`: `uv run python proof/selection/features.py OUT.json` from the
  repository root. Version 1 had the author's checkout hard-coded (`REPO`); version 2 takes
  the repository root from the script's location. Nothing else changed.
- `selection/scan_used.py`: `python3 proof/selection/scan_used.py [ROOT ...]`. Version 1's
  default roots were five directories on the author's machine (development checkouts, a
  research folder and two caches) and `/tmp`; version 2 takes the roots from the command line,
  then from `A11Y_SCAN_ROOTS` (separated like `PATH`), and otherwise scans this repository
  and `/tmp`. The scan of section 2 of the registration used the version-1 roots.
- `scorer-readiness/draw.py`: `python3 proof/scorer-readiness/draw.py sample.json <answer-key folder>`.
  Version 1 read the answer key from a fixed folder; version 2 takes it from the second
  argument (default `~/.cache/readablestem/heldout`, where `make heldout-gold` puts it). The
  output is byte for byte `sample.json`.

## What you cannot check

- **When.** There is no outside timestamp. The only times are the files' own modification
  times, which anyone can set, and this repository's commits, which are local until pushed. The
  registration was written at 11:00 local time on 2026-10-13 and first committed in this
  repository at 14:01; the machine that held the original is the author's. `PREREG-v1.sha256` (then
  `PREREG.sha256`) was appended to as the addenda were written (it was last written at 16:10, when
  Addendum 4 was added), so its first version is not recoverable; the hash of
  `PREREGISTRATION.md` in it is the one that the first addendum and the headings run cite.
  Version 2 is later than all of this, so it does not by itself show that the text was fixed
  before the run: that rests on version 1's hash, and on the claim that the two versions differ
  only in wording, which you can check for the scripts (above) but not for the prose without
  version 1's text. To make the claim "frozen before any system ran" testable, publish the
  hash of this folder's `PREREG-v1.sha256` and `PREREG-v2.sha256` somewhere with an independent
  clock (a signed tag on a pushed commit, a gist, or an OpenTimestamps proof) and then say so
  here. That has not been done.
- **What the author saw before the draw.** `selection/scan_used.py` and `features.py` show how
  development material was excluded; that they were all of it rests on the author's word and on
  the search the registration describes.
- **The registered text's local paths.** Version 1 of section 14 of the registration pointed at
  a folder on the author's machine. Version 2 says the files are in `proof/` of this repository,
  with the same relative layout. Some sentences still name scratch folders under `~/.cache`.
