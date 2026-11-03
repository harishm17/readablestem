# ReadableSTEM web app: accessibility conformance report (DRAFT)

**Status: draft, not published, not reviewed by the owner.** This is not a statement that
ReadableSTEM conforms to WCAG 2.2 or to any other standard. It lists what has been tested and
what has not. Nothing here is a guarantee that a page or a converted file is accessible.

Format: ITI VPAT 2.5Rev, WCAG edition, success criteria of WCAG 2.2 levels A and AA.

| | |
|---|---|
| Name of product | ReadableSTEM web app (the pages a signed-in person and a visitor use) |
| Version | The repository at the commit that carries this file |
| Report date | 2026-11-03 (draft) |
| Product description | A hosted tool that converts a STEM course PDF into a web page with equations as MathML, headings and described figures, and a review screen where a person decides what the tool was unsure about. |
| Contact | To be set by the owner when the report is published |
| Evaluation methods | Section 2 |

## 1. Scope

In this report: the pages of the web app: sign-in, My documents, the upload form, the job page,
the review screen (split and list views), the Symbols screen, the record, account and delete
pages, the accept-many pages, and the public pages (home, privacy, terms, known limits, status,
the guides, the page for accessibility offices). The Walk test lists them
(`PAGES` in `tests/test_web_keyboard_browser.py`).

Not in this report: the converted file a student receives (HTML, and the Word, EPUB and Canvas
exports made from it). It is a different subject, described in [../output.md](../output.md)
and checked by its own suites, and it needs its own report. The demo page `/demo` is such a
file.

## 2. Evaluation methods

| Method | What it covers | What it cannot show |
|---|---|---|
| **Keyboard-only walk** (`tests/test_web_keyboard_browser.py`, Chromium through Playwright). A script presses Tab and Shift+Tab through every page in the scope list, at 1280, 390 and 320 CSS px, with every disclosure open where noted, and runs the main flows with the keyboard alone: sign in, upload, job page and downloads, review decisions and Undo, an edit and a refused edit, the list view, the page tools, the Symbols screen, deleting a document and an account. | Reachable controls, no trap, skip link, focus visible and not covered, accessible names present, order of the page's parts, focus after each action, no change of page on focus. | Whether the order reads well, whether a name is a good one, anything a screen reader says. Chromium only. |
| **axe-core 4.14.0** with the WCAG 2.2 A/AA and best-practice rule tags, in the browser suites listed in section 5, on every state of the screens named there. | The part of each criterion a rule can check. | Most of what a criterion asks of meaning (axe finds a fraction of problems). |
| **Reflow and text spacing** at 320 CSS px and with the WCAG 1.4.12 override, 400% zoom viewport sizes, landscape and portrait. | 1.4.4, 1.4.10, 1.4.12, 1.3.4. | Browsers other than Chromium. |
| **Stylesheet checks** (`tests/test_web_focus_style.py`, `tests/test_web_review_split.py`): colour pairs and the focus ring. | 1.4.3, 1.4.11, 2.4.7 colours. | Colours the stylesheet does not declare (images). |
| **Code inspection** of the templates (`src/readablestem/web/templates/`), `app.css` and `app.js`. | Structure that is true by construction. | Behaviour. |
| **Screen reader session** (VoiceOver in Safari, [../screen-reader-test.md](../screen-reader-test.md)). | | **Not done yet.** Rows that depend on it say so. |

## 3. Terms

- **Supports**: shown by the evidence named in the row.
- **Partially Supports**: some of the content does not meet the criterion; the row says what.
- **Not Applicable**: the web app has no content the criterion is about.
- **Not yet evaluated**: the answer depends on a screen reader's output, or has not been looked
  at. The row says which, and what is already known. Not a result.

## 4. Summary (draft)

Of 56 rows (Level A 32, Level AA 24), the counts are in section 10. Every **Not yet
evaluated** row that needs the screen reader session is marked "VoiceOver" with the result row
of the sheet that feeds it (R1 to R17).

## 5. WCAG 2.2 Level A

| Criterion | Level | Evidence and remarks |
|---|---|---|
| 1.1.1 Non-text Content | Not yet evaluated | VoiceOver (R6, R9 apply to the converted file; for the app, the page images and crops). Tested: every image has a text alternative or is hidden from assistive technology (axe `image-alt`, `role-img-alt`, in the suites below). Whether the alternatives are good, such as "Page 1 of the PDF." on the review screen's page image, needs a person. |
| 1.2.1 Audio-only and Video-only (Prerecorded) | Not Applicable | The app has no audio or video (no `<audio>`, `<video>` or `<track>` in the templates or `app.js`). |
| 1.2.2 Captions (Prerecorded) | Not Applicable | As 1.2.1. |
| 1.2.3 Audio Description or Media Alternative (Prerecorded) | Not Applicable | As 1.2.1. |
| 1.3.1 Info and Relationships | Not yet evaluated | VoiceOver (R2, R14). Tested: axe's structure and form rules pass on every state (headings, lists, landmarks, table headers, labels, `aria-*` use). Written in the templates: one `h1` per page, labelled regions, `<nav>` with names, `<fieldset>`/`role=group` for choices. |
| 1.3.2 Meaningful Sequence | Not yet evaluated | VoiceOver (R1). Known: on the review screen the page source puts the decision first, then the optional checks, then the page image, so the decision is first for a keyboard and a screen reader (and for a phone, where it stacks); on a wide window the page image is drawn beside it. The tab walk records that order. Whether it reads well needs a person. |
| 1.3.3 Sensory Characteristics | Not yet evaluated | Not reviewed. Controls are named by their words (the walk checks each has a name), but some text points by position ("listed below", "above") and no one has checked that none relies on position alone. |
| 1.4.1 Use of Color | Supports | Status is written in words and the colour only repeats it (`test_status_is_written_in_words_not_only_colour`, `tests/test_web_review_browser.py`); marks on the page image carry a number and text; forced-colours runs keep borders (`tests/test_web_review_split_browser.py`, `tests/test_web_inventory_browser.py`). |
| 1.4.2 Audio Control | Not Applicable | No audio. |
| 2.1.1 Keyboard | Supports | `tests/test_web_keyboard_browser.py`: every control on every page in scope is reached by Tab, and the main flows are done with keys only (Enter, Space, arrow keys on a choice, Tab, Shift+Tab, typing). The one step a script cannot do from the keyboard is choosing a file in the operating system's dialog; the walk opens the chooser with Space or Enter on the file field. Nothing needs a pointer: the page-tools region is chosen with number fields, and marks on the page image are links. Chromium only. |
| 2.1.2 No Keyboard Trap | Supports | The walk presses Tab until focus leaves the page or comes round, and Shift+Tab back through the same stops, on every page in scope; a deliberately trapped test page is reported as a failure (`test_the_walker_reports_a_missing_focus_ring_a_trap_and_a_missing_skip_link`). No dialog or widget traps focus (there are no modal dialogs). |
| 2.1.4 Character Key Shortcuts | Supports | The review screen's single-key shortcuts (N, P, A, E) are off until the person turns them on, can be turned off, and never act while typing (`test_keyboard_shortcuts_are_opt_in_and_never_fire_while_typing`). |
| 2.2.1 Timing Adjustable | Supports | The only time limit is the sign-in session, 7 days (`SESSION_MAX_AGE_S`, `web/app.py`): longer than 20 hours. Text typed when a session ends is kept (`test_typed_text_survives_the_session_ending`, `tests/test_web_review_ux_browser.py`). |
| 2.2.2 Pause, Stop, Hide | Partially Supports | The job page's status box (every 3 s) and My documents (every 5 s, only while a document is converting) update by themselves with no control to pause them. The updates do not move focus (`test_the_documents_list_and_the_job_status_refresh_without_taking_focus_away`; `test_the_steps_update_without_touching_the_live_region`, `tests/test_web_job_page_browser.py`), and each page says the list can be refreshed by hand. Whether the progress counts as essential has not been decided. Nothing blinks or moves otherwise. |
| 2.3.1 Three Flashes or Below Threshold | Supports | No animation or flashing content: `app.css` declares no `@keyframes`, and under `prefers-reduced-motion` all transitions are off (`test_forced_colors_and_reduced_motion_keep_the_controls_visible_and_still`, `tests/test_web_scale_browser.py`). |
| 2.4.1 Bypass Blocks | Supports | Every app page starts with a skip link that is the first Tab stop, is visible when focused, and puts focus in `main` (`test_every_page_can_be_walked_with_the_tab_key`, skip-link check, every page in scope); the review screen adds "Skip to the decision". Header, main and footer are landmarks. VoiceOver (R3) will confirm what it says. |
| 2.4.2 Page Titled | Supports | Titles are unique per page (`test_page_titles_are_unique`, `tests/test_web_pages.py`) and say where the person is; the job page title changes when conversion finishes (`test_finish_notice_opt_in_title_and_announcement`). VoiceOver (R17) will confirm what is heard first. |
| 2.4.3 Focus Order | Supports | The walk checks the page's parts are visited header, main, footer and that Shift+Tab reverses the order, on every page in scope. After a decision, focus goes to the next decision's heading (`test_the_whole_review_is_decided_and_undone_by_keyboard_with_focus_on_the_next_decision`, `test_focus_moves_to_the_next_decisions_heading_after_each_decision`). On the review screen the order is decision, optional checks, page image, by design (see 1.3.2). |
| 2.4.4 Link Purpose (In Context) | Not yet evaluated | VoiceOver (R5). The walk finds every link has a name; row actions carry their target in visually hidden text ("Delete Vectors"). Whether each reads well out of context is a person's judgement. |
| 2.5.1 Pointer Gestures | Supports | No path-based or multi-point gesture: `app.js` and the templates use no touch, pointer-move or drag handlers; everything is a click, a key or a form. |
| 2.5.2 Pointer Cancellation | Supports | Actions run on the native `click`/submit event (up event), never on `mousedown`/`pointerdown` (no such handler in `app.js`). |
| 2.5.3 Label in Name | Supports | By construction in the templates: a button's visible words come first and extra context is added in a visually hidden span after them ("Accept" + " equation 1"). Not covered by a dedicated test. |
| 2.5.4 Motion Actuation | Not Applicable | Nothing is operated by moving or shaking the device. |
| 3.1.1 Language of Page | Supports | `<html lang="en">` in `base.html`. (VoiceOver R13 for the converted file.) |
| 3.2.1 On Focus | Supports | The walk fails if moving focus changes the page address, on every page in scope (`test_every_page_can_be_walked_with_the_tab_key`). |
| 3.2.2 On Input | Supports | No `change` handler submits a form or navigates: choices (the kind filter, the language, a symbol's replacement) take effect only on their own button; `app.js` change and input handlers only mark a form edited, check a file, or save a draft, and the live check of an edit posts to a status line without moving focus or leaving the page. |
| 3.2.6 Consistent Help | Supports | One footer on every page (`base.html`) with the feedback link in the same place, when a feedback address is configured. |
| 3.3.1 Error Identification | Supports | After a failed form the error summary takes focus, names each problem and links to its field (`test_the_error_summary_takes_focus_after_every_failed_form`; the keyboard walk repeats it for sign-in, upload and a refused edit); a file the browser can see is wrong is said at once in a live region. VoiceOver (R15) will confirm what is heard. |
| 3.3.2 Labels or Instructions | Supports | The walk finds a name on every stop (`test_every_page_can_be_walked_with_the_tab_key`); axe's `label` and `form-field-multiple-labels` rules pass on every state; hints are tied to fields with `aria-describedby`. |
| 3.3.7 Redundant Entry | Not yet evaluated | Not reviewed. |
| 4.1.1 Parsing | Not Applicable | Removed from WCAG 2.2. |
| 4.1.2 Name, Role, Value | Not yet evaluated | VoiceOver (R14). Tested: the walk finds a name on every Tab stop, and axe's name, role and value rules pass on every state. Native controls are used throughout; disclosures are `<details>`. |

## 6. WCAG 2.2 Level AA

| Criterion | Level | Evidence and remarks |
|---|---|---|
| 1.2.4 Captions (Live) | Not Applicable | No live media. |
| 1.2.5 Audio Description (Prerecorded) | Not Applicable | No media. |
| 1.3.4 Orientation | Supports | No orientation lock; the review screen is tested portrait and landscape (844 x 390 and 390 x 844, `tests/test_web_review_split_browser.py`). |
| 1.3.5 Identify Input Purpose | Supports | The only fields that collect the person's own details, the sign-in email fields, carry `autocomplete="email"` (`_signin.html`, `dev_login.html`). Other fields (titles, notes, course names) are not about the person and are `autocomplete="off"`. |
| 1.4.3 Contrast (Minimum) | Supports | axe `color-contrast` (a serious rule) passes on every state in the browser suites; the review screen's colour pairs are computed from `app.css` (`test_the_review_colours_have_enough_contrast`). Text over images is not checked. |
| 1.4.4 Resize Text | Supports | Checked as viewports equal to 200% and 400% zoom (640, 320 and 320 x 256 CSS px) with no loss of content (`test_pages_reflow_at_320_css_pixels`, `tests/test_web_browser.py`; `tests/test_web_review_browser.py`). |
| 1.4.5 Images of Text | Partially Supports | The interface's text is text. The review screen shows images of the PDF's pages and of its equations and figures, beside the tool's reading, so a person can compare them with the original; they cannot be restyled. |
| 1.4.10 Reflow | Supports | No sideways scrolling at 320 CSS px on public pages, My documents, the job page, the review screen and the Symbols screen, also with a long file name and the text-spacing override; the page image and wide tables scroll inside their own named, focusable regions (`test_pages_reflow_at_320_css_pixels`, `test_no_state_scrolls_sideways_at_320_css_px`, `test_every_state_reflows_at_320_css_px`). The keyboard walk repeats the page set at 320 px. |
| 1.4.11 Non-text Contrast | Supports | Marks on the page image 3:1 (`test_the_review_colours_have_enough_contrast`); the focus ring and halo 3:1 against the page and panels (`tests/test_web_focus_style.py`); forced-colours runs keep borders. Other component borders are covered by axe only. |
| 1.4.12 Text Spacing | Supports | The text-spacing override is applied in the reflow tests above (`test_pages_reflow_at_320_css_pixels`, `test_no_state_scrolls_sideways_at_320_css_px`), with no sideways scrolling; overlap and clipping are not checked by a script. |
| 1.4.13 Content on Hover or Focus | Not yet evaluated | Not reviewed: marks on the page image show their name tag on hover and focus; whether it is dismissible and hoverable as the criterion asks has not been checked. |
| 2.4.5 Multiple Ways | Supports | Site navigation and the footer's links (`base.html`), the guides index, and `sitemap.xml` (`web/routes/discovery.py`); app pages are steps in a process. |
| 2.4.6 Headings and Labels | Not yet evaluated | VoiceOver (R2, R12). Tested: axe's heading and label rules pass (no empty headings, one `h1`). Whether they describe their topic needs a person. |
| 2.4.7 Focus Visible | Supports | A 3 px ring with a halo on `:focus-visible` (`app.css`); the walk finds a ring on every Tab stop of every page in scope (including marks on the page image, the page region and disclosures). |
| 2.4.11 Focus Not Obscured (Minimum) | Supports | The walk finds, for every Tab stop, the focused control in the window and not covered by another element (there is no sticky header over the content); the review screen keeps the selected mark and the decision heading in view (`test_stacked_the_selected_mark_is_inside_the_page_region`). |
| 2.5.7 Dragging Movements | Supports | Nothing needs dragging; an area of a page is chosen with number fields (`_page_tools.html`; `app.js` has no drag handler). |
| 2.5.8 Target Size (Minimum) | Supports | Controls are 44 x 44 CSS px on the Symbols screen and the choice cards, marks on the page image at least 24 x 24 (`test_every_mark_is_a_link_of_at_least_24_css_px`, `test_undo_is_a_44_px_target_and_the_marks_are_24_px_or_more`, `test_every_control_is_a_44px_target_and_nothing_scrolls_sideways`); axe's target-size rule is in the tag set. Links inside a sentence are exempt. |
| 3.1.2 Language of Parts | Not yet evaluated | VoiceOver (R13). The interface is English; text from a document in another language is shown without a `lang` on the review screen. Not reviewed. |
| 3.2.3 Consistent Navigation | Supports | One shared header, navigation and footer (`base.html`) in the same order on every page. |
| 3.2.4 Consistent Identification | Not yet evaluated | Not reviewed. |
| 3.3.3 Error Suggestion | Supports | Error messages say what to do ("is not a PDF. Choose a PDF file."; a refused LaTeX edit names the problem and keeps what was typed; `test_an_edit_is_typed_saved_and_a_refused_one_is_explained_by_keyboard`). |
| 3.3.4 Error Prevention (Legal, Financial, Data) | Supports | Deleting a document or an account has a confirmation page with the consequences and a "No, keep it" link (`delete_confirm.html`, `account_delete_confirm.html`; the keyboard tests go through both); a review decision can be undone (`Undo`). |
| 3.3.8 Accessible Authentication (Minimum) | Supports | Sign-in is with a Google or Microsoft account, or an emailed link where that is enabled: no password to remember, no puzzle, no code to copy (`web/auth.py`, `web/email_login.py`). |
| 4.1.3 Status Messages | Not yet evaluated | VoiceOver (R16). Built: decisions and page actions put a status line next to the heading that takes focus; the job page has a live region that speaks once per step; errors use `role=alert` and take focus. Whether each is spoken has to be heard. |

## 7. Evidence

| Evidence | Where |
|---|---|
| Keyboard walk, every page and flow | `tests/test_web_keyboard_browser.py`, helpers `tests/fixtures/web/keyboard.py` |
| Focus ring colours | `tests/test_web_focus_style.py` |
| axe-core WCAG 2.2 A/AA | `tests/test_web_browser.py`, `test_web_ux_browser.py`, `test_web_review_split_browser.py`, `test_web_review_ux_browser.py`, `test_web_job_page_browser.py`, `test_web_inventory_browser.py`, `test_web_signin_browser.py`, `test_web_review_chips_browser.py`, `test_web_review_other_reading_browser.py` |
| Reflow and text spacing | `tests/test_web_browser.py`, `test_web_ux_browser.py`, `test_web_job_page_browser.py`, `test_web_review_browser.py`, `test_web_review_split_browser.py` |
| Target size, forced colours, reduced motion | `tests/test_web_scale_browser.py`, `test_web_inventory_browser.py`, `test_web_review_chips_browser.py`, `test_web_review_other_reading_browser.py`, `test_web_review_split_browser.py` |
| Contrast of the review colours | `tests/test_web_review_split.py` |

## 8. What is still needed before this can be published

1. A VoiceOver session in Safari ([../screen-reader-test.md](../screen-reader-test.md)), by the
   owner. It feeds the rows marked VoiceOver: 1.1.1, 1.3.1, 1.3.2, 2.4.4, 2.4.6, 3.1.2, 4.1.2,
   4.1.3, and confirms 2.4.1, 2.4.2, 3.3.1.
2. A look by a person at the rows marked "Not reviewed": 1.3.3, 1.4.13, 3.2.4, 3.3.7, and a
   decision on 2.2.2.
3. At publication, every **Not yet evaluated** row is given a tested level (the form allows "Not
   Evaluated" only for level AAA), a contact is set, and the report is linked from the page for
   accessibility offices.
4. Tests in browsers other than Chromium for the web app (the student file is already run in
   three).

## 9. Found and fixed while writing this

The keyboard walk found one thing in the app. After a page was left out, put back, or a line of
text was chosen to change, the redirect named a fragment (`#page-tools`) that was not
focusable; a browser skips `autofocus` when the address has a fragment, so focus stayed on the
page itself and the person was left at the top for the next Tab. The redirects now name the
tools' summary (`#page-tools-summary`) and the tool sections take focus (`tabindex="-1"`), so
focus lands where the action happened (`test_a_page_is_left_out_and_put_back_by_keyboard_and_focus_stays_at_the_page_tools`).

## 10. Counts

Level A, 32 rows: Supports 18, Partially Supports 1, Not Applicable 6, Not yet evaluated 7 (of
which need VoiceOver 5).
Level AA, 24 rows: Supports 16, Partially Supports 1, Not Applicable 2, Not yet evaluated 5 (of
which need VoiceOver 3).
