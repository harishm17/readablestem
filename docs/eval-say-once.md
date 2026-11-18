# The say-once measure: what it found on 2026-11-18

`pipeline/say_once.py` counts, for each page, which words are said by no element and which
are said twice (docs/steps.md, "Say once"). This is what it showed over every file we have,
before any line was set. `readablestem eval say-once` gives the corpus figures again.

Pipeline 0.5.11 (no version bump: nothing it writes is stored). No model, no second read of the
file. The corpus is a development set, so none of these figures meets a bar or sets a public
claim.

## What was measured

| Set | Files | Pages | Pages of 40 words or more | Words |
|---|---|---|---|---|
| The private corpus (counts only; no file, page or word of it is named here) | 27 of 28 | 280 | 225 | 71,809 |
| Every tracked public PDF of the repository, held-out and sealed sets left out | 100 of 117 | 428 | 326 | 89,616 |

The 117 tracked files are 100 different PDFs (16 are copies: the demo and site examples are
the same files as the proof bundle's) and one that cannot be read on purpose
(`tests/fixtures/layout/damaged_render_abort.pdf`). The corpus has one file layout cannot
read. Copies are counted once.

## The distribution

Share of a page's words in no element, over pages of 40 words or more (the same pages the line
is judged on). A cell is the number of pages at or over that share.

| Share of words | Corpus (225 pages) | Public (326 pages) |
|---|---|---|
| any (0.1% or more) | 9 | 15 |
| 1% | 4 | 0 |
| 2% | 1 | 0 |
| 5% | 0 | 0 |
| 15% (the reviewer's line) | 0 | 0 |
| median, 90th, 99th percentile, largest | 0, 0, 1.1%, 2.1% | 0, 0, 0.4%, 0.7% |
| all words, all pages | 0.046% (33 words) | 0.028% (25 words) |

Share of a page's words said twice (`two_places`, `place_and_text`, `said_twice`):

| Share of words | Corpus | Public |
|---|---|---|
| 1% | 36 | 12 |
| 5% | 19 | 6 |
| 10% | 11 | 4 |
| 15% | 8 | 3 |
| 50% | 2 | 1 |
| all words, all pages | 1.54% | 0.46% |

Nearly all of it is `place_and_text` (805 words in the corpus, 377 in public): a word inside an
equation, table or figure box that a paragraph also says. It is not a duplicate in the page
a student gets in every case, but it is a place where two elements claim the same words, and the
public cases below are real overlaps in layout.

Left out by layout's own rules, in glyphs: furniture 4,909 (1.6% of the corpus's glyphs) and
7,970 (2.1% of public's), hidden text 270 and 553, a contents line's leaders and numbers 22 and
142, OpenStax level tags ("[BL]", "[OL]") 0 and 158.

## The reviewer's line, tested

The independent reviewer's suggested line: a page of at least 40 words with 15% or more of them
in no element. Reviewer's own count: 0.39% of 50,122 body words missing over 28 corpus PDFs, one
bad page in 306. This function counts 0.046% over the 71,809 words of 27 corpus files, a
different count (the reviewer counted words of the page absent from the converted text; this
counts words no element has, with the page's own furniture and hidden text left out).

- At 15% and 40 words the line flags **0 of 551 pages** (225 and 326), so 0 are real and 0 are
  false. The cleanest reading is that it is quiet on all of them; it says nothing about whether
  it would find a page that is missing a block, because none of these pages is.
- The largest share of any page is 2.1% in the corpus and 0.7% in public. A line anywhere from
  3% to 15% is therefore equally quiet on what we have. Nothing here picks within that range.
- A planted fault is found: the generated page of three paragraphs with one taken out of
  layout's answer is 33% missing, and the same page whole is 0%
  (`tests/test_layout_silent_loss.py`, "say once"). A page that lost one block of eight
  (about 12%) would not be flagged at 15%.
- So the line stays where the reviewer put it, as the first thing to measure and nothing
  tuned. Moving it is WS4.11's decision, with a held-out set that has real losses in it.

## The worst pages, read one by one

The 30 worst public pages by share in no element (pages of 40 words or more). Fifteen of the 30
have no word in no element. The other fifteen have one to three words each, 23 words in all,
and each was read against the page:

| Cause | Pages | Words | Where |
|---|---|---|---|
| Scoring artifact of my own function: a hyphenation test document, where the element holds a whole word that the page's halves or its greedy match do not account for | 4 | 8 | `data/dev-v2/dev/authored/latex-hyphenation` pages 1 to 4 |
| Maths drawn inside a line: an underscript or a hat sits under the word, so the page reads `argmθin` and the element `argmθ in`, and a word and a letter run together | 3 | 5 | `data/real-tables/2407.13734.pdf` pages 5 and 6, `data/real-tables/2505.03022.pdf` page 2 |
| A word broken across a page break: its first half ends one page and its second starts the next, and a page's words are matched with that page's elements | 2 | 2 | `data/real-tables/2409.08224.pdf` pages 1 and 2 |
| A figure box beside text: a hyphen half and two words round the three boxes drawn over the page's quoted text | 1 | 3 | `data/real-tables/2409.08224.pdf` page 8 |
| A whole ordinary word the elements say, left over by the match: not explained | 3 | 3 | `2409.08224.pdf` page 6, `data/dev-v2/dev/authored/word-lecture` pages 3 and 4 |
| A figure's own label | 1 | 1 | `tests/workflows/corpus/public/r15_openstax_chem_ch3.pdf` page 6 |
| Phonetic symbols spelled differently by the element (my function) | 1 | 1 | `tests/workflows/corpus/selfmade/d1_scripts.pdf` page 1 |
| A margin text taken for a running head | 0 | 0 | none found; see what it cannot see |
| A lost block or column | 0 | 0 | none |
| A table | 0 | 0 | none counted here (see twice, below) |

Faults of the measure found while reading and fixed before these figures (each turned a page
of the first run into a clean one): OpenStax teacher tags (`[BL][OL]`) taken off by layout
(`teacher_tag`), the page numbers and leaders of a contents line (`contents`), a footnote's
mark glued to its word, a figure's caption repeating the words of the formulas in it
(`caption_marked`), a table's caption, the rows a table claims before the page's rows are
prepared (the key terms of a physics page), and a word broken at a row end being taken by any
other copy of its first half (`_unsaid`).

The worst pages by share said twice are overlaps layout makes, not scoring:

| Page | Words twice | Cause |
|---|---|---|
| `tests/fixtures/handwriting/overlays.pdf` p2 | 95 of 190 | a figure box over the half page that holds a typed paragraph also read as text |
| `data/dev-v2/dev/authored/html-slides/html-slides.pdf` p1 | 24 of 93 | stacked slide layers: the boxes of one layer's formulas sit over other layers' text |
| `data/real-tables/2409.08224.pdf` p8 | 189 of 997 | three figure boxes over body text (quoted boxes) |
| `tests/workflows/corpus/selfmade/f2_figures.pdf` p1, `s15_charts_figures.pdf` p1 | 11 each | labels of a figure read as paragraphs and also inside the figure's box |
| `data/dev-v2/dev/authored/latex-matrices/latex-matrices.pdf` p3 | 14 of 241 | the entries of a matrix read as loose paragraphs inside the equation's box |
| `data/real-tables/2407.13734.pdf` p3, `2401.00649.pdf` p15 | 9 and 4 | two inline equations whose boxes overlap (a footnote mark read as an equation on p15) |

## What it cannot see

- Wrong reading order: the words are matched as a bag. `eval order` measures order.
- A block that layout took for a running head or a page number: it is counted as left out by
  rule (`furniture`), which is what that rule is for, so it is not a fault of this measure.
  34 public pages and 27 corpus pages have 10% or more of their glyphs left out by rule, 6 and 3
  of them half or more. The 14 public pages with the largest share are pages of 0 to 33
  words (a page that is a plot with a page number, a slide with its bars, a covered layer),
  judged from their counts: no page was opened to check what was dropped.
- A word said in the wrong place, a formula read wrongly, a picture's content.
- A page kept whole as its picture (it has its own decision), and a page read from a picture.

## Cost

On a 28-page paper (`data/real-tables/2505.03022.pdf`), counting takes 0.14 to 0.16 s of CPU
(about 0.005 s a page) next to 23 s of CPU for layout. Switching the recording of the page's
words off made no difference that the box's noise (a load of about 20) did not hide: 23.0, 22.8
and 23.7 s with it, 22.5, 23.2 and 24.1 s without. Over the 100 public files the mean was
0.002 s a page counted, against 0.78 s a page for layout.
