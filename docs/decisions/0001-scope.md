# ADR 0001: Beta scope

| Field | Value |
|---|---|
| Status | **Proposed** |
| Date | 2026-10-06 |
| Decider | Harish |

This record fixes what the beta does and does not do. The lines under "Decision" are the
proposed scope; the questions further down are still open.

## Context

| Fact | Basis |
|---|---|
| ADA Title II web and course-material deadline for public entities serving over 50,000 people: **April 26, 2027** (smaller ones: April 2028) | EDUCAUSE and university accessibility-office guidance, as read on 2026-10-06. Not yet checked against the rule text (28 CFR part 35); the landing page does not state it until it is |
| Target output is accessible HTML with MathML (accessibility guides advise against tagged PDF for maths) | Accessibility-office guidance; not reproduced here |
| The tool "assists"; it never claims conformance with WCAG, the ADA or any other standard | Project rule |
| Free and open source; no paid tiers, ads or donations | Project decision |
| Course PDFs from the author's university are test inputs only: private, local, in `private-inputs/`, never in the repository, the gold set, screenshots or write-ups | Project rule |
| Public examples (eval reports, README, write-ups) use OpenStax-derived or authored examples only | Project rule |
| Beta live **Monday 9 November 2026** (stretch 2 November) | Project plan |
| Equation checkpoint **Monday 26 October**: does the equation path produce scorer numbers on the gold set? If not, the beta is equations and headings, and figures move to the week of 16 November | Project plan |
| Beta checkpoint **Sunday 1 November**, cut order if behind: figure alt text first, then heading polish | Project plan |
| Google Cloud has no hard spending cap (budgets only alert); if Cloud Run is used, put it in a separate project | Google Cloud billing documentation |

## Decision (proposed)

**In the beta**

- PDF input only: equations, headings and short figure alt text
- Google sign-in plus an allowlist
- One Cloud Run service with min-instances=1, a custom domain, an uptime check with an email alert, an in-app daily spend cap and provider spend limits
- Structured logs with no document content
- A daily error-aggregation job that runs before the 24 h deletion and stores categories and counts only, never text
- An opt-in box at upload that allows 30-day retention (default: uploads and traces deleted at 24 h)
- A privacy and terms page

**Planned for the week of 16 November** (some of it is already built: the demo page, the axe-core checks, the CI eval gate, optional Langfuse tracing)

- Static OpenStax demo page (no upload), validators (axe-core, MathML), Langfuse with masked inputs, CI eval gate

**Later**

- Out of 2026: long figure descriptions
- February 2027: PPTX input
- Out of the beta: university SSO

**Since this record was written.** Tables, long figure descriptions, links and language,
colour and course-notation memory were built behind flags that are off by default
(`docs/design/content-features.md`); tables have their own amendment in ADR 0003. They are
not part of the beta scope above until their flags are turned on.

**Questions that were open on 2026-10-06**

| Question | Outcome |
|---|---|
| Tables in the beta? | Out for 2026 and listed on the known-limits page. The code exists behind `FEATURE_TABLES` (off) |
| Model routing (cheap cases to a cheap model)? | Yes: ADR 0002 |
| Fallback model per step? | Yes, each model is the other's fallback: ADR 0002 |
| Hosting | Cloud Run, one service: ADR 0004 |
| Code licence | **MIT**, decided 2026-10-06. The shortest widely recognised permissive licence; Apache-2.0's patent grant was judged unnecessary for a small project. Data under `data/gold/` stays CC BY 4.0 |

## New facts from building the skeleton (2026-10-06, checked against live OpenStax APIs)

| Finding | Evidence |
|---|---|
| Most **current** OpenStax STEM editions are **CC BY-NC-SA 4.0**, not CC BY (e.g. calculus-volume-1/2/3, university-physics-volume-1/2/3, college-physics-2e, chemistry-2e, precalculus-2e) | OpenStax CMS API and the archive book JSON agree for every book named here (checked one by one); full CMS table in `data/openstax-licenses.csv` |
| CC BY books still live: `physics` (high-school Physics), `statistics`, `introduction-intellectual-property`, `física-universitaria-volumen-1` (Spanish) | same |
| Retired first editions are CC BY and still served by the archive (e.g. `college-physics`, `precalculus`, `college-algebra`, `algebra-and-trigonometry`, `intermediate-algebra`, `chemistry`, `introductory-statistics`) | same; `in_archive_release` column |
| The sample in `data/gold/` (3 sections of `physics` ch. 2) is a pipeline smoke test, **not** the final gold-set choice | The gold-set page choice was left open |
| OpenStax marks every bold vector with an `mstyle` wrapper (16 of the 30 sample equations). The scorer now pushes that style down to the tokens, so `\mathbf{d}` matches | `tests/test_equations.py`, `tests/test_gold_sample.py` |
| With correct LaTeX for all 30 sample equations: exact_lenient 0.83 (25/30), exact_strict 0.43, mean TEDS 0.87. The 5 lenient misses are OpenStax operators inside text (`<mtext>time =</mtext>`) | `make eval-latex-check`; table in `docs/development.md` |
| latex2mathml 3.81.1 does not render `\begin{aligned}` as a table; `\begin{array}{rcl}` matches OpenStax's 3-column layout | checked locally 2026-10-06 |

Open questions this raises (not legal advice; unverified until checked against OpenStax's
terms):

1. Keep the public gold set CC BY only (current code rule), or also use CC BY-NC-SA books? ShareAlike may affect how a derived dataset can be licensed.
2. Is a retired edition acceptable as ground truth, given its alt text and MathML may be older than the current edition? **Answered for the held-out evaluation on 2026-10-13, before any system ran on it: yes** (`proof/DECISION-retired-editions-2026-10-13.md`; the licence of each retired book was read from OpenStax's CMS and archive and is in `data/openstax-licenses.csv` and `proof/selection/licenses-2026-10-13.csv`, which is not legal advice). The rest of this ADR stays Proposed. Before that decision, `gold fetch` refuses non-live books unless `--allow-retired` is passed, and every record carries `book_state`.
3. Equation scoring defaults: report strict exact match, lenient exact match (mi/mn/mtext treated alike, trailing period ignored) and TEDS (1 - TED / max nodes), or a subset? Also: should lenient mode split operators out of OpenStax text (`<mtext>time =</mtext>` vs `\text{time} =`)? That affects 5 of the 30 sample equations.

## Consequences

Drafted from what the repository shows; the author confirms or edits.

- **Easier.** There is one input (a PDF) and one output (a single self-contained HTML
  file), so there is one thing to measure and one thing to review. OpenStax's published
  MathML, alt text and heading outline give an answer key for the three element kinds in
  scope.
- **Harder.** Word and PowerPoint sources have to be exported to PDF first. Pages the
  pipeline cannot convert faithfully (scans, handwriting, drawings) are kept as images with
  a note rather than guessed at, so some documents come back only partly converted.
  Using CC BY books only narrows the public test material to Physics, Statistics and the
  retired first editions (question 2).
- **When to revisit.** If measured results on documents outside the OpenStax sample show
  the scope is too wide or too narrow; if the 26 October equation checkpoint fails; or if
  the licence answers in questions 1 and 2 change what the public gold set can contain.
