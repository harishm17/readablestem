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

src, out = sys.argv[1], sys.argv[2]
record = json.loads(Path(src).read_text(encoding="utf-8"))
metrics = record["metrics"]
assert record["pipeline_version"] == "0.5.23" and metrics["listed"] is False
assert metrics["pages"] == []
uploads = metrics["uploads"]
assert uploads["measured"] == 0 and len(uploads["answers_missing"]) == 49
assert metrics["model_order"]["calls"] == metrics["model_order"]["pages"]

for name, source in metrics["sources"].items():
    assert source["all"]["pages"] == 0 and source["groups"] == {}, name
    empty = copy.deepcopy(source["main"])
    assert empty["pages"] == 0, name
    source["all"] = source["apart"]
    source["apart"] = empty

uploads["measured"] = len(uploads["answers_missing"])
uploads["answers_missing"] = {}
record["provenance"]["corrected"] = (
    "2026-11-27, after the run, by scripts/heldout_correct_0523.py (totals only), on the "
    "record as written: each source's 'apart' totals moved to 'all' and the uploads set as "
    "measured, "
    "because the order report at e3737ac4 counted requests answered live as unanswered "
    "(fixed in 70e2a851). Every request was answered (1013 calls for 1013 pages). The "
    "per-group totals could not be rebuilt and are empty."
)
with open(out, "w", encoding="utf-8") as handle:
    json.dump(record, handle, indent=1)
    handle.write("\n")
print("measured", uploads["measured"], "html all pages", metrics["sources"]["html"]["all"]["pages"])
