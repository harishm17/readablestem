"""The audit: how often the rules are right, measured by a person.

The rules are blunt. A faithful paraphrase is "unsupported", a changed word inside a
sentence that keeps a quarter of its 4-grams is "supported", a fraction the text layer
prints in another order can look unsupported. So the pre-registration (7.5, 7.8) has the
rule checked on a seeded sample: per system and per type, up to 50 units the rule flagged
and up to 50 it did not, on a sheet that names no system and does not say which the rule
flagged, in a seeded random order. A person writes one label for each unit: `invented`
(the PDF does not say this) or `supported` (it does) or `unsure`.

From the labels, per system and type:

- precision = labelled `invented` among the flagged sample;
- the miss rate = labelled `invented` among the unflagged sample;
- recall = precision x flagged / (precision x flagged + miss rate x unflagged), the flagged
  and unflagged counts being the whole population the sample was drawn from;
- the audited invented rate = (precision x flagged + miss rate x unflagged) / all units.

The raw rate (flagged / all) and the audited numbers are published together. Intervals:
Wilson for precision and miss rate; a percentile bootstrap (labels resampled within each
stratum, 10,000 resamples, `default_rng(0)`) for recall and the audited rate. `unsure`
labels are left out of the arithmetic and counted.

Nobody and nothing but the person labelling writes labels: there is no model labeller.
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from readablestem.eval import pipeline_eval as pe
from readablestem.eval.invented.pool import RESAMPLES, SEED
from readablestem.eval.invented.score import Verdict
from readablestem.eval.invented.text import Reference, norm_words

PER_STRATUM = 50
SAMPLE_SEED = "a11y-stem invented-content audit sample 2026-10-13"
ORDER_SEED = "a11y-stem held-out labels 2026-10-13"
LABELS = ("invented", "supported", "unsure")
SHEET_FIELDS = ["id", "type", "text", "nearest_in_pdf", "label", "note"]
# The units the audit samples: the deterministic rules' own.
AUDITED = (
    "sentence",
    "number",
    "heading",
    "equation_aligned",
    "equation_unaligned",
    "table_cell",
)


@dataclass(frozen=True)
class AuditSource:
    """One scored output: who made it, which section and run, the verdicts on its units
    and the PDF text it was scored against."""

    system: str
    section: str
    run: str
    verdicts: Sequence[Verdict]
    reference: Reference


@dataclass(frozen=True)
class AuditItem:
    id: str
    system: str
    section: str
    run: str
    kind: str
    index: int
    flagged: bool
    text: str
    nearest: str


def _rng(seed: str) -> random.Random:
    return random.Random(int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest()[:8], "big"))


def nearest_in_pdf(text: str, reference: Reference, *, width: int = 280) -> str:
    """The stretch of the PDF's text that shares the most words with `text`: a help for
    the person labelling, who still has the PDF."""
    words = set(norm_words(text))
    if not words:
        return ""
    lines = [line.strip() for page in reference.text.split("\f") for line in page.split("\n")]
    best, best_score = "", 0.0
    for i in range(len(lines)):
        window = " ".join(lines[i : i + 3])
        found = set(norm_words(window))
        if not found:
            continue
        score = len(words & found) / len(words | found)
        if score > best_score:
            best, best_score = window, score
    return best if len(best) <= width else best[:width] + " [cut]"


def build_sample(
    sources: Sequence[AuditSource],
    *,
    per_stratum: int = PER_STRATUM,
    seed: str = SAMPLE_SEED,
    order_seed: str = ORDER_SEED,
) -> tuple[list[AuditItem], dict[str, Any]]:
    """The sample: up to `per_stratum` flagged and as many unflagged units per system and
    type, in one seeded random order. Also the strata's population sizes (the key)."""
    pool: dict[tuple[str, str, bool], list[tuple[AuditSource, Verdict]]] = defaultdict(list)
    for source in sources:
        for verdict in source.verdicts:
            if verdict.kind in AUDITED:
                pool[(source.system, verdict.kind, verdict.unsupported)].append((source, verdict))
    picker = _rng(seed)
    chosen: list[tuple[AuditSource, Verdict]] = []
    strata: dict[str, dict[str, int]] = {}
    for key in sorted(pool):
        members = pool[key]
        take = min(per_stratum, len(members))
        picks = sorted(picker.sample(range(len(members)), take))
        chosen += [members[i] for i in picks]
        system, kind, flagged = key
        entry = strata.setdefault(f"{system}|{kind}", {})
        entry["flagged" if flagged else "unflagged"] = len(members)
        entry["sampled_flagged" if flagged else "sampled_unflagged"] = take
    _rng(order_seed).shuffle(chosen)
    items = [
        AuditItem(
            id=f"u{number:04d}",
            system=source.system,
            section=source.section,
            run=source.run,
            kind=verdict.kind,
            index=verdict.index,
            flagged=verdict.unsupported,
            text=verdict.text,
            nearest=nearest_in_pdf(verdict.text, source.reference),
        )
        for number, (source, verdict) in enumerate(chosen, start=1)
    ]
    return items, {"strata": strata, "seed": seed, "order_seed": order_seed}


def write_sheet(items: Sequence[AuditItem], path: Path) -> None:
    """The sheet the person labels: no system, no verdict of the rule."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SHEET_FIELDS)
        writer.writeheader()
        for item in items:
            writer.writerow(
                {
                    "id": item.id,
                    "type": item.kind,
                    "text": item.text,
                    "nearest_in_pdf": item.nearest,
                    "label": "",
                    "note": "",
                }
            )


def write_key(items: Sequence[AuditItem], meta: Mapping[str, Any], path: Path) -> None:
    """The key (system and the rule's verdict per id) and the strata sizes. Not for the
    person labelling."""
    data = {
        **meta,
        "items": {
            item.id: {
                "system": item.system,
                "section": item.section,
                "run": item.run,
                "kind": item.kind,
                "index": item.index,
                "flagged": item.flagged,
            }
            for item in items
        },
    }
    path.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")


def read_labels(path: Path) -> dict[str, str]:
    """id -> label from a filled sheet. A label outside `LABELS` is an error; an empty one
    means not labelled yet and is left out."""
    labels: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            label = (row.get("label") or "").strip().lower()
            if not label:
                continue
            if label not in LABELS:
                raise ValueError(f"{row['id']}: label {label!r} is not one of {', '.join(LABELS)}")
            labels[row["id"]] = label
    return labels


def _rate(k: int, n: int) -> float | None:
    return k / n if n else None


def audit_stratum(
    flagged_labels: Sequence[bool],
    unflagged_labels: Sequence[bool],
    n_flagged: int,
    n_unflagged: int,
) -> dict[str, Any]:
    """Precision, miss rate, recall and the audited rate of one system and type from the
    labels (True = invented) of the sampled flagged and unflagged units."""
    pf, qf = sum(flagged_labels), len(flagged_labels)
    pu, qu = sum(unflagged_labels), len(unflagged_labels)

    def estimates(p_flag: float | None, p_miss: float | None) -> tuple[float | None, float | None]:
        if p_flag is None and p_miss is None:
            return None, None
        tp = (p_flag or 0.0) * n_flagged
        fn = (p_miss or 0.0) * n_unflagged
        total = n_flagged + n_unflagged
        return (tp / (tp + fn) if tp + fn else None), ((tp + fn) / total if total else None)

    precision, miss = _rate(pf, qf), _rate(pu, qu)
    # Each stratum that has units needs labelled units, or recall and the audited rate
    # cannot be said (a flagged-only audit says nothing about what the rule missed).
    estimable = (n_flagged == 0 or qf > 0) and (n_unflagged == 0 or qu > 0)
    recall, audited = estimates(precision, miss) if estimable else (None, None)
    low = high = a_low = a_high = None
    if estimable and (qf or qu):
        rng = np.random.default_rng(SEED)
        flag_arr = np.asarray(flagged_labels, dtype=float)
        miss_arr = np.asarray(unflagged_labels, dtype=float)
        pf_draw = (
            rng.choice(flag_arr, size=(RESAMPLES, qf)).mean(axis=1)
            if qf
            else np.full(RESAMPLES, np.nan)
        )
        pu_draw = (
            rng.choice(miss_arr, size=(RESAMPLES, qu)).mean(axis=1)
            if qu
            else np.full(RESAMPLES, np.nan)
        )
        tp = np.nan_to_num(pf_draw) * n_flagged
        fn = np.nan_to_num(pu_draw) * n_unflagged
        with np.errstate(invalid="ignore", divide="ignore"):
            rec = np.where(tp + fn > 0, tp / (tp + fn), np.nan)
        aud = (tp + fn) / max(n_flagged + n_unflagged, 1)
        if not np.isnan(rec).all():
            low, high = (float(x) for x in np.nanpercentile(rec, [2.5, 97.5]))
        a_low, a_high = (float(x) for x in np.percentile(aud, [2.5, 97.5]))
    return {
        "population": {"flagged": n_flagged, "unflagged": n_unflagged},
        "labelled": {"flagged": qf, "unflagged": qu},
        "invented_in_flagged": pf,
        "invented_in_unflagged": pu,
        "precision": precision,
        "precision_wilson95": pe.wilson_interval(pf, qf),
        "miss_rate": miss,
        "miss_rate_wilson95": pe.wilson_interval(pu, qu),
        "recall": recall,
        "recall_bootstrap95": None if low is None else [low, high],
        "raw_rate": _rate(n_flagged, n_flagged + n_unflagged),
        "audited_rate": audited,
        "audited_rate_bootstrap95": None if a_low is None else [a_low, a_high],
    }


def score_audit(key: Mapping[str, Any], labels: Mapping[str, str]) -> dict[str, Any]:
    """Per system and type, from a key (`write_key`) and the labels read from the sheet."""
    out: dict[str, Any] = {}
    unsure: dict[str, int] = defaultdict(int)
    flagged: dict[str, list[bool]] = defaultdict(list)
    unflagged: dict[str, list[bool]] = defaultdict(list)
    missing = 0
    for item_id, item in key["items"].items():
        label = labels.get(item_id)
        stratum = f"{item['system']}|{item['kind']}"
        if label is None:
            missing += 1
        elif label == "unsure":
            unsure[stratum] += 1
        else:
            (flagged if item["flagged"] else unflagged)[stratum].append(label == "invented")
    for stratum, sizes in key["strata"].items():
        out[stratum] = audit_stratum(
            flagged[stratum],
            unflagged[stratum],
            sizes.get("flagged", 0),
            sizes.get("unflagged", 0),
        ) | {"unsure": unsure[stratum]}
    return {"strata": out, "unlabelled_items": missing}
