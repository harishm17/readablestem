"""One-command reproduction of the public proof: `readablestem proof`, `make proof`.

A proof set (`proof/sets/*.json`) names OpenStax sections and pins their answer key by
SHA-256. A live run (`--live`, with `OPENAI_API_KEY` and a `--max-usd` cap) calls the
models and records everything the pipeline was told by the outside world: model answers,
second-check verdicts, axe-core results, the printed PDFs, the image identities. That is
the bundle (`proof.bundle`). The default run, a replay, feeds the bundle back through the
real pipeline and scoring code with no key, no network and no cost, and writes PROOF.md.

Modules:

- `registry`: proof sets and pins; `bundle`: the recorded run on disk;
- `replay`: recording and replaying clients, media and axe; `runner`: the ReadableSTEM arm;
- `stats`: Wilson and cluster-bootstrap intervals; `goldinfo`: section facts, rule G1;
- `a11y_tables`, `report`, `document`: the tables and PROOF.md;
- `stages`: the parts other work packages fill in (invented content, comparisons);
- `intermediate`: the form other systems' outputs take; `sources`: OpenStax fetch and print;
- `engine`: the command's flow; `errors`: exit codes.

The harness measures what a machine can measure about ReadableSTEM's output. It does not
measure conformance with WCAG, ADA or any other standard, and nothing it writes may be
described as "compliant" or "verified".
"""
