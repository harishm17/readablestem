# Handwriting, measured once: an answer key of real human ink (2026-10-21)

Status: **registered, not yet run.** This page is committed before any model call. The registration
(set, manifest hash, metrics, decision rule, spend cap) is
[data/handwriting/PREREGISTRATION.md](../data/handwriting/PREREGISTRATION.md), hashed in
`data/handwriting/REGISTRATION.sha256`; the driver refuses to call a model if any registered file
has changed. The results follow below once the run is made.

## Why

[eval-image-pages.md](eval-image-pages.md) measured the scanned and handwritten pages pass
([ADR 0007](decisions/0007-image-pages.md)) on synthetic scans of printed pages and on real papers
made into scans, and says plainly: "Handwriting is measured only by eye on four pages. There is no
handwriting answer key." This builds that answer key and measures handwriting once, so that the
owner can decide whether the app may advertise handwriting.

## The set, in short

Real human ink from Google's MathWriting dataset (people copying a rendered LaTeX expression on a
touchscreen; **CC BY-NC-SA 4.0**, so not committed: a builder and a manifest of hashes are), set on
scanned-looking pages with display expressions on lines of their own and inline expressions inside
short typed sentences; the gold is the label of each expression, in the format the image-pages eval
already scores.

| | Dev (MathWriting `valid`) | Held-out (MathWriting `test`) |
|---|---|---|
| Units / PDF pages | 5 / 10 | 12 / 24 |
| Gold equations (display / inline) | 75 (32 / 43) | 205 (56 / 149) |

Manifest SHA-256 `81dcbe862ec01e10ebf6fdddfac8276d2800d1fe422e612c93af139b6d9f8676`.
Attribution: [data/handwriting/ATTRIBUTION.md](../data/handwriting/ATTRIBUTION.md).

## Primary, secondary and the rule (as registered)

- **Primary:** equations right end to end, `exact_lenient`, on held-out (the metric of
  eval-image-pages.md), with a 95% cluster bootstrap over units.
- **Secondary:** the symbols score, detection recall and precision, review flags (required decisions
  per page, silent wrong equations on the symbols and the strict score), `[illegible]`, fallbacks,
  cost and seconds per page, and strata (display or inline, pen, paper, length).
- **Rule:** handwriting may be advertised if the held-out primary is at least 0.50 and silent wrong
  equations are 0, given that every display equation is a required decision and inline
  expressions ride with their paragraph.
- **Run:** `claude-opus-5-5` as the page reader, production settings, once per part, dev then
  held-out, no tuning on either, $8.00 at list price in all.
