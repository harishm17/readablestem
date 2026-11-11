# Contributing

Bug reports, small fixes and new test documents are welcome. For anything larger, open an
issue first: the scope is deliberate (`docs/decisions/0001-scope.md`), and some things
are left out on purpose.

## Set up

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12 or newer.

```
uv sync --all-groups --all-extras
make check          # ruff, ruff format --check, then the offline tests
make ci-local       # what CI runs, on Python 3.12 and 3.14, including ty check src
```

The browser tests print pages with Chromium and need two font packages
(`uv run playwright install chromium`; `fonts-noto-core` and `fonts-liberation`). The
full command list is in `docs/development.md`; `docs/README.md` says where everything else
is.

## Rules the code and the repository follow

- **Tests are offline by default.** They use the fake model client and PDFs generated in
  the repository. A test that calls a real model is marked `live_model` and is opt-in; use
  your own key with a spend cap, and never commit a key. OpenAI requests are sent with
  `store=False`; do not change that.
- **Test behavior, not code.** CLAUDE.md, "What tests to write here", says which tests
  earn a place. In short: a bug fix gets one regression test, and copy, markup, constants,
  private helpers and permutations of the same path do not get tests.
- **Workflow tests** (`tests/workflows`, marked `workflow`) follow a person through the
  app on a corpus file. One file per workflow, `test_wfNN_<name>.py`; one test per thing
  the person gets, named for it (`test_the_student_file_has_one_top_heading`). A test reads
  what the person gets through a parser (the student HTML, a page of the app), never by
  searching raw HTML. It converts only files of the corpus manifest, from recorded
  answers. A test that an open finding makes false is marked `@harness.finding("F-nnn")`,
  which fails the run once the finding is fixed until the mark is removed.
  `tests/workflows/test_suite_rules.py` checks the names, the files and the marks.
- **Only inputs that can be published.** Test and example documents must be your own work
  (contributed under MIT) or OpenStax material from books published under CC BY, with
  attribution.
  Never add course material, lab manuals, lecture notes, student work or any file you
  were given access to. If a bug needs a private document to reproduce, describe it and
  build a synthetic fixture instead.
- **No document content in logs, traces or error codes.** Use category codes.
- **Wording.** The tool assists. Do not claim conformance with a standard, a guarantee
  that output is accessible, or a checked result, in code, docs or the interface.
- **Numbers carry their sample size and an interval,** and say what they do not show.
  Changing a prompt or a model means re-running the relevant eval task and committing the
  new report (`docs/eval.md`, "How to rerun"); CI's eval gate fails on a regression and the
  eval guard fails a pull request that changes a model constant or a prompt without its
  `latest-*` report (opt-out for refactors: `docs/eval.md`, "Eval guard").
- **`--max-usd` caps reservations, not spend.** Each model call reserves its worst case
  (a main-model call reserves far more than it costs), so a cap near the expected spend
  stops a run part-way. Set it to at least four times the expected spend, keep
  `MODEL_CONCURRENCY` low for small runs, and record answers once with `--answers` so
  reruns replay for free.
- **Style.** ruff with line length 100, type hints that pass `ty check src`, and docstrings
  that say why.
- **No error without a trace.** A handler that catches broadly does something with the
  failure: it returns the answer that stands for it, counts it, logs its type (never its
  text, which may quote a document), or raises. `except: pass` and `except: continue` on a
  broad handler fail the linter (ruff S110, S112).
- **No code without a caller.** `scripts/dead_code.py` (in `make lint` and CI) fails for a
  function, class, method or property of the package that nothing calls. Delete it; its
  history is in git. A new runtime dependency needs a note in the pull request, and its license
  has to fit `THIRD_PARTY_NOTICES.md`.

## Commits and pull requests

Small commits with plain messages that say what changed and why. Run `make check` first.
Describe how you tested the change and, for behavior that touches accessibility, what you
checked by hand.

## License

Contributions are under the MIT license of the project. The hosted service also links
PyMuPDF (AGPL-3.0); see `THIRD_PARTY_NOTICES.md` for what that means for redistribution.

## Security

Do not report vulnerabilities in a public issue. See [SECURITY.md](SECURITY.md).
