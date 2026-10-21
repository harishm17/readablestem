# ADR 0006: A page math pass to place equations, grounded in the text layer

| Field | Value |
|---|---|
| Status | **Proposed**, behind `FEATURE_PAGE_MATH` (default off); measured on the development sets only (docs/eval-page-math.md) |
| Date | 2026-10-21 |
| Decider | Harish |

## Context

The held-out run (2026-10-14; the analysis of 2026-10-20, written up in
`docs/eval-secondary-metrics.md`) found that the gap between ReadableSTEM and a chatbot that
reads a whole page is in *where an equation starts and ends*, not in reading it. Where the
span ReadableSTEM cut equals the answer key's span (1,878 equations) it is exact 73.4%, the
same as reading perfect crops (73.0%); where the span differs (556) it is exact 0%, and 304
answer-key equations get no aligned prediction. The causes are layout's geometry rules:
exercise numbers inside the equation, systems split into rows, inline fractions cut, merged
runs, lone variables missed, and prose swallowed so the model abstains. The equation model
sees only the crop, so it cannot correct a bad span. Recognition error is the same as the
chatbot's (2.6%).

A whole-page reader has what a crop lacks: the sentence, the exercise's number, the
neighbouring lines. A cheap model reads a page for about $0.001 (claude-haiku-5-5). What we
cannot do is let it write the document: the chatbots invent (29% of their equations have no
partner in the answer key against 2.7% for ReadableSTEM). So the page reader may *propose
spans and a second reading*, and everything it proposes must be anchored in the PDF's own
text layer.

## Decision

Add one model call per page, after layout and before the crops, that proposes where the
mathematics is. Layout's rules stay as the fallback and as the check.

1. **Page math pass** (`pipeline/page_math.py`, prompt `models/prompts/page_math.md`). Input:
   the page at 150 dpi and the page's text layer as numbered rows, the rows layout works
   with (`L0 | text`), in reading order. Output (strict structured output): the page's math
   expressions in reading order, each with an *anchor* (the row id, the character offset and
   the exact quoted string; a multi-line display equation is a list of such anchors), a
   `display` (inline or block), a `role` (`math`, `exercise_or_item_number`,
   `equation_number`, `not_math_but_looks_like_it`) and its LaTeX reading. The prompt states
   the semantics: an exercise or item number is never part of an equation; a system or an
   aligned derivation is one block; an inline fraction is one expression; a lone variable in
   a sentence is inline maths; prose commentary beside steps is not part of the equation.
   claude-haiku-5-5 at effort `medium` (`ANTHROPIC_EFFORT_PAGE_MATH`; `low` was measured on a
   development slice and cuts the answer key's spans right about 0.20 of the time against 0.32
   for `medium`, for $0.001 against $0.0016 a page).
2. **Verification** (`page_math.verify`, then `page_math_reconcile._consistent`). Every quote
   must equal the row's text at its anchor (same normalisation as layout's `_clean`: control
   characters out, whitespace collapsed); otherwise the quote is searched in the row (the
   occurrence nearest the stated offset, compatibility forms and blanks ignored) and used
   where found; otherwise the expression is dropped. A block span must also be *consistent*:
   its LaTeX and the text it quotes have the same letters, digits and Greek letters (Dice
   similarity 0.8 of the two symbol bags), because a reading that says more or less than the
   quote means the quote cut the expression short. Nothing the page pass says becomes output
   text: the document's text still comes from the text layer, and the MathML still comes from
   the equation step reading a crop. The pass only chooses spans and offers a second reading.
3. **Reconciliation with layout** (`pipeline/page_math_reconcile.py`, applied inside
   layout's page analysis through a small hook; layout is analysed twice when the flag is on,
   the second time with the verified plan). A verified expression replaces layout's cut only
   where it is safe to: a row is cut at the anchor's offsets, exercise and equation numbers
   are cut off into their own rows (an equation number goes through layout's existing
   `equation_label` machinery), a block expression becomes one display region over its rows
   *where layout found no display equation there, or where the span covers every row of the
   regions it overlaps* (a system layout split, a determinant it left a row out of; rows with
   no letter or digit, the brackets, are taken in). A block span that would split a layout
   region, or leave in a shared row a piece layout reads as maths, is refused and layout's cut
   stays: on the development set the splits cost more than they gained (aligned derivations,
   which the answer key keeps whole). An inline expression becomes an inline run at its
   offsets (single variables included; glued word endings such as the "th" of "nth" go with
   it). Layout regions and runs the page pass did not confirm stay, marked `layout`. With the
   flag off, or when the call fails, nothing changes.
4. **Two readings** (`equation_step`, `page_math_reconcile.compare`). The crop of every
   reconciled span goes through the existing equation step unchanged. Its LaTeX is compared
   with the page pass's LaTeX as normalised symbol streams (`eval.equations_symbols`, the
   same normalisation the eval's secondary scorer uses, with the weight of letters left out:
   the page pass reads a bold vector as a plain letter and the text-layer style check owns
   that question). If they agree, the fix may be
   accepted when its existing checks pass too (agreement never overrides a failed text-layer
   check). If they disagree, the main model reads the crop once more, as escalation works
   today; if its answer matches neither reading the fix is a required review flag. A
   layout-only span gets its confidence multiplied by `PAGE_MATH_UNCONFIRMED_FACTOR`. The
   design proposed 0.9; on the development set that sent 16 equations to a person for being
   unconfirmed and 14 of them were right, so the default is 1.0 (the mechanism stays, off).
5. **Cost.** One haiku call per page (measured $0.0016 to $0.0020 at effort medium with the
   image and the text layer), plus the main-model escalations that disagreements cause. Target for the whole
   pipeline: at most about $0.017 per page on the development sets. Page-pass calls are
   recorded like every model call (`DocumentStats`, the eval's `--answers` recording), count
   against the job's dollar caps and its call cap (one call per page).

**Failure behaviour.** Fail open: a refused, invalid or over-budget page pass, a stale plan
or an exception while applying it leaves that page exactly as layout alone would have it.
The anchors never reach the model as instructions: the text layer travels as fenced
untrusted context, with the length limit raised for this step (`VisionRequest.context_limit`).

## Measuring it

Development sets only (`data/gold`, `dev-v2`); the held-out set is not used. The baseline is
this branch's pipeline with the flag off, recorded once (`--answers`) and replayed; the flag-on
run records its own answers and shares every request that is identical. Report on the
primary `exact_lenient` (unchanged) and the secondary scorers (`docs/eval-secondary-metrics.md`),
detection recall and precision, span fidelity, unflagged wrong per 100, review load, invented
equations, cost per page, and a bucket breakdown (spans, recognition, scorer). Flag precision
and recall are measured on dev: of the equations the new rules send to a person, how many
were wrong, and of the wrong ones, how many were sent.

## Result on the development sets (2026-10-21)

Measured on 114 dev-v2 pages and the original development set, flag off against on
(docs/eval-page-math.md): the primary `exact_lenient` does not move (0.486 to 0.482, interval
-1.9 to +0.9 points), the symbols-correct score by +0.5 points, detection precision falls by
about 8 points (the pass places inline pieces the answer key does not count); the share of
symbols-wrong equations that reach a person goes from 41% to 58%, the wrong ones nobody is asked
about fall from 4.8 to 3.5 per 100 gold, at about the same decisions per page and $0.0016 more
per page. The gain is the second reading, not the spans: at this commit layout's rules already
cut the exercise numbers and systems the pass was meant to fix, and the pass cannot anchor what
the text layer does not hold in order (nested fractions, stacked nuclides, brace paths). It stays
off; a fresh held-out set (about 17 sections or more, paired) decides whether to turn it on for
the review screen alone.

## Consequences

- **Easier.** Spans no longer depend only on geometry rules; the second reading gives a
  signal that does not come from the equation model's own confidence (AUROC 0.35, ADR 0005).
  The text layer stays the source of text, so the chatbots' invention failure is not
  imported.
- **Harder.** Layout runs twice per document when the flag is on (about 0.15 s per page
  extra). The prompt and the reconciliation are tuned on a development set, which is not
  evidence about other documents: a fresh held-out set has to confirm any gain. One more
  prompt to version, one more model call that can fail, and a hook inside the largest file.
- **Not decided here.** Whether to turn it on for production; that waits for a held-out
  measurement. Handwriting and scanned pages are kept as images and never reach this pass.
- **When to revisit.** If the page pass drops or moves more spans than it fixes on a fresh
  set, or if layout's own rules close the gap (docs/layout.md), remove it.
