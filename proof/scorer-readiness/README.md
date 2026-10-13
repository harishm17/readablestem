# Scorer readiness check (registration 7.1 b)

Gold only, no system under test. `draw.py <out.json> <gold folder>` draws the seeded sample of 40
equations (8 per book; seed in `PREREGISTRATION-ADDENDUM-1.md`), `make_pred.py` holds the
known-correct LaTeX written by hand from the gold MathML, `report.json` is
`a11y-stem eval equations --pred pred.jsonl` over the whole answer key (the 1,329 equations with
no prediction count as errors; the 40 predicted ones are the sample). Result: 28 of 40 exact under
the lenient scorer. The author has not checked the 40 against the rendered gold yet.

`draw.py` differs from the registered file only in how the gold folder is given.
