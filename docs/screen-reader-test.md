# Screen reader test (VoiceOver, about 15 minutes)

A protocol you can run alone, on a Mac, with VoiceOver in Safari (Chrome is optional, see
the end). It checks what the automated tests cannot: what a screen reader says. The
results will feed the accessibility conformance report that is being drafted (there is
none yet) and the testing line in [vendor-information.md](vendor-information.md), which
today says nobody has tested with a screen reader. Until a session is recorded, rows that
depend on screen reader behaviour are not claimed as supported.

You test three things:

- **A.** The committed demo page, `/demo` (file: `src/readablestem/web/static/demo/index.html`).
- **B.** One converted page of your choice (a download from a finished job).
- **C.** The review screen of the web app (last section).

The skip link, contents list and page markers exist only in files made since the
navigation update. The committed demo predates it, so use a freshly converted page for
those steps (4, 5, 16 and 17) and write "n/a" for A.

## Set up (2 minutes)

1. Note the macOS, Safari and VoiceOver versions (Apple menu, About This Mac; Safari,
   About Safari).
2. Safari, Settings, Advanced: turn on "Press Tab to highlight each item on a webpage".
   Otherwise Tab skips links and buttons.
3. Open the demo (`https://readablestem.com/demo`) and, in a second tab, the file for B
   (File, Open File in Safari).
4. Turn VoiceOver on with Cmd+F5. "VO" below is Control+Option (or Caps Lock).
5. To keep what VoiceOver says: VoiceOver Utility, Visuals, Show caption panel; or press
   VO+Shift+C after any item to copy the last thing it said.

Keys used:

| Do this | Keys |
|---|---|
| Next or previous item | VO+Right, VO+Left |
| Read from here | VO+A (Control stops speech) |
| Rotor (Headings, Landmarks, Links, Tables) | VO+U, then Left or Right to pick a list, Up or Down to pick an item, Return to go there, Esc to close |
| Next heading | VO+Cmd+H |
| Next link, next table | VO+Cmd+L, VO+Cmd+T |
| Go into (interact with) an item | VO+Shift+Down |
| Come out | VO+Shift+Up |
| Press a button or follow a link | VO+Space |
| Move the keyboard focus | Tab, Shift+Tab |

## A and B: the converted file (8 minutes)

Do each step on A, then on B. The "Should hear" column is what a pass sounds like. The
wording is VoiceOver's own and can differ by version; what must be there is the structure
(a heading is called a heading, a fraction has a top and a bottom). Where it differs,
write what you heard in Notes. "Row" points to the results table below.

| # | Do | Should hear | Row |
|---|---|---|---|
| 1 | Load the page. VO+A for 20 seconds, then stop. | The title first. Text reads in order, without skipped pieces. | R1, R17 |
| 2 | Rotor, Headings. | One level 1 heading ("2.3 Position vs. Time Graphs" on the demo), then level 2 headings in a sensible order, none skipped. "Teacher Support" repeats on the demo; that is expected. | R2 |
| 3 | Rotor, Landmarks. | At least Main and the footer ("About this version"). The demo also has the "About this demo" box. | R2 |
| 4 | Reload, press Tab once. | "Skip to main content, link", visible on screen. Press Return: VoiceOver lands at the start of the main text, not back at the top. | R3 |
| 5 | Rotor, Landmarks. | A navigation named "Contents". Go into it: the links are the headings, nested, and each goes to its heading. | R4 |
| 6 | VO+Cmd+H to a heading, then VO+Right through a paragraph. | Text reads as text; a link says "link" and reads its own words ("Figure 2.12"), not "click here". | R5 |
| 7 | Find the first equation. On the demo: "d sub 0 equals 0" in the paragraph "As we said before..." (inline), then the display equation under it, a fraction (delta d over delta t). VO+Right onto each. | Announced as math, and read as a sentence; for the inline one roughly "d sub 0 equals 0". The tool's own reading says "bold d" for the bold d; VoiceOver may say just "d". Write down what you hear. | R6 |
| 8 | On the fraction, VO+Shift+Down, then VO+Right a few times, then VO+Shift+Up. | You can step inside: the top part (delta d), then the bottom (delta t), and back out. It is spoken as a fraction, not flat text. If VoiceOver says only "math" and you cannot enter, fail. | R6, R7 |
| 9 | Find an equation with a number. On the demo: the display equation labelled 2.9, near "Find the tangent line". | Announced with its number ("Equation 2.9"). The number is not a lone digit. | R6 |
| 10 | Find the multi-line calculation. On the demo: under "Solution" of the worked example, "v equals delta d over delta t ...". VO+Shift+Down into it. | You can reach each row, and the rows come in order. (The tool's reading says "row 1: ...; row 2: ..." through "250 meters per second".) | R6, R7 |
| 11 | Find a subscript in text. On the demo: "v avg" in "average velocity, v avg and the intercept". | Heard as a subscript ("sub" or "subscript"), not run together as "vavg". | R6 |
| 12 | Press Tab from the top and watch the focus ring. | Tab visits only links and wide equations or tables (scroll boxes). No trap; Shift+Tab goes back. Every stop is visible and named. | R8 |
| 13 | Find a figure (VO+Right, or the rotor). | "Image" and its description (for example "Line graph of position in km versus time in minutes: ..."), then the caption. Not a file name. | R9 |
| 14 | B only, if it has a figure with a long description ("Description of Figure ..."): open it with VO+Space. | "Description of Figure N", collapsed, then expanded, then the text. | R9 |
| 15 | B only, if it has a table: Rotor, Tables; VO+Shift+Down into it; arrow with VO+Arrows. | "Table, N rows, M columns", the caption first. Moving across a cell says its column header, down a column its row header ("Time, 10 s"). Header names are words, including headers that are maths. | R10 |
| 16 | B only, if it has page markers: step through the page. | "Page 12" markers read as text between blocks, never in the middle of a sentence. | R11 |
| 17 | B only, if it has footnotes: go to a note mark. | The mark reads as a link; following it reaches the note; "Back to the text" returns. | R11 |
| 18 | Go to the end of the page. | "About this version" and the short note for students. | R12 |
| 19 | VO+Right into any paragraph. | Read with an English voice (the file says `lang="en"`). A passage in another language, if B has one, changes voice. | R13 |

## C: the web app (5 minutes)

Use your own account. Converting spends API budget, so use a job that already exists; if
you have none, upload a one-page PDF once.

| # | Do | Should hear | Row |
|---|---|---|---|
| 20 | Open the home page signed out. Rotor, Headings, then Tab once. | A level 1 heading and a sensible heading list. The first Tab stop is "Skip to main content"; Return moves to the main content. | R2, R3 |
| 21 | Sign in with the button. | Buttons and fields are named; sign-in works with the keyboard alone. | R8, R14 |
| 22 | Upload page: choose a file that is not a PDF (a .txt renamed to .pdf, or a .txt), submit. | Focus goes to an error summary, announced as an alert; the link in it goes to the file field; the field repeats the message. | R15 |
| 23 | Optional: upload a good one-page PDF and stay on the job page. | The status box speaks changes once per step ("Converting ...", then the result), not every few seconds. Focus does not jump. | R16 |
| 24 | Open a job with decisions open, then its review screen. | Tab: the first stop is "Skip to main content", the second "Skip to the decision"; Return lands on the decision's heading ("Decision 1 of 3 ..."). The page title starts with "Decision 1 of 3". | R3, R8, R17 |
| 25 | Tab to the decision's controls and press Accept with Space. | A status line is spoken ("Equation 1: Accepted. 2 decisions left."). Focus is on the next decision's heading. | R16 |
| 26 | Shift+Tab once from that heading. | "Undo, button". Press it: "Undone. It is waiting for your decision again." | R14, R16 |
| 27 | On an item, open "Edit the LaTeX" (or the alt text field), enter something invalid or empty, Save edit. | The error summary takes focus and is spoken; the field is named and keeps what you typed. | R15 |
| 28 | Reject or "Keep as image" on one item. | A status line says what happened. | R14, R16 |
| 29 | Tab to the page image's region and to a mark. | "Page 1 image with 5 marks", then marks named like "1: Equation 5, needs your decision (1 of 3)". A mark has text, not only colour. | R14 |
| 30 | Leave the single-key shortcuts (N, P, A, E) off. | They are off by default because single keys can collide with VoiceOver's quick keys. Do not turn them on for this test. | none |

## Results (fill in)

Write pass, fail or n/a for A, B and C, and a note for anything that failed or surprised
you. "Criterion" is the WCAG 2.2 success criterion the row feeds in the conformance
report.

| Row | What it covers | Criterion | A demo | B my page | C app | Notes (what you heard) |
|---|---|---|---|---|---|---|
| R1 | Reads in a sensible order, nothing missing | 1.3.2 meaningful sequence | | | n/a | |
| R2 | Headings and landmarks are found and make sense | 1.3.1 info and relationships, 2.4.6 headings and labels | | | | |
| R3 | Skip link works | 2.4.1 bypass blocks | n/a | | | |
| R4 | Contents navigation works | 2.4.1, 2.4.5 multiple ways | n/a | | n/a | |
| R5 | Links make sense from their text | 2.4.4 link purpose, 1.3.1 | | | n/a | |
| R6 | Equations are announced and spoken as maths | 1.1.1 non-text content, 1.3.1, 4.1.2 name, role, value | | | n/a | |
| R7 | You can step into a fraction or matrix | 1.3.1, 4.1.2 | | | n/a | |
| R8 | Keyboard only: no trap, order matches reading, focus visible | 2.1.1 keyboard, 2.1.2 no keyboard trap, 2.4.3 focus order, 2.4.7 focus visible | | | | |
| R9 | Figure alt text and long descriptions | 1.1.1 | | | n/a | |
| R10 | Table headers are spoken with cells | 1.3.1 | n/a | | n/a | |
| R11 | Page markers and footnotes | 1.3.1, 2.4.1 | n/a | | n/a | |
| R12 | The "About this version" note is found | 2.4.6 | | | n/a | |
| R13 | Language of the page and of parts | 3.1.1 language of page, 3.1.2 language of parts | | | | |
| R14 | Controls have names, roles and states | 4.1.2 | n/a | n/a | | |
| R15 | Errors are found and described | 3.3.1 error identification, 3.3.3 error suggestion | n/a | n/a | | |
| R16 | Status messages are spoken without moving focus | 4.1.3 status messages | n/a | n/a | | |
| R17 | Page titles say where you are | 2.4.2 page titled | | | | |

For R17, listen to the first thing VoiceOver says on each page you loaded and mark
whether the title tells you which page it is.

Also write down, once:

- macOS, Safari and VoiceOver versions, the date, and which page B was.
- Any equation VoiceOver spoke wrongly, with what it said and what the PDF has. That is a
  conversion error, not a screen reader problem; it belongs to the review of that
  document.
- Anything you could not do with the keyboard alone.

## Recording the results

1. Copy this file to a place outside the repository and fill in the table there. Do not
   commit your results.
2. For each fail, add the exact words VoiceOver said (from the caption panel or
   VO+Shift+C) and where it happened (the demo's element ids look like `p3-e5`).
3. The maintainer then records a dated summary (versions, pass or fail per row, what was
   heard) in [mathml-alttext.md](mathml-alttext.md) or a results section, with no
   personal data. A row that failed or surprised the tester stays "Partially Supports";
   a row that passed on every page tested may be raised to "Supports".

## Optional: Chrome

Repeat steps 7, 8 and 10 on A in Chrome with VoiceOver on, and write what differs in
Notes. Chrome's MathML support is newer, so a reader that says nothing for an equation in
Chrome does not fail the row if Safari passes, but note it: students on a Mac may use
either.

NVDA with MathCAT on Windows is a separate test: [mathml-alttext.md](mathml-alttext.md).
