"""What a student hears: our MathML and the gold's, read by two screen-reader speech engines.

A student does not hear our speech strings (`pipeline.speech`); their screen reader reads the
MathML in the page. NVDA 2026.1 and JAWS speak it with MathCAT, MathJax and ChromeVox with Speech
Rule Engine (SRE). This module runs any pair of MathML (ours and the gold's, for any equation
set) through both and reports where they sound different. It is an offline measure: it needs the
two engines built by `scripts/speech_oracles/build.sh` and says so, and skips, when they are not
there.

The two engines are checked against each other's rules, not against a listener. Two readings that
are the same say the markup is read the same way; two that differ say a student may hear
something different. Neither says which reading is right, and VoiceOver is not covered.

`scripts/screenreader_speech.py` is the command line; `eval pipeline --speech` adds the
aggregate numbers to a report. Nothing here changes a scorer or a committed report.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from readablestem.eval.pipeline_eval import wilson_interval

REPO = Path(__file__).resolve().parents[3]
BUILD_SCRIPT = "scripts/speech_oracles/build.sh"
# Seconds one engine process may take for a whole batch before it is killed.
RUN_TIMEOUT = 900


def pinned_versions() -> dict[str, str]:
    """KEY=value lines of `scripts/speech_oracles/versions.env`, the one place `build.sh` and
    this module read the engine versions from."""
    path = REPO / "scripts" / "speech_oracles" / "versions.env"
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.startswith("#"):
            out[key.strip()] = value.strip()
    return out


MATHCAT_STYLES = ("ClearSpeak", "SimpleSpeak")
SRE_STYLES = ("clearspeak", "mathspeak")
ENGINES = tuple(f"mathcat:{s}" for s in MATHCAT_STYLES) + tuple(f"sre:{s}" for s in SRE_STYLES)
# Below this many pairs a class is listed but not ranked among the worst.
MIN_CLASS_N = 3


def oracle_dir() -> Path:
    """Where `build.sh` puts the engines; `SPEECH_ORACLES_DIR` moves it."""
    return Path(os.environ.get("SPEECH_ORACLES_DIR") or REPO / ".local" / "speech-oracles")


def _mathcat_paths() -> tuple[Path, Path]:
    root = oracle_dir()
    return root / "mathcat-batch", root / "MathCAT" / "Rules"


def _sre_paths() -> tuple[Path, Path]:
    root = oracle_dir()
    return root / "sre", Path(__file__).resolve().parents[3] / "scripts/speech_oracles/sre_batch.js"


def missing_engines() -> dict[str, str]:
    """Engine family -> why it cannot run here; empty when both can."""
    out: dict[str, str] = {}
    binary, rules = _mathcat_paths()
    if not binary.is_file() or not rules.is_dir():
        out["mathcat"] = f"MathCAT is not built ({binary}); run {BUILD_SCRIPT}"
    sre_dir, script = _sre_paths()
    if not (sre_dir / "node_modules" / "speech-rule-engine").is_dir() or not script.is_file():
        out["sre"] = f"Speech Rule Engine is not installed in {sre_dir}; run {BUILD_SCRIPT}"
    elif subprocess.run(["node", "--version"], capture_output=True, check=False).returncode != 0:
        out["sre"] = "node is not on PATH (Speech Rule Engine needs it)"
    return out


def available_engines() -> tuple[str, ...]:
    gone = missing_engines()
    return tuple(e for e in ENGINES if e.split(":")[0] not in gone)


def skip_message() -> str | None:
    """A clear sentence when nothing can run, a partial note when one engine is missing."""
    gone = missing_engines()
    if not gone:
        return None
    lines = list(gone.values())
    if len(gone) == 2:
        return "speech oracles skipped: " + "; ".join(lines)
    return "one speech oracle skipped: " + lines[0]


# ================================================================================= engines


def _run(cmd: Sequence[str], rows: Sequence[Mapping[str, str]]) -> list[dict]:
    payload = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    name = Path(cmd[0]).name
    try:
        proc = subprocess.run(
            list(cmd),
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=RUN_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{name} timed out after {RUN_TIMEOUT} s") from None
    if proc.returncode != 0:
        # stderr can quote the input, and the input is document content: say only what failed.
        raise RuntimeError(f"{name} failed with exit code {proc.returncode}")
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def speak_mathcat(mathml: Sequence[str]) -> list[dict]:
    """MathCAT, one result per input: {speech: {style: text}, chemistry: bool, error}."""
    binary, rules = _mathcat_paths()
    rows = [{"id": str(i), "mathml": m} for i, m in enumerate(mathml)]
    out = _run([str(binary), str(rules)], rows)
    if len(out) != len(rows):
        raise RuntimeError("MathCAT returned a different number of results than it was given")
    return out


def speak_sre(mathml: Sequence[str]) -> list[dict]:
    """Speech Rule Engine, one result per input: {speech: {domain: text}, error}."""
    sre_dir, script = _sre_paths()
    rows = [{"id": str(i), "mathml": m} for i, m in enumerate(mathml)]
    out = _run(["node", str(script), str(sre_dir)], rows)
    if len(out) != len(rows):
        raise RuntimeError("SRE returned a different number of results than it was given")
    return out


@dataclass
class Reading:
    """One MathML, as every available engine says it."""

    speech: dict[str, str] = field(default_factory=dict)  # engine -> text
    errors: dict[str, str] = field(default_factory=dict)  # engine family -> message
    chemistry: bool | None = None  # MathCAT recognised chemistry (None: MathCAT not run)


def read_all(mathml: Iterable[str], engines: Sequence[str] | None = None) -> dict[str, Reading]:
    """Speak each distinct MathML once. Returns {mathml: Reading}."""
    wanted = tuple(engines) if engines is not None else available_engines()
    distinct = list(dict.fromkeys(mathml))
    readings = {m: Reading() for m in distinct}
    if not distinct:
        return readings
    if any(e.startswith("mathcat:") for e in wanted):
        for m, result in zip(distinct, speak_mathcat(distinct), strict=True):
            r = readings[m]
            r.chemistry = bool(result.get("chemistry"))
            if result.get("error"):
                r.errors["mathcat"] = _first_line(result["error"])
            for style, text in (result.get("speech") or {}).items():
                if f"mathcat:{style}" in wanted:
                    r.speech[f"mathcat:{style}"] = text
    if any(e.startswith("sre:") for e in wanted):
        for m, result in zip(distinct, speak_sre(distinct), strict=True):
            r = readings[m]
            if result.get("error"):
                r.errors["sre"] = _first_line(result["error"])
            for style, text in (result.get("speech") or {}).items():
                if f"sre:{style}" in wanted:
                    r.speech[f"sre:{style}"] = text
    return readings


def _first_line(text: str) -> str:
    """An engine's error message without the input it quotes (the MathML, from its first `<`)."""
    lines = text.strip().splitlines()
    head = lines[0].split("<", 1)[0].strip() if lines else ""
    return head[:80] or "error"


# ============================================================================== comparison

_PAUSES = re.compile(r"[,.;:]")
_SPACES = re.compile(r"\s+")


def normalise(speech: str) -> str:
    """Speech compared as words: case, pauses and spacing do not make two readings different."""
    return _SPACES.sub(" ", _PAUSES.sub(" ", speech.lower())).strip()


def word_diff(ours: str, gold: str) -> str:
    """The words that differ, as `gold [..] -> ours [..]` pieces; empty when the same."""
    a, b = normalise(gold).split(), normalise(ours).split()
    pieces = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "equal":
            pieces.append(f"gold [{' '.join(a[i1:i2])}] ours [{' '.join(b[j1:j2])}]")
    return "; ".join(pieces)


_SCRIPTS = {"msub", "msup", "msubsup"}


def shape_class(gold_mathml: str, chemistry: bool | None) -> str:
    """A coarse class of the gold equation, for finding which kinds read worst."""
    tags = set(re.findall(r"<(?:m:)?([a-z]+)[\s>/]", gold_mathml))
    if chemistry:
        return "chemistry"
    if "mtable" in tags:
        return "table or matrix"
    if "mmultiscripts" in tags:
        return "prescripts"
    if tags & {"munder", "mover", "munderover"}:
        return "limits, accents, arrows"
    if "mfrac" in tags:
        return "fraction"
    if tags & {"msqrt", "mroot"}:
        return "root"
    if tags & _SCRIPTS:
        return "scripts"
    if "mtext" in tags:
        return "text in math"
    return "plain"


@dataclass
class Pair:
    id: str
    gold: str
    ours: str | None  # None: no proposal
    source: str = ""  # which set or section it came from
    label: str | None = None  # a class the caller already has; else `shape_class` is used


@dataclass
class PairResult:
    pair: Pair
    cls: str
    gold: Reading
    ours: Reading | None
    equal: dict[str, bool]
    diff: dict[str, str]

    def as_row(self) -> dict:
        row = {
            "id": self.pair.id,
            "source": self.pair.source,
            "class": self.cls,
            "gold_chemistry": self.gold.chemistry,
            "ours_chemistry": self.ours.chemistry if self.ours else None,
            "has_proposal": self.ours is not None,
            "speech_equal": self.equal,
            "speech": {
                e: {"gold": self.gold.speech.get(e), "ours": self.ours.speech.get(e)}
                for e in self.equal
            }
            if self.ours
            else {},
            "diff": self.diff,
        }
        errs = {**{f"gold_{k}": v for k, v in self.gold.errors.items()}}
        if self.ours:
            errs.update({f"ours_{k}": v for k, v in self.ours.errors.items()})
        if errs:
            row["errors"] = errs
        return row


def compare(pairs: Sequence[Pair], engines: Sequence[str] | None = None) -> list[PairResult]:
    wanted = tuple(engines) if engines is not None else available_engines()
    texts = [p.gold for p in pairs] + [p.ours for p in pairs if p.ours is not None]
    readings = read_all(texts, wanted)
    results = []
    for p in pairs:
        gold = readings[p.gold]
        ours = readings[p.ours] if p.ours is not None else None
        equal: dict[str, bool] = {}
        diff: dict[str, str] = {}
        for e in wanted:
            g = gold.speech.get(e)
            o = ours.speech.get(e) if ours else None
            # A reading the engine could not produce on either side is not "the same".
            same = g is not None and o is not None and normalise(g) == normalise(o)
            equal[e] = same
            if ours is None:
                diff[e] = "no proposal"
            elif g is None or o is None:
                diff[e] = "engine error"
            elif not same:
                diff[e] = word_diff(o, g)
        results.append(
            PairResult(p, p.label or shape_class(p.gold, gold.chemistry), gold, ours, equal, diff)
        )
    return results


# ============================================================================== aggregates


def _fraction(equal: int, n: int) -> dict:
    ci = wilson_interval(equal, n)
    return {
        "equal": equal,
        "n": n,
        "fraction": round(equal / n, 4) if n else None,
        "ci95": [round(ci[0], 3), round(ci[1], 3)] if ci else None,
    }


def summarize(results: Sequence[PairResult], engines: Sequence[str], *, worst: int = 5) -> dict:
    """Aggregates: speech-equal per engine, over everything and over proposals, and worst classes.

    `fraction` counts a pair with no proposal as different (a student gets nothing there);
    `with_proposal` leaves those out, and so do the classes and sources: which kinds of equation
    read worst is a question about the ones we wrote.
    """
    out: dict = {
        "n_pairs": len(results),
        "n_with_proposal": sum(r.ours is not None for r in results),
        "engines": {},
        "worst_classes": {},
        "by_source": {},
        "chemistry": {},
    }
    for e in engines:
        out["engines"][e] = {
            **_fraction(sum(r.equal.get(e, False) for r in results), len(results)),
            "with_proposal": _fraction(
                sum(r.equal.get(e, False) for r in results if r.ours is not None),
                sum(r.ours is not None for r in results),
            ),
        }
        by_class: dict[str, list[bool]] = defaultdict(list)
        by_source: dict[str, list[bool]] = defaultdict(list)
        for r in results:
            if r.ours is None:  # a missed equation is a detection problem, counted above
                continue
            by_class[r.cls].append(r.equal.get(e, False))
            if r.pair.source:
                by_source[r.pair.source].append(r.equal.get(e, False))
        ranked = sorted(
            (c for c, v in by_class.items() if len(v) >= MIN_CLASS_N),
            key=lambda c: (sum(by_class[c]) / len(by_class[c]), -len(by_class[c]), c),
        )
        out["worst_classes"][e] = [
            {"class": c, **_fraction(sum(by_class[c]), len(by_class[c]))} for c in ranked[:worst]
        ]
        out["by_source"][e] = {s: _fraction(sum(v), len(v)) for s, v in sorted(by_source.items())}
    if any(r.gold.chemistry is not None for r in results):
        gold_chem = [r for r in results if r.gold.chemistry]
        out["chemistry"] = {
            "gold_recognised": _fraction(len(gold_chem), len(results)),
            "ours_recognised_of_gold_chemistry": _fraction(
                sum(bool(r.ours and r.ours.chemistry) for r in gold_chem), len(gold_chem)
            ),
        }
    errors = Counter(k for r in results for rd in (r.gold, r.ours) if rd for k in rd.errors)
    if errors:
        out["engine_errors"] = dict(errors)
    return out


def top_problems(results: Sequence[PairResult], engine: str, *, k: int = 8) -> list[dict]:
    """The most common ways ours differs from gold under one engine, as word-level changes."""
    counts: Counter[str] = Counter()
    example: dict[str, str] = {}
    for r in results:
        d = r.diff.get(engine)
        if not d or d in {"no proposal", "engine error"}:
            continue
        for piece in d.split("; "):
            counts[piece] += 1
            example.setdefault(piece, r.pair.id)
    return [{"change": c, "n": n, "example": example[c]} for c, n in counts.most_common(k)]


def format_markdown(summary: Mapping, *, title: str = "Speech under screen-reader engines") -> str:
    lines = [f"### {title}", ""]
    lines.append(
        f"{summary['n_pairs']} equations, {summary['n_with_proposal']} with a proposal. "
        "Share whose speech equals the gold's (95% Wilson interval; no proposal counts as "
        "different):"
    )
    lines += [
        "",
        "| engine | equal / n | share | 95% interval | of proposals |",
        "|---|---|---|---|---|",
    ]
    for e, v in summary["engines"].items():
        wp = v["with_proposal"]
        ci = f"{v['ci95'][0]:.3f}-{v['ci95'][1]:.3f}" if v["ci95"] else "n/a"
        frac = f"{v['fraction']:.3f}" if v["fraction"] is not None else "n/a"
        wfrac = f"{wp['fraction']:.3f}" if wp["fraction"] is not None else "n/a"
        lines.append(f"| {e} | {v['equal']} / {v['n']} | {frac} | {ci} | {wfrac} (n={wp['n']}) |")
    for e, rows in summary["worst_classes"].items():
        if not rows:
            continue
        lines += [
            "",
            f"Worst classes under {e} (equations with a proposal, at least {MIN_CLASS_N}):",
            "",
        ]
        for row in rows:
            lines.append(f"- {row['class']}: {row['equal']} / {row['n']} equal")
    if summary.get("chemistry"):
        c = summary["chemistry"]
        lines += [
            "",
            "MathCAT recognised as chemistry: "
            f"{c['gold_recognised']['equal']} / {c['gold_recognised']['n']} gold equations; of "
            f"those, {c['ours_recognised_of_gold_chemistry']['equal']} / "
            f"{c['ours_recognised_of_gold_chemistry']['n']} of ours.",
        ]
    if summary.get("engine_errors"):
        lines += ["", f"Engine errors (count of readings): {summary['engine_errors']}"]
    lines += [
        "",
        "Equal speech under an engine means the markup reads the same there. It does not say the "
        "reading is right: the gold's own markup can read badly (OpenStax sets a minus sign inside "
        "a number, `x` then `-3`, which MathCAT says as 'negative'), so a difference can be ours "
        "reading better. It is not a listening test. VoiceOver is not covered.",
    ]
    return "\n".join(lines) + "\n"


def report_block(results: Sequence[PairResult], engines: Sequence[str]) -> dict:
    """The `speech` block an eval report carries: aggregates only. No ids, no speech strings and
    no per-source numbers (a source is a book or document name, which a held-out or sealed set
    must not list)."""
    s = summarize(results, engines)
    s.pop("by_source", None)
    s["oracles"] = oracle_versions()
    s["note"] = (
        "share of equations whose speech equals the gold's under each engine; no proposal counts "
        "as different; a difference can be the gold reading badly; not a listening test; "
        "VoiceOver not covered"
    )
    return s


def oracle_versions() -> dict[str, str]:
    v = pinned_versions()
    return {
        "mathcat": f"{v['MATHCAT_VERSION']} (NVDA 2026.1, JAWS)",
        "sre": f"{v['SRE_VERSION']} (MathJax, ChromeVox)",
    }


def protected_gold(path: Path) -> str | None:
    """A refusal message when `path` is held-out or sealed material, else None.

    Same rule as `eval pipeline --set heldout`: the registered held-out gold must be reached
    through its own command, and anything under the cache home (where the sealed check part and
    the held-out sets are extracted and their runs kept) is never listed item by item.
    """
    from readablestem.cli import heldout as heldout_cli
    from readablestem.core.paths import cache_dir

    if message := heldout_cli.unselected(path):
        return message
    resolved = path.resolve()
    protected = cache_dir().resolve()
    if resolved == protected or protected in resolved.parents:
        return f"{path} is under the cache home, where held-out and sealed sets live; refused"
    return None


# =================================================================================== inputs


def pairs_from_items(items: Iterable[Mapping], gold: Iterable[Mapping]) -> list[Pair]:
    """Pairs from `eval pipeline --items` rows and the gold records they were scored against.

    Handles the equations task (one row per equation: `id`, `pred_mathml`) and the document task
    (one row per page with `pairs`: `gold_id`, `pred_mathml` or `pred_latex`). A gold equation with
    no proposal is a pair with `ours=None`. The source is the page or book the id sits under.
    """
    records = {r["id"]: r for r in gold if r.get("kind") == "equation" and r.get("mathml")}
    out: list[Pair] = []
    seen: set[str] = set()

    def add(gold_id: str, ours: str | None) -> None:
        record = records.get(gold_id)
        if record is None or gold_id in seen:
            return
        seen.add(gold_id)
        out.append(Pair(gold_id, record["mathml"], ours, source=source_of(gold_id)))

    for row in items:
        if "pairs" in row:
            for p in row["pairs"]:
                add(p["gold_id"], p.get("pred_mathml") or _latex_to_mathml(p.get("pred_latex")))
            for gold_id in row.get("missed_gold_ids") or ():
                add(gold_id, None)
        elif row.get("id") in records:
            add(row["id"], row.get("pred_mathml") or _latex_to_mathml(row.get("pred_latex")))
    return out


def source_of(gold_id: str) -> str:
    """`openstax/chemistry-atoms-first/1-4-measurements/eq-003` -> `chemistry-atoms-first`."""
    parts = gold_id.split("/")
    return parts[1] if len(parts) >= 3 and parts[0] in {"openstax", "authored"} else parts[0]


def _latex_to_mathml(latex: str | None) -> str | None:
    if not latex:
        return None
    from readablestem.pipeline.validate import safe_latex_to_mathml

    try:
        return safe_latex_to_mathml(latex, "inline")
    except ValueError:
        return None


def pairs_from_jsonl(path: Path) -> list[Pair]:
    """Generic input for any equation set: one JSON object per line.

    Keys: `id`, `gold` (MathML) or `gold_latex`, `ours` (MathML) or `ours_latex` (omit or null
    for no proposal), and optional `source` and `class`.
    """
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        row = json.loads(line)
        gold = row.get("gold") or row.get("gold_mathml") or _latex_to_mathml(row.get("gold_latex"))
        if not gold:
            raise ValueError(f"{path}:{n + 1}: no gold MathML (gold, gold_mathml or gold_latex)")
        ours = row.get("ours") or row.get("ours_mathml") or _latex_to_mathml(row.get("ours_latex"))
        out.append(Pair(str(row.get("id", n)), gold, ours, row.get("source", ""), row.get("class")))
    return out


def report_for_run(task: str, items: Sequence[Mapping], gold_dir: Path) -> dict:
    """The `eval pipeline --speech` block for an equations or document run.

    Aggregates only, so it is safe in a report for any set; a message instead when the engines
    are not built.
    """
    from readablestem.eval.pipeline_eval import load_gold

    gone = skip_message()
    engines = available_engines()
    if not engines:
        return {"skipped": gone}
    pairs = pairs_from_items(items, load_gold(gold_dir))
    block = report_block(compare(pairs, engines), engines)
    block["task"] = task
    if gone:
        block["skipped"] = gone
    return block
