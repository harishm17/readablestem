# Reader B: a second reading of a handwritten expression

Code: `src/readablestem/models/math_readers/`; tests: `tests/test_math_readers.py`,
`tests/test_math_readers_launch.py`. Status: **the adapters exist and nothing calls them.** Both
are off, and no pipeline step, route or review screen reads their output yet.

A reader takes one expression crop (a PNG) and returns LaTeX candidates, best first, each with
the confidence the model reported about itself (not calibrated, and not comparable between
readers). The purpose is a reading that does not make the same mistakes as the page pass's, so
that where the two differ the tool can ask a person instead of trusting either.

## The protocol

`MathReader.read(ReadRequest(image_png)) -> ReadResult(candidates, call)`.

- `ReadRequest` accepts only PNG data up to 4 MiB (`ValueError` otherwise, before any
  reservation). Cut the crop tight and render it large.
- `ReadResult.call` is a content-free `ReaderCall`: reader name, model id, an `Outcome` code
  (`ok`, `refused`, `invalid_output`, `timeout`, `api_error`), sends, tokens, dollars, latency.
- A reader never raises for a provider error, a refusal, a timeout or unreadable output; it
  returns no candidates and the outcome says why. It raises `BudgetExceeded` when a spend
  reservation is refused (nothing is sent) and `ConfigError` or `UnknownModelError` when it is
  built without its key or with an unpriced model.
- Candidates are cleaned (math delimiters and control characters removed, at most 2,000
  characters, at most five, distinct).

## The two readers

| Reader | Setting | Key | What makes it differ from the page pass |
|---|---|---|---|
| `claude` | `MATH_READER_CLAUDE_ENABLED`, `MATH_READER_CLAUDE_MODEL` (default `claude-sonnet-5-5`), `MATH_READER_CLAUDE_EFFORT` | `ANTHROPIC_API_KEY` | Another model than the page pass's, its own instructions (`prompt.CLAUDE_INSTRUCTIONS`: structure first, each symbol settled before the whole, the pairs handwriting confuses), and different input: one tight high-resolution crop with no page picture and no document text |
| `openai` | `MATH_READER_OPENAI_ENABLED`, `MATH_READER_OPENAI_MODEL` (default `gpt-6-luna`) | `OPENAI_API_KEY` | A different model family; sent with `store: false` like every OpenAI request here |

Both are built by hand over HTTP (`transport.py`) rather than through the step clients, which
are shaped for the pipeline (a `Step`, the job's call cap, the server-side refusal fallback).
So a Claude refusal is a refusal here: `stop_reason == "refusal"` is `refused`. The prompts are
constants in `prompt.py`, not files under `models/prompts/`: the eval guard measures those, and
no step uses these yet. The probe that measures the readers decides whether either is used.

## Spend, timeouts, retries

The send loop (`engine.py`) follows `models/openai_client.py`:

- The worst case of one send is reserved before it (`models.pricing.estimate_max_cost_usd`
  for the actual image) and settled at the usage the reply states. `count_call=False`: a reader
  is an optional extra reading, so it holds dollars under every cap but does not use up the
  job's call cap.
- Each send is bounded by `MODEL_TIMEOUT_S`. Connection errors, timeouts and HTTP 408, 409,
  429 and 5xx are resent up to `MODEL_MAX_RETRIES` times, each resend reserving and settling on
  its own, with the same backoff and `retry-after` rule; a 429 pauses the provider's gate for
  the process. A refused resend returns the failure already had.
- Charge per send: the reply's usage; an HTTP error or a connection that never left, $0; a
  timeout or a connection that broke after sending, the whole reservation.
- Logs carry the reader name, status, outcome, counts and dollars. Never the crop, the
  LaTeX, an error body or a key (the Anthropic key travels in `x-api-key`, the OpenAI key in
  `authorization`, neither in a URL).

## Turning one on

Each reader sends a crop of the instructor's handwriting to a model provider. That can change
who the deployment's providers are: with `MODEL_PROVIDER=openai`, the Claude reader makes
Anthropic a new recipient, and the other way round for the OpenAI reader. Before either is
turned on in production:

1. Name the provider in the privacy notice (`web/templates/privacy.html`) as sending
   handwriting crops, and update the "Where it goes" row of `docs/vendor-information.md` and
   the "Where document data goes" table in `docs/security.md`.
2. A production start (`Settings.check_production`) is refused while a reader is on and the
   notice does not contain the provider's name ("Anthropic", "OpenAI"), and `readablestem ops
   launch-check` lists the same. This is a text match on the template as written: the
   template shows some provider names only for the selected `MODEL_PROVIDER`, so read the
   rendered page as well.
3. Check the provider's retention terms for the key used and state them as the sheet does for
   the existing providers.

The page's wording about handwriting ("not converted") stays true until a later change uses a
reader's output; this change does not alter any page.
