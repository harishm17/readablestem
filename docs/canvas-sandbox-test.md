# Testing the Canvas export in a real Canvas

What the automated tests can and cannot show, and the steps Harish runs by hand in a Canvas
sandbox. Nothing here has been run in a real Canvas: the export was built from Canvas's
published sanitizer rules and is checked against a model of them
(`tests/test_export_canvas.py`), and the Common Cartridge is checked against a list of
the CC 1.3 structure rules (`tests/test_export_cartridge.py`). Those are the reasons this
test exists. About 45 minutes the first time.

## What the automated tests already cover

| Question | Test | Not covered |
|---|---|---|
| Does the paste page keep text, headings, tables, links, alt text, MathML and ids after Canvas's sanitizer rules? | `test_export_canvas.py` (a model of the sanitizer built from canvas-lms's own list) | Canvas's editor (TinyMCE) cleaning a page again when someone opens it for editing; link rewriting; MathJax |
| Is the cartridge a well-formed CC 1.3 package of web content? | `test_export_cartridge.py` (`check_cartridge`) | The IMS validator; how any LMS reads it |
| Do the equations carry the maths font stack, and does Canvas's list keep it? | `test_export_maths_fonts.py` | Which font a reader's computer then uses |
| Is the student file drawn as maths (fractions stacked, scripts raised, nothing cut off) at 1280 and 375 px, with pictures loading? | `test_student_file_browsers.py` in Chromium, Firefox and WebKit (an engine that is not installed is skipped) | Canvas's own page around it |
| Do the pages of the app pass axe? | `test_web_*_browser.py` | The Canvas pages |

## Before you start

- A Canvas course you can break. Either a free-for-teacher account at canvas.instructure.com
  or a sandbox course at UT Dallas (ask the Canvas support team for a "sandbox" or
  "development" course; say you are testing an accessibility tool and will not use student
  data). Use a synthetic document, never a real course's material.
- A finished job in the app, made from a short synthetic PDF with a heading, a list, an
  equation, a figure with alt text and a table. Note the job's page title.
- Two browsers (for example Chrome and Safari or Firefox), and if you can, a phone with
  the Canvas Student app.
- A screen reader if you have one (NVDA with Firefox or Chrome on Windows; VoiceOver with
  Safari on a Mac). If not, skip the screen reader lines and mark them "not tested".

On the job page, download both: **For Canvas (zip with images)** with your course number
(the number after `/courses/` in the course's address), and **Download as a Common Cartridge
(.imscc)**. Open the zip's `README.txt` once; its steps are the ones in part A.

## A. The zip: a Canvas Page and a linked file

1. In the course, **Files**: make the folder named in the README, upload the zip into it
   and choose to expand it. Write down: did the `images` folder appear, and did the zip
   expand without a message?
2. Open `index.html` from Files (select it, then the preview or the file's link). Write
   down: **do the pictures show?** (This is the question the README says is not confirmed.)
   Does the page open, or does it download?
3. **Pages**, new page: switch to the HTML editor, paste all of `canvas-page.html`, save.
   Write down: any message from Canvas on save?
4. Open the saved page as a student (**Student View**) and check, in this order: the
   headings (the page title is the top heading, so the document's own headings start one
   level lower); a picture and its alt text (inspect or hover); a table (caption, header
   cells); a list; the link text; the equation.
5. The equation: write down which of these happened.
   (a) Drawn by Canvas's MathJax (a larger, serif TeX-like look; right-click shows a MathJax
   menu).
   (b) Drawn by the browser (the same look as `index.html` opened from your computer).
   (c) Shown as plain text or not at all.
   Then, if (b): which font? Windows and Mac usually differ. If it looks like a sans-serif
   fallback with badly placed parts, the stack found no maths font: note the operating
   system and browser.
6. Open the page for editing and save it again without changing anything, then view it:
   does anything disappear (this is Canvas's editor cleaning it)? Compare the equations,
   the table and the picture before and after.
7. On a phone (Canvas Student app or a mobile browser at the page's address): can you read
   the page, are the equations cut off, can you scroll a wide equation or table sideways?
8. Screen reader, if available: move through the headings list, the table (does it say the
   column header with each cell?) and the equation (does it read as maths?). A pasted page
   loses `headers` on irregular tables and `tabindex` on scroll boxes: the README says so;
   note whether it matters in practice.

## B. The Common Cartridge

1. **Settings**, **Import Course Content**, content type **Common Cartridge 1.x Package**,
   choose the `.imscc` file. Import into a new, empty course if you can.
2. Write down the import result: finished, finished with issues (open **Issues** and copy
   the text), or failed.
3. Where did the material arrive? Check **Modules**, **Pages**, **Files** and
   **Assignments**. For each, write what exists: a module item (what type), a page, a file
   under `web_resources/<folder>/index.html`.
4. Open it as a student, and repeat the checks of steps A4 to A5: pictures, headings,
   table, equation.
5. If it arrived as a file: do its pictures show (A2)? If it arrived as a page: did Canvas
   clean it (compare the table, the picture and the equation with the source)?
6. If you can, import the same file into **Blackboard**, **Moodle** or **D2L** and
   write what each did. A free Moodle sandbox (sandbox.moodledemo.net) is enough.

## C. The maths font stack

The stack is written on each `<math>` as `Latin Modern Math, STIX Two Math, Cambria Math,
Noto Sans Math, Libertinus Math, TeX Gyre Pagella Math, XITS Math, Asana Math, DejaVu Math
TeX Gyre, math`. Canvas has no way to add a font file to a page.

1. In the saved Canvas Page, open the browser's developer tools on an equation. In the
   computed styles, is `font-family` still the whole list (Canvas's editor can shorten it)?
   Which family does the "Rendered Fonts" panel say was used?
2. Repeat on a Windows and a Mac computer if you can: Windows should use Cambria Math, a Mac
   STIX Two Math or Latin Modern Math if installed.
3. Compare with the same equation in `index.html` from your computer.

## What to send back

One table, one row per step, with **worked / partly / failed / not tested** and a sentence.
These rows decide what changes in the repository:

| If this is the result | The change |
|---|---|
| A2: pictures show in an `.html` file in Files | The README and `/help/posting` can say so, and the "paste a page" route becomes optional |
| A2: pictures do not show | Keep the Page route as the main one; say so in the help page |
| A5: Canvas draws the equation with MathJax | The font stack is moot in Canvas; keep it for the standalone file |
| A5 (b) with a poor look, or C1: the list is shortened | Reorder or shorten the stack (`export_canvas.MATH_FONT_STACK`) |
| A6: the second save removes something | Add it to "What Canvas still removes" in `docs/output.md` and the README |
| B3: it arrived as a file only | Say so on the help page; the instructor links it from a module. Consider adding Canvas's own page resource type to the cartridge |
| B2: the import reports an issue about the manifest | Send the issue text; `export_cartridge.manifest_xml` is the place to change |

Then change the `TODO(Harish)` lines in `help_posting.html` and `_job_ready.html` (they say
"not yet tried") and the sentence "Not tested against a live Canvas" in `docs/output.md`.
