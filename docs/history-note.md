# History note: original and public commit hashes

This repository's history was rewritten once, before it was made public, to remove personal
details (section 2). A rewrite gives every commit a new hash. Some documents, written
before the rewrite, cite commits by their original hash: `PROOF.md` ("Code: commit `297126f34137`"),
the bundle files under `proof/bundles/`, `proof/PREREGISTRATION-ADDENDUM-1.md` to `-7.md`, and several
pages under `docs/`. Those files are registered or recorded and have not been edited. This page maps
each original hash to the one in this repository, so the order of events can be checked.

Dates and the order of commits are unchanged. Apart from section 2, only the hashes differ.

## 1. How to look a commit up

`history-commit-map.txt` (next to this file) lists every original commit and its public
hash, one pair per line (`original public`). A line whose public hash is all zeros is a commit that
was dropped by the rewrite: three of them, a merge that became redundant and two commits whose only
change was a line that the rewrite had already made the same. For example:

```
grep ^297126f docs/history-commit-map.txt
git show $(grep ^297126f docs/history-commit-map.txt | cut -d" " -f2)
```

The tag `proof-heldout-2026-10` is a tag in this repository too. It points at the rewritten
version of the commit the original tag pointed at.

## 2. What the rewrite changed

- **Author, committer and tagger.** Every commit and the tag now carry the same name and address.
  Before the rewrite, 18 commits used a second address.
- **Words in a few documents.** In some versions of a few files, the rewrite replaced working
  names and notes about how the project was drafted, the names of the people who asked for this evidence,
  paths on the author's machine, references to private planning notes and a private network name
  with neutral wording. The same
  was done to commit messages where they appeared. The files concerned are `.gitignore`, `README.md`,
  `SECURITY.md`, `pyproject.toml`, `docs/decisions/0001-scope.md`, `docs/design/beta-architecture.md`,
  `docs/development.md`, `docs/deploy-railway.md`, `docs/launch.md`, `proof/REGISTERED.md`,
  `src/readablestem/pipeline/parse.py` (comments), `tests/test_gold_sample.py` and the five registered
  files in the next point.
- **Version 1 of the registration.** The table in `proof/REGISTERED.md` says that version 2 of the
  registration differs from version 1 only in wording: it removed the names of the people who
  asked for this evidence, an authorship line and paths on the author's machine. Version 1 is in the
  history of the author's private development repository. In this repository, the early
  versions of five registered files (`proof/PREREGISTRATION.md`, `proof/PARSERS-PLAN.md`,
  `proof/selection/features.py`, `proof/selection/scan_used.py` and `proof/scorer-readiness/draw.py`)
  have had those same lines reworded in the commits that first added them, with the wording
  version 2 uses for them. So **no commit in this repository holds the text that `PREREG-v1.sha256` was
  taken over**; the version 1 hash cannot be checked from this history (version 1 itself was never
  published; see "What you cannot check" in `proof/REGISTERED.md`). The files at the tip of `main` and at the tag are the version 2 files,
  and `cd proof && sha256sum -c --ignore-missing PREREG-v2.sha256` and `make proof-check` pass on
  them. The rewrite did not touch any file at the tip of `main` that is listed in
  `PREREG-v2.sha256` or `PREREG-after-review-v1.sha256`, and it touched no binary file.
- **Nothing else** was rewritten: no code, test or data file differs apart from the above, and
  the commit order, dates and messages (apart from the words above) are the original.

## 3. The commits that matter for the order of events

| Original | Public | Date | What |
|---|---|---|---|
| `2834022a211d` | `7b5776849d42` | 2026-10-13 14:01 -0500 | Version 1 of the registration is first committed |
| `b0c4f43dbc4f` | `b9b7afb076d1` | 2026-10-13 17:17 -0500 | Registered files, hash lists and Addendum 5 |
| `305d2a99e742` | `50aace698218` | 2026-10-13 21:10 -0500 | Registration version 2 (wording only) |
| `94bd6d57440e` | `eea561f89680` | 2026-10-13 23:16 -0500 | The chatbot arms and the figure judge are moved to Claude |
| `71eeef1f4698` | `f24f87590cc9` | 2026-10-13 23:25 -0500 | Held-out set limits name the Claude substitution |
| `297126f34137` | `add59dd8fa17` | 2026-10-13 23:27 -0500 | Addendum 6: the code commit of the held-out run (no held-out call before it) |
| `f39198d80a98` | `ea80de659b80` | 2026-10-14 03:45 -0500 | The recorded held-out run is committed |
| `1ba60a507eec` | `b39a2fc707b4` | 2026-10-14 03:46 -0500 | Addendum 7, written after the run |
| `f99466830cc2` | `499c189c9279` | 2026-10-14 04:17 -0500 | The commit the tag `proof-heldout-2026-10` points at |

The original tag `proof-heldout-2026-10` pointed at `f99466830cc2`; the tag here points at
`499c189c9279`. The commits made after `297126f` in the original history are after `add59dd8fa17`
here, so "committed `297126f` before any held-out call" reads "committed `add59dd8fa17` before any held-out call".

## 4. Every original hash cited in this repository

Found by searching all text files at the tip of `main` for any abbreviated or full hash of an original
commit. The last column is where it is cited (`file:line`, first few).

| Cited as | Original | Public | Cited in |
|---|---|---|---|
| `03a537c` | `03a537c5e413` | `af6e128bae9f` | `docs/development.md:137` |
| `4d574fb` | `4d574fbcd263` | `568366f8c4fe` | `docs/eval.md:742` |
| `0ecf511` | `0ecf511416ca` | `66c855454e59` | `docs/design/content-features.md:365` |
| `0fcb6f1` | `0fcb6f14cb8a` | `0689334c7937` | `docs/design/content-features.md:365` |
| `9ad41db` | `9ad41db63759` | `da88a483481c` | `docs/deploy.md:757`, `docs/design/content-features.md:365` |
| `dc891a2` | `dc891a28991d` | `6c90061476c0` | `docs/layout.md:1254` |
| `91697be` | `91697bebf718` | `f28faa968a5b` | `docs/eval.md:1480` |
| `293160e` | `293160ed1905` | `f2ce247a8c14` | `docs/eval-invented.md:127`, `proof/PREREGISTRATION-ADDENDUM-1.md:98`, `proof/PREREGISTRATION-ADDENDUM-4.md:14`, `proof/PREREGISTRATION-ADDENDUM-5.md:15` and 2 more |
| `c2773682c1c9`, `c2773682c1c919f8ff3d69cde40d313b1bfd5dd5` | `c2773682c1c9` | `6087e4fcdcf7` | `proof/PROOF-development-sample.md:16`, `proof/bundles/smoke-physics-2/bundle.json:5` |
| `501b5a2`, `501b5a2865fa1e5fda1c45006ffb51287f0bf7a6` | `501b5a2865fa` | `5af2486ba49d` | `proof/PREREGISTRATION-ADDENDUM-1.md:97`, `proof/PREREGISTRATION-ADDENDUM-2.md:8`, `proof/PREREGISTRATION-ADDENDUM-1.md:98`, `proof/PREREGISTRATION-ADDENDUM-2.md:29` |
| `e9b5979`, `e9b5979f`, `e9b5979f043b56b1bbb7b2f3742fd87a769e7e51` | `e9b5979f043b` | `7e870723dbf4` | `proof/PREREGISTRATION-ADDENDUM-2.md:19`, `proof/PREREGISTRATION-ADDENDUM-4.md:13`, `proof/PREREGISTRATION-ADDENDUM-5.md:10`, `proof/PREREGISTRATION-ADDENDUM-2.md:29` and 3 more |
| `ca034ad`, `ca034adcd3595c0b9b8fb3252b9c3d95eb0e3bdc` | `ca034adcd359` | `b3885acfd1ed` | `proof/PREREGISTRATION-ADDENDUM-5.md:11`, `proof/PREREGISTRATION-ADDENDUM-5.md:12`, `proof/interim/README.md:19` |
| `305d2a9` | `305d2a99e742` | `50aace698218` | `proof/PREREGISTRATION-ADDENDUM-6.md:93` |
| `0ccdf10` | `0ccdf106991d` | `70b13761559a` | `proof/PREREGISTRATION-ADDENDUM-6.md:92` |
| `94bd6d5` | `94bd6d57440e` | `eea561f89680` | `proof/PREREGISTRATION-ADDENDUM-6.md:92` |
| `71eeef1`, `71eeef1f469884a078559399d073a8e3804374ed` | `71eeef1f4698` | `f24f87590cc9` | `proof/PREREGISTRATION-ADDENDUM-6.md:91`, `proof/PREREGISTRATION-ADDENDUM-6.md:92` |
| `297126f`, `297126f34137`, `297126f341375e8433065614297e9f7d73778b75` | `297126f34137` | `add59dd8fa17` | `PROOF.md:14`, `proof/PREREGISTRATION-ADDENDUM-7.md:10`, `proof/PREREGISTRATION-ADDENDUM-7.md:63`, `proof/bundles/heldout-2026-10/bundle.json:5` |
| `6c8bbda` | `6c8bbda9fe47` | `daa93c7183ed` | `docs/eval.md:1861`, `docs/eval.md:1906` |

Hashes cited as `293160e` and `501b5a2` and the like are commits of the project's history as it was
when the text was written. The search finds only hashes of commits that exist in this repository.
A cited hash that is not in this table is either not a commit of this history or a different kind of
hash (a SHA-256 of a file is 64 digits and is not affected by the rewrite).
