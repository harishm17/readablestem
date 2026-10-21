# Addendum 2: how "unsupported" is read for the chatbots, and what else was added after the held-out runs

Written 2026-10-21, **after** the three held-out arms were recorded and their primary results were
read. It changes no registered number: the primary and secondary scores, the detection numbers, the
unflagged-wrong rate, the cost, the intervals and the two registered tests are exactly what
`scripts/realworld_report.py` printed first.

## 1. The unsupported-equation metric

Section 5 of the registration defines "unsupported / invented" as predictions with no gold partner
whose symbols the PDF's text layer does not support (`text_layer_supported`, the document task's
field). That field needs the text of the region an equation was cut from, which exists for
ReadableSTEM (layout cut it) and not for a chatbot, whose equations are read from its HTML. Applied
to the chatbots it counts every extra equation as unsupported, so the first table of the held-out
report printed 0.181 (Opus) and 0.071 (Haiku) for a quantity that does not measure the same thing as
ReadableSTEM's 0.045. Those chatbot figures are not comparable and are not used.

What the report uses instead, for every arm by one rule: the invented-content rules for equations of
`eval/invented` (docs/eval-invented.md, registered for the OpenStax proof), applied to each arm's
output HTML against the PDF's own text layer and the paper's gold (`scripts/realworld_invented.py`):
(a) an equation aligned to a gold equation that holds a letter, digit or Greek letter the gold does
not; (b) an equation with no gold partner, at least 3 symbols, that matches no window of the PDF's
symbol stream. ReadableSTEM's HTML is made again by replaying its recorded answers offline
(`--answers-offline`, nothing spent; the replayed HTML holds 1,938 `<math>` elements on the development
part and 1,570 on the held-out part, against 2,011 and 1,658 equation elements in the recorded runs: the
rest are elements that get no MathML in the output). The
registered ReadableSTEM-only value (the document task's `text_layer_supported`) is also given.

## 2. Other things added, all descriptive

- One chatbot reply was a refusal: the Opus arm, on 2405.01155 (a held-out paper), returned an empty
  answer with `stop_reason: refusal` after 554 thinking tokens ($0.10). As registered (section 7), it
  is scored as it is (found nothing) and not asked again. A sensitivity analysis without that paper
  (14 papers, all arms) is reported beside the registered one and labelled as not registered.
- The report also gives required decisions per PDF page of every kind (not only on aligned
  equations), the score by where an equation was typed (running text, table, figure, algorithm), and
  the breakdown of undetected and span failures.
- `data/realworld/results-dev.json`, `results-heldout.json`, `invented-dev.json` and
  `invented-heldout.json` are the machine-readable results.
