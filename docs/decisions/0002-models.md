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

## Smoke test (2026-10-07)

One rendered equation (`v = d / t = 12 m / 4 s`), both models, structured output:

| Model | Latency | Input tokens | Output tokens | Result |
|---|---|---|---|---|
| gpt-6-luna | 3.75 s | 213 | 82 (42 reasoning) | correct LaTeX, confidence 0.99 |
| gpt-6.1-sol | 3.29 s | 213 | 41 | correct LaTeX, confidence 0.99 |

## Consequences

TODO(Harish)
