# proof/

The first registered held-out run (2026-10-14): what was fixed before it, the model answers it
recorded, and the reports replayed from them. The result itself is in
[docs/results.md](../docs/results.md); [`PROOF.md`](../PROOF.md) at the top of the repository
is the full report, written by `make proof` from the recording here with no key and no network
([docs/proof.md](../docs/proof.md) has the commands).

| Path | What is there |
|---|---|
| [`REGISTERED.md`](REGISTERED.md) | Start here: the files the registration rests on, and what can and cannot be checked from this repository alone |
| [`PREREGISTRATION.md`](PREREGISTRATION.md), `PREREGISTRATION-ADDENDUM-1.md` to `-8.md` | The registration and its addenda, each frozen with a hash (`PREREG-*.sha256`, [`original-hashes/`](original-hashes/)) |
| [`selection/`](selection/) | The seeded rule that chose the 15 sections, its inputs and its output |
| [`sets/`](sets/) | The proof sets `make proof` knows: the held-out set and the development sample |
| [`bundles/`](bundles/) | The recorded runs: every model request and answer, the chatbot and parser arms, with a manifest of hashes |
| [`interim/`](interim/) | The held-out results that need no model, reported before the model run |
| [`scorer-readiness/`](scorer-readiness/) | The scorer's ceiling: known-correct LaTeX scored against the answer key |
| [`parsers/`](parsers/) | The harness that ran the open PDF parsers (a separate uv project; the app does not depend on it) |
| [`PROOF-development-sample.md`](PROOF-development-sample.md) | The same report on the development sample |
| `record-heldout.sh`, `record-chat-runs.py` | How the recording was made, with its spend caps |
| `Dockerfile` | The replay on a clean machine, in one command |
| `verify-rename.py` | Checks that the registered files differ from the tagged ones only by the product's new name (Addendum 8) |
| [`DECISION-retired-editions-2026-10-13.md`](DECISION-retired-editions-2026-10-13.md), [`PARSERS-PLAN.md`](PARSERS-PLAN.md) | Why the retired first editions were used, and the plan for the parser arms |

The registration files are frozen. Do not edit one; what changed after it is written in an
addendum.
