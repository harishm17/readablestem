# The static reviewer demo

`site/` builds a small website that shows what ReadableSTEM does on real pages, for people who
will not sign in or run anything: technical reviewers and university staff. It is plain HTML
and two stylesheets (the web app's design tokens, copied by the build with the self-hosted fonts they
name, and `site/assets/site.css`). It has no scripts, no frameworks, no trackers and no
requests to other sites; the fonts are files in the build, not a font service.

    make site                 # writes site/_build (OUT=<dir> for another folder)
    open site/_build/index.html

The build needs no key, no network and no model call, and it costs nothing. The same inputs
give the same bytes (CI builds it twice and compares).

## Where it is served

The app serves the build at `https://readablestem.com/showcase/` (`web/routes/discovery.py`).
The Docker image runs `site/build.py` into `src/readablestem/web/showcase/` before installing the
package (`.dockerignore` lets the lecture-notes PDF and `proof/sets/` through for it). The app adds
each page's canonical address, link-preview tags and structured data as it sends it, and lists the
pages in `/sitemap.xml` under their canonical address (a folder's `index.html` is the folder:
`/showcase/method/`, in the canonical link, the structured data and the sitemap alike;
`web.seo.showcase_canonical`); the stored conversions and the stemcheck sample are sent sandboxed, like
`/demo`, and marked `noindex`. Without a build (local development, tests) `/showcase/` is a 404
and nothing links to it; `make site OUT=src/readablestem/web/showcase` makes one.

Search engines and AI crawlers: `web/seo.py` lists the public pages once (the sitemap names them;
every other page is `noindex`), and writes `/robots.txt`, `/llms.txt` and the IndexNow key file.
When a public page's text changes, update its date in `PUBLIC_PAGES` (or `SHOWCASE_UPDATED`).

## Pages

| Page | What it holds |
|---|---|
| `index.html` | What ReadableSTEM is, who it is for, what it does not do, the headline numbers from `PROOF.md` with the set labelled, links to the README, `PROOF.md`, the method and the limits |
| `examples/index.html` | The worked examples in one table |
| `examples/<id>/index.html` | For one document: the PDF pages beside the HTML they became, what the checks found, the decisions a person would see (a static view of the review screen), how some equations are read aloud, the files and the credit |
| `examples/<id>/converted.html` | The tool's full output page, as stored |
| `examples/<id>/listen.html` | The listening transcript of the document: each equation's stored speech beside the player (`web.listen`) |
| `before-after/index.html` | For the chosen equations: the PDF's text layer and its reading, beside the HTML's MathML and its reading |
| `stemcheck/index.html`, `stemcheck/sample-report.html` | A short explanation of [stemcheck](https://github.com/harishm17/stemcheck) and one report it wrote |
| `method/index.html` | How the numbers and the examples were made, and the limits (read from `PROOF.md` and `proof/sets/`) |
| `attribution/index.html` | Title, authors, licence and link for every OpenStax page, and the credits for the other material |

Every page carries a footer naming the author and the licences (MIT for the code and the site;
CC BY 4.0 for the OpenStax material).

## Where the content comes from

Everything is committed. Nothing is fetched or generated at build time.

- **Stored results.** Each example is a folder `site/examples/<id>/` with `meta.json` (title,
  the PDF pages to show, the equations for the spoken readings, a one-line description) and
  the three files that `readablestem demo build` writes: `report.json` (the stored result),
  `converted.html` (the output) and `source.pdf` (the exact input). `meta.json` may point at
  the files elsewhere in the repository with `artefacts`, `html`, `pdf` and `report`: the
  Physics 2.3 example uses the app's own demo under `src/readablestem/web/static/demo/`, and the
  lecture-notes fixture uses `tests/fixtures/layout/lecture_notes.pdf`.
- **The review decisions.** The build loads the stored result with the app's own models and
  helpers (`web.review.group_fixes`, `web.messages`, `web.compare`, `web.speech_diff`) and
  writes a static card per decision: the crop from the PDF, the proposal, how it is read aloud
  against how the PDF's text is read, what differs, why the tool is asking, and what each choice
  would do. The app's templates are not reused, because they are forms that need a session.
  The cards say the buttons are not live.
- **The headline numbers.** The home page reads `PROOF.md` as it stands at build time. It takes
  the title, the opening quotes, the status table and a fixed list of rows (`HEADLINE_SPECS` in
  `site/build.py`), and labels the set (development or held out) from the file's own words.
  A row it cannot find is left out. If `PROOF.md` is missing the page says so. When a
  held-out `PROOF.md` is committed, the same build shows its numbers with a held-out label and
  its own list of limits.
- **The stemcheck sample.** `site/stemcheck/sample-report.html` is a report written by
  stemcheck 0.2.0 for a chatbot's HTML of OpenStax Physics 2.2 and 2.3. `meta.json` there
  records the inputs' hashes and the command. To make another, run
  `uv run --project <stemcheck checkout> stemcheck check <pdf> <html> -o <folder>` and copy
  `report.html` over the sample.

## Writing the examples again without a model

When the output changes (the assembler, the stylesheet, the About region) and the models'
answers do not, the stored results are still right and only the HTML has to be written again:

```
uv run readablestem demo rerender <report.json> <source.pdf> <converted.html> [--no-note]
```

It cuts the pictures from the PDF as a conversion does, writes the file with today's assembler
from the stored result, puts the demo note back (`--no-note` for a fixture), and keeps the
stored result as it was written (only its `html_checks` are replaced, and only if they differ).
No page is fetched, no model is asked, nothing is paid. The result still says which pipeline
version converted it. The 2026-10-14 Tier 3 rebuild of the public demo and the four examples was
made this way: one `h1`, the dark colour scheme and the labelled "About this version" region.

## Adding an example

1. Convert a page or a fixture with the Claude provider, with a spend cap:
   `uv run --env-file <env file> readablestem demo build --page <slug> --provider anthropic --max-usd <cap> --out <folder>`
   (OpenStax pages), or `MODEL_PROVIDER=anthropic ... readablestem convert <pdf> --out <html> --report <json> --max-usd <cap>`
   (a fixture; wrap the result as `{"demo": {...}, "result": ...}` as the other `report.json`
   files are). The spend cap is a ceiling on reservations, not on what is spent; a low cap
   stops a run part-way and the report says so (`stopped_by`).
2. Put `report.json`, `converted.html`, `source.pdf` and a `meta.json` in a new
   `site/examples/<id>/`. Use only OpenStax material under CC BY 4.0, or fixtures that are MIT.
   Do not use the held-out sections.
3. Name the pages to show and the equations for the spoken readings in `meta.json`. Look at the
   page first: the choice is editorial, and the site says so.
4. `make site`, then `uv run pytest tests/test_site_build.py` and
   `uv run pytest -m browser tests/test_site_browser.py`.

## Tests

- `tests/test_site_build.py` (offline): every page parses, every link and fragment resolves
  inside the site, nothing is loaded from outside, no scripts, the build is repeatable, the
  tone rules hold (no claim of conformance), every OpenStax page is credited, and the parsers.
- `tests/test_site_browser.py` (`-m browser`): axe-core at 1280 and 320 CSS pixels on the
  site's pages (all rules, including WCAG 2.2 and best practice), on the converted pages and
  the stemcheck report (serious and critical only, since those are other tools' output), no
  sideways scrolling at 320 px, text spacing, scrollable regions reachable by keyboard, the
  skip link, and every page with JavaScript off.

## Publishing

The hosted app serves the build at `https://readablestem.com/showcase/` ("Where it is served"
above); that is how it is published. No GitHub Pages workflow exists, and `make site` itself
publishes nothing. All links inside the site are relative, so a copy also works from any folder or
host. The links to the README, `PROOF.md` and the docs point at `main` of `REPO_URL` in
`site/build.py`; change it there if the repository moves. The site is about 8 MB, most of it the
stemcheck report and the stored PDFs.

## Design system (the web app)

`DESIGN.md` records the visual system and `PRODUCT.md` the product facts it serves. The tokens
are `web/static/tokens.css` (fonts, colour in light and dark, type, space, radius, widths, layers
and the paper palette); buttons are `web/static/controls.css`; the other components are in
`web/static/app.css` under "Components", each with its responsive and forced-colour rules beside
it (`DESIGN.md`, "Stylesheets"). `/style` shows every one in the
visitor's theme (it is `noindex`, not in the sitemap, and nothing links to it). Pages people read
(landing, guides, help, offices, privacy, terms, limits) set `{% block body_class %}mode-read{%
endblock %}`: serif h1 and h2, 18px text in a 68ch column. App screens keep Source Sans 3
headings at 16px. Fonts are self-hosted (`web/static/vendor/README.md`); no page makes an
outside request. `tests/test_design_tokens.py` checks the contrast of every token pair, the
14px floor and the font budget.

The landing page's "Hear the difference" is built by `scripts/hero_assets.py` from `web/static/demo/` (an
equation-only crop of the PDF page, text layer, MathML, reading, font subset into `web/static/hero/` and `vendor/fonts/`); the app never runs it and
`tests/test_hero_assets.py` regenerates and compares. The proof tiles on /limits and the comparison chart (compact on the landing page) come from `web/proof_facts.py` (PROOF.md, parsed once into `proof_facts.json`).
