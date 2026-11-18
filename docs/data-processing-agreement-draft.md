# Data processing agreement: draft, not published (WS25.3)

**Status: draft for the owner. Nothing here is on the site.** The public
[security page](../src/readablestem/web/templates/security.html) says, in one section, "No agreement
is offered". That stays true until the owner decides the points below and a lawyer has read the
text. Do not link this file from a public page and do not send it to an institution as it stands.

## What the code and deploy documents already show

These can be quoted from the sources named; the security page already does.

| Fact | Source |
| --- | --- |
| Who receives page pictures: the model provider the server is set to (Anthropic by default, OpenAI where that path is on). The PDF file, its file name and the instructor's email address are not sent. | `web/recipients.py`, `web/offices.py` (`sent_material`, `data_items`) |
| Hosting and the database: Railway, United States region. Daily backups are kept about six days. | `docs/deploy-railway.md`, `web/offices.py` (`BACKUP_DAYS`) |
| Retention: a document and everything made from it are deleted by an hourly job after the stored time, or at once on "Delete now". | `web/retention.py`, the privacy notice |
| Uploads are not used by ReadableSTEM to train any model. | `/privacy`, `/security` |
| Tracing (Langfuse) and analytics receive data only where the settings turn them on. | `web/recipients.py` |
| Students never sign in. Student records are forbidden by the terms. | `/terms` |

## What needs the owner or a lawyer

Each line is a decision, not a fact the code can supply.

1. **Who signs.** The service is "built and maintained by Harish Manoharan". There is no legal
   entity in the repository. An agreement needs a named party (a person, or a company that does
   not exist yet).
2. **Whether to offer one at all.** Today the answer on the site is no. An agreement a school asks
   for is a commitment about deletion times, breach notice and audits that a one-person beta may
   not be able to keep.
3. **The sub-processor list as a promise.** `web/recipients.py` lists who receives data today.
   Promising a list, and notice before it changes, is a different thing from showing it.
4. **Model-company terms.** What Anthropic and OpenAI keep, and for how long, is their own
   statement. No zero-retention agreement is in place. Whether to seek one is a cost and
   contract decision. Until then the page may only repeat what they state.
5. **Breach notice.** A number of days and a contact. The repository has no incident
   procedure with a promised time; `docs/operations.md` is the nearest text.
6. **Liability and warranty.** The terms disclaim both. An agreement would need a figure or the
   same disclaimer, and Texas law is already named in the terms.
7. **FERPA.** A "school official" clause would make the service a party to the school's
   obligations. The terms forbid uploading student records instead. Choosing the other route is
   a legal call.
8. **Data location and transfers.** Railway's region is set in the deploy settings; the model
   providers' processing locations are theirs. No promise about location is made.
9. **A security contact and `security.txt`.** The contact address is a setting
   (`contact_email`). A `/.well-known/security.txt` file was not added: the triage line for
   WS25.3 does not name it and it needs the owner's chosen address and expiry date.

## Skeleton, for the lawyer to fill

1. Parties and roles (institution as controller, service as processor): *owner decision 1*.
2. Subject matter: converting course PDFs; the instructor's account details.
3. What is processed and what is not: the two lists on `/security` and the offices page.
4. Sub-processors and notice of change: *decisions 3 and 4*.
5. Retention and deletion: the times in `/privacy`; deletion on request.
6. Security measures: a pointer to `docs/security.md`, not a copy.
7. Breach notice: *decision 5*.
8. Audit and reports: none offered (`/security`, "What has and has not been tested").
9. Liability, law, term: *decision 6*.

When any of these is settled, change the security page's last section in the same commit and
rerun `scripts/page_dates.py`.
