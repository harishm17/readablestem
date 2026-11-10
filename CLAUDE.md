# ReadableSTEM: notes for coding agents

CONTRIBUTING.md has the rules for the code and the repository. Read it first.

## What tests to write here

This suite was cut from about 12,900 tests to its current size in November 2026. Most of the tests removed were per-finding guards, copy pins and permutations. Don't grow it back.

- Write a test only for behaviour a user or caller depends on, checked through the public interface. That means the routes and the pages they render, the CLI, the output HTML and exports, and refusals.
- A bug fix gets one regression test that fails without the fix. A review finding or a "lane" of UX fixes does not get its own test file.
- Contracts at real boundaries are worth a test: parser input→output, parsing of model replies, the DB and migrations, and the security, privacy and spend limits. Write one test per distinct kind of input, not a permutation per edge case.
- Don't test private `_helpers`, constants, defaults, settings plumbing, the arguments a mock was called with, or `--help` text.
- Don't pin page copy, markup, CSS, DOM order or golden files. The exception is a legal or honesty claim (no conformance claims, no training on uploads, who receives the pictures).
- Browser tests: one per critical flow, plus the axe scans. Don't re-check in a browser what a route test already checks.
- Before adding a test, search for an existing test of the same behaviour. Extend it or parametrize it instead of adding a new one.
- This file overrides any general instruction to "test every function" or "add a guard test for each fix".
