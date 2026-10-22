# Handwriting, measured once: an answer key of real human ink (2026-10-21)

[eval-image-pages.md](eval-image-pages.md) measured the scanned and handwritten pages pass
([ADR 0007](decisions/0007-image-pages.md)) on synthetic scans of printed pages and on real papers
made into scans, and says plainly: "Handwriting is measured only by eye on four pages. There is no
handwriting answer key." This page builds that answer key and measures handwriting once, so that the
owner can decide whether the app may advertise handwriting. The set, the metrics and the decision
rule were registered before any model call
([data/handwriting/PREREGISTRATION.md](../data/handwriting/PREREGISTRATION.md), hashed in
`data/handwriting/REGISTRATION.sha256`, committed in `7e33024`); nothing about the pipeline, the
prompt or the settings was changed after any number. Every model answer was recorded once and
every table below is re-scored from the recordings (`scripts/handwriting_eval.py report`).

**The short version.** On 24 pages of real human handwriting (205 expressions, held-out), the image
page pass with `claude-opus-5-5` ends with **0.566** of the equations exactly right end to end on
the primary score (95% cluster interval 0.515 to 0.618; 0.576 on the symbols score), and **no wrong
equation without a review item** (0 silent wrong equations, on the symbols and on the strict
score). Registered rule: advertise if the primary is at least 0.50 and silent wrong equations are 0.
**By the letter of the rule, both hold.** What the rule does not capture, and the owner should read
before advertising: 43% of the equations are wrong, and the reason none is silent is that the
model calls 85% of handwritten blocks `medium` or `low` confidence, so 5.7 decisions per page are
required (typed pages: 1.4); it silently dropped four whole display equations of one page (nothing
flags them); and 35 of the 56 expressions set as display equations came out as inline maths in a
paragraph or list item, not as display equations. The cost is $0.066 a page. Total spend $2.09 of
the $8.00 cap.

**Added afterwards (same day): the silent omission is now a required item.** A check compares the
page picture with the regions the blocks account for and makes a stretch of writing nobody read a
required decision ("The omission check" below). Replayed at no cost, it flags the known page, adds
no decision on the 10 development pages and 0.04 a page on the clean held-out pages (one false
flag in 23), and changes neither the primary score nor the silent wrong equations (0 and 0). It
does not touch the 35 display expressions read as inline maths.

## The set

Real human ink from Google's MathWriting dataset: people copying a rendered LaTeX expression on a
touchscreen or with a digital pen, so that the label is what the writer was asked to write.
**Licence CC BY-NC-SA 4.0 (Google LLC), so none of it is committed**: the repository holds the
builder and `data/handwriting/manifest.json` (identifiers and SHA-256 hashes of every ink file,
page picture and gold file); `scripts/handwriting_eval.py fetch`, `build` and `verify` rebuild the
set from the 2.9 GB archive (verified byte for byte on the author's machine, with the DejaVu Serif
font hashed in the manifest). Attribution: [data/handwriting/ATTRIBUTION.md](../data/handwriting/ATTRIBUTION.md)
(Gervais, Fadeeva and Maksai, arXiv:2404.10690). Only human ink is used; MathWriting's synthetic
ink is not. Other sources: CROHME 2019 (research use only, no licence file found) and openly
licensed scanned lecture notes with a typed transcript (none found) were not used, so there is one
source; its breakdown is by the dataset's own splits and by strata.

| | Dev (MathWriting `valid`) | Held-out (MathWriting `test`) |
|---|---|---|
| Units / PDF pages | 5 / 10 | 12 / 24 |
| Gold equations (display / inline) | 75 (32 / 43) | 205 (56 / 149) |
| Tokens 3-7 / 8-15 / 16+ | 42 / 32 / 1 | 51 / 141 / 13 |
| Ink black / blue / graphite | 15 / 46 / 14 | 72 / 101 / 32 |
| Plain / ruled paper | 57 / 18 | 120 / 85 |

Manifest SHA-256 `81dcbe862ec01e10ebf6fdddfac8276d2800d1fe422e612c93af139b6d9f8676`. The two parts
are different splits of the dataset, which keeps every expression in one split only.

**How the pages are made** (`src/readablestem/eval/handwriting_set.py`). A label is used if it
converts by the pipeline's own LaTeX path with no unknown command, has 3 to 40 tokens, is at most
150 characters, and has no text mode and no run of plain letters spelling a word. Pages are US
Letter at 300 dpi, two to a document, up to seven blocks each: a short typed sentence with one or
two handwritten expressions inside it ("Since [ink] we obtain [ink] directly."), or a handwritten
display expression on a line of its own, centred or after a typed label ("3."). The ink is drawn at
pen width in black, blue or graphite, a third of the documents on ruled paper, and each page is
degraded by `eval/scan.py` (150 to 200 dpi, small rotation, shading, grain, blur, JPEG 55 to 75) and
set in an image-only PDF with no text layer. The gold is one record per expression in the document
task's format, so the existing scorers apply unchanged.

**What it is not.** The ink is not paper handwriting: it is touchscreen ink, often fast and sloppy,
drawn onto a composed page and then degraded like a scan. The label is the writer's task, not a
transcription of the ink, so a faithful reading can be marked wrong. The words around the inline
expressions are typed, and the prose is not scored. There is no handwritten prose, diagram, table,
crossing-out or multi-line derivation. Claude may have seen MathWriting in training.

## How it was run

Production pipeline at `origin/main` (`e4edab1`), `FEATURE_IMAGE_PAGES=true`, page reader
`claude-opus-5-5` at its default effort (`low`) and 200 dpi, equation step `claude-opus-5-5` main and
`claude-haiku-5-5` cheap, no second automated check, one unit at a time, once per part: dev (cap
$2.00), then held-out (cap $8.00 minus the dev spend). Image page prompt `image_page-632b4d29`
(unchanged, hashed in the registration). Both runs finished every unit; no cap stopped either.

## Results

| | Dev (75 equations, 10 pages) | **Held-out (205 equations, 24 pages)** |
|---|---|---|
| **equations right end to end, primary `exact_lenient`** | 0.587 [0.493, 0.663] | **0.566 [0.515, 0.618]** |
| equations right end to end, symbols (credit) | 0.653 | 0.576 |
| detection recall / precision | 0.973 / 0.986 | 0.980 / 1.000 |
| **silent wrong equations**, symbols / strict | 0 / 0 | **0 / 0** |
| matched equations that are an asked item | 72 of 73 | 194 of 201 |
| output equations with no gold partner | 1 | 0 |
| gold equations nothing was aligned to (missed) | 2 (not asked) | 4 (not asked; one page, below) |
| blocks `medium` or `low` confidence | 56 of 64 (0.875) | 133 of 157 (0.847) |
| required decisions / optional notes per page | 5.8 / 0.6 | 5.7 / 0.8 |
| `[illegible]` markers | 1 | 0 |
| pages read / kept as image / fell back | 10 / 0 / 0 | 24 / 0 / 0 |
| page read: $ per page / whole pipeline: $ per page | 0.043 / 0.051 | 0.062 / 0.066 |
| page read: seconds per page | 9.2 | 10.6 |

(The interval is a 95% cluster bootstrap over units: 10,000 resamples, seed 20261021. Dev is not
pooled with held-out. On the symbols score the held-out interval, uncredited, is 0.520 to 0.634.)

### By stratum (held-out; descriptive, no tests; the per-unit range is 0.385 to 0.750)

| | gold equations | found | primary right | symbols right |
|---|---|---|---|---|
| display (set on its own line) | 56 | 52 | 32 (0.571) | 33 (0.589) |
| inline (inside a typed sentence) | 149 | 149 | 84 (0.564) | 85 (0.570) |
| black ink / blue / graphite | 72 / 101 / 32 | 68 / 101 / 32 | 0.542 / 0.564 / 0.625 | 0.542 / 0.574 / 0.656 |
| plain / ruled paper | 120 / 85 | 120 / 81 | 0.542 / 0.600 | 0.558 / 0.600 |
| 3-7 / 8-15 / 16+ tokens | 51 / 141 / 13 | 51 / 138 / 12 | 0.627 / 0.539 / 0.615 | 0.647 / 0.546 / 0.615 |

Nothing in the strata separates clearly: a difference of a few points on 32 to 150 equations
is inside the noise of 12 units (the interval above is 10 points wide). Ruled paper and graphite ink
are not worse.

### What the review flags did (held-out)

- **Nothing wrong was silent, because almost everything was asked.** 194 of 201 matched equations
  belong to an asked item; the 7 that do not are all right. Of the 85 wrong ones, all 85 are in an
  item a person is asked to decide. The mechanism is not the design's structural rule: a paragraph
  is required when the model gives it `medium` or `low` confidence, and on handwriting it does so for
  85% of the blocks (Opus on the scans of printed pages: 1% on the development scans, 7% on the held-out paper scans). So the zero is a measured
  property of this model's confidence on this ink, not something the pipeline enforces, and it
  could change with a model or prompt that is more sure of itself.
- **Display equations are not display equations.** Of the 56 expressions set on a line of their
  own, 17 came out as display equation blocks (each a required decision with a second reading of
  the crop: the crop and page readings agreed on 14, 3 of the 14 wrong, and disagreed on 3, all
  3 wrong), 35 as inline maths inside a paragraph or list item (the model made 35 list items on
  these pages, every one with a marker such as "1." or "(b)", about the number of labelled display
  lines; I did not check them one by one), and 4 were not found. So "every display equation is required by construction" held
  for a third of them; the rest were covered by their paragraph's confidence.
- **One page lost four equations without a flag.** In `hw-heldout-08` the reader returned only
  the first line of the second page (216 output tokens: "If [k] then [..] follows.") and nothing for
  the four display equations below it (`x=\sum_{i=1}^{K}w_{i}P_{i}s_{i}`, `P=\frac{W}{t}=\frac{1}{2}\frac{F^{2}}{m}t`,
  `\Delta\sum_{x}f(x)=f(x)`, `F(z)=F^{\prime}(z)F^{\prime\prime}(z)`). The page counted as converted;
  its one paragraph was `medium`, so a person is asked about that paragraph and not about what is
  missing. These four are the whole of the "missed" row; the original page is one click away
  ("Show the original page"), and nothing says to look. This is not counted as a silent wrong
  equation by the registered definition (it is unmatched, not matched and wrong). It is a silent
  omission and is the one failure here that the review design does not catch.
  **Closed afterwards** by the omission check below: that page is now flagged.
- **The model guesses.** It wrote `[illegible]` 0 times on 24 pages where 43% of the equations are
  wrong; its protection is the confidence label, not an admission that a symbol cannot be read.

### What the wrong ones look like (by eye, after the numbers)

Looked at: the 12 held-out equations with the lowest symbols similarity among those that are wrong,
and 18 more drawn at random (seed 7) from the remaining wrong ones: my reading of the pictures,
one reader, not checked by anyone else, from the composed page before degradation (the model saw
the degraded scan, at lower resolution). They do not enter the decision.

Of the 18 drawn at random: **4** are the model's reading faithful to the ink and wrong only by
notation the label did not use (`\bar{A}` for `\overline{A}`, `\ldots` for `...`, `\ell` for `l`,
`\varepsilon` for `\epsilon`); **11** are ink that is ambiguous in the way handwriting is (`x` or
`X`, `p` or `\rho`, `\Gamma` or `T`, `\phi` or `\Phi`, `s` or `\delta`, `\tau` or `T`); **3** are
the model's mistakes where the ink is fairly clear (a square root read as `T` and the formula
turned into `\text{TH/d}`; a blackboard bold `\mathbb{Z}` the ink does not show; the structure of
a stack of sub- and superscripts rearranged). Of the 12 worst: 2 where the label is not what the
ink shows, 3 ambiguous, 2 notation, 5 mistakes, among them the one clean misread in a display:

| Where (hw-heldout-) | Label (the writer's task) | The pipeline's reading | By eye |
|---|---|---|---|
| 02, eq 2 (display) | `f^{-1}(y)=\pm\sqrt{y}` | `f(y)=\pm\sqrt{y}` | the superscript is clearly in the ink and was dropped; asked |
| 01, eq 9 | `(S\cap Y)^{\sigma}=S^{\sigma}\cap Y` | `(\sin y)^{5}=\sin^{5}y` | cramped ink, misread; asked |
| 02, eq 4 | `i\partial_{t}u+\Delta u=-Au` | `i\partial_i n + \delta n = \cdot A n` | t and i, u and n look alike; asked |
| 12, eq 16 | `Lu=u\|_{\Omega\\ \omega}` | `L_u=4\|\Omega\omega` | the ink shows what the model read: the label is not reached |
| 12, eq 1 | `R/Q\cap Q/P=\{0\}` | the same with `\mathbb{R}`, `\mathbb{Q}`, `\mathbb{P}` | plain letters in the ink; blackboard bold invented |
| 09, eq 7 | `Im2\overline{A}B` | `\mathrm{Im}\,2\bar{A}B` | right; notation only |
| 07, eq 12 | `D_{I^{+2}}` | `D_{\underline{7}+2}` | ink ambiguous |

## The omission check (added afterwards, the same day)

The page `hw-heldout-08` above is a page the pass called converted whose reader stopped after one
line. The fix is not in the prompt (it did not change, so no answer was re-recorded) but in a check
that needs no model: `pipeline/image_page_omission.py`, ADR 0007, point 8. The page picture's ink is
compared with the regions the blocks' boxes cover, the boxes grown by 3% of the page width and 6% of
its height because the reader's boxes sit off handwriting by up to a line; ink that no box is near,
in a stretch of at least 2% of the page's ink and five grid rows (about 2% of the page height), is a
figure element with a required decision (`image_page.omitted_region`: "There is writing here that
nothing in the converted page accounts for ...") that shows the region as a picture marked "Not
converted" until the reviewer describes it or keeps it. Rules, frames, a scanner's bar, the rim of
the page, grain and the strips where running headers and footers sit are ignored.

**How the thresholds were set.** On development material only: the 10 handwriting development
pages, the 36 synthetic scans of printed pages and the 8 real pages of docs/eval-image-pages.md (no
answer key; these were also looked at by eye to see what a false flag looks like). The vertical margin
(6%) is the smallest, in steps of 20 thousandths, at which the development scans of printed pages and
the handwriting development pages flag nothing and the real pages stop flagging for the box margin.
Disclosure: while building the check I looked at pictures of held-out handwriting pages with the
reader's boxes drawn on them, to see how far boxes sit off the ink (that is what showed that boxes on
handwriting need a wide margin), and printed the number of flagged held-out pages at several settings
(not their pictures, for the printed-page scans). No threshold was chosen to change a held-out count;
but the held-out numbers below are not a clean "once". The thresholds are frozen as they were after
the development work.

**Measured by replay, at $0** (`scripts/image_page_omission_replay.py replay`: every set run twice
through the real pipeline on its recorded answers, with the check off and on; no request is sent).

| | pages | pages flagged | extra required decisions a page | primary, off to on | silent wrong equations (symbols / strict), off to on |
|---|---|---|---|---|---|
| handwriting, development | 10 | 0 | 0 (5.80 to 5.80) | 0.587 to 0.587 | 0 / 0 to 0 / 0 |
| **handwriting, held-out** | 24 | **2** | **+0.083 (5.71 to 5.79)** | 0.566 to 0.566 | 0 / 0 to 0 / 0 |
| printed pages as scans, development | 36 | 0 | 0 (3.50 to 3.50) | same in both arms (the equation step's recorded answers are for an older prompt) | same in both arms |
| papers as scans, held-out (every page with a recorded answer) | 59 | 5 | +0.085 (1.92 to 2.00) | 0.578 to 0.578 | 46 / 78 to 46 / 78 |
| 8 real pages (7 converted; no answer key) | 7 | 4 | +0.57 (7.71 to 8.29) | n.a. | n.a. |

- **The known page is flagged.** `hw-heldout-08`, page 2: the region covers the lower three of the
  four dropped display equations (the first of the four sits within the margin of error of the one
  line that was returned). It is one more required decision on that page (8 instead of 7).
- **False flags on clean pages.** Handwriting held-out: one page of 23 (`hw-heldout-02`: a box off its
  line by more than the margin, so the end of a sentence's last expression lies outside it): 0.043
  extra decisions a page on the clean pages. Handwriting development and printed-page scans
  development: none. The real photographs and notebook pages are the hard case: 3 of 7 flagged
  falsely (the shadow along a paper's edge; two pages whose boxes are off their ink by more than a
  tenth of the page), 1 truly (van 't Hoff's left margin column of numbers, which the reader left
  out). These are the pages the reader's boxes are worst on.
- **Detection by simulation.** Dropping blocks from the reader's own answers (the last one to three,
  and one or two from the middle; made up, the pages and the boxes are real) and asking whether the
  page flags, by the ink in the dropped blocks (share of the page's ink). Handwriting held-out:
  1 of 3 below 3%, 2 of 3 from 3% to 6%, 24 of 35 from 6% to 12%, 176 of 225 above 12% (0.78). Printed
  pages development: 1 of 214, 13 of 222, 63 of 272, 126 of 194 (0.65 above 12%). Papers held-out:
  10 of 267, 15 of 187, 53 of 177, 353 of 407 (0.87 above 12%). The check finds a dropped column or
  stretch of lines and is nearly blind to a dropped line or short paragraph next to a block, which is
  the price of boxes that sit off the ink. Real omissions are not made by deleting a block (a reader
  that stops early may also read what it did read differently), so these are a ceiling on what the
  check sees, not a rate of what it finds.
- **It also found real omissions in the typed scans** (docs/eval-image-pages.md, "The omission
  check").
- **It does not catch the display equations read as inline maths** (35 of the 56 in held-out): the ink
  is inside a paragraph's or a list item's box, so it is accounted for. Reported as asked; changing how
  those pages are laid out is a different change and is not attempted here.
- **Primary and silent wrong equations do not change**: an omission item is a figure item with no
  equation in it, and replay gives the same 0.566 and 0 / 0 (held-out), 0.587 and 0 / 0 (development).

**What it cannot catch.** A wrong reading (it looks at where ink is, not what it says); a dropped
line or short paragraph next to a block (inside the margin of error of the boxes, 6% of the page
height) or whose ink lies under a loose block's box; writing in the outer 1.5% of the page, a line or
two in the top and bottom 6% (running headers and footers), or inside a frame drawn round text; a
page on which a block has no usable box (not judged) or whose picture could not be read. It is not a
transcription check: it says "something here was not read", not what.

## Decision, against the registered rule

**Rule:** handwriting may be advertised if, on held-out, the primary is at least 0.50 and silent
wrong equations are 0. **Held-out: primary 0.566 (interval 0.515 to 0.618, whose lower end is above
0.50) and silent wrong equations 0 on the symbols score and 0 on the strict score. The rule is
met.** Dev agrees (0.587, 0). The run was not stopped by a cap and 12 of 12 units finished.

What "may be advertised" can honestly mean on this evidence, for the owner to word: the pass reads
about 57% of messy touchscreen handwritten expressions exactly, on this kind of page, and it
leaves nobody without an item to decide on the equations it did find, at a price of about six
required decisions per page and $0.066 a page. It does not mean "reads handwriting", and the
evidence does not support wording that says the output is accurate or needs no check. The three
findings above (the silent omission of a whole block of display equations, display equations
folded into paragraphs, a zero that depends on the model's own low confidence) are the reasons not
to read the rule's pass as more than it says. Nothing here changes the rule, which was fixed first.
The "what a different design would change" the registration allows, not measured and not part of
the decision: required inline maths would add nothing here (177 of 184 matched inline equations were
already in asked items); a check that a page's reading ends where the page's ink ends would have
caught the dropped half page (built afterwards: the section above).

## What this does not show

- It is one source and one population of writers (MathWriting contributors, mostly on touchscreens),
  composed onto pages with typed words, and degraded synthetically. It says nothing about paper
  notebooks photographed under a lamp, handwritten prose, diagrams, tables, crossings-out,
  multi-line derivations, or other scripts. The ink was written to copy a printed formula; real
  notes are written to think.
- The label is what the writer was shown, not a reading of the ink. A faithful reader scores below
  100% on ink that does not show its label, and the notation conventions of LaTeX (`\bar` or
  `\overline`) are a source of "wrong" the scorer cannot tell from a misread. The by-eye split is my
  judgement on 30 of the 85 wrong equations and no more.
- One run per part, 12 held-out units, 205 equations: the interval is 10 points wide, and no stratum
  is tested. The rule's threshold sits 6.6 points below the point estimate and 1.5 points below the
  interval's lower end; a rerun with the same model would move it by an unknown amount (the earlier
  OpenStax runs moved by 0.7 points; no repeat was made here, by rule).
- Zero silent wrong equations is an observed count of a model's confidence labels, not a property
  of the design: 85% of handwritten blocks were `medium` or `low`. It says nothing about a reviewer.
  Nothing here tests whether a person given 5.7 decisions a page finds the 43% that are wrong.
- The set's pages are sparse (6.5 blocks a page); cost per page on a dense handwritten page would
  be higher, as on the dense papers ($0.136 a page).
- MathWriting may be in the model's training data; contamination cannot be checked, and would push
  the numbers up.
- Licence: CC BY-NC-SA 4.0. This is an evaluation of a free accessibility tool and no model is trained
  on the data, but the owner should confirm that a product decision counts as non-commercial use
  before the set is used again; nothing of it is committed.
- The equation step's prompt hash on `origin/main` (`equation-68ebbbf9`) differs from the one
  recorded in eval-image-pages.md (`equation-df0e12f1`) because the chemistry change merged in
  between; the image page prompt is unchanged (`image_page-632b4d29`).

## Spend

Recorded live spend, at list price, summed over every call in the answers files: **$2.09** of $8.00.

| | $ |
|---|---|
| dev: page reader `claude-opus-5-5` (10 pages) | 0.43 |
| dev: equation step (12 crops) | 0.08 |
| held-out: page reader `claude-opus-5-5` (24 pages) | 1.50 |
| held-out: equation step | 0.08 |

No run was repeated; the tables were re-scored from the recordings
(`scripts/handwriting_eval.py run --part heldout --offline`, `report`). The answers files and the
rebuilt pages stay outside the repository (`.local/handwriting`, `~/.cache/readablestem/handwriting`),
because they carry the dataset's labels and ink.

## Reproduce

```
uv run python scripts/handwriting_eval.py fetch      # download, check the archive's SHA-256, unpack
uv run python scripts/handwriting_eval.py verify     # rebuild and compare with data/handwriting/manifest.json
uv run python scripts/handwriting_eval.py report --part heldout   # needs the recorded run
```

A new live run needs `ANTHROPIC_API_KEY`, `run --part dev|heldout --max-usd N`, and a registration
whose hashes hold; it refuses a second live run of a part.
