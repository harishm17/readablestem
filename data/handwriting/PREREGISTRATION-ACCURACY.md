# Registration: handwriting accuracy fixes, one run on a fresh held-out set (2026-10-22)

Written and hashed (`REGISTRATION-ACCURACY.sha256`) before any model call on the fresh set.
Nothing below changes after a number is seen. The write-up is
[docs/eval-handwriting.md](../../docs/eval-handwriting.md), "Root causes and fixes".

## The question

The first handwriting measurement ([PREREGISTRATION.md](PREREGISTRATION.md)) found 0.566 of
held-out equations right and no silent wrong equation, with 5.7 required decisions a page and 35
of 56 display equations set as inline maths. Error analysis on its recorded answers (development
and held-out, no model call) and tuning on the development part only led to a set of changes to the
image page reader. This asks whether, on expressions nobody has looked at, the changes did what
the analysis says they should, and whether they lost anything.

## The fresh set

MathWriting `test` ids and labels that neither the development part nor the first held-out part used
(0 of 192 shared by id, 0 by label, checked), composed by the same builder
(`scripts/handwriting_eval.py` and `src/readablestem/eval/handwriting_set.py`, unchanged: hashed in
the first registration), with its own seed (`handwriting-fresh-2026-10-22`, scan seed
20261022) by `scripts/handwriting_accuracy.py build-fresh`. 12 units, 24 PDF pages, 192 gold
equations (57 display, 135 inline); tokens 3-7 / 8-15 / 16+: 62 / 122 / 8; ink black / blue /
graphite 91 / 67 / 34; 45 on ruled paper. Manifest `data/handwriting/manifest-fresh.json`, SHA-256
`dd3fc95d9f2d00c1a16cdbb335145b4cf221e4bddeb086c573a0eaba42b22a56` (rebuilt byte for byte by
`verify-fresh`). The data (CC BY-NC-SA 4.0) is not committed.

## What is run, once each

Settings of the first measurement: `claude-opus-5-5` page reader at effort `low`, 200 dpi, equation
step `claude-opus-5-5` main and `claude-haiku-5-5` cheap, no second automated check, one unit at a
time, document task, answers recorded to a file.

- **Control:** `origin/main` at `f206326` (a detached worktree; the driver script of this branch is
  used with its `src` first on the path), image page prompt `image_page-632b4d29`. It reproduces the
  first measurement's development numbers exactly from its recorded answers (0.587, 0 silent, 5.80
  decisions a page; checked offline, no spend).
- **New:** this branch at commit `740c80d` (the files and hashes in
  `REGISTRATION-ACCURACY.sha256` are that commit's), image page prompt `image_page-7a9e3a80`, second
  reading prompt `image_page_crops-b1620b4f`, with omission recovery on and the second reading ON for
  the recording only. The **shipped configuration** is the same code with the second reading off
  and acceptance of agreed readings off (`IMAGE_PAGES_SECOND_READING` and `IMAGE_PAGES_ACCEPT_AGREED`
  false, the defaults). Its numbers are produced by replaying the recording with the second reading
  off: every page and crop answer is in the file, so it spends nothing and its readings are the
  recorded ones. The recording with the second reading on, and a replay with agreed readings
  accepted, are secondary and are labelled so.
- Caps at list price: control $2.20, new $3.20. No run is repeated or re-run to finish; a cap that
  stops a run means the units that finished are scored and the rest are said so.
- The changes (all in the registered files): a paragraph or list item that holds one formula and
  no words is a display equation, its marker its number; stretches of ink the first reading left out
  are read from a picture of just that part; each `medium` block with a formula can be read a second
  time from a picture of its line (recorded as a check; with acceptance on, a block whose two readings
  agree, the second sure of every symbol, is an optional note); prompt: lines of one formula are
  equations, plain letters stay plain, every drawn mark is copied.

Registered files and hashes are in `REGISTRATION-ACCURACY.sha256` (this file, the fresh manifest, both
prompts, the image page code and the driver); the driver refuses a live fresh run if one has changed.

## What is reported

For both arms, from the document task's report and `handwriting_accuracy.py report`, with the
first measurement's held-out numbers beside them: the primary, **equations right end to end,
`exact_lenient`** (95% cluster bootstrap over units, 10,000 resamples, seed 20261021), and the symbols score; detection
recall and precision; **silent wrong equations** (matched, wrong, not a required decision; symbols
score and strict score, plus unmatched equations and output equations with no gold partner);
**required decisions and optional notes per page**; blocks `medium` or `low`; display equations
set as display equations (of the gold display expressions); calibration (share right by the page
reader's confidence); the omission check's flags and what recovery did; cost per page (page read
and whole pipeline) and seconds per page. The paired difference of the primary (new minus control)
with a cluster bootstrap over the 12 units. Error classes of the wrong equations, by eye, as in the
first write-up, labelled as by eye.

## Decision rule

The fixes **worked** on the fresh set if, for the shipped configuration against the control, all of:

1. silent wrong equations on the symbols score are 0;
2. the primary is not lower than the control's by more than 0.02;
3. at least 80% of the gold display equations are set as display equations;
4. required decisions a page are not more than the control's plus 0.2.

**Accuracy improved** only if the paired difference of the primary is positive with the 95%
interval above 0; otherwise the write-up says "not detected", whatever the point estimate. Stated
before the run: the analysis predicts a gain from layout and notation only (a few points) and none
from reading ink better; the second reading is expected to separate wrong readings from right ones
without being safe to accept (agreement between two readings by one model is not evidence of being
right). The default for acceptance of agreed readings is **off** whatever the fresh numbers say; its
replay is reported with its silent wrong equations so that the owner can decide.

## Limits stated in advance

One run per arm; 12 units and 192 equations, an interval about 10 points wide; one source and one
writer population; typed words around the ink; synthetic scanning; the label is the writer's task,
not a reading of the ink; the model may have seen the data; the changes were chosen after looking
at the development and first held-out answers (the fresh set is the only clean confirmation); the
first held-out set was used for the error analysis and so is no longer clean.

## Addendum 1 (2026-10-22, written after the control run and before any call of the new arm)

The control run was stopped by its cap ($2.20): 10 of 12 units finished (20 of 24 pages, 161 of 192
gold equations; one page of unit 10 ended in an `api_error`, so 19 of 20 pages were converted). It
spent $1.77 on those units, of which $0.61 went to two page reads billed at about four times their
list rate (the refusal fallback's dearer billing the first write-up also met: 9,877 input tokens and
about 1,000 output tokens priced at $0.30 a page), so the cap of $2.20 was set on $0.066 a page and the
run came in at $0.088. By this registration nothing is re-run to finish: the control is reported on
the 10 units it finished, and the new arm is compared with it on the units both finished (paired) as
well as on all the units the new arm finishes (criteria 1 and 3 are about the new arm alone). The
new arm's cap is raised from $3.20 to $3.60 for the same reason, before its first call; nothing else
changes.
