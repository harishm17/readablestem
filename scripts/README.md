# scripts/

Measurement and maintenance scripts. The app does not import any of them. Each script starts
with a docstring that says what it does and how to run it. Many replay recorded answers and
call no model.

| Files | What they are for | Explained in |
|---|---|---|
| `check_shards.py`, `dead_code.py`, `eval_guard.py` | Run by CI: every test ran once across the shards, nothing in `src/` is uncalled, and a change to a model, a prompt or an acceptance rule comes with its evaluation | [docs/development.md](../docs/development.md); [docs/eval.md](../docs/eval.md), "Eval guard" |
| `dependency_record.py`, `make_glyph_names.py`, `page_dates.py`, `layout_identity.py`, `corpus_refresh.py` | Keep generated files and records in step with the code | [docs/development.md](../docs/development.md) |
| `brand_icons.py`, `make_social_card.py`, `hero_assets.py`, `hero_screenshot.py`, `design_shots.py` | Draw the site's icons, the link-preview image and the landing page's equation block; take the landing page's product picture; take screenshots for design review | [docs/web.md](../docs/web.md) |
| `bench_layout.py`, `bench_runtime.py` | Time and memory of layout analysis and of the web app | [docs/development.md](../docs/development.md) |
| `dev_*.py` | Analyses of runs on the development sets: paired comparisons, where a chatbot is right and the pipeline is not, chemistry classes | [docs/eval.md](../docs/eval.md) |
| `eq_*.py` | Equation diagnostics on recorded runs, with no model call | [docs/eval-equation-diagnostics.md](../docs/eval-equation-diagnostics.md), [docs/eval-equation-round5.md](../docs/eval-equation-round5.md) |
| `realworld_*.py`, `rw_*.py`, `fresh5_*.py` | The real-paper sets from arXiv: select, register, build, run and report | [docs/eval-realworld.md](../docs/eval-realworld.md), [docs/eval-equation-fresh5.md](../docs/eval-equation-fresh5.md) |
| `heldout_*.py` | Secondary scores of the first registered held-out run, from its recorded answers | [docs/eval-secondary-metrics.md](../docs/eval-secondary-metrics.md) |
| `handwriting_*.py`, `image_page*.py`, `image_pages_*.py`, `typed_scans_check.py`, `page_type_corpus_compare.py` | Scanned and handwritten pages: the answer keys, the evaluation, error analysis and replays | [docs/eval-handwriting.md](../docs/eval-handwriting.md), [docs/eval-image-pages.md](../docs/eval-image-pages.md) |
| `chem_check_replay.py` | The chemistry species check on recorded answers | [docs/chemistry.md](../docs/chemistry.md) |
| `figures_v2_*.py` | The second figure answer key: pool, draw and build | [docs/eval-figures-long.md](../docs/eval-figures-long.md) |
| `tables_*.py` | The real-document table sets and their labels | [docs/tables.md](../docs/tables.md) |
| `screenreader_speech.py`, `speech_oracles/` | What a screen reader's speech rules (MathCAT, SRE) say for our MathML and the answer key's | [docs/eval.md](../docs/eval.md) |
