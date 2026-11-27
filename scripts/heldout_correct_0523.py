"""Correct the 0.5.23 held-out order record, made live at e3737ac4, without running it again.

At e3737ac4 the order report counted a request the model answered live as one the recording
lacked, so every scored document went to `apart` (answers missing) and the headline was empty.
Every request was answered (model_order: 1013 calls for 1013 pages). This moves each source's
`apart` totals to `all`, empties `apart`, and sets the uploads as measured. `main` stays empty
(no held-out document is in the real or self-made group, as in the 0.5.22 record). The
per-group totals cannot be rebuilt from totals and are left empty. Totals only: the record
keeps no page, and none is read.

    python3 -I scripts/heldout_correct_0523.py IN OUT
"""

import copy
import json
import sys
from pathlib import Path


def require(holds: bool, what: str) -> None:
    """Stop unless the record is the one this correction was written for (not an `assert`,
    which `python -O` drops)."""
    if not holds:
        sys.exit(f"refused: {what}")


src, out = sys.argv[1], sys.argv[2]
record = json.loads(Path(src).read_text(encoding="utf-8"))
metrics = record["metrics"]
require(record["pipeline_version"] == "0.5.23", "not the 0.5.23 record")
require(metrics["listed"] is False and metrics["pages"] == [], "the record lists pages")
require(record["not_finished"] == [], "a document did not finish")
uploads = metrics["uploads"]
require(
    uploads["measured"] == 0 and len(uploads["answers_missing"]) == 49,
    "not every scored document was set apart",
)
order = metrics["model_order"]
# Every page was asked once, and every answer that did not apply failed the check: no request
# ended without an answer.
reasons = ("unknown_number", "repeated_number", "no_numbers", "too_much_furniture")
require(order["calls"] == order["pages"], "a page was not asked")
require(order["applied_pages"] + order["failed"] == order["pages"], "pages unaccounted for")
require(order["failed"] == sum(order[r] for r in reasons), "a call gave no answer")

for name, source in metrics["sources"].items():
    require(source["all"]["pages"] == 0 and source["groups"] == {}, f"{name}: headline not empty")
    empty = copy.deepcopy(source["main"])
    require(empty["pages"] == 0, f"{name}: main not empty")
    source["all"] = source["apart"]
    source["apart"] = empty

uploads["measured"] = len(uploads["answers_missing"])
uploads["answers_missing"] = {}
record["provenance"]["corrected"] = (
    "2026-11-27, after the run, by scripts/heldout_correct_0523.py (totals only), on the "
    "record as written: each source's 'apart' totals moved to 'all' and the uploads set as "
    "measured, "
    "because the order report at e3737ac4 counted requests answered live as unanswered "
    "(fixed in 70e2a851). Every request was answered (1013 calls for 1013 pages; 987 "
    "answers applied, 26 that failed the check). The "
    "per-group totals could not be rebuilt and are empty."
)
with open(out, "w", encoding="utf-8") as handle:
    json.dump(record, handle, indent=1)
    handle.write("\n")
print("measured", uploads["measured"], "html all pages", metrics["sources"]["html"]["all"]["pages"])
