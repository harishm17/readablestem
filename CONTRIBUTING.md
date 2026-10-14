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
  your own key with a spend cap, and never commit a key. Model requests are sent with
  `store=False`; do not change that.
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
  new report (`docs/eval.md`, "How to rerun"); CI's eval gate fails on a regression.
- **`--max-usd` caps reservations, not spend.** Each model call reserves its worst case
  (a main-model call reserves far more than it costs), so a cap near the expected spend
  stops a run part-way. Set it to at least four times the expected spend, keep
  `MODEL_CONCURRENCY` low for small runs, and record answers once with `--answers` so
  reruns replay for free.
- **Style.** ruff with line length 100, type hints that pass `ty check src`, and docstrings
  that say why. A new runtime dependency needs a note in the pull request, and its licence
  has to fit `THIRD_PARTY_NOTICES.md`.

## Commits and pull requests

Small commits with plain messages that say what changed and why. Run `make check` first.
Describe how you tested the change and, for behaviour that touches accessibility, what you
checked by hand.

## Licence

Contributions are under the MIT licence of the project. The hosted service also links
PyMuPDF (AGPL-3.0); see `THIRD_PARTY_NOTICES.md` for what that means for redistribution.

## Security

Do not report vulnerabilities in a public issue. See [SECURITY.md](SECURITY.md).
