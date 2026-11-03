"""Reader B on OpenAI (vision): the Responses API, image only.

Sent with `store: false`, as every OpenAI request of this repository is (CONTRIBUTING.md).
Why not `OpenAIVisionClient`: that client is shaped for the pipeline steps (a `Step`, the
job's call cap, schema classes, fallback routing); a reader is one optional extra reading.
Prices and the worst-case estimate come from `models.pricing`, so only a priced model is
accepted. The refusal content part is the outcome "refused".
"""

from __future__ import annotations

import base64
import json
from typing import Any

from readablestem.config import Settings
from readablestem.core.budget import JobBudget
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
from readablestem.models.pricing import cost_usd, estimate_max_cost_usd, price_for

URL = "https://api.openai.com/v1/responses"


class OpenAIReader(HttpReader):
    name = "openai"

    def __init__(
        self,
        settings: Settings,
        budget: JobBudget,
        *,
        transport: Transport | None = None,
        **kw: Any,
    ) -> None:
        super().__init__(settings, budget, transport=transport, **kw)
        self._key = settings.require_openai_key()
        self.model = settings.math_reader_openai_model
        price_for(self.model)  # an unpriced model fails now, not mid-job

    def _prepare(self, request: ReadRequest) -> Prepared:
        data = base64.b64encode(request.image_png).decode("ascii")
        body = {
            "model": self.model,
            "instructions": prompt.INSTRUCTIONS,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{data}",
                            "detail": "high",
                        },
                        {"type": "input_text", "text": prompt.USER_TEXT},
                    ],
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "math_reading",
                    "strict": True,
                    "schema": prompt.ANSWER_SCHEMA,
                }
            },
            "max_output_tokens": prompt.REPLY_TOKENS,
            "store": False,
        }
        return Prepared(
            url=URL,
            headers={"authorization": f"Bearer {self._key}", "content-type": "application/json"},
            content=json.dumps(body, separators=(",", ":")).encode(),
        )

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
        if data.get("error"):
            raise Rejected("api_error", "error_object")
        usage = data.get("usage")
        cost: float | None = None
        tokens_in = tokens_out = 0
        if isinstance(usage, dict):
            tokens_in = as_count(usage.get("input_tokens"))
            tokens_out = as_count(usage.get("output_tokens"))
            details = usage.get("input_tokens_details")
            cached = as_count(details.get("cached_tokens")) if isinstance(details, dict) else 0
            cost = cost_usd(self.model, tokens_in, cached, tokens_out)
        seen = {"input_tokens": tokens_in, "output_tokens": tokens_out}
        text: str | None = None
        output = data.get("output")
        for item in output if isinstance(output, list) else []:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            parts = item.get("content")
            for part in parts if isinstance(parts, list) else []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "refusal":
                    raise Rejected("refused", "refusal", cost, **seen)
                if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    text = part["text"]
        found = prompt.candidates_from_json(text)
        if found is None:
            code = "incomplete" if data.get("status") == "incomplete" else "bad_json"
            raise Rejected("invalid_output", code, cost, **seen)
        return Parsed(found, tokens_in, tokens_out, cost)
