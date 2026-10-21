# Attribution

The handwriting evaluation set is built from the MathWriting dataset:

- Philippe Gervais, Anastasiia Fadeeva, Andrii Maksai. "MathWriting: A Dataset For Handwritten
  Mathematical Expression Recognition", 2024. https://arxiv.org/abs/2404.10690
- Data and code: https://github.com/google-research/google-research/tree/master/mathwriting
- The data is licensed by Google LLC under Creative Commons Attribution-NonCommercial-ShareAlike
  4.0 International (https://creativecommons.org/licenses/by-nc-sa/4.0/); the code of that repository
  is Apache 2.0.

No ink, page picture or label file is committed to this repository. `manifest.json` lists
sample identifiers and SHA-256 hashes. The pages are rebuilt locally from the dataset archive
(`scripts/handwriting_eval.py fetch`, `build`, `verify`) and stay in
`~/.cache/readablestem/handwriting`. The ink is drawn at pen width, composed with typed words into
pages and degraded like a scan, which is a change to the material; any short excerpt of a label in
docs/eval-handwriting.md is quoted with this attribution, under the same licence. The MIT licence
of this repository does not apply to the dataset.
