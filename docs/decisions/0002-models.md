# ADR 0002: Models for the vision steps

| Field | Value |
|---|---|
| Status | **Accepted** (Harish, 2026-10-07) |
| Date | 2026-10-07 |
| Decider | Harish |

## Decision

- Provider: **OpenAI**, Responses API, structured outputs (`responses.parse` with a
  Pydantic schema), Python SDK `openai>=3.26`.
- Models: **`gpt-6.1-sol`** (main) and **`gpt-6-luna`** (cheap). Each is the other's
  fallback when a call fails (timeout, API error, refusal, invalid output).
- Routing: simple inline equations start on `gpt-6-luna`; display equations, figures, and
  any cheap-model result below the auto-accept confidence or failing a check escalate to
  `gpt-6.1-sol`.
- Prices checked 2026-10-07 on developers.openai.com/api/docs/pricing (USD per 1M tokens):

  | Model | Input | Cached input | Output |
  |---|---|---|---|
  | gpt-6.1-sol | 2.00 | 0.10 | 10.00 |
  | gpt-6-luna | 0.10 | 0.01 | 0.50 |

- Data handling: OpenAI API inputs are not used for training by default. Private test
  runs on `private-inputs/` keep tracing off.

**Update (2026-10-29).** The default in code is still `MODEL_PROVIDER=openai` with the pair
above, but production runs `MODEL_PROVIDER=anthropic`: `claude-opus-5-5` (main) and
`claude-haiku-5-5` (cheap), each the other's fallback, with their own spend-limit defaults
(`PROVIDER_LIMIT_DEFAULTS` in `config.py`). The decision above is kept as written; the OpenAI
path still works as an alternative.

## Smoke test (2026-10-07)

One rendered equation (`v = d / t = 12 m / 4 s`), both models, structured output:

| Model | Latency | Input tokens | Output tokens | Result |
|---|---|---|---|---|
| gpt-6-luna | 3.75 s | 213 | 82 (42 reasoning) | correct LaTeX, confidence 0.99 |
| gpt-6.1-sol | 3.29 s | 213 | 41 | correct LaTeX, confidence 0.99 |

## Consequences

Drafted from what the repository shows; the author confirms or edits.

- **Easier.** One provider and one SDK. Structured outputs make every answer pass a schema
  before any check sees it. Because each model is the other's fallback, one model failing
  does not stop a conversion. Run costs are small: `docs/eval.md` records $0.0017 per
  equation and $0.0034 per PDF page on the sample.
- **Harder.** The product depends on one provider's availability, prices and model names;
  the price table above is dated 2026-10-07 and must be re-checked. Model answers are not
  deterministic, so two runs on the same input can differ (the eval reports give
  confidence intervals for this reason). Document images and the text around them are
  sent to that provider, which the privacy page has to say.
- **When to revisit.** A price change, a model being retired, or a drop in the eval
  gate's numbers after a model or prompt change (`readablestem eval gate`).
