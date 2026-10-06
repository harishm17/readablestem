# a11y-stem

> **README OUTLINE ONLY. You write this (Harish).** Every section below is a placeholder.
> Section order follows the planning notes. Prompts are questions to answer, not text
> to paste. README v1 ("beta in progress", no demo link) is due **Wed Nov 4**; the live
> link and GIF go in only after the smoke test passes and the uptime monitor is on.
> Wording rules: the tool "assists"; never "compliant". Examples come from OpenStax only.

<!-- About field (GitHub sidebar): TODO (Harish writes this): one-line description,
     live URL (after Nov 9), topic tags. -->

## 1. Title and one-line pitch

TODO (Harish writes this)

- What does it do, and for whom (instructors? accessibility offices?)
- What is the one outcome a reader should remember?

## 2. Live demo and GIF

TODO (Harish writes this)

- Link plus GIF near the top, only once the live smoke test passes.
- One line on the demo's limits (sign-in, allowlist, page quota, static OpenStax demo page).

## 3. Headline numbers (2-4 bullets)

TODO (Harish writes this)

- For each number: the metric, the dataset, the sample size, the baseline, and a link to
  the script that reproduces it (`a11y-stem eval equations ...`).
- Only measured numbers. None exist yet.

## 4. What it does (3-6 bullets)

TODO (Harish writes this)

## 5. How it works

TODO (Harish writes this). The diagram can be drawn for you to check.

- Architecture diagram: parse -> route by element type -> validate -> review -> HTML.
- Name the models and data stores actually used (not decided yet).

## 6. Results table

TODO (Harish writes this)

- Metric definitions (exact match strict/lenient, TEDS), sample size, ablations.
- What the numbers do not show.

## 7. Cost and latency

TODO (Harish writes this)

- p50/p95 latency per page, $ per page, how they were measured.

## 8. Quick start

TODO (Harish writes this)

- One command (at most 4 for v1), then a table of environment variables (see `.env.example`).
- Developer commands that exist today are listed in `docs/development.md`.

## 9. Deploy

TODO (Harish writes this)

- Platform, and how the public demo is kept from running up costs (spend cap, quotas).

## 10. Eval and tests

TODO (Harish writes this)

- The command that reruns them, plus the CI badge (after the GitHub remote exists).

## 11. Limitations / claims we do not make

TODO (Harish writes this)

- What it does not do (e.g. tables, PPTX, long figure descriptions in the beta).
- Never claims WCAG or ADA compliance.

## 12. Roadmap and license

TODO (Harish writes this)

- Short roadmap.
- License: code is MIT (`LICENSE`, decided 2026-10-06). The gold-set data keeps its own
  license: `data/gold/` is OpenStax content under CC BY 4.0 (see `data/gold/ATTRIBUTION.md`).
