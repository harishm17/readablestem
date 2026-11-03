# Chemistry prompts, round 2: a dev re-record that did not clear the gate

Development sets only (dev-v2, `--part dev`); nothing here is held-out. The candidate change was
**not shipped**: the prompts on this branch are the ones on main. The numbers are kept so the
next attempt starts from them.

## What was tried

New Chemistry sections for `equation.md` and `equation_notation.md` (the same text in both) and a
new one for `table.md` (cells with formulas, `\ce` named as the one allowed package command).
The first commit of this branch holds the candidate text; the second takes it out again.

- Written for the converter's `\ce` subset (docs/chemistry.md, "Writing it: `\ce`"): a state with
  a condition inside the one `\ce` (`\ce{Zn^2+(aq, 0.10 M)}`), gas and precipitate marks
  (`\ce{BaSO4 v}`), the excited nuclide (`\ce{^{60}_{28}Ni^*}`), one `\ce` per half-reaction with
  its label as `\text{}` beside it, a redox couple as two `\ce` around a slash, cell notation as
  one `\ce` per species with the bars as maths, quantities (`K`, `\Delta H^\circ`, `E^\circ_\text{cell}`)
  as ordinary LaTeX around the species.
- The old example that produced an italic "a q", `\mathrm{Zn^{2+}(aq,\ 0.10\ M)}`, was dropped
  and the reason given in the text. Prompt style follows Anthropic's guidance for the models in
  use (Opus 5.5 and Haiku 5.5): the goal and why, positive instructions, examples marked
  illustrative, no capitals or stacked prohibitions.
- Prompt versions: `equation-68ebbbf9` to `equation-2827c17f`; the table prompt changed too.
- Not touched: `image_page*.md` (another branch), `equation_line.md` (it has no chemistry text).

## How it was run

- Oracle: MathCAT 0.7.7 and Speech Rule Engine 4.1.5, built with `scripts/speech_oracles/build.sh`
  (into `.local/speech-oracles/`, git-ignored) and used through `--speech` on `eval pipeline` and
  `scripts/screenreader_speech.py`. "Speech equal" is the share of equations whose spoken form
  equals the gold's, no proposal counting as different (MathCAT ClearSpeak and SimpleSpeak give the
  same count here).
- Arms, same code (main at `d70a7c1`), production flags (tables, image pages, long descriptions,
  notation memory on), `claude-opus-5-5` and `claude-haiku-5-5`, `--set dev-v2 --part dev`,
  document task on the 12 chemistry documents (the eleven *Chemistry: Atoms First* sections and
  the authored chemistry document, 443 gold equations, 79 PDF pages):
  - **control**: the old prompts. Answers of the 2026-10-26 recording replayed where the request is
    the same (about 760 of 761 requests in the end); the roughly 200 requests the code has changed
    since were asked live and added to the file. All 12 documents.
  - **candidate**: the new prompts, every request live (the prompt text is in every request).
    **10 of 12 documents**, 385 of the 443 gold equations, 59 of 79 PDF pages. Left out for
    budget, chosen by cost before any result was seen: 1-4 measurements (20 gold) and 2-3 atomic
    structure (38 gold), the two most expensive sections.
  - All comparisons below are over those 10 documents, control and candidate alike.
- Regression check on non-chemistry: the equations task, every 10th equation of the three OpenStax
  algebra sets of dev-v2 (112 equations), both prompts.
- Scores: `scripts/dev_chem_classes.py --speech`, the report's `chemistry` block
  (`chem_right_excl_times`, `silent_harm`) and `scripts/screenreader_speech.py`. "Chemically right"
  is `exact_chem` or any earlier rung, over the chemistry gold; `excl x` leaves out the 35 gold
  equations that are a lone multiplication sign (the sentence already says "times").
- Recordings are kept in `~/.cache/readablestem/dev-v2/round2/` (answers files, per-item rows,
  reports, the spend log; mode 0600). The run folders `eval pipeline` made under
  `~/.cache/readablestem/dev-v2/runs/` hold only the report, because `--answers FILE` writes to the
  file named, not to the folder.

## Results

Chemistry, 10 documents, 385 gold equations (the OpenStax sections 277, the authored document 108):

| | control (old prompts) | candidate | difference |
|---|---|---|---|
| chemically right | 290 of 385 (75.3%) | 288 of 385 (74.8%) | -2 (-0.5 points) |
| chemically right, excl x | 290 of 350 (82.9%) | 288 of 350 (82.3%) | -2 (-0.6 points) |
| of which OpenStax / authored | 182 of 277 / 108 of 108 | 181 of 277 / 107 of 108 | -1 / -1 |
| silent harm per 100 gold | 1.30 (5 equations) | 2.08 (8 equations) | +0.78 |
| speech equal to the gold, MathCAT | 136 of 385 (35.3%) | 134 of 385 (34.8%) | -2 |
| speech equal to the gold, SRE clearspeak / mathspeak | 35 / 44 | 35 / 44 | 0 |

Paired over the 350 gold equations excl x: 1 gained, 3 lost (sign test p = 0.63), so the difference
is not distinguishable from model variance. The control on all 12 documents (443 gold), for
reference: chemically right 299 (67.5%), excl x 299 of 384 (77.9%), silent harm 1.35 per 100
(6 equations), MathCAT speech equal 139 of 443 (31.4%). The recording of 2026-10-26 scored 292
(65.9%) on the same 443 (docs/chemistry.md).

Non-chemistry regression check, equations task, 112 equations, `exact_lenient`: control 70 of 112
(0.625), candidate 69 of 112 (0.616). 16 of the 112 differ in LaTeX, 15 of them in spacing or brace
style only; the one that changed score is a system written as `cases` by the control and as
`\left\{ \begin{array}{rcl} ... \end{array} \right.` by the candidate.

What the new text did change in the model's answers (the 306 equations aligned in both runs):

| in the reading | control | candidate |
|---|---|---|
| `\mathrm{...}` | 9 | 0 |
| a state with a condition (`(aq, `) | 7 | 8 |
| cell notation with `\mid` | 3 | 5 |
| `\xrightarrow` | 7 | 7 |
| equations whose LaTeX differs at all | | 73 |

So the model followed the new text where it was specific (no more `\mathrm` formula), and the
score did not move: the readings that used `\mathrm` were already counted right on the chemistry
scorer, and what separates the wrong ones from the right ones is detection and span (in these 10 documents:
63 table cells not found, 10 partial spans, 14 lone x not found), not how a formula is spelled.

The four changes of outcome (all noise-sized): gained 20-key-terms eq-001 (an alpha particle read
where the control had no reading); lost authored eq-100 and 20-key-terms eq-006 (readings that
became failed fixes with a required decision, the picture kept) and 16-4 eq-025 (a free-standing
`-56.2 kJ/mol; K = 6.8 x 10^9` read without `\Delta G^\circ`). Of the eight silent-harm equations of the candidate, two (16-3 eq-011, 17-3 eq-057) are wrong in
both arms and were put to the reviewer in the control and not in the candidate (the second check
did not flag them: flag variance); one (16-4 eq-025, above) is a new misreading; the other five are
the same as the control's.

## Decision

The gate was a chemistry gain of at least +3 points on chemically right and no non-chemistry
regression. The candidate gained -0.5 points on 385 gold equations (-0.6 excl x), raised silent harm
by 0.78 per 100, and lost one equation of 112 on the non-chemistry check (variance, but a
regression by the rule). **The prompt change is not shipped.** Because no prompt or model changed
on this branch, `scripts/eval_guard.py` asks for no report and `data/eval/latest-*.json` are not
touched.

Limits of the result: 10 of 12 documents; one run per arm (the models are not deterministic, the
intervals of docs/eval.md apply); the control reuses answers recorded in October for most of its
requests; the gold of the OpenStax sections is the people-written MathML, which the
`exact_chem` rung forgives for spelling. A better lever than the prompt text, going by the classes
above, is detection (tables, spans), which the prompts do not decide.

## Spend

About $2.1 of the $2.50 cap, from the `live_cost_usd` of each report (pipeline plus second check):
control $0.34, candidate $1.37 (documents) , equations check $0.42 (two arms of $0.21), plus an
aborted first control attempt with Message Batches (killed after 50 minutes, under $0.10, not
measured). Three candidate runs stopped at the tool's per-job cap with no section finished
(`budget_job_usd`: `--max-usd` counts calls in flight at their worst case, so a cap that looks
generous trips early with several sections at once); their answers stayed in the file but the
re-runs asked some of them again, so part of the $1.37 was spent twice. For the next run: put the
cap at several times the expected spend, use `--section-workers 1` when the cap is tight, and do not
use `--batch` for a document run (it waits stage by stage, about 70 answers an hour).
