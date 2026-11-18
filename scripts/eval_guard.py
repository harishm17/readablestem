"""Fail a pull request that changes a model, a prompt or an acceptance rule without its eval.

    python3 scripts/eval_guard.py --base origin/main [--head HEAD] [--labels-json '["x"]']

Run by `.github/workflows/eval-guard.yml`. The CI eval gate compares the committed
`data/eval/latest-<task>.json` with its baseline, so a change to a model constant or a
prompt cannot fail it by itself: nothing re-measures. This guard closes that hole. A change
is "a model constant" when a setting in `src/readablestem/config.py` that changes what a
model answers (the model ids, and the effort and output-token settings of each step: see
`is_answer_setting`) has another parsed value than on the base; runtime knobs (timeouts,
concurrency, call limits) are not. It is "a prompt" when a `.md` file under
`src/readablestem/models/prompts/` (or a folder in it) is added, changed or removed; a
file only moved without a change of content is not. Either
needs the matching `latest-*.json` reports in the same pull request (`docs/eval.md`, "How
to rerun").

A change can also move what a student is given without moving a model or a prompt: a rule
that decides whether a reading is taken as it stands or goes to a person. That is an
acceptance rule, and it needs the run record of the replay (`REPLAY_RECORD`, made at no cost
by `readablestem eval burden` from the recorded answers; the CI eval gate re-makes it and
fails on a difference). What counts (`rule_changes`) is read from the code, not from a list
of modules or lines: in a module of `src/readablestem/pipeline/` with a function that sets
`Status.ACCEPTED` or `Status.NEEDS_REVIEW`, every module-level constant (an ALL_CAPS name)
and every such function, by parsed value; and a setting of `config.py` with "accept" in its
name (the confidence a reading must reach). A module that decides nothing of this kind, and
a comment, a docstring or the formatting of any, is not a change.

Opt-out for a change that does not alter what a model is asked or which model answers (a
rename, a comment, a typo that is not in the prompt text the model sees): the label
`eval-unchanged` on the pull request, or a commit trailer `Eval-Unchanged: <reason>` (the
reason is required) in any commit of it. The opt-out is printed in the log so a reviewer
sees it.

Standard library only. Exit 0: fine or opted out. Exit 1: reports missing.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

CONFIG = "src/readablestem/config.py"
PROMPTS_DIR = "src/readablestem/models/prompts/"
PIPELINE_DIR = "src/readablestem/pipeline/"
# The run record of the $0 replay that CI's eval gate re-makes and compares
# (`readablestem eval burden --against data/eval/burden-corpus.json --same`).
REPLAY_RECORD = "burden-corpus.json"
EVAL_DIR = "data/eval/"
OPT_OUT_LABEL = "eval-unchanged"
TRAILER = re.compile(r"^Eval-Unchanged:[ \t]*(\S.*)$", re.IGNORECASE)

# Settings of config.py that change what a model is asked or answers. Everything else with
# "model" in its name (timeouts, retries, concurrency, call limits, fake_models,
# preload_table_models, model_config) is a runtime knob and needs no new report.
ANSWER_SETTINGS = frozenset(
    {
        "main_model",
        "cheap_model",
        "anthropic_main_model",
        "anthropic_cheap_model",
        "decisions_model",
        "claude_check_model",
        "claude_check_effort",
        "page_math_model",
        "page_witness_model",
        "page_audit_model",
        "image_pages_model",
        "figure_model_max_edge_px",
    }
)
_ACCEPT_SETTING = re.compile(r"\w*accept\w*")
_DECIDES = frozenset({"ACCEPTED", "NEEDS_REVIEW"})  # the `Status` members that settle a reading
_CONSTANT = re.compile(r"[A-Z][A-Z0-9_]*")
_ANSWER_PATTERNS = (
    re.compile(r"anthropic_effort_\w+"),
    re.compile(r"\w*max_output_tokens\w*"),
    re.compile(r"\w*(thinking|reasoning)\w*"),
)

ANTHROPIC_REPORTS = (
    "latest-equations.json",
    "latest-headings.json",
    "latest-figures.json",
    "latest-document.json",
)
# Prompt file (without .md) -> the committed reports that measure it. A prompt that is not
# listed is measured by the document task, which runs the whole pipeline.
PROMPT_REPORTS: dict[str, tuple[str, ...]] = {
    "equation": ("latest-equations.json", "latest-document.json"),
    "equation_line": ("latest-equations.json", "latest-document.json"),
    "equation_notation": ("latest-equations.json", "latest-document.json"),
    "check_equation": ("latest-equations.json",),
    "figure": ("latest-figures.json", "latest-document.json"),
    "figure_exam": ("latest-figures.json", "latest-document.json"),
    "figure_long": ("latest-figures.json", "latest-document.json"),
    "check_figure": ("latest-figures.json",),
    "judge_figure": ("latest-figures.json",),
    "judge_long_description": ("latest-figures.json",),
    "table": ("latest-tables-local.json", "latest-document.json"),
    # The scanned and handwritten pages pass is not in the document task's sections (typed
    # PDFs), so its report is the practice-set run of the pass itself (docs/eval-handwriting.md).
    "image_page": ("latest-image-pages.json",),
    "image_page_crops": ("latest-image-pages.json",),
    "image_page_notation": ("latest-image-pages.json",),
    # The two handwritten-expression readers (`pipeline.image_page_reconcile` reads each crop
    # with one of them): measured with the same practice-set run.
    "math_reader": ("latest-image-pages.json",),
    "math_reader_claude": ("latest-image-pages.json",),
    # The page audit runs in shadow and changes no output, so the document task does not
    # measure it: its report is its own run (`eval page-audit`, docs/eval.md).
    "page_audit": ("latest-page-audit.json",),
}
DEFAULT_PROMPT_REPORTS = ("latest-document.json",)
# Model constant (name, lower case) -> the reports that record those models. The OpenAI
# pair is recorded by the tables report (a local run, no model); the Claude pair by the
# model tasks. Any other model setting is measured by the document task.
MODEL_REPORTS: dict[str, tuple[str, ...]] = {
    "main_model": ("latest-tables-local.json",),
    "cheap_model": ("latest-tables-local.json",),
    "anthropic_main_model": ANTHROPIC_REPORTS,
    "anthropic_cheap_model": ANTHROPIC_REPORTS,
    "image_pages_model": ("latest-image-pages.json",),
    "anthropic_effort_image_pages": ("latest-image-pages.json",),
    "page_audit_model": ("latest-page-audit.json",),
    "anthropic_effort_page_audit": ("latest-page-audit.json",),
}
DEFAULT_MODEL_REPORTS = ("latest-document.json",)


def is_answer_setting(name: str) -> bool:
    """Whether the config name (any case) is a model id or an effort, output-token, thinking
    or reasoning setting."""
    lowered = name.lower()
    return lowered in ANSWER_SETTINGS or any(p.fullmatch(lowered) for p in _ANSWER_PATTERNS)


def is_accept_setting(name: str) -> bool:
    """Whether the config name (any case) is a bar a reading must reach to be accepted."""
    return bool(_ACCEPT_SETTING.fullmatch(name.lower()))


def _value(node: ast.expr) -> str:
    """The parsed value of a right-hand side: `Field(default=x, ...)` is x, so a change of
    a bound or a description is not a change of the value."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Field":
        for keyword in node.keywords:
            if keyword.arg == "default":
                node = keyword.value
                break
    return ast.dump(node)


def model_assignments(
    source: str, wanted: Callable[[str], bool] = is_answer_setting
) -> dict[str, set[str]]:
    """Setting (lower case) that `wanted` names -> every parsed value it is assigned in
    `source`, in the module and in class bodies. Comments, spacing and line breaks do not
    matter (`ast`)."""
    found: dict[str, set[str]] = {}
    scopes: list[Sequence[ast.stmt]] = []
    tree = ast.parse(source)
    scopes.append(tree.body)
    scopes += [n.body for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    for body in scopes:
        for node in body:
            if isinstance(node, ast.AnnAssign) and node.value is not None:
                targets = [node.target]
            elif isinstance(node, ast.Assign):
                targets = node.targets
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and wanted(target.id):
                    value = _value(node.value)  # type: ignore[arg-type]
                    found.setdefault(target.id.lower(), set()).add(value)
    return found


def changed_model_names(
    base_source: str, head_source: str, wanted: Callable[[str], bool] = is_answer_setting
) -> list[str]:
    """Names of settings (answer settings, or what `wanted` names) added, removed or given
    another value."""
    before = model_assignments(base_source, wanted)
    after = model_assignments(head_source, wanted)
    return sorted(
        name for name in before.keys() | after.keys() if before.get(name) != after.get(name)
    )


def _decides(node: ast.stmt) -> bool:
    return isinstance(node, ast.FunctionDef) and any(
        isinstance(part, ast.Attribute) and part.attr in _DECIDES for part in ast.walk(node)
    )


def acceptance_rules(source: str) -> dict[str, str]:
    """Rule name -> parsed value, for a pipeline module that decides whether a reading is
    accepted (it has a function that sets `Status.ACCEPTED` or `Status.NEEDS_REVIEW`): its
    module-level constants and those functions. A function is its arguments and its body
    without the docstring, so a comment, a docstring or the formatting is not a change. A
    module that decides nothing has no rules."""
    tree = ast.parse(source)
    deciders = [node for node in tree.body if isinstance(node, ast.FunctionDef) and _decides(node)]
    if not deciders:
        return {}
    rules: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = node.targets
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and _CONSTANT.fullmatch(target.id):
                rules[target.id] = _value(node.value)  # type: ignore[arg-type]
    for node in deciders:
        body = node.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            body = body[1:]  # the docstring
        rules[f"{node.name}()"] = ast.dump(node.args) + "".join(ast.dump(part) for part in body)
    return rules


def rule_changes(base_source: str, head_source: str) -> list[str]:
    """Names of the acceptance rules of a pipeline module added, removed or changed."""
    before, after = acceptance_rules(base_source), acceptance_rules(head_source)
    return sorted(
        name for name in before.keys() | after.keys() if before.get(name) != after.get(name)
    )


def prompt_name(path: str) -> str | None:
    """`equation` for `src/readablestem/models/prompts/equation.md` (also in a subfolder),
    else None."""
    if not path.startswith(PROMPTS_DIR) or not path.endswith(".md"):
        return None
    rest = path[len(PROMPTS_DIR) : -len(".md")]
    return rest.rsplit("/", 1)[-1] or None  # a file in a subfolder counts by its own name


def required_reports(
    changed_files: Iterable[str],
    config_names_changed: Sequence[str],
    rules_changed: Sequence[str] = (),
) -> dict[str, list[str]]:
    """Report file name -> the reasons it must change, for this change set. `rules_changed`
    names the acceptance rules the change moves (`rule_changes`), each as `where: name`."""
    needed: dict[str, list[str]] = {}
    for rule in rules_changed:
        needed.setdefault(REPLAY_RECORD, []).append(f"acceptance rule {rule} changed")
    for path in sorted(set(changed_files)):
        name = prompt_name(path)
        if name is None:
            continue
        for report in PROMPT_REPORTS.get(name, DEFAULT_PROMPT_REPORTS):
            needed.setdefault(report, []).append(f"prompt {name}.md changed")
    for name in config_names_changed:
        for report in MODEL_REPORTS.get(name, DEFAULT_MODEL_REPORTS):
            needed.setdefault(report, []).append(f"{CONFIG}: {name} changed")
    return needed


def opt_out(labels: Iterable[str], commit_messages: Iterable[str]) -> str | None:
    """Why the check is waived (the label, or the trailer's reason), or None."""
    if any(label.strip().lower() == OPT_OUT_LABEL for label in labels):
        return f"label {OPT_OUT_LABEL}"
    for message in commit_messages:
        # A trailer sits in the last paragraph, after the subject and a body or blank line.
        paragraphs = [p for p in re.split(r"\n\s*\n", message.strip()) if p.strip()]
        if len(paragraphs) < 2:
            continue
        for line in paragraphs[-1].splitlines():
            found = TRAILER.match(line.strip())
            if found:
                return f"commit trailer Eval-Unchanged: {found.group(1).strip()}"
    return None


def check(
    changed_files: Sequence[str],
    base_config: str,
    head_config: str,
    labels: Iterable[str] = (),
    commit_messages: Iterable[str] = (),
    rules_changed: Sequence[str] = (),
) -> tuple[int, list[str]]:
    """(exit code, lines to print). `rules_changed`: the acceptance rules of the pipeline the
    change moves, each as `where: name`; the caller reads them from the two versions of each
    changed module (`main`)."""
    names = changed_model_names(base_config, head_config) if CONFIG in changed_files else []
    if CONFIG in changed_files:
        names_rules = changed_model_names(base_config, head_config, is_accept_setting)
        rules_changed = [*rules_changed, *(f"{CONFIG}: {name}" for name in names_rules)]
    needed = required_reports(changed_files, names, rules_changed)
    if not needed:
        return 0, [
            "eval guard: no model constant, prompt or acceptance rule changed; nothing to check"
        ]
    updated = {f[len(EVAL_DIR) :] for f in changed_files if f.startswith(EVAL_DIR)}
    missing = {report: why for report, why in needed.items() if report not in updated}
    if not missing:
        return 0, ["eval guard: every report this change needs is updated: " + ", ".join(needed)]
    waiver = opt_out(labels, commit_messages)
    if waiver:
        lines = [f"eval guard: waived by {waiver}", "reports not updated:"]
        lines += [f"  {EVAL_DIR}{report}: {', '.join(why)}" for report, why in missing.items()]
        return 0, lines
    lines = [
        "eval guard: this change alters a model, a prompt or an acceptance rule but not its "
        "eval report(s):"
    ]
    lines += [f"  {EVAL_DIR}{report}: {', '.join(why)}" for report, why in sorted(missing.items())]
    lines += [
        "",
        "Re-run the task(s), promote the report `--as latest` and commit it with the change:",
        'docs/eval.md, section "How to rerun" (`readablestem eval pipeline`, then',
        "`readablestem eval promote ... --as latest`).",
        f"For an acceptance rule, re-make {EVAL_DIR}{REPLAY_RECORD} from the recorded answers, at",
        'no cost: docs/eval.md, "The replay record" (`readablestem eval burden --out ...`).',
        "",
        "If the change does not alter what a model is asked, which model answers or what a",
        f"rule accepts (a rename, a comment), add the label `{OPT_OUT_LABEL}` to the pull",
        "request, or a commit trailer `Eval-Unchanged: <reason>`. Both are documented in",
        'docs/eval.md, "Eval guard".',
    ]
    return 1, lines


def _git(*args: str, cwd: Path | None = None) -> str:
    done = subprocess.run(["git", *args], capture_output=True, text=True, check=False, cwd=cwd)
    if done.returncode != 0:
        raise SystemExit(f"error: git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout


def _show(ref: str, path: str) -> str:
    done = subprocess.run(
        ["git", "show", f"{ref}:{path}"], capture_output=True, text=True, check=False
    )
    return done.stdout if done.returncode == 0 else ""


def changed_files(base: str, head: str, cwd: Path | None = None) -> tuple[list[str], str]:
    """(the paths the pull request changes since it branched from `base`, the path the base
    had `config.py` under). A file moved with its content unchanged (a rename of the package
    folder) is not a change of that file; `git diff -M` tells."""
    files: list[str] = []
    config_path = CONFIG
    for line in _git("diff", "--name-status", "-M", f"{base}...{head}", cwd=cwd).splitlines():
        status, *paths = line.split("\t")
        if status.startswith("R"):
            if paths[1] == CONFIG:
                config_path = paths[0]
            if status == "R100":
                continue
            files.append(paths[1])
        else:
            files.append(paths[-1])
    return files, config_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", required=True, help="the base ref, e.g. origin/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--labels-json", default="[]", help="JSON list of the pull request labels")
    args = parser.parse_args(argv)
    try:
        labels = [str(x) for x in json.loads(args.labels_json)]
    except ValueError:
        parser.error("--labels-json must be a JSON list of strings")
    changed, config_path = changed_files(args.base, args.head)
    base = _git("merge-base", args.base, args.head).strip()
    messages = _git("log", "--format=%B%x00", f"{args.base}..{args.head}").split("\x00")
    rules = [
        f"{path.removeprefix('src/readablestem/')}: {name}"
        for path in sorted(set(changed))
        if path.startswith(PIPELINE_DIR) and path.endswith(".py")
        for name in rule_changes(_show(base, path), _show(args.head, path))
    ]
    code, lines = check(
        changed, _show(base, config_path), _show(args.head, CONFIG), labels, messages, rules
    )
    print("\n".join(lines))
    if code:
        print(
            "::error::eval guard: re-run the eval for the changed model, prompt or acceptance "
            "rule (docs/eval.md, How to rerun)"
        )
    return code


if __name__ == "__main__":
    sys.exit(main())
