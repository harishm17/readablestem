# Addendum 1: sizing of the held-out Opus arm, written after the development runs and before any held-out run

Written 2026-10-21. It registers no new metric, no new analysis and no change to the held-out
papers, gold, arms, settings or scorers. `PREREGISTRATION.md` stands.

## 1. What the development runs measured (for sizing only)

`scripts/realworld_run.py tally` (from the recorded answers; the spend log has one line per start,
and the Haiku arm's first start, stopped by a connection error, is in the tally but not in the
log):

| Development arm | Papers | Pages | Spend | Per page |
|---|---|---|---|---|
| ReadableSTEM | 25 | 114 | $2.674 | $0.0235 |
| Chatbot, Haiku | 25 | 114 | $0.255 | $0.0022 |
| Chatbot, Opus | 6 (the first 6 of `chat_order(dev)`) | 27 | $2.372 | $0.0878 |

## 2. The held-out Opus arm

Section 7 of the registration planned $5.5 for the Opus arm on the held-out part and said that
Opus would run on the first N held-out papers that fit under that cap. At the measured $0.0878 a
page the 71 held-out pages cost about $6.2, which is more than $5.5 but well inside the hard cap
of $20 for the whole work (spent so far $5.30; planned held-out ReadableSTEM about $1.7 and Haiku
about $0.2). So: **the Opus arm runs on all 15 held-out papers**, its budget is raised to $7.0 of
actual spend, and the run is started with `--max-usd 8.0` because the spend guard holds the worst
case (input plus 64,000 output tokens, about $1.7) for the call in flight; one worker. If the
actual spend reaches $7.0 before the last paper, the run stops there and the comparison is on the
papers it finished (section 7 of the registration, as written). Nothing else in section 7 changes.

## 3. Changes to the scripts since the registration (none touches a system or a scorer)

- `scripts/realworld_run.py chat` starts the recording again, up to four times, when a connection
  error ends a start (the API client raised `APIConnectionError` while counting tokens, before any
  request that could be billed, three times during the development runs); jobs already recorded
  are skipped, as they were before.
- `src/readablestem/gold/arxiv_tex.py`: the extractor keeps the name of the floats an equation is in
  (`float_kind`), used by the report to say where equations were typed. The gold is not changed:
  `scripts/realworld_build.py verify` rebuilds every paper's gold from the cached downloads and
  finds the hash of the manifests (checked after the change; "all gold files rebuild identically").
- `scripts/realworld_report.py` prints, besides the registered tables, the score by where the
  equation was typed (text, table, figure, algorithm) and the breakdown of undetected and span
  failures. Descriptive only.
