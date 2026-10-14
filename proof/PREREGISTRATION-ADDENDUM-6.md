# Addendum 6: the model-dependent parts run on Claude, and the budget rules

Written 2026-10-13 at about 23:30 local time, before any model has seen any of the 15 held-out
sections. `PREREGISTRATION.md` (version 2, SHA-256
`2c16815a6d18faf452d3e28b3bd4d80721e839a213a08b81b5f5377ecd48cd34`) and Addenda 1 to 5 stand,
except where this addendum says it replaces something. Every metric, the set, the statistics
and the failure rules are unchanged (section 6).

## 1. Why

The OpenAI account ran out of credit during the first live attempt (Addendum 3). The owner
decided on 2026-10-13 that OpenAI stays paused and that every model call of this run goes to
Claude, with a ceiling of $45 for all Claude spend of this task. **No held-out model result
exists yet**: no a11y-stem document, equations or figures run, no chatbot reply, no judge
verdict. What exists is what Addenda 3 to 5 say: the headings task (no model call) and the
open parsers.

## 2. The system under test: a11y-stem on Claude

`a11y-stem proof --live --provider anthropic`, with:

| Setting | Value |
|---|---|
| Main model | `claude-opus-5-5`, effort `medium` |
| Cheap model | `claude-haiku-5-5`, effort `low` |
| Refusal fallback | server-side fallback on for `claude-opus-5-5`, as shipped (`ANTHROPIC_REFUSAL_FALLBACK` true) |
| Second automated check | off (it is OpenAI's Decisions API; with this provider there is none) |
| Everything else | production defaults, as in registration 6.1: `auto_accept_confidence` 0.85, `abstain_confidence` 0.4, every content flag off, `max_pages_per_job` 30, concurrency 4 |
| Tasks and repeats | as registered: document x2, equations x2, figures x2, headings x1 |

This configuration was chosen on the development set only (three OpenStax Physics sections).
The owner's summary of that development work, which is the basis of the choice: equations
`exact_lenient` 0.767 in 3 of 3 runs, 0 meaning-level misses accepted unflagged, 0.295
required decisions per PDF page, $0.0046 per page. The files of those runs are not in this
repository. One more run of the same configuration on the development set was made today to
check the plumbing and to price the run (spend in section 7): `exact_lenient` 23/30 = 0.767,
0.326 required decisions per PDF page. **The configuration is frozen**; nothing about it is
changed after this addendum.

The registration's statement that the second check is on (6.1) is replaced by the row above.
The model names in registration 6.1 and 6.2 (gpt-6.1-sol, gpt-6-luna) are replaced by the ones
in this addendum. No prompt, rule, threshold or scorer of a11y-stem differs from the code that
Addendum 1 froze, except as section 4 lists.

## 3. The chatbot comparison

- **Prompt**: Purdue Innovative Learning's sentence, verbatim as registered (`chat_prompts.json`,
  key `purdue`, SHA-256 of the file `9b7a20b88889c2401b5c76c2ca41e95419e29107ea57ce7aeb61ad0c80c87d54`,
  unchanged): "Convert to accessible HTML compliant with WCAG 2.2 AA with complete MathJax
  headers and remove [cite]." It is quoted as the input it is, not as a claim this project
  makes.
- **Primary arm** `chatbot-opus-purdue`: `claude-opus-5-5`. It replaces `chatbot-sol-purdue`
  and is the arm of family A (registration 8.3: the same five comparisons, k = 5).
- **Cheap arm** `chatbot-haiku-purdue`: the same prompt on `claude-haiku-5-5`, descriptive,
  replacing `chatbot-luna-purdue`. It is run if the budget rules of section 7 allow it.
- **Not run**: the expert-prompt arm (`chatbot-sol-expert`). It was secondary and descriptive in
  the registration; it is not run on Claude, and the report says so.
- **Runs**: two per arm, as registered. Section 7 says when the primary arm gets one run
  instead. The report states, per arm, how many runs were made.
- **Request**: the same as registered, through the Messages API: the PDF as a document block, a
  150 dpi PNG of every page, the prompt last; no system prompt, no tools, no refusal fallback,
  the model's default effort, `max_tokens` 64,000 (thinking counts toward it), streamed. A reply
  that is cut off (`max_tokens`, or a full context window), refused or empty is a failure of the
  section and is not asked again. Infrastructure errors are retried at most twice, as
  registered. The cost of a call is its usage at list price (`models/pricing.py`); a call that
  failed after the request may have been processed is charged its input tokens.
- **Purdue's page** documents a second recipe, for Claude: a longer prompt that asks for a
  `template.html` file to be uploaded with the PDF, a lesson number and title, and a list of
  design requirements. The registered source text in this repository (registration 6.2, the
  comparison section of the report) quotes only the Gemini section of the page. The copy of the
  page read on 2026-10-09 (kept outside this repository; nothing new was fetched) has the
  Claude section. That recipe is **not registered and not run**: it depends on a template file
  the page supplies and on inputs a section PDF does not have.

## 4. The judges

The figure judge (prompt `judge_figure-137e2607`, registration 7.3) is `claude-opus-5-5`.
For the chatbot arms it is built with the refusal fallback off, so that it never changes model
inside a call (`eval.judge.make_judge`). For a11y-stem's own figures task the judge is the
run's own client and main model, so the opus refusal fallback is on there (the harness did not
separate them, and this is not changed). The invented-content table has one model-judged row,
figure descriptions, which uses the same verdicts; every other invented-content row is rule
based and calls no model. **The judge is not calibrated against human labels**; the author's
labelling pass (registration 7.8) remains to be done, and the report says so. A pass rate is
agreement with this judge, not accuracy.

## 5. Code and what was frozen

| Item | Value |
|---|---|
| Branch and commit | `feat/public-proof` at `71eeef1f469884a078559399d073a8e3804374ed`; this addendum's commit adds this file and its hash line and nothing else, and the run starts from it with a clean checkout |
| Merged before | `feat/claude-provider` (provider, Claude client, prices, judge choice; commit `0ccdf10`); then `94bd6d5` (the chatbot arms and their figure judge on Claude, `--chat-runs`, report text that names the recorded provider) and `71eeef1` (a test, and a line in the set's limits) |
| Pipeline | version 0.4.0; prompts equation `equation-df0e12f1`, figure `figure-ac51f42d`, judge `judge_figure-137e2607`, table `table-b3769ce7`; `src/a11y_stem/pipeline/`, `models/prompts/` and `eval/equations.py` are identical to `305d2a9` |
| Invented-content code | fingerprint `672e19fc97e14b7daa97015833b0dbcd913725598f8e6cdaa2b5e4326720d295` (it was `b2f7596b...` in Addendum 1; the Claude-provider branch added provider fields to `eval/invented/figures.py` and `cli.py`, and no rule changed). Caps and thresholds of the rules are as registered: sentences of 7 or more words, 25% of 4-grams, difflib ratio 0.85 for headings and 0.8 for unaligned equations of 3 or more symbols |
| Dependencies | `uv.lock` SHA-256 `7894d33a4cfc2bf2d30f63c722fde994b017f84d48cde25e0a7f8b0f81c64bf4` (adds the `anthropic` SDK) |
| Held-out answer key | manifest SHA-256 `e9e7e429a5ce20ebc36c4c313ea46a8754d8b47cad6d359ec5dfb7f14d899ceb`, unchanged |
| The 15 PDFs | the ones the parsers were given (`interim/heldout-2026-10/pdfs/`). The run's work folder was seeded with the page, image and equation-picture caches and the archive pages of the earlier attempts (renderings and downloads, no model output; the earlier attempts' partial recordings are not used). Rendering the 15 sections from that folder, with no model call, gave 15 PDFs byte for byte equal to the parsers' PDFs and created no new cache file |
| Parser plan | `PARSERS-PLAN.md` as frozen in Addendum 1; family B stays at k = 8 with four arms named there |

## 6. What does not change

The 15 sections and their answer key; every metric of registration 7 (primary and secondary);
the intervals and tests of section 8 (cluster bootstrap by section, B = 10,000, seed 0, Wilson
beside it, Bonferroni within each family, k = 5 for family A, k = 8 for family B); the
failure rules of section 9 (a section a system cannot convert counts as missed in every
denominator; infrastructure failures may be re-run at most twice with every attempt logged);
the rule against tuning on these sections (section 10), which now includes the Claude
configuration of section 2; and the wording rules of section 11. The open parsers' results
(Addenda 4 and 5, `interim/`) are reused as recorded and are not run again: they read the same
15 PDFs. Docling with formula recognition stays not carried out (Addendum 4, item 3).

## 7. Budget and the order of the run

**Ceiling: $45.00 for all Claude spend of this task**, counted from usage at list price (the
hard stop is on the sum of the recorded costs of every step; each step is also capped
separately and refuses a call it cannot hold). It is below the registration's $50 and
replaces it (sign-off item 2 of the set file). Spent before the run, on the development set
and not on held-out sections, as the sum of the recorded costs: **$1.08** (a development run
of the four tasks, $0.41; the Purdue prompt on claude-haiku-5-5 on the three development
sections with its figure judge, $0.11; the same prompt on claude-opus-5-5 on one development
section, $0.55; one-word probe requests, under a cent each). From these, before the run:
a11y-stem's document task cost $0.0058 per element (equation or figure) on the development set;
the chatbot recipe cost $0.043 per PDF page on claude-opus-5-5 (about 4.9k input tokens and
1.1k output tokens per page) and $0.001 per page on claude-haiku-5-5. For the 242 held-out pages
that is about $10.3 for one opus run and $0.25 for one haiku run, and for a11y-stem about
$20 to $25 (uncertain: the held-out sections hold 1,369 equations).

Order, each step with its own cap, and the rules that decide what is run:

1. **a11y-stem**, all four tasks in the order document (x2), headings, figures (x2), equations
   (x2), with `--keep-finished --resume`, cap **$26.00** (the figure judge inside it). If the
   cap stops it, the runs that finished are kept and the report opens with the incomplete-run
   banner; nothing is re-run to fill the gap.
2. **Chatbot, opus arm, run 1** on all 15 sections, with its figure judge, cap the smaller of
   $13.00 and $45.00 minus the spend so far minus $2.00. If that cap stops the arm before all
   15 sections have a reply, the arm is started again (the replies already made are kept and
   not paid for twice) with whatever the ceiling then allows, minus $2.00; if that does not
   hold the rest, the arm is reported as not run, not half scored.
3. **Chatbot, haiku arm, runs 1 and 2**, with its figure judge, cap the smaller of $2.50 and
   what is left under the ceiling. If less than $0.60 is left, the arm is not run.
4. **Chatbot, opus arm, run 2**, only if the spend so far (the recorded costs of steps 1 to 3)
   plus 1.15 times the recorded cost of opus run 1, plus $0.60 for its figure judge, is at most
   $44.00. Otherwise the arm has one run, the report says "one run", and the comparison uses
   that one run. Half-made runs are dropped, never scored.
5. The open parsers' recorded outputs are added to the recorded run without being run again.

If a step fails because of the provider (an empty balance, a rate limit that does not clear,
an outage), the run stops there and the report says how far it got. If the ceiling would be
crossed, the step that would cross it is not started. The spend by arm is reported from the
files of the recorded run. Nothing else calls the API while the run is going.

## 8. Sign-off items of the set file

(1) Retired first editions as ground truth: decided 2026-10-13
(`DECISION-retired-editions-2026-10-13.md`). (2) Spend: approved by the owner as the $45 ceiling
above. (3) The frozen items of registration section 12 are in place, with the changes this
addendum lists. `--confirm-signoff` is passed on that basis.

## 9. Not done, so the reader knows

No human labels exist: the meaning-level labels of wrong equations, the judge's agreement with
the author and the invented-content audit (7.8) are the author's and are not done. The
scorer-readiness check (Addendum 1, section 3) has not been checked by the author against the
rendered gold. The expert-prompt chatbot arm and the Claude recipe on Purdue's page are not
run. None of these can change a number already measured; each limits what the numbers may be
called.
