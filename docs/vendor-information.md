# ReadableSTEM: information sheet for an accessibility or procurement office

One page. It describes the beta as it is on 4 November 2026; it is not a contract or an audit.
Where it differs from the live pages, those are right. The repository is `harishm17/readablestem`.

## What it does

An instructor signs in, uploads a PDF of their own course material and gets back one HTML
file: headings, lists, tables, links, figure descriptions and equations as MathML that a
screen reader can read. A model proposes the LaTeX for each equation and the description of
each figure (and, where a server has the features on, rebuilt tables and long descriptions of
charts). Automatic checks compare each proposal with the PDF's own text. The instructor
decides the parts the tool is unsure about, can edit any part, and posts the file in their
course. The tool assists the instructor. It does not make a document accessible by itself, and
it does not say that a document meets a standard or a law.

A review record (a page to print, and a CSV) lists what the tool did, what the instructor
decided and when, what was not converted, and what was not checked.

## What happens to a document

| Question | Answer |
|---|---|
| Who can use it | Anyone with a Google account (or a Microsoft school or work account of a university that is set up), with lower daily limits for new accounts; a server can instead be set to an allowlist (`SIGNUP_MODE`). Each person sees only their own documents and can delete their account. Students do not sign in. |
| What may be uploaded | Course material the instructor wrote or may adapt. Not student records or graded work. No school-official or data processing agreement is offered. |
| What is stored | The PDF and file name, images cut from it, the conversion result, the HTML, and the review decisions with their times. |
| How long | Deleted 24 hours after the instructor last worked on it; 7 days if the instructor keeps it; 30 days if the instructor also lets us look at the results. Deleting by hand is immediate. Backups are kept about 6 days. |
| Where it goes | Railway hosts the application and database (United States). Images cut from the page (a picture of each scanned or handwritten page, if the instructor asks for those), with a little nearby text, go to the model provider's API (Anthropic or OpenAI, as set). Never sent: the PDF file, its file name, the email address. Providers say API data is not used for training by default; OpenAI requests are sent with `store` off; Anthropic's policy is to delete API data within 30 days (longer if flagged). Tracing, if on, sends timings and counts only. |
| Logs and usage notes | Event names, random ids, counts, timings, costs and error categories. No document text, images, file names or email addresses. |
| Accounts | Email address, name and the sign-in provider's id. One session cookie; no analytics or advertising cookies. |
| Who can see a document | Its owner, and people the owner names. Others get the same "not found" page as for a document that does not exist. We look only if the instructor chose that, for 30 days. |

## Where it falls short

- **Accuracy was measured on 26 documents** the tool had not seen (registered before the
  run; version 0.5.9, 4 November 2026; 1,715 equations). 0.79 of equations came out exactly
  right end to end. Scored by how a screen reader reads them, 0.840 against 0.836 for a
  general chatbot: no difference the test can detect. It asked about 46% of its wrong
  equations (goal 60%). Earlier test (version 0.4.0): headings F1 0.96; a model judged 97% of figure descriptions acceptable.
  Chemistry is the weakest area. The tool accepts what its checks pass without a person
  looking; the review record lists those parts.
- **Scanned and handwritten pages** convert only when the instructor ticks "Convert scanned or
  handwritten pages" (a server setting). A person decides on every converted equation and
  table, and the original page stays in the file. On nine real papers made into scans, about 2
  in 3 equations were exactly right. Handwriting: 0.57 on 24 held-out pages.
  Otherwise such pages stay pictures marked "Not converted".
- **Other limits** are on the Known limits page.
- **Files:** PDF, and a LaTeX file or project zip. Word and PowerPoint need LibreOffice on the
  server; readablestem.com has none. Limits: see Known limits.
- **Checks look at structure, not meaning:** language, title, headings, lists, tables, links
  and image alt attributes. Nobody has tested the output by listening with NVDA, JAWS or
  VoiceOver, and it has not been tried in a live course site.
- **No published conformance report.** No VPAT or accessibility conformance report for the web
  application is published yet (draft: [acr/](acr/)); no independent security test.
  The application was reviewed against a checklist (`docs/security.md`); automated tests walk
  every page by keyboard and run axe-core.
- **Support.** Built and maintained by Harish Manoharan, with no support contract. The code is
  open source (MIT); the hosted service is offered under AGPL-3.0 terms because it links
  PyMuPDF (see Terms).

## Where to read more

`docs/security.md`, `docs/web.md`, `docs/output.md`, `docs/eval.md` and the site's Privacy,
Terms and Known limits pages.
