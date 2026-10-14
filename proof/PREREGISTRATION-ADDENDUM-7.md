# Addendum 7: what happened during the Claude run

Written 2026-10-14, after the run and before this repository's README and report were
finished. It registers nothing new. `PREREGISTRATION.md` (version 2) and Addenda 1 to 6 stand,
with the corrections and deviations below. The recorded run is
`proof/bundles/heldout-2026-10/`; its report is `PROOF.md`.

## 1. The order of the run, and the spend

All times are local, 2026-10-13 and 2026-10-14. Code commit of the run: `297126f` (Addendum
6's commit; the checkout was clean). Recorded spend is the sum of the recorded costs in the
run's files.

| Step | Time | Result | Recorded spend |
|---|---|---|---|
| 1. a11y-stem, `--live --provider anthropic --max-usd 26 --keep-finished --resume --confirm-signoff` | started 23:27; stopped 01:27 (section 2); resumed 01:34; finished 02:16 | all seven runs complete (document x2, headings, figures x2, equations x2), none stopped by the cap, none with a failed model call | $10.8775 (figure judge $0.6882 included) |
| 2. chatbot, opus arm, run 1 (cap $13.00) | 02:16 to 02:31 | 15 of 15 sections `ok` | $11.3385 |
| 3. chatbot, haiku arm, runs 1 and 2 (cap $2.50) | 02:31 to 02:42 | 30 of 30 `ok` | $1.4878 |
| 4. chatbot, opus arm, run 2 (cap $13.00) | 02:42 to 02:56 | 15 of 15 `ok` | $11.0022 |
| 5. figure judge on the opus and haiku arms (every output's figure descriptions), and adding both arms to the recorded run | 02:57 to 03:10 | all judged; no judge call failed | $0.7222 and $0.7814 |
| 6. open parsers added to the recorded run, not run | 03:10 | the 90 files equal `interim/heldout-2026-10/parsers/` | $0 |

The rule of Addendum 6 section 7 for the second opus run was met: the spend of steps 1 to 3 was
$23.70, plus 1.15 times $11.3385, plus $0.60, is $37.34, at most $44.00. Recorded Claude spend
of the held-out run: **$36.21** (a11y-stem $10.88; opus arm $22.34 and its judge $0.72;
haiku arm $1.49 and its judge $0.78). Not in the recorded files, and counted against the
$45 ceiling: the interrupted run of section 2, about $1.3 (estimated, see below), and the
$1.08 of development-set checks that Addendum 6 listed before the run. The task's total is
therefore about **$38.6**, under $45.00. The account balance is the only exact record of it.

## 2. Deviations

1. **An interruption.** The harness that started step 1 kills a background job after two
   hours. It killed the process at 01:27, while the second equations run (`equations.run2`) was
   at about 1,400 of the roughly 1,570 model calls the first equations run made. That run was
   in flight and unrecorded, so it was made again whole from the start after the restart
   (`--resume`, which exists for this, Addendum 2): the six runs that had finished were read
   back, and `PROOF.md` says "interrupted once and resumed". The calls of the lost partial run
   were billed and are in no file; at the first equations run's cost per call the estimate is
   $1.3. Nothing from the lost run was read or used. The two equations runs in the report are
   independent complete runs.
2. **The chatbot arms were recorded in steps** with `proof/record-chat-runs.py`, which calls the
   same `proof.chatbot.record_arms` that `--add-systems chatbot` calls and writes the same files
   into the same work folder, so that the second opus run could be decided from the actual spend
   (Addendum 6, section 7, rule 4). `--add-systems chatbot --arms ...` then found every job
   recorded, paid nothing for them, judged the figure descriptions and added the arm. The two
   arms were added with two commands (`--arms chatbot-opus-purdue`, then `--arms
   chatbot-haiku-purdue`). Every arm has two runs, as registered.
3. **Family B was judged at k = 6 in the first replay and at k = 8 in `PROOF.md`.** Addendum 1
   (D3) fixes k = 8 for four parser arms, and the interim report and the README used it.
   The report's code counted the parsers that ran (three), so its first replay printed k = 6
   (99.167% intervals, sign-flip threshold 0.0083). After the run the code was changed to count
   the registered four on a held-out set (`proof/parsers.py`, `family_b_k`); a development set
   is unchanged. Nothing was re-run or re-scored. **One verdict differs between the two**: MinerU
   against a11y-stem on heading F1 reads "a11y-stem is better" at k = 6 and "higher by point
   estimate" at k = 8 (interval 0.0047 to 0.0789 at k = 8; sign-flip p = 0.0073 against a
   threshold of 0.00625). The k = 8 reading is the registered one and is in `PROOF.md`. Every
   other verdict, and every number, is the same at both. This is a harness change made after
   the first held-out output existed; it changes a label and not a score, and both readings
   are stated here (registration section 10, item 4).
4. **Code after the run.** `src/a11y_stem/proof/parsers.py` (item 3) and a test were committed
   after the run. The recorded run's code identity (pipeline, prompts, models, settings) is
   unchanged, and replay checks it. The bundle names `297126f` as the commit it was recorded
   at.
5. **The work folder** was seeded with the renderings, downloads and archive pages of the
   earlier attempts (no model output; Addendum 6, section 5). The 15 PDFs in the recorded run
   are byte for byte the ones the parsers read.
6. **`proof/parsers/uv.lock`** is changed by `uv sync` in that folder (it adds the `anthropic`
   package, which the harness now needs). The registered lock of Addendum 1 was restored, and
   the hash list still holds.

## 3. Things in the results that the reader should know before reading them

- The Opus chatbot arm scores higher than a11y-stem on end-to-end equations (0.622 against
  0.504). The registered interval of the difference excludes 0 and the sign-flip test does not,
  so the report says "higher by point estimate". This is a result against a11y-stem; nothing
  in it was changed or excluded.
- a11y-stem's review screen asked about none of the wrong headings in the document task, as in
  the interim headings run, and about 34% of the wrong equations. The report's review-load
  table says so.
- The configuration summary Addendum 6 gave for the development set (0 meaning-level misses
  accepted unflagged, 0.295 required decisions per page) was the owner's. On the held-out
  sections there are 1.76 required decisions per PDF page. The meaning-level labels do not exist
  yet, so the first statement cannot be checked on these sections.
