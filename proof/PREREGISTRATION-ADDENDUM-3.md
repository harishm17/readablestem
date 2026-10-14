# Addendum 3: the model runs are blocked (no API credit); a correction to Addendum 2

Written 2026-10-13 at about 15:30 local time. `PREREGISTRATION.md`, Addendum 1 and Addendum 2
stand, except for the correction in section 2.

## 1. What blocks the run

The OpenAI account behind the key in `readablestem/.env` has no credit. A probe call at 15:26
(one tiny request, `max_output_tokens` 16) returned HTTP 429, code `credit_balance_exhausted`,
"You have no credits remaining". The run needs paid model calls (ReadableSTEM's pipeline, the
second check, the chatbot arms, the figure judge); none can be made. The integrator does not
add credit and does not change billing; that is the author's.

Consequence: **no ReadableSTEM run and no chatbot run exists for the held-out set.** No held-out
number for either system has been produced, none has been seen, and nothing about them can
be said. The registration, its frozen files and the harness stand ready; once credit is added
the run is the three commands in `docs/proof.md` ("Recording the held-out set"), with the
caps of Addendum 1 (D5), and `--resume` makes the ReadableSTEM recording safe to interrupt.

## 2. Correction to Addendum 2

Addendum 2 says the first attempt was stopped because it ran at about 19 model calls a minute
and would need about ten hours. That reading was wrong. The model calls were being refused:
the second-check call count stopped growing at 255 while the primary-call count kept rising,
which is what refused and retried calls look like, and the second attempt's "1,052 model
calls in 15 minutes" were refused calls too. The credit was probably exhausted during
attempt 1 (from about 14:55). The decision to stop attempt 1 and to add `--resume` rested on
the wrong pace estimate. The harness change is still sound (a recording that can be
interrupted is safer), and it altered no system or score, but the reason given for it was
mistaken.

## 3. Other attempts that produced nothing

- Attempt 2 of the ReadableSTEM recording (15:05 to 15:20) and the first start of the chatbot
  arms (4 workers, 15:08 to 15:19; all 56 jobs ended `crashed`, after 3 attempts each with a
  429) ran at the same time as the parsers. No run finished and no reply was received.
  Their files are kept apart, unused (`chatbot-attempt1-ratelimited`).
- The second start of the chatbot arms (2 workers, 15:21 to 15:26) ended the same way
  (`chatbot-attempt2-noquota`).
- Spend by these attempts: the part of attempt 1 that was billed before the credit ran out
  (an estimate of $1 to $3 at list price; the account balance, which I cannot read, is the
  record). Nothing was billed after.

## 4. What still runs without credit

The open parsers need no API. They are recorded for the held-out set (CPU, one section at a
time) and their outputs scored against the held-out gold with the same code as in the plan;
they are reported as parser-only numbers, without ReadableSTEM next to them, and not as the
family B comparison, which needs ReadableSTEM's run.
