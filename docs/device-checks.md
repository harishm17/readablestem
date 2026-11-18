# Device checks

Ten checks that need a real device, a real account or a real reader. Scripts and the browser on
the build machine cannot do them: no screen reader, no iPhone, no Word, no Canvas. Each check
below has an id (D1 to D10), the steps, what it gates, who can run it, and a result block that
starts as `Result: not done`.

Until a check has a recorded result, nothing in the app, the site or the docs may say that it
passed. [vendor-information.md](vendor-information.md) and the draft accessibility conformance
report ([acr/acr-draft.md](acr/acr-draft.md), not published) say so today.

## How to run a check

- Use the live site or a staging copy with sign-in set up, and your own account. Converting
  spends model budget (about $0.20 for a document of 14 pages), so use one document for all of
  the checks that need one: a PDF of about 14 pages with equations, a figure with a long
  description, a table and page markers, and no scans. Use a finished job that already exists
  when there is one.
- Write the result in a copy of this file kept outside the repository, with no personal data.
  The maintainer then records a dated summary in the Result block here (date, device and
  versions, pass or fail per step, findings).
- A finding is what you saw or heard, with the page and what you did. Copy the exact words a
  screen reader said (VoiceOver: VO+Shift+C copies the last thing it said). An equation spoken
  wrongly is a conversion error for that document, not a screen reader problem: say which
  equation and what the PDF has.
- Anything wrong becomes a new finding in the repository's findings list. Do not change a
  public claim because of a result; a person decides that.

## What each check gates

| Check | Gates |
|---|---|
| D1, D2 | Any statement that sign-in, upload, review and download work on Safari, on a Mac or an iPhone |
| D3, D4 | Any statement about VoiceOver, and the VoiceOver rows of the conformance report |
| D5 | Any statement about NVDA, and the rows of the conformance report that name a screen reader on Windows |
| D6 | Any statement about JAWS |
| D7 | Any statement about TalkBack or Android |
| D8 | Any statement that the EPUB, Word and Canvas downloads open and read well in the apps they are for |
| D9 | Any statement that Listen speaks a Spanish document in a Spanish voice |
| D10 | Any statement about high contrast, forced colors and zoom |

## Rows for the screen reader checks (D3 to D7)

Each step in D3 to D7 feeds one or more of these rows. A row that passed on every page tested
may be raised to "Supports" in the conformance report; one that failed or surprised the tester
stays "Partially Supports". The criterion is the WCAG 2.2 success criterion the row feeds.

| Row | What it covers | Criterion |
|---|---|---|
| R1 | Text reads in a sensible order, nothing missing | 1.3.2 meaningful sequence |
| R2 | Headings and landmarks are found and make sense | 1.3.1 info and relationships, 2.4.6 headings and labels |
| R3 | The skip link works | 2.4.1 bypass blocks |
| R4 | The contents list works | 2.4.1, 2.4.5 multiple ways |
| R5 | Links make sense from their text | 2.4.4 link purpose |
| R6 | Equations are announced and spoken as math | 1.1.1 non-text content, 1.3.1, 4.1.2 name, role, value |
| R7 | You can step into a fraction or a matrix | 1.3.1, 4.1.2 |
| R8 | Keyboard only: no trap, order matches reading, focus visible | 2.1.1, 2.1.2, 2.4.3, 2.4.7 |
| R9 | Figure descriptions and long descriptions | 1.1.1 |
| R10 | Table headers are spoken with cells | 1.3.1 |
| R11 | Page markers and footnotes | 1.3.1, 2.4.1 |
| R12 | The "About this version" note is found | 2.4.6 |
| R13 | Language of the page and of parts | 3.1.1, 3.1.2 |
| R14 | Controls have names, roles and states | 4.1.2 |
| R15 | Errors are found and described | 3.3.1, 3.3.3 |
| R16 | Status messages are spoken without moving focus | 4.1.3 |
| R17 | Page titles say where you are | 2.4.2 |

## D1. Safari on a Mac: sign in, upload, review, download

Who can run it: anyone with a Mac and Safari, and a Google account, a Microsoft account and an
email address that can receive mail. About 15 minutes.

Gates: D1 in the table above.

Steps:

1. Note the macOS and Safari versions.
2. Sign in with Google (“Sign in with Google”). Land on the documents list with your name.
3. Sign out. Start a Google sign-in and press Cancel on Google's page. You should see “Sign-in
   did not finish”, not a server error.
4. Sign in with Microsoft (“Sign in with your university account (Microsoft)”; a personal
   Outlook account is enough, if the server offers it). Do you land on the documents list with
   your name?
5. Sign out and ask for an email sign-in link (“Email me a sign-in link”, if the server offers
   it). Write how long it took to arrive, whether it went to spam, whether the link works once
   and is refused a second time.
6. Upload the PDF. Does the file picker take it? Stay on the job page while it converts.
7. Open the review screen. Make one decision with the mouse and one with the keyboard.
8. Download the HTML file. Open it in Safari: does it look like the preview?

Result: not done
Date:
Device and versions:
Findings:

## D2. Safari on an iPhone

Who can run it: anyone with an iPhone and a document already converted. About 15 minutes.

Gates: D2 in the table above.

Steps:

1. Note the iOS and Safari versions.
2. Upload the PDF from the Files app. Does the file picker take it, and does the page scroll
   while the job runs?
3. Open the review screen. When you type in a field, does the on-screen keyboard cover the field
   you are typing in? Does Safari's bottom toolbar hide a button?
4. Settings, Accessibility, Display and Text Size, Larger Text, at the largest size: do the job
   page, the review screen and the student page still read without sideways scrolling?
5. Open the student link signed out. Is the title near the top of the screen, or pushed down by
   the contents list?
6. Open the downloaded HTML file from the Files app: does it open and read well?

Result: not done
Date:
Device and versions:
Findings:

## D3. VoiceOver on the student page, on a Mac and on an iPhone

Who can run it: a person who uses VoiceOver, or the owner with the keys below. About 20 minutes
on each device.

Gates: D3 in the table above. Feeds R1 to R13 and R17.

Set up on the Mac: note the macOS, Safari and VoiceOver versions. In Safari, Settings,
Advanced, turn on “Press Tab to highlight each item on a webpage”, otherwise Tab skips links and
buttons. Turn VoiceOver on with Cmd+F5. “VO” is Control+Option. To keep what VoiceOver says, use
VoiceOver Utility, Visuals, Show caption panel. On the iPhone: Settings, Accessibility,
VoiceOver.

| Do this on the Mac | Keys |
|---|---|
| Next or previous item | VO+Right, VO+Left |
| Read from here | VO+A (Control stops speech) |
| Rotor (Headings, Landmarks, Links, Tables) | VO+U, Left or Right to pick a list, Up or Down to pick an item, Return to go there, Esc to close |
| Next heading | VO+Cmd+H |
| Go into an item, come out | VO+Shift+Down, VO+Shift+Up |
| Press a button or follow a link | VO+Space |

Use a document converted by the current version (the skip link, contents list and page markers
exist only in files made since the navigation update), and also the committed demo page
(`/demo`) for steps that name it.

Steps (what a pass sounds like is in the second column; the wording is VoiceOver's own and can
differ by version, but a heading must be called a heading, and a fraction must have a top and
a bottom):

| # | Do | A pass | Row |
|---|---|---|---|
| 1 | Load the page, read from the top for 20 seconds, stop. | The title first. Text reads in order, nothing skipped. | R1, R17 |
| 2 | Headings rotor. | One level 1 heading, then level 2 headings in a sensible order. They are the document's sections. | R2 |
| 3 | Landmarks rotor. | Main, and the region “About this version”. | R2 |
| 4 | Reload, press Tab once, press Return. | “Skip to main content, link”. VoiceOver lands on the title. | R3 |
| 5 | Landmarks rotor, the “Contents” navigation. | One collapsed “Contents” control on a narrow window, the list on a wide one. Each link goes to its heading. | R4 |
| 6 | Move through a paragraph with a link. | A link says “link” and reads its own words. | R5 |
| 7 | Move through two equations: an inline one and a display one. | Announced as math, read as a sentence. Write down what you hear. Not skipped, not spelled letter by letter. | R6 |
| 8 | On a fraction: go in, move right a few times, come out. | You can step inside: the top part, then the bottom, then back out. | R6, R7 |
| 9 | An equation with a number. | Announced with its number, not a lone digit. | R6 |
| 10 | A multi-line calculation: go in. | You reach each row, in order. | R6, R7 |
| 11 | A subscript in text. | Heard as a subscript, not run together. | R6 |
| 12 | Tab through the page. | Visits only links and wide equations or tables. No trap. Shift+Tab goes back. Each stop is visible and named. | R8 |
| 13 | A figure. | “Image” and its description, then the caption. Not a file name. | R9 |
| 14 | A figure with a long description: open “Description of Figure N”. | Collapsed, then expanded, then the text. | R9 |
| 15 | A table: tables rotor, go in, arrow across and down. | “Table, N rows, M columns”, the caption first. A cell says its column header and its row header. | R10 |
| 16 | Page markers: step through a page. | “Page 12” reads as text between blocks, never in the middle of a sentence. Are they in the way? | R11 |
| 17 | A footnote mark, if there is one. | The mark is a link, following it reaches the note, “Back to the text” returns. | R11 |
| 18 | The end of the page. | “About this version” and the short note for students. | R12 |
| 19 | A paragraph, and a passage in another language if the document has one. | An English voice for the page; another voice for the passage. | R13 |
| 20 | Listen: from the job page open “Listen as a student” and press play. | There is sound, the voice is reasonable, and the highlighted text follows the speech. | none |
| 21 | A table whose column header is only math (a unit or a symbol), if the document has one: arrow across the cells of that column. D5, D6 and D7 run this one too. | Not tested with any reader. The header is named by its reading (`aria-label` on the `<th>`) and holds its MathML: write down what is announced when moving across the data cells (the reading once, twice or not at all), and whether the math inside the header can still be entered. | R10 |

Result: not done
Date:
Device and versions (Mac and iPhone):
Findings:

## D4. VoiceOver on the review screen

Who can run it: a person who uses VoiceOver. About 20 minutes. Use a job with decisions open of
more than one kind (an equation, a figure, a table, a heading).

Gates: D4 in the table above. Feeds R2, R3, R8, R14, R15, R16 and R17.

Steps:

1. Open the job and then its review screen. Press Tab: the first stop is “Skip to main
   content”, the second “Skip to the decision”. Return lands on the decision's heading, which is
   read (“Decision 1 of N ...”). Does the page title start the same way?
2. For one item of each kind (equation, figure, table, heading), decide it with the keyboard
   alone: reach its controls, press Accept, Keep as image, or the choice that kind has, and
   write what each control was called and whether its role and state were spoken.
3. After a decision, listen for the status line (for example “Equation 1: Accepted. 2 decisions
   left.”). Is it spoken without focus jumping away from where you were? Where did focus go?
4. Press Undo (the first stop after Shift+Tab from the next decision's heading). Is “Undone ...”
   spoken?
5. Open “Edit the LaTeX” (or the alt text field), leave it empty or enter something invalid,
   press Save edit. Does the error summary take focus and is it spoken? Does the link in it go
   to the field? Does the field keep what you typed?
6. While something is working (a read again, a save), is the busy state spoken once, or not at
   all, or over and over?
7. Tab to the page image region and to a mark: “Page 1 image with N marks”, then marks named
   with their number, kind and state. Does a mark have words, not only a color?
8. Single-key shortcuts (N, P, A, E) are on until turned off with “Use keyboard shortcuts”.
   Do they collide with VoiceOver's quick keys? Write what you find, with them on and off.

Result: not done
Date:
Device and versions:
Findings:

## D5. NVDA on Windows, with Firefox and with Chrome

Who can run it: a person who uses NVDA, with a Windows machine. About 40 minutes (20 in each
browser).

Gates: D5 in the table above.

Steps: do D3's steps 1 to 19 on the student page, and D4's steps 1 to 8 on the review screen,
with NVDA in Firefox and again in Chrome. Use NVDA's own keys: the Elements list (NVDA+F7) for
headings, landmarks, links and tables, H for the next heading, T for the next table, and
Insert+Space to switch between browse mode and focus mode. NVDA reads math with MathCAT when it
is set up in the NVDA settings: write down whether it is, and the NVDA, MathCAT and browser
versions. The test of the `MATHML_ALTTEXT` setting with NVDA and MathCAT is a different
test with its own decision rule: [mathml-alttext.md](mathml-alttext.md). Run both in one session
if you can.

Result: not done
Date:
Device and versions (Windows, NVDA, browsers):
Findings:

## D6. JAWS with Chrome on Windows

Who can run it: a person who uses JAWS, with a Windows machine. About 20 minutes.

Gates: D6 in the table above.

Steps: do D3's steps 1 to 19 on the student page, with JAWS in Chrome. Use JAWS's lists of
headings (Insert+F6), links (Insert+F7) and landmarks (the semicolon key). Write down how JAWS
reads an equation, and the JAWS and Chrome versions.

Result: not done
Date:
Device and versions (Windows, JAWS, Chrome):
Findings:

## D7. TalkBack on Android, with Chrome

Who can run it: a person with an Android phone who uses TalkBack. About 20 minutes.

Gates: D7 in the table above.

Steps: with TalkBack on, open the student page in Chrome, then do D3's steps 1 to 19 as far as
TalkBack can (headings, links and controls through the reading controls; swipe right and left
for the next and previous item). Then open “Listen as a student” and press play: is there
sound, and is the voice one the phone offers? Write the Android, TalkBack and Chrome versions,
and which of the steps could not be done on this device.

Result: not done
Date:
Device and versions:
Findings:

## D8. The downloads in the apps they are for

Who can run it: anyone with the apps. About 20 minutes. Download each from a finished job.

Gates: D8 in the table above.

Steps:

1. “Download as EPUB”: open it in Apple Books on a Mac or an iPhone. Do equations show? Does the
   table of contents work? Does VoiceOver read the math there?
2. “Download as Word (.docx)”: open it in Word, then Review, Check Accessibility. Write every
   error and warning it lists, and whether the equations are editable equations.
3. If you have any Canvas access (a free Canvas account works): import “For Canvas (zip with
   images)” and, separately, “Download as a Common Cartridge (.imscc)”. Does each import? How
   does the page look, do pictures show, are the equations readable? This is the check that the
   “not yet tried in a live course” sentences on the posting page and the job page wait for.

Result: not done
Date:
Device and versions (Books, Word, Canvas):
Findings:

## D9. Listen in Spanish

Who can run it: anyone who can hear the difference, best a Spanish speaker. About 5 minutes.

Gates: D9 in the table above.

Steps: convert or open a document written in Spanish, open “Listen as a student” and press
play. Does it switch to a Spanish voice, or read Spanish with an English voice? Does a passage
in another language inside an English document change voice? Do the highlighted words follow the
speech? Write the browser, the device and the voice names.

Result: not done
Date:
Device and versions:
Findings:

## D10. Windows high contrast and 400% zoom

Who can run it: anyone with a Windows machine. About 15 minutes.

Gates: D10 in the table above.

Steps: on Windows, turn on a contrast theme (Settings, Accessibility, Contrast themes), and in
the browser set the zoom to 400%. On each of the job page, the review screen and a student share
page, check that:

1. Every control has a visible edge and the focus ring is visible.
2. Text, buttons, status labels, the marks on the page image and the equations are all readable,
   and nothing disappears or is shown in a color you cannot tell from the background.
3. At 400% there is no sideways scrolling for text (a wide equation or table may scroll inside
   its own box), nothing is cut off, and every control is reachable.
4. A status shown by color (decided, waiting, optional) also has words.

Result: not done
Date:
Device and versions (Windows, browser):
Findings:

## Where this came from

This list replaces the earlier VoiceOver protocol (`screen-reader-test.md`, removed) and a
30-minute device check kept outside the repository. Their steps are in D1 to D4, D8 and D9, and
the criterion rows R1 to R17 are the earlier protocol's results table.
