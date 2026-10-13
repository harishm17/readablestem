# Addendum 5: corrections after an outside review of the interim held-out results

Written 2026-10-13, after Addendum 4 and after the interim report existed, because a review of
that report found things that Addendum 4 states wrongly or leaves out. The registration and
Addenda 1 to 4 stand, with the corrections below. This addendum changes no system and no
registered metric, and it registers nothing about a held-out run that has not happened.

## 1. The code commit of the headings and parser runs

Addendum 4 says the headings task ran at code commit `e9b5979f043b56b1bbb7b2f3742fd87a769e7e51`.
It ran with the working tree at **`ca034adcd3595c0b9b8fb3252b9c3d95eb0e3bdc`**. The difference is
the proof harness, tests and documents only: `git diff e9b5979 ca034ad --stat` is 51 files, of
which four are under `src/` (`src/a11y_stem/proof/{chatbot,engine,labels,stages}.py`), and
nothing under `src/a11y_stem/pipeline`, `core`, `models` or `eval`. The headings task's code
(`pipeline/`, `eval/pipeline_eval.py`) is also identical at `293160e`, apart from comments in
`pipeline/page_type.py`. The fixes made in the review that prompted this addendum touch the
proof harness, its tables, the interim report script and, in `eval/render.py`, the text of one
error message; none touches the pipeline.

## 2. What was added to the interim report after the results were known

The registered rows are unchanged (the report script recomputes the same numbers; the previous
report's SHA-256 was `a480c9ca86441b504e052b070a093003b8f5c6a8925438cd0ccdf02b7e0f54e0`, the
rebuilt one is `96f2eab1c1fc6e5bb505c6d2277910f578a360c474c94b9d55d6aab67f98116e`; the data
under `proof/interim/heldout-2026-10/` are untouched, tree hash `6273d2ad...` of Addendum 4).
Added, and labelled in the report as added after the results were known:

- the heading flags of a11y-stem's review screen (registration 7.4, which Addendum 4's report
  left out): 0 of 28 wrong headings were asked about, 0 of 29 required decisions pointed at one;
- a sign-flip test of each heading comparison, printed beside the registered bootstrap interval.
  A "better" is no longer written where the sign-flip test (Bonferroni threshold 0.05 / 8)
  disagrees; and no verdict at all is written for fewer than 10 sections, which applies to the
  3-section development report. The registered interval rule is otherwise unchanged;
- recall of headings split into free-standing and boxed, the extra headings that are only a
  number, a row for equations after markup-only normalisation, and a row of the invented-content
  rule with HTML comments removed. None replaces a registered row;
- statements that the PDFs are printed by this project's own stylesheet, that Docling ran in its
  default settings, that MinerU ran at its basic tier and not the tier its documentation names as
  the default, that the registered Docling formula arm was **not carried out** (Addendum 4,
  item 3) and that the invented-content rule has not been audited.

## 3. Correction to the README

The README said the failed attempts "produced no output". They produced model replies that were
never saved or read, and part of the first attempt was billed (Addendum 3, section 3).

## 4. The model weights

The run records name package versions, not weights, and the runs were not offline.
`proof/parsers/WEIGHTS.md` lists the weight files and revisions the cache held afterwards, with
their SHA-256 and write times; the write times precede the runs.

## 5. What a reader can now check, and what not

The selection code, its inputs and outputs, the parser plan, the retired-edition decision and
the hash list are in this repository (`proof/REGISTERED.md`), and `tests/test_proof_registered.py`
re-runs the draw. There is still no outside timestamp. The hash of `PREREG.sha256` and of
`PREREG-after-review.sha256` (the file that holds this addendum's hash) should be anchored
outside this repository before the repository is published.
