# Scorer readiness check (registration 7.1 b)

Gold only, no system under test. `draw.py <out.json> <gold folder>` draws the seeded sample of 40
equations (8 per book; seed in `PREREGISTRATION-ADDENDUM-1.md`), `make_pred.py` holds the
known-correct LaTeX written by hand from the gold MathML, `report.json` is
`readablestem eval equations --pred pred.jsonl` over the whole answer key (the 1,329 equations with
no prediction count as errors; the 40 predicted ones are the sample). Result: 28 of 40 exact under
the lenient scorer. The author has not checked the 40 against the rendered gold yet.

`draw.py` and `make_pred.py` are the registered files (SHA-256 in `../PREREG-v2.sha256`). `make_pred.py` is byte for byte as in version 1. `draw.py` differs from version 1 in line 7 only: it read the answer key from a fixed path on the author's machine, and now takes the folder as its second argument (default `~/.cache/readablestem/heldout`, where `make heldout-gold` puts it). It was run as `python3 draw.py sample.json <answer-key folder>` and writes the identical `sample.json`.
