# ADR 0001: Beta scope

| Field | Value |
|---|---|
| Status | **Proposed. Prefilled from the planning notes; Harish to confirm** |
| Date | 2026-10-06 |
| Decider | Harish |
| Sources | the private planning notes |

How to use this: tick each line you agree with, edit what you don't, then change Status to
"Accepted". The Context and Decision rows are copied facts. The Consequences section is
yours to write.

## Context (copied facts)

| Fact | Source |
|---|---|
| ADA Title II web/course-material deadline for public entities serving over 50,000 people: **April 26, 2027** (smaller ones: April 2028) | the planning notes (EDUCAUSE and Univ. of Utah links) |
| Target output is accessible HTML with MathML (accessibility guides advise against tagged PDF for maths) | the planning notes |
| The tool "assists"; it never claims "compliant" | the planning notes |
| Free and open source; no paid tiers, ads or donations | the planning notes |
| Course PDFs used for testing stay private and local, never in the repo, gold set, screenshots or write-up | Project rule |
| Public examples (eval report, README, Show HN) use OpenStax-derived examples only | the planning notes |
| Beta live **Mon Nov 9** (stretch Nov 2); repo public **Wed Nov 4** with a "beta in progress" README | the planning notes |
| Equation checkpoint **Mon Oct 26**: does equation v0 produce scorer numbers on the gold set? If not, beta = equations + headings and figures move to the Nov 16 week | the planning notes |
| Beta checkpoint **Sun Nov 1** cut order: figure alt text first, then heading polish | the planning notes |
| Google Cloud has no hard spending cap (budgets only alert); if Cloud Run is used, put it in a separate project | the planning notes |

## Decision (prefilled; confirm each)

**In the beta**

- [ ] PDF input only: equations, headings and short figure alt text
- [ ] Google sign-in plus an allowlist
- [ ] One Cloud Run service with min-instances=1, a custom domain, an uptime check with an email alert, an in-app daily spend cap and provider spend limits
- [ ] Structured logs with no document content
- [ ] A daily error-aggregation job that runs before the 24 h deletion and stores categories and counts only, never text
- [ ] An opt-in box at upload that allows 30-day retention (default: uploads and traces deleted at 24 h)
- [ ] A privacy and terms page

**Nov 16 week**

- [ ] Static OpenStax demo page (no upload), validators (axe-core, MathML), Langfuse with masked inputs, CI eval gate

**Later**

- [ ] Out of 2026: long figure descriptions
- [ ] February 2027: PPTX input
- [ ] Out of the beta: university SSO

**Still open (your call)**

| Question | Due | Recommendation on file |
|---|---|---|
| Tables in the beta? | Sun Oct 25 | Keep tables out of 2026; list them on the known-limits page |
| Model routing (cheap cases to a cheap model)? | Sun Oct 25 | the November queue |
| Fallback model per step? | Sun Oct 25 | the November queue |
| Hosting (default Cloud Run) | Sun Oct 18 | The hosting memo comes first |
| Code license | Thu Oct 8 (skeleton review gate) | **Decided 2026-10-06: MIT** (Harish left the choice open). Shortest, most widely recognised permissive license; no need for Apache-2.0's patent grant on a solo project. Data under `data/gold/` stays CC BY 4.0 |

## New facts from building the skeleton (2026-10-06, verified against live OpenStax APIs)

| Finding | Evidence |
|---|---|
| Most **current** OpenStax STEM editions are **CC BY-NC-SA 4.0**, not CC BY (e.g. calculus-volume-1/2/3, university-physics-volume-1/2/3, college-physics-2e, chemistry-2e, precalculus-2e) | OpenStax CMS API and the archive book JSON agree for every book named here (checked one by one); full CMS table in `data/openstax-licenses.csv` |
| CC BY books still live: `physics` (high-school Physics), `statistics`, `introduction-intellectual-property`, `física-universitaria-volumen-1` (Spanish) | same |
| Retired first editions are CC BY and still served by the archive (e.g. `college-physics`, `precalculus`, `college-algebra`, `algebra-and-trigonometry`, `intermediate-algebra`, `chemistry`, `introductory-statistics`) | same; `in_archive_release` column |
| The sample in `data/gold/` (3 sections of `physics` ch. 2) is a pipeline smoke test, **not** the gold-set choice | Gold-set page choice is yours (due Thu Oct 8) |
| OpenStax marks every bold vector with an `mstyle` wrapper (16 of the 30 sample equations). The scorer now pushes that style down to the tokens, so `\mathbf{d}` matches | `tests/test_equations.py`, `tests/test_gold_sample.py` |
| With correct LaTeX for all 30 sample equations: exact_lenient 0.83 (25/30), exact_strict 0.43, mean TEDS 0.87. The 5 lenient misses are OpenStax operators inside text (`<mtext>time =</mtext>`) | `make eval-latex-check`; table in `docs/development.md` |
| latex2mathml 3.81.1 does not render `\begin{aligned}` as a table; `\begin{array}{rcl}` matches OpenStax's 3-column layout | checked locally 2026-10-06 |

Questions this raises for you (not legal advice; unverified until you check):

1. Keep the public gold set CC BY only (current code rule), or also use CC BY-NC-SA books? ShareAlike may affect how a derived dataset can be licensed.
2. Is a retired edition acceptable as ground truth, given its alt text and MathML may be older than the current edition? Until you decide, `gold fetch` refuses non-live books unless `--allow-retired` is passed, and every record carries `book_state`.
3. Equation scoring defaults: report strict exact match, lenient exact match (mi/mn/mtext treated alike, trailing period ignored) and TEDS (1 - TED / max nodes), or a subset? Also: should lenient mode split operators out of OpenStax text (`<mtext>time =</mtext>` vs `\text{time} =`)? That affects 5 of the 30 sample equations.

## Consequences

TODO (Harish writes this)

- What gets easier and harder because of this scope?
- What would make you revisit it (e.g. the Oct 26 checkpoint, the Nov 22 go/no-go)?
- Why were tables and PPTX cut?
