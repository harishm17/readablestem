# The registered files, and how far they can be checked

The held-out set was fixed in a registration before any system ran on it. This page lists the
files that registration rests on, what you can check from this repository alone, and what you
cannot.

## Files

| File | What it is |
|---|---|
| [`PREREGISTRATION.md`](PREREGISTRATION.md) | the registration, as frozen (SHA-256 `8127b143...`). Published with the names of the people who asked for this evidence and the paths on the author's machine removed (see docs/history-note.md) |
| `PREREGISTRATION-ADDENDUM-1.md` to `-5.md` | what was frozen later (the parser plan, prompts, caps), what happened to the first attempts, and what was run. Each is hashed in `PREREG.sha256` (1 to 4) or `PREREG-after-review.sha256` (5) |
| [`PARSERS-PLAN.md`](PARSERS-PLAN.md) | the parser plan Addendum 1 cites by hash |
| [`DECISION-retired-editions-2026-10-13.md`](DECISION-retired-editions-2026-10-13.md) | the decision to use retired first editions (ADR 0001, question 2), dated before any system ran |
| [`selection/`](selection/) | how the 15 sections were chosen: `features.py` (properties of each section), `draw.py` (the seeded draw), `budget.py` (the page-count rules), their outputs (`features.json`, `draw.json`, `budget.json`), the logs, `rules-before-draw.sha256`, and the licence table. The scripts still name directories on the author's machine |
| [`scorer-readiness/`](scorer-readiness/) | the check of what the scorer gives a perfect transcriber (28 of 40) |
| `PREREG.sha256` | the SHA-256 of all of the above and of the answer key's files |

## What you can check from this repository

```
cd proof && sha256sum -c --ignore-missing PREREG.sha256
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
- `tests/test_proof_registered.py` does the three checks above.

## What you cannot check

- **When.** There is no outside timestamp. The only times are the files' own modification
  times, which anyone can set, and this repository's commits, which are local until pushed. The
  registration was written at 11:00 local time on 2026-10-13 and first committed in this
  repository at 14:01; the machine that held the original is the author's. `PREREG.sha256` was
  appended to as the addenda were written (it was last written at 16:10, when Addendum 4
  was added), so its first version is not recoverable; the hash of
  `PREREGISTRATION.md` in it is the one that the first addendum and the headings run cite. To
  make the claim "frozen before any system ran" testable, publish the hash of this folder's
  `PREREG.sha256` somewhere with an independent clock (a signed tag on a pushed commit, a gist,
  or an OpenTimestamps proof) and then say so here. That has not been done.
- **What the author saw before the draw.** `selection/scan_used.py` and `features.py` show how
  development material was excluded; that they were all of it rests on the author's word and on
  the search the registration describes.
- **The registered text's local paths.** Section 14 of the registration points at
  a folder on the author's machine. The same files are in this folder, with the same
  relative layout.
