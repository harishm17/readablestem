"""Reader B on Claude: the Messages API, one crop, decorrelated from the page pass.

Why it is built to differ from the page read (`pipeline/image_page.py`), which is Claude too:
another model (`Settings.math_reader_claude_model`, not the page pass's), its own
instructions (`prompt.CLAUDE_INSTRUCTIONS`), and different input: one tight crop of the
expression, rendered large by the caller, with no page picture and no document text. Two
readings that share a model, a prompt and a view of the page make the same mistakes, and
their agreement then says little; this one only agrees when the marks themselves are clear.

Data handling: the crop goes to Anthropic's API, as every Claude request of this
repository does (published policy: API data not used for training by default, deleted
within 30 days; docs/security.md). No document text is sent, so there is nothing to fence.
If `MODEL_PROVIDER` is `openai`, this reader makes Anthropic a new provider of the
deployment and the privacy notice must say so first (`ops.launch`).

The request is built by hand (not through `AnthropicVisionClient`, which is shaped for
the pipeline steps: a `Step`, the job's call cap, schema classes, the server-side refusal
fallback). A refusal is therefore a refusal here: `stop_reason == "refusal"` is "refused".
"""

from __future__ import annotations

import base64
import json
from typing import Any

from readablestem.config import Settings
from readablestem.core.budget import JobBudget
from readablestem.models.anthropic_client import WORKSPACE_HEADER
from readablestem.models.math_readers import prompt
from readablestem.models.math_readers.base import ReadRequest
from readablestem.models.math_readers.engine import (
    HttpReader,
    Parsed,
    Prepared,
    Rejected,
    as_count,
    json_object,
)
from readablestem.models.math_readers.transport import HttpReply, Transport
from readablestem.models.pricing import (
    cost_usd,
    estimate_max_cost_usd,
    output_token_limit,
    price_for,
)

URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
_FINISHED = frozenset({"end_turn", "stop_sequence"})
_CUT_OFF = frozenset({"max_tokens", "model_context_window_exceeded"})


class ClaudeReader(HttpReader):
    name = "claude"

    def __init__(
        self,
        settings: Settings,
        budget: JobBudget,
        *,
        transport: Transport | None = None,
        **kw: Any,
    ) -> None:
        super().__init__(settings, budget, transport=transport, **kw)
        self._key = settings.require_anthropic_key()
        self._workspace = settings.anthropic_workspace_id
        self._effort = settings.math_reader_claude_effort
        self.model = settings.math_reader_claude_model
        price_for(self.model)  # an unpriced model fails now, not mid-job

    def _prepare(self, request: ReadRequest) -> Prepared:
        data = base64.b64encode(request.image_png).decode("ascii")
        body = {
            "model": self.model,
            "max_tokens": output_token_limit(self.model, prompt.REPLY_TOKENS),
            "system": prompt.CLAUDE_INSTRUCTIONS,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/png", "data": data},
                        },
                        {"type": "text", "text": prompt.USER_TEXT},
                    ],
                }
            ],
            "output_config": {
                "effort": self._effort,
                "format": {"type": "json_schema", "schema": prompt.ANSWER_SCHEMA},
            },
        }
        headers = {
            "x-api-key": self._key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }
        if self._workspace:
            headers[WORKSPACE_HEADER] = self._workspace
        return Prepared(URL, headers, json.dumps(body, separators=(",", ":")).encode())

    def _worst_case_usd(self, request: ReadRequest) -> float:
        return estimate_max_cost_usd(
            self.model,
            n_images=1,
            text_chars=len(prompt.USER_TEXT),
            max_output_tokens=prompt.REPLY_TOKENS,
            images=[request.image_png],
        )

    def _parse(self, reply: HttpReply) -> Parsed:
        data = json_object(reply.body)
        blocks = data.get("content")
        if data.get("type") == "error" or not isinstance(blocks, list):
            raise Rejected("api_error", "unexpected_body")
        usage = data.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        read = as_count(usage.get("cache_read_input_tokens"))
        write = as_count(usage.get("cache_creation_input_tokens"))
        tokens_in = as_count(usage.get("input_tokens")) + read + write
        tokens_out = as_count(usage.get("output_tokens"))
        cost = cost_usd(self.model, tokens_in, read, tokens_out, write) if usage else None
        seen = {"input_tokens": tokens_in, "output_tokens": tokens_out}
        stop = data.get("stop_reason")
        if stop == "refusal":
            raise Rejected("refused", "refusal", cost, **seen)
        if stop in _CUT_OFF:
            raise Rejected("invalid_output", "cut_off", cost, **seen)
        if stop not in _FINISHED:
            raise Rejected("api_error", "unexpected_stop", cost, **seen)
        text = "".join(
            block["text"]
            for block in blocks
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ).strip()
        found = prompt.candidates_from_json(text)
        if found is None:
            raise Rejected("invalid_output", "bad_json", cost, **seen)
        return Parsed(found, tokens_in, tokens_out, cost)
