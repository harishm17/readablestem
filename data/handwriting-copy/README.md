# Handwriting copy set

A small set of real iPad handwriting with answer keys that nobody typed. A few volunteers copy CC BY
worked examples from OpenStax textbooks by hand, in any notes app, and export a PDF. The answer key
is the textbook's own MathML for the passage they copied. It is meant for measuring how well
handwriting is read on vector ink from real notes apps (the [MathWriting](../handwriting/ATTRIBUTION.md)
pages are scans of drawn ink, and CC BY-NC-SA). See [eval-handwriting.md](../../docs/eval-handwriting.md)
for what the existing handwriting measurements do and do not show.

This folder holds the kit's documentation. The set itself (`manifest.json`, `gold.jsonl`,
`ATTRIBUTION.md`, `pages/`) is written here by `export` after the pages have come back, and only
holds pages whose writer said yes to publishing. No number from this set exists yet.

## The flow

The tool is `scripts/handwriting_copy_set.py`. It costs nothing: `select` reads public OpenStax
pages at a low request rate and caches them; nothing calls a model. Keep the work folder and the
returned PDFs **outside the repository** (the script refuses a folder inside it): they hold people's
handwriting.

```
S=handwriting-copy-2026-11            # any string; the same seed draws the same passages
W=/somewhere/outside/the/repo/copy-work
uv run python scripts/handwriting_copy_set.py select  --work $W --seed $S --writers 3 --per-writer 10
uv run python scripts/handwriting_copy_set.py brief   --work $W --send-to "where writers send PDFs"
#   ... give each writer their brief-wNN.html; ask the consent question in it; PDFs come back ...
uv run python scripts/handwriting_copy_set.py intake  --work $W --returns /somewhere/else/returned
uv run python scripts/handwriting_copy_set.py consent --work $W --writer w01 --publish yes
uv run python scripts/handwriting_copy_set.py review  --work $W            # PNGs to look at
uv run python scripts/handwriting_copy_set.py review  --work $W --approve w01-01 w01-02 --reviewer r01
uv run python scripts/handwriting_copy_set.py gold    --work $W
uv run python scripts/handwriting_copy_set.py manifest --work $W --out $W/manifest.json
uv run python scripts/handwriting_copy_set.py export  --work $W --dest data/handwriting-copy
uv run python scripts/handwriting_copy_set.py purge   --work $W              # unpublished pages
```

1. **select** draws the passages with the written rule below and records the seed, the pinned book
   versions, what was refused and why, and every passage's source and licence in `selection.json`.
   It also saves each passage's source XHTML under `passages/`. `--pin slug=ARCHIVE@VERSION` repeats
   an earlier draw on the same book versions; `--live-only` refuses retired editions.
2. **brief** writes one printable, on-screen HTML page per writer, with their pseudonymous id
   (`w01`, `w02`, ...), what to copy (rendered from the source, with the credit under each
   passage), how to export a PDF that keeps the ink, how to send it, and the consent question. Any
   notes app works. The text is in the script (`BRIEF_*`, `CONSENT_QUESTION`).
3. **intake** matches returned PDFs to passages by the code in the file name (`w01-03.pdf`; case,
   spacing, a leading `p` and extra words are tolerated). It reports files it could not match,
   files naming two passages, codes it does not know, and two files for one passage; it matches none
   of those. A PDF is accepted if it opens, has 1 to 4 pages (a whole notebook is refused, because
   it may hold other notes) and has ink: `vector` (20 or more drawn paths) or `raster` (an image
   only; flagged, because the vector branch cannot use it). Only a known notes-app name (or
   `other`) is kept from the file's producer and creator strings, because the raw strings can hold
   a name or a device name. Reports and `intake.json` hold counts, codes and that app label, never
   original file names, raw metadata or page content.
4. **gold** writes `gold.jsonl` from the saved source: one record per formula in reading order, its
   MathML as published, its display mode and context, plus the prose as published. Nothing is typed.
   The records score with `readablestem.eval.equations` (`score_mathml`, `score_latex`).
5. **review** renders every accepted page to a PNG in `$W/review/` (plus a text file of any hidden
   text layer, which iPad exports often carry) and a person looks at each one for names, faces,
   other notes or anything else that should not be public. `review --approve CODE ... --reviewer r01`
   records who (a pseudonymous id like `r01`) and when; `--reject` keeps a page private. A mark is
   tied to the file's hash, so a replaced page needs a new look.
6. **consent** records each writer's answer (`yes` or `no`, a date, and a hash of the exact text
   asked). A yes counts only for that text: if the consent text changes, the answer is out of date
   and the page stays private until the writer is asked again. With no recorded answer, a writer's
   pages are private.
7. **manifest** and **export** write the publishable manifest. Writers appear only by id. A page is
   `published` only when its writer's current yes is recorded **and** a person has approved that
   exact file. `export` refuses to run while a consented page is still `awaiting_review`. It writes
   each published page as a new PDF holding the page content only (annotations are baked into the
   page first): no outline, attachments, links, annotations, thumbnails, XMP, custom keys or dates,
   and only a known app name as producer. It then reads the new file back and, if anything
   identifying is found, keeps nothing and stops (`verify_publishable`). That check covers what
   notes apps and people are known to leave; it is not a proof, and the content of the page itself
   is what the person reviewing looks at. Pages without a yes are `private`: the manifest keeps only
   counts for them (pages, ink kind, no file, no hash) and the PDFs stay in the work folder.
   Published pages are licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) and
   credited to the writer id and to the OpenStax source (`ATTRIBUTION.md`, which gives each book's
   own licence). Pages that are `private`, `awaiting_review`, `missing`, `deleted` or `rejected` are
   listed with their status so the counts add up.
8. **purge** deletes pages that were not published: those of a writer whose current answer is no,
   and any page 90 days after it arrived (the period the consent text promises). Counts stay in
   `intake.json`; the manifest shows such pages as `deleted`.

The consent text asks for adults only (18 or over), says who is asking, that a person looks at every
page, how to withdraw and where to reply, and how long unpublished pages are kept.

## The passage rule (version 1)

Written before any passage is drawn; the same text is stored in `selection.json`.

- **Books.** Only the books in the subject table of the script, and only if the committed licence
  table ([openstax-licenses.csv](../openstax-licenses.csv)) says CC BY **and** the live archive record
  **and** the live CMS record both say plain CC BY. Anything else is refused and recorded. Live and
  retired editions are allowed (retired first editions are CC BY; see the held-out set's
  [decision](../../proof/DECISION-retired-editions-2026-10-13.md)); each passage records the state.
- **Passages.** One worked example (`data-type="example"`) on a numbered section page. It needs no
  image, figure or table (so all of it can be copied), 3 to 12 formulas with at least 3 of 3 or more
  characters, 15 to 220 words of prose, and at least 5 formulas per 100 words.
- **Subjects.** Algebra 3, calculus 2, physics 3, statistics 2, chemistry 1 (weights; largest
  remainder). Sections about limits, derivatives, integrals or rates of change count as calculus.
  There is no CC BY calculus textbook in the table, so calculus passages come from the precalculus
  book's calculus sections, and fewer than the quota may be found; a shortfall is handed to the
  other subjects and recorded, never filled with a book that fails the licence gate.
- **The draw.** Within a subject, sections are ordered by `sha256("<seed>|<subject>|<book>|<section>")`.
  Each section gives its qualifying example with the smallest `sha256("<seed>|<section>|<example id>")`.
  One passage per section. Passages are interleaved by subject and dealt to the writers in turn.

A dry run on the live archive on 2026-11-03 (3 writers, 10 each) found 30 passages with 223 formulas,
70 of them display equations; calculus and statistics ran short and the shortfall went to the others.
That run was a check of the rule, not the set.

## What the set cannot show

- A handful of writers and about 10 passages each: any rate has a wide interval, and it says nothing
  about writers who are not these. Writers know the pages will be read by a machine, and they copy
  printed text, which is tidier than notes taken in a lecture.
- Worked examples only: no diagrams, tables, crossed-out derivations or prose-heavy pages.
- A page that is `raster` tests the picture path, not the vector path.
- Handwriting can identify a person even without a name. Consent is asked in the brief and recorded
  here, and every page is looked at by a person before it is published; a CC BY licence cannot be taken back from copies already made.

## Files in this folder after `export`

| File | What it is | Licence |
|---|---|---|
| `manifest.json` | passages, sources, versions, per-page status, writers' consent, totals | MIT |
| `gold.jsonl` | the answer key: published MathML and prose of each passage | each book's CC BY licence (OpenStax) |
| `ATTRIBUTION.md` | books, authors, licences | |
| `pages/<code>.pdf` | consented handwriting pages only | CC BY 4.0, credited to the writer id |
