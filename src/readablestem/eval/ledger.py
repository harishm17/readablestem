"""`readablestem eval ledger`: one table of how good a release is, and of what is not known.

Each eval reports its own lane on its own set. The ledger is one list of rows (`ROWS`) over
all of them, led by the rows that say what a student meets: how often a whole course
document reaches them with an error nobody was asked about. It measures nothing itself. It
reads the records the evals keep (`<eval>-<set>.json` in `data/eval`: `order`, `burden`,
`outputs`, and `whole`, below), and for every row and every record that feeds it prints the
count, the figure, a bound and a label:

- **held-out**: scored on held-out documents, under the production profile, by this release;
- **development score**: scored on documents the readers were built on, or under settings
  that are not the production profile. It guards against going backward and meets no bar;
- **earlier release**: the record was made by another pipeline version or under another
  profile than the code that prints the ledger. It says nothing of this release;
- **not measured: proof owed**: no record feeds the row. The line says what proof is owed.

**The bound.** A count from a sample allows more than it shows: 0 errors in 237 formulas
still allows about 1.3 in 100. Each figure is printed with a one-sided 95% bound on the side
that matters (the upper one where lower is better): exact for a share (Clopper-Pearson) and
for a rate of events (Poisson), an order statistic for a median. The bound treats pages as
independent. Errors come in clusters by document, so the true bound is wider, and the rows
that count documents are the ones to read for that.

**Two bars** (`eval/split.py`). The goal bar is the figure a lane is built toward, written
in `ROWS`. It is met only when the bound is inside it, not when the figure is; a goal of 0 is
met by a count of 0, and the line still prints what that count allows. The release bar is
the figure of the first held-out run, kept in `data/eval/ledger-bars.json` with the set it
was measured on (`--register-bars` writes the bars that are missing, and no bar is ever
replaced without being named): a release may not be worse than it, held as measured, with
no bound. Only a held-out line is held against either. Exit code 0: no held-out line is
worse than its release bar; 1: one is, or, with `--for-release`, a row that has a release
bar has no held-out figure of this release on the bar's set; 2: refused.

**Refused.** A record that does not say its split, its set's hash, the profile's hash and the
pipeline version is not read around: the run stops and names it.

**The whole-document record** (`whole-<set>.json`) is what the top rows are made of. Its
`metrics` hold one row a document, with no name: `pages`, `silent_errors` (anything wrong in
the student file that carried no required decision), `doubt_false` and `doubt_true` (doubt
flags: a check or a reader disagreed, and the flag was false or true), and `rule_required`
(decisions a rule asks for whatever the readers say, by rule). Beside them, `rule_owners`
(rule -> who shows what the rule catches) and `hand_sample` (`pages` that passed every check
and were read by hand, and how many were found `with_fault`). False flags per true flag is
over doubt flags only; a rule-required decision is printed on its rule's line and nowhere
else in this record. It is written by the run that audits held-out whole documents and
samples the pages it passed, which needs a recording that does not exist yet.

The ledger is also written as a file (`--out`), per release and row: the figures, their
labels, the counts and both bars. Its lanes are named as `web.events.LANES` names them, and
its `release` is spelled as a `decision_outcome` event's, so a page can set production's
numbers of the same release beside a lane's.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from readablestem.eval import measure, run_record, split
from readablestem.ops import profile as run_profile
from readablestem.pipeline.output_audit import OUTPUTS

LEVEL = 0.95
EVALS = ("whole", "burden", "order", "outputs")
BARS_FILE = "ledger-bars.json"

HELD_OUT = "held-out"
DEVELOPMENT = "development score"
OTHER_SETTINGS = "development score (development settings)"
EARLIER = "earlier release"
NOT_MEASURED = "not measured: proof owed"

Unit = Literal["events per 100", "per 100", "%", "median", "ratio"]
# What a record gives a row: (events, units) for a rate, (units with it, units) for a share,
# (false, true) for the ratio, and every unit's value for a median.
Raw = tuple[float, ...]


class Refused(ValueError):
    """The ledger cannot be made from what it was given."""


# ------------------------------------------------------------------------------ the bounds


def _binomial_at_most(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p)."""
    if p <= 0:
        return 1.0
    if p >= 1:
        return 1.0 if k >= n else 0.0
    logs = (
        math.lgamma(n + 1)
        - math.lgamma(i + 1)
        - math.lgamma(n - i + 1)
        + i * math.log(p)
        + (n - i) * math.log1p(-p)
        for i in range(min(k, n) + 1)
    )
    return min(1.0, sum(math.exp(term) for term in logs))


def _poisson_at_most(k: int, mean: float) -> float:
    """P(X <= k) for X ~ Poisson(mean)."""
    if mean <= 0:
        return 1.0
    logs = (i * math.log(mean) - mean - math.lgamma(i + 1) for i in range(k + 1))
    return min(1.0, sum(math.exp(term) for term in logs))


def _solve(falling: Callable[[float], float], target: float, low: float, high: float) -> float:
    """Where a function that falls from `low` to `high` crosses `target`."""
    for _ in range(80):
        middle = (low + high) / 2
        if falling(middle) > target:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def share_bound(k: int, n: int, *, upper: bool) -> float:
    """The one-sided 95% bound on a share that showed `k` of `n` (Clopper-Pearson)."""
    if upper:
        if k >= n:
            return 1.0
        return _solve(lambda p: _binomial_at_most(k, n, p), 1 - LEVEL, 0.0, 1.0)
    if k <= 0:
        return 0.0
    return _solve(lambda p: _binomial_at_most(k - 1, n, p), LEVEL, 0.0, 1.0)


def rate_bound(events: int, units: int) -> float:
    """The one-sided 95% upper bound on events a unit, from `events` in `units` (Poisson)."""
    high = events + 4 * math.sqrt(events + 1) + 5
    return _solve(lambda mean: _poisson_at_most(events, mean), 1 - LEVEL, 0.0, high) / units


def median_bound(values: Sequence[float], *, upper: bool) -> float | None:
    """The one-sided 95% bound on a median: the nearest order statistic that holds it, or
    None when there are too few values for one (fewer than 5)."""
    ordered = sorted(values)
    n = len(ordered)
    for rank in range(1, n + 1):
        if _binomial_at_most(rank - 1, n, 0.5) >= LEVEL:
            return ordered[rank - 1] if upper else ordered[n - rank]
    return None


# -------------------------------------------------------------------------------- the rows


@dataclass(frozen=True)
class Row:
    key: str
    lane: str
    name: str
    unit: Unit
    of: str  # what is counted over: "pages", "documents"
    better: Literal["lower", "higher"] = "lower"
    goal: float | None = None
    eval: str | None = None  # the eval whose records feed the row; None: none does yet
    read: Callable[[Mapping[str, Any]], Raw | None] | None = None
    owed: str = ""  # what proof is owed while nothing feeds the row


def _documents(metrics: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [row for row in metrics.get("documents", []) if row.get("pages")]


def _silent(metrics: Mapping[str, Any]) -> Raw | None:
    rows = _documents(metrics)
    if not rows:
        return None
    return (sum(row.get("silent_errors", 0) for row in rows), sum(row["pages"] for row in rows))


def _clean(metrics: Mapping[str, Any]) -> Raw | None:
    rows = _documents(metrics)
    return (sum(not row.get("silent_errors", 0) for row in rows), len(rows)) if rows else None


def _flags(metrics: Mapping[str, Any]) -> Raw | None:
    rows = _documents(metrics)
    false = sum(row.get("doubt_false", 0) for row in rows)
    true = sum(row.get("doubt_true", 0) for row in rows)
    return (false, true) if false or true else None


def _hand_sample(metrics: Mapping[str, Any]) -> Raw | None:
    sample = metrics.get("hand_sample") or {}
    return (sample.get("with_fault", 0), sample["pages"]) if sample.get("pages") else None


def _decisions(metrics: Mapping[str, Any]) -> Raw | None:
    values = tuple(float(row["per_page"]) for row in _documents(metrics))
    return values or None


def _student_page(metrics: Mapping[str, Any]) -> Mapping[str, Any]:
    return metrics.get("sources", {}).get("html", {}).get("all", {})


def _pages_not_right(metrics: Mapping[str, Any]) -> Raw | None:
    sums = _student_page(metrics)
    return (sums["pages"] - sums.get("right", 0), sums["pages"]) if sums.get("pages") else None


def _documents_right(metrics: Mapping[str, Any]) -> Raw | None:
    sums = _student_page(metrics)
    if not sums.get("documents"):
        return None
    return (sums.get("documents_right", 0), sums["documents"])


def _furniture(metrics: Mapping[str, Any]) -> Raw | None:
    sums = _student_page(metrics)
    return (sums["pages_with"]["furniture"], sums["pages"]) if sums.get("pages") else None


def _wrong_places(output: str) -> Callable[[Mapping[str, Any]], Raw | None]:
    def read(metrics: Mapping[str, Any]) -> Raw | None:
        whole = metrics.get("whole", {})
        if not whole.get("pages") or not whole.get("built", {}).get(output):
            return None
        return (sum(by[output] for by in whole["counts"].values()), whole["pages"])

    return read


def _not_built(metrics: Mapping[str, Any]) -> Raw | None:
    whole = metrics.get("whole", {})
    failed = sum(sum(codes.values()) for codes in whole.get("failed", {}).values())
    files = sum(whole.get("built", {}).values()) + failed
    return (failed, files) if files else None


_HELD_OUT_RUN = "a held-out run under the production profile"

ROWS: tuple[Row, ...] = (
    Row(
        "whole.silent_errors",
        "whole",
        "Silent errors",
        "events per 100",
        "pages",
        goal=1,
        eval="whole",
        read=_silent,
        owed="the page audit over held-out whole documents, with a hand-read sample of the "
        "pages it passed",
    ),
    Row(
        "whole.documents_clean",
        "whole",
        "Documents with no silent error",
        "%",
        "documents",
        better="higher",
        goal=90,
        eval="whole",
        read=_clean,
        owed="the same run as the row above",
    ),
    Row(
        "whole.decisions",
        "whole",
        "Required decisions a page, the median document",
        "median",
        "documents",
        goal=1,
        eval="burden",
        read=_decisions,
        owed="`eval burden` on a replay of held-out whole documents",
    ),
    Row(
        "whole.false_flags",
        "whole",
        "False flags per true flag, doubt flags only",
        "ratio",
        "flags",
        goal=2,
        eval="whole",
        read=_flags,
        owed="every doubt flag on held-out whole documents judged true or false",
    ),
    Row(
        "whole.hand_sample",
        "whole",
        "Pages that passed every check and had a fault, read by hand",
        "per 100",
        "pages",
        eval="whole",
        read=_hand_sample,
        owed="a seeded sample of at least 40 passed pages, read against the page",
    ),
    Row(
        "pages.not_right",
        "pages",
        "Pages not read right in the student page, by the scorer",
        "per 100",
        "pages",
        eval="order",
        read=_pages_not_right,
        owed="`eval order --for-release` on the held-out layout set, once, when the reader "
        "is ported",
    ),
    Row(
        "pages.documents_right",
        "pages",
        "Documents with every gold page right",
        "%",
        "documents",
        better="higher",
        eval="order",
        read=_documents_right,
        owed="the same run as the row above",
    ),
    Row(
        "pages.furniture",
        "pages",
        "Pages that read a running head, a page number or a stamp aloud",
        "per 100",
        "pages",
        goal=2,
        eval="order",
        read=_furniture,
        owed="the same run as the two rows above",
    ),
    Row(
        "pages.check_flags",
        "pages",
        "Flags of the page check, and verdicts that change between two runs of one input",
        "per 100",
        "pages",
        owed="the page check's shadow recording, run twice on one input",
    ),
    Row(
        "equations.meaning",
        "equations",
        "Formulas whose meaning is wrong, with no flag",
        "per 100",
        "formulas",
        goal=0.2,
        owed=_HELD_OUT_RUN + " scored with meaning labels, on 1,500 or more judged formulas",
    ),
    Row(
        "equations.whole_boxes",
        "equations",
        "Boxes that hold the whole formula",
        "%",
        "boxes",
        better="higher",
        owed="the box rules' eval on held-out papers, one figure a document",
    ),
    Row(
        "equations.in_tables",
        "equations",
        "Formulas inside tables read wrong",
        "per 100",
        "formulas",
        owed=_HELD_OUT_RUN + " that reads the formulas of table cells",
    ),
    Row(
        "text.unreadable",
        "text",
        "Private-use or U+FFFD characters on a page with no flag",
        "events per 100",
        "pages",
        goal=0,
        owed=_HELD_OUT_RUN + " of the text check",
    ),
    Row(
        "text.silent_pages",
        "text",
        "Pages with a text error and no flag",
        "per 100",
        "pages",
        goal=1,
        owed="a new held-out half labeled by hand",
    ),
    Row(
        "scans.words",
        "scans",
        "Words changed on a print page",
        "per 100",
        "words",
        goal=1,
        owed="a new held-out half of scanned pages",
    ),
    Row(
        "tables.wrong_shown",
        "tables",
        "Wrong tables shown with no decision",
        "per 100",
        "shown tables",
        goal=0,
        owed="a held-out sample of 150 found tables, every shown table read by eye",
    ),
    Row(
        "figures.wrong_values",
        "figures",
        "Wrong stated values on vector charts",
        "per 100",
        "values",
        goal=0,
        owed=_HELD_OUT_RUN + " of the figure step; raster and course figures measured first",
    ),
    Row(
        "structures.wrong",
        "structures",
        "Drawn structures wrong with no question",
        "per 100",
        "drawings",
        goal=0,
        owed="a held-out set that includes hand-drawn structures",
    ),
    *(
        Row(
            f"output.{output}",
            "output",
            f"Wrong places in the {output} file",
            "events per 100",
            "pages",
            eval="outputs",
            read=_wrong_places(output),
            owed="`eval outputs` on a replay of held-out whole documents",
        )
        for output in OUTPUTS
    ),
    Row(
        "output.not_built",
        "output",
        "Files that could not be built",
        "per 100",
        "files",
        goal=0,
        eval="outputs",
        read=_not_built,
        owed="the same run as the rows above",
    ),
)

LANES = {
    "whole": "Whole documents",
    "pages": "Pages",
    "equations": "Equations",
    "text": "Text",
    "scans": "Scans",
    "tables": "Tables",
    "figures": "Figures",
    "structures": "Drawn structures",
    "output": "The files a student is given",
}


# ---------------------------------------------------------------------------- one figure


def figure(row: Row, raw: Raw) -> dict[str, Any]:
    """What a row's line says of one record: the figure, its bound and the counts behind it.
    A figure is None when it cannot be stated (flags with no true one among them)."""
    upper = row.better == "lower"
    if row.unit == "median":
        value: float | None = float(statistics.median(raw))
        bound = median_bound(raw, upper=upper)
        counts = f"{len(raw)} {row.of}"
        return {"value": value, "bound": bound, "n": len(raw), "counts": counts}
    k, n = int(raw[0]), int(raw[1])
    if row.unit == "ratio":
        flags = k + n
        value = k / n if n else None
        low = share_bound(n, flags, upper=False)
        bound = (1 - low) / low if low > 0 else None
        return {"value": value, "bound": bound, "k": k, "n": n, "counts": f"{k} false, {n} true"}
    if row.unit == "events per 100":
        bound = 100 * rate_bound(k, n)
        counts = f"{k} in {n} {row.of}"
    else:
        bound = 100 * share_bound(k, n, upper=upper)
        counts = f"{k} of {n} {row.of}"
    return {"value": 100 * k / n, "bound": bound, "k": k, "n": n, "counts": counts}


def release_of(record: Mapping[str, Any]) -> str:
    """A record's release, spelled as a `decision_outcome` event spells it."""
    return f"{record['pipeline_version']}+{record['profile']['sha256'][:12]}"


def _label(record: Mapping[str, Any], release: str) -> str:
    if release_of(record) != release:
        return EARLIER
    if record["profile"].get("name") == run_profile.DEVELOPMENT:
        return OTHER_SETTINGS
    return HELD_OUT if record["split"]["name"] == split.HELD_OUT else DEVELOPMENT


def _within(row: Row, value: float, bar: float) -> bool:
    return value <= bar if row.better == "lower" else value >= bar


def _goal(row: Row, found: Mapping[str, Any]) -> str:
    if row.goal is None:
        return ""
    value, bound = found["value"], found["bound"]
    if value is None:
        return "not shown"
    if not _within(row, value, row.goal):
        return "not met"
    if row.goal == 0 or (bound is not None and _within(row, bound, row.goal)):
        return "met"
    return "not shown"


def _release(row: Row, found: Mapping[str, Any], bar: Mapping[str, Any] | None) -> str:
    if bar is None:
        return "none yet"
    if bar["set_sha256"] != found["set_sha256"]:
        return "not judged: the bar is of another set"
    if found["value"] is None:
        return "worse"
    return "met" if _within(row, found["value"], bar["value"]) else "worse"


# ------------------------------------------------------------------------------ the ledger


def read_records(folder: Path) -> list[tuple[str, str, dict[str, Any]]]:
    """Every kept record the ledger reads in `folder`, as (eval, file name, record). A file
    that is not a stamped record is refused, not skipped."""
    found = []
    for name in EVALS:
        for path in sorted(folder.glob(f"{name}-*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise Refused(f"cannot read {path.name}: {exc}") from exc
            absent = split.missing(record) if isinstance(record, dict) else ["anything"]
            if absent:
                raise Refused(f"{path.name} does not say {', '.join(absent)}")
            found.append((name, path.name, record))
    return found


def read_bars(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    try:
        return dict(json.loads(path.read_text(encoding="utf-8"))["bars"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Refused(f"cannot read the release bars in {path.name}: {exc}") from exc


def _rule_lines(file: str, record: Mapping[str, Any], label: str) -> list[dict[str, Any]]:
    metrics = record["metrics"]
    rows = _documents(metrics)
    pages = sum(row["pages"] for row in rows)
    counts: dict[str, int] = {}
    for row in rows:
        for rule, count in (row.get("rule_required") or {}).items():
            counts[rule] = counts.get(rule, 0) + count
    owners = metrics.get("rule_owners") or {}
    return [
        {
            "rule": rule,
            "owner": owners.get(rule, ""),
            "decisions": count,
            "pages": pages,
            "per_100_pages": 100 * count / pages,
            "set": record.get("set") or "corpus",
            "label": label,
            "record": file,
        }
        for rule, count in sorted(counts.items())
    ]


def build(
    records: Sequence[tuple[str, str, Mapping[str, Any]]],
    bars: Mapping[str, Mapping[str, Any]],
    *,
    release: str,
    stamps: Mapping[str, Any],
) -> dict[str, Any]:
    """The ledger of `release`, as plain data: every row with the lines its records give it,
    and the rule-required decisions by rule. `stamps`: what the ledger says about itself."""
    rows, rules = [], []
    for row in ROWS:
        lines = []
        for name, file, record in records:
            if name != row.eval or row.read is None:
                continue
            raw = row.read(record["metrics"])
            if raw is None:
                continue
            label = _label(record, release)
            found = figure(row, raw) | {
                "set": record.get("set") or "corpus",
                "set_sha256": record["split"]["set_sha256"],
                "label": label,
                "release": release_of(record),
                "record": file,
                "stopped_early": len(record.get("not_finished") or []),
            }
            judged = label == HELD_OUT
            found["goal_bar"] = _goal(row, found) if judged else ""
            found["release_bar"] = _release(row, found, bars.get(row.key)) if judged else ""
            lines.append(found)
        lines.sort(key=lambda line: (line["label"] != HELD_OUT, line["label"], line["set"]))
        rows.append(
            {
                "key": row.key,
                "lane": row.lane,
                "name": row.name,
                "unit": row.unit,
                "better": row.better,
                "goal": row.goal,
                "release_bar": dict(bars[row.key]) if row.key in bars else None,
                "owed": row.owed,
                "measured": lines,
            }
        )
    for name, file, record in records:
        if name == "whole":
            rules += _rule_lines(file, record, _label(record, release))
    return {**stamps, "release": release, "level": LEVEL, "rows": rows, "rules": rules}


def failures(ledger: Mapping[str, Any], *, for_release: bool = False) -> list[str]:
    """Why a release fails: each held-out line worse than its release bar and, when the
    ledger is `for_release`, each row with a release bar and no held-out line judged on it."""
    found = []
    for row in ledger["rows"]:
        held = [line for line in row["measured"] if line["label"] == HELD_OUT]
        worse = [line for line in held if line["release_bar"] == "worse"]
        found += [
            f"{row['name']}: {_shown(row, line['value'])} on {line['set']} is worse than the "
            f"release bar of {_shown(row, row['release_bar']['value'])}"
            for line in worse
        ]
        judged = [line for line in held if line["release_bar"] in ("met", "worse")]
        if for_release and row["release_bar"] is not None and not judged:
            found.append(f"{row['name']}: no held-out figure of this release on its bar's set")
    return found


def register(
    ledger: Mapping[str, Any],
    bars: Mapping[str, Mapping[str, Any]],
    *,
    replace: Sequence[str] = (),
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """The release bars with this ledger's held-out figures written in where a row has no
    bar, or is named in `replace`; and the keys written. A figure from a run in which a step
    stopped early is no baseline and is not written."""
    unknown = sorted(set(replace) - {row["key"] for row in ledger["rows"]})
    if unknown:
        raise Refused(f"no such row: {', '.join(unknown)}")
    kept = {key: dict(bar) for key, bar in bars.items()}
    written = []
    for row in ledger["rows"]:
        if row["key"] in kept and row["key"] not in replace:
            continue
        for line in row["measured"]:
            if line["label"] != HELD_OUT or line["value"] is None or line["stopped_early"]:
                continue
            kept[row["key"]] = {
                "value": line["value"],
                "set": line["set"],
                "set_sha256": line["set_sha256"],
                "release": line["release"],
                "date": ledger["date"],
            }
            written.append(row["key"])
            break
    return kept, written


def stamps(settings_stamp: Mapping[str, Any], commit: str) -> dict[str, Any]:
    from readablestem.pipeline.run import PIPELINE_VERSION

    return {
        "date": dt.date.today().isoformat(),
        "pipeline_version": PIPELINE_VERSION,
        "profile": dict(settings_stamp),
        "commit": commit,
        "provenance": run_record.provenance(),
    }


# ------------------------------------------------------------------------------ the print


def _shown(row: Mapping[str, Any], value: float | None) -> str:
    if value is None:
        return "-"
    if row["unit"] == "%":
        return f"{value:.1f}%"
    if row["unit"] in ("median", "ratio"):
        return f"{value:.2f}" if row["unit"] == "median" else f"{value:.1f}"
    return f"{value:.1f} per 100"


def _bar_cell(row: Mapping[str, Any], bar: float | None, said: str) -> str:
    if bar is None:
        return said or "-"
    side = "at most" if row["better"] == "lower" else "at least"
    wanted = "none" if bar == 0 else f"{side} {_shown(row, bar)}"
    return wanted + (f": {said}" if said else "")


def render(ledger: Mapping[str, Any], failed: Sequence[str] = ()) -> str:
    """A ledger as the lines `readablestem eval ledger` prints."""
    lines = [
        f"quality ledger: release {ledger['release']}, commit {ledger['commit']}, {ledger['date']}",
        run_profile.stamp_line(ledger["profile"]),
        "Each figure is printed with its counts and a one-sided 95% bound. Only a held-out "
        "line is held against a bar, and a goal is met only when the bound is inside it.",
    ]
    head = ("row", "set", "figure", "counts", "bound", "label", "release bar", "goal bar")
    owed = []
    for lane, title in LANES.items():
        cells = []
        for row in (row for row in ledger["rows"] if row["lane"] == lane):
            bar = row["release_bar"]
            for line in row["measured"]:
                early = f", {line['stopped_early']} steps stopped early"
                cells.append(
                    (
                        row["name"],
                        line["set"],
                        _shown(row, line["value"]),
                        line["counts"],
                        _shown(row, line["bound"]),
                        line["label"]
                        + (f" ({line['release']})" if line["label"] == EARLIER else "")
                        + (early if line["stopped_early"] else ""),
                        _bar_cell(row, bar["value"] if bar else None, line["release_bar"]),
                        _bar_cell(row, row["goal"], line["goal_bar"]),
                    )
                )
            if not row["measured"]:
                cells.append(
                    (
                        row["name"],
                        "-",
                        "-",
                        "-",
                        "-",
                        NOT_MEASURED,
                        "-",
                        _bar_cell(row, row["goal"], ""),
                    )
                )
            if not any(line["label"] == HELD_OUT for line in row["measured"]):
                owed.append(f"- {row['name']}: {row['owed']}.")
        lines += ["", f"{title}:", *measure.table(head, cells)]
    lines += ["", "Rule-required decisions, by rule (in no ratio above):"]
    if ledger["rules"]:
        lines += measure.table(
            ("rule", "set", "decisions", "per 100 pages", "label", "shown by"),
            [
                (
                    rule["rule"],
                    rule["set"],
                    rule["decisions"],
                    f"{rule['per_100_pages']:.1f}",
                    rule["label"],
                    rule["owner"] or "not named",
                )
                for rule in ledger["rules"]
            ],
        )
    else:
        lines.append(NOT_MEASURED + ".")
    lines += ["", "Proof owed, by row with no held-out figure of this release:", *owed]
    if failed:
        lines += ["", "The release FAILS:", *(f"- {why}." for why in failed)]
    else:
        lines += ["", "No held-out figure is worse than its release bar."]
    return "\n".join(lines)
