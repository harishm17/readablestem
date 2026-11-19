# data/

Answer keys, registrations and committed evaluation reports. Where a folder holds other
people's material, its `ATTRIBUTION.md` or README names the source and the license;
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) has the full list.

| Folder | What is there | Explained in |
|---|---|---|
| [`eval/`](eval/) | The committed reports: the `baseline-*.json` files the CI regression gate compares against, the `latest-*.json` reports, the gate's tolerances and the corpus records | [docs/eval.md](../docs/eval.md), "How to rerun" |
| [`gold/`](gold/) | The first answer keys, from OpenStax (CC BY 4.0): equations, headings and figures, layout pages and table pages, with a manifest of hashes | [docs/eval.md](../docs/eval.md) |
| [`dev-v2/`](dev-v2/) | The extended development set | its [README](dev-v2/README.md) |
| [`gold-figures-v2/`](gold-figures-v2/) | The second figure answer key, for long descriptions | [docs/eval-figures-long.md](../docs/eval-figures-long.md) |
| [`authored/`](authored/) | Table documents written for this project (MIT): sources, PDFs and answer keys | [`authored/tables/README.md`](authored/tables/README.md) |
| [`real-tables/`](real-tables/), [`real-tables-heldout/`](real-tables-heldout/), [`real-tables-heldout2/`](real-tables-heldout2/) | Tables in real, openly licensed documents: a development set and two held-out sets | each folder's README; [docs/tables.md](../docs/tables.md) |
| [`realworld/`](realworld/) | Real papers from arXiv: the registration of each fresh split with its hash, the candidates, selections, manifests and result records | [docs/eval-realworld.md](../docs/eval-realworld.md), [docs/eval-equation-fresh5.md](../docs/eval-equation-fresh5.md) |
| [`chem-heldout/`](chem-heldout/) | The hashes of the held-out chemistry answer key | [docs/eval-chemistry-heldout.md](../docs/eval-chemistry-heldout.md) |
| [`handwriting/`](handwriting/), [`handwriting-copy/`](handwriting-copy/) | Handwriting: registrations, manifests and results of the evaluation, and the kit for collecting hand copies with consent | [docs/eval-handwriting.md](../docs/eval-handwriting.md); [`handwriting-copy/README.md`](handwriting-copy/README.md) |
| [`image-pages/`](image-pages/) | The registration of the scanned-papers held-out run | [docs/eval-image-pages.md](../docs/eval-image-pages.md) |

Two files sit beside the folders: `openstax-licenses.csv` (the license of each OpenStax book, as
retrieved) and `dependency_facts.toml` (what was decided about each dependency;
`scripts/dependency_record.py` writes [docs/dependencies.md](../docs/dependencies.md) from it).

A registration (`PREREGISTRATION*.md`) is frozen with its hash (`REGISTRATION*.sha256`) before
the run it describes. Do not edit one; a change is a new addendum.
