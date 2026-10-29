# ReadableSTEM: information sheet for an accessibility or procurement office

One page. It says what the tool does, what happens to a document, and where it falls short.
It is written from how the beta behaves today, not from a contract or an audit by anyone
outside the project. The command is `readablestem` (the old `ReadableSTEM` still works); the GitHub repository is `harishm17/readablestem`.

## What it does

An instructor signs in, uploads a PDF of their own course material (notes, slides, problem
sets) and gets back an HTML file with the headings, lists, tables, links, figure
descriptions and equations written so that a screen reader can read them. Equations are
written as MathML. A model proposes the LaTeX for each equation and the description of each
figure; automatic checks compare the proposal with the PDF's own text; the instructor
decides the parts the tool is unsure about. The instructor then posts the HTML file in
their course. The tool assists the instructor. It does not make a document accessible by
itself and it does not say that a document meets a standard or a law.

A review record (a page to print, and a CSV) lists, for each document, what the tool did,
what the instructor decided and when, which parts were not converted, which automatic
checks ran, and what was not checked.

## What happens to a document

| Question | Answer |
|---|---|
| Who can use it | Anyone with a Google account (or a Microsoft school or work account of a university that is set up), with lower daily limits for new accounts; a server can instead be set to people on an allowlist (`SIGNUP_MODE`). Each person sees only their own documents, and can delete their account. |
| What may be uploaded | Course material the instructor wrote or may adapt. Not student records or graded work. |
| What is stored | The PDF and file name, images cut from it (equations, figures), the conversion result, the HTML, and the review decisions with their times. |
| How long | Deleted automatically 24 hours after the instructor last worked on it; 7 days if the instructor keeps it; 30 days if the instructor also lets the developer look at results. Deleting by hand removes it from the database at once. Daily backups are kept about 6 days, so a deleted document can stay in one for up to a week. |
| Where it goes | Railway hosts the application and its database. Images cut from the page, with a short piece of nearby text, are sent to the model provider's API (Anthropic or OpenAI, as configured) to propose LaTeX and descriptions. The whole PDF, the file name and the email address are not sent. The provider states that API data is not used to train its models by default. Anthropic's published policy is to delete API inputs and outputs within 30 days (longer for material its safety systems flag), so a deleted document can remain there that long. Optional tracing sends timings and counts only. |
| Logs and usage notes | Event names, random ids, counts, timings, costs and error categories. No document text, images, file names or email addresses. |
| Accounts | Email address, name and the sign-in provider's id for people on the list. One session cookie; no analytics or advertising cookies. |
| Who can see a document | Its owner. Other users get the same "not found" page as for a document that does not exist. The developer can look at results only when the instructor chose that, for 30 days. |

## Where it falls short

- **Accuracy is measured on a small sample.** On three sections of one high-school physics
  textbook, 23 of 30 equations came out exactly right. PDFs typeset with LaTeX or exported
  from Word may do worse. The instructor is asked about the parts the tool is unsure about;
  the rest is accepted by the tool without a person looking.
- **Not converted:** handwriting and scanned pages (kept as pictures, marked "Not
  converted"), chemistry notation, and any equation or figure the tool could not read
  (kept as a picture of the original, with a note). Two-column pages and slides convert
  less well than single-column notes.
- **Checks look at structure, not meaning:** language, title, order of headings, lists,
  tables, links and the alt attribute of every image. Nobody has tested the output with
  NVDA, JAWS or VoiceOver, and it has not been tried in a live course site.
- **Size:** up to 30 pages and 20 MB per upload (see the application's "Known limits").
- **No conformance report.** There is no VPAT or accessibility conformance report for the
  web application itself, and no independent security test. The developer reviewed the
  application against a written checklist and ran automated accessibility tests on its
  pages (`docs/security.md`, `docs/web.md`).
- **One developer.** The beta is run by an individual developer (an independent project), not a company or a university, with
  no support contract. The code is MIT; the hosted service is offered under AGPL-3.0 terms because it links PyMuPDF.

## Where to read more

`docs/security.md` (controls and audit findings), `docs/web.md` (how the web application
works), `docs/output.md` (what is in the HTML file), `docs/eval.md` (how accuracy was
measured). The application's own Privacy, Terms and Known limits pages are the wording
students and instructors see.
