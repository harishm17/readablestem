# Test corpus

`corpus.json` lists every file the workflow tests and the evals read, with where it comes
from, its license and its SHA-256. `src/readablestem/eval/corpus.py` reads it; the rules are
in [docs/eval.md](../../../docs/eval.md#the-test-corpus).

- `selfmade/`: PDFs made for this repository, each next to the HTML, LaTeX, Markdown or Word
  source it was printed from. MIT, like the rest of the repository.
- `public/`: two cuts of OpenStax textbooks and three files from Wikimedia Commons (below).
- `answers.jsonl.gz`: recorded model answers the workflow tests convert from, for the files
  that may be committed. Answers read from CC BY-NC-SA files are kept in the fetch cache
  instead ([docs/eval.md](../../../docs/eval.md#recording-and-replay)).
- `gold/`: reading-order labels, one file per entry. Labels for files made here hold the
  text of each block. Labels for anyone else's file hold no text, only ranges into the
  page's own words and a hash per block.

The other public files (MIT OpenCourseWare, arXiv) are not here. They are
fetched by URL into `.cache/corpus/` and checked against the hash in the manifest:

    uv run python -m readablestem.eval.corpus check --fetch

## Files from OpenStax

OpenStax's download URLs do not keep serving the same bytes, so the ten pages each test
needs are kept here. Both books are licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The pages are unchanged; only
the rest of each book was left out.

- `public/r15_openstax_chem_ch3.pdf`: pages 139 to 148 of *Chemistry*, OpenStax,
  © 2018 Rice University. Download for free at https://openstax.org/details/books/chemistry.
- `public/r16_openstax_physics_ch4_end.pdf`: pages 199 to 208 of *College Physics*, OpenStax,
  © 2020 Rice University. Access for free at openstax.org
  (https://openstax.org/details/books/college-physics).

## Files from Wikimedia Commons

Wikimedia answers a download from a CI runner with "too many requests", so these three files
are kept here. Each was saved again with PyMuPDF; the pages are unchanged.

- `public/r19_poster_codesign.pdf`: *Poster: Co-designing a Public Engagement Programme
  (Scotland's Health Research and Innovation Conference)* by Faye Watson, 2025, licensed
  under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
  [File page](https://commons.wikimedia.org/wiki/File:Poster_Co-designing_a_Public_Engagement_Programme_%28Scotlands_Health_Research_and_Innovation_Conference%29.pdf).
- `public/r20_newsletter_drp.pdf`: *Department of Research Programs Newsletter, Vol 1,
  Issue 1, March 2014*, Walter Reed National Military Medical Center. Public domain (a work
  of the United States government).
  [File page](https://commons.wikimedia.org/wiki/File:Department_of_Research_Programs_Newsletter_Vol_1%2C_Issue_1%2C_March_2014_%28IA_03DRPMARCH2014NEWSLETTERVOLUME1ISSUE1%29.pdf).
- `public/r21_infographic_orcid.pdf`: *ORCID infographic 2019* by ORCID, released under
  [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).
  [File page](https://commons.wikimedia.org/wiki/File:ORCID_Infographic_2019.pdf).

## Printing a self-made file again

    uv run python tests/workflows/corpus/print_selfmade.py s01_newsletter_3col OUTDIR

prints the entry's source with the options in its `made` field. Chromium and LaTeX do not
give the same bytes twice, so a reprint is a new file: replace the PDF, update `sha256` and
`bytes` in the manifest, and check its gold still describes the pages.
