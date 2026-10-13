"""Figure descriptions: the part of the invented-content rate a model has to judge.

A description (an alt text, a text written for a figure, a long description) is
"unsupported" when the image-aware judge (`eval/judge.py`, prompt `judge_figure`, the main
model, no fallback) sets `factual_error`: it states something the image contradicts. The
judge sees the description as the candidate, the OpenStax alt text as a reference when the
gold has one, the caption, and the figure's image; never which system wrote the candidate.

Three things are done here, none of them by a model except the last:

- `load_references`: the gold figures (number, caption, OpenStax alt, image) of the
  sections a PDF holds;
- `bind`: which gold figure a description belongs to, by the rule of section 7.3 of the
  pre-registration (its text or caption holds the figure's number, "Figure 12.3", or at
  least half of the gold caption's word 4-grams). A description whose own HTML holds the
  image (a11y-stem's data URI) needs no gold figure for its image;
- `judge_sample`: a seeded sample of the descriptions that have an image, judged, with
  the rate's Wilson interval and what the judge cost (its list price, from the
  `ModelCall`s; never added to any system's cost).

The judge is a model and is not calibrated against human labels yet (`eval calibrate`
measures that). A rate from it is "agreement with a model judge reading the image",
never accuracy. A description with no image to judge against is counted, not judged.
"""

from __future__ import annotations

import hashlib
import io
import random
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from a11y_stem.config import Settings
from a11y_stem.eval import judge as judge_mod
from a11y_stem.eval import pipeline_eval as pe
from a11y_stem.eval.invented.read import Description
from a11y_stem.eval.invented.text import ngrams, norm_words, overlap
from a11y_stem.models.base import VisionClient

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_SIDE = 2048
CAPTION_SHARE_MIN = 0.5


@dataclass(frozen=True)
class FigureReference:
    """A gold figure."""

    id: str
    number: str | None
    caption: str
    alt: str
    image_url: str | None
    image: bytes | None = None


def load_references(
    gold_dir: Path,
    pages: Sequence[str] | None = None,
    *,
    image_for: Callable[[str], bytes] | None = None,
) -> list[FigureReference]:
    """The gold figures in reading order. `pages` limits and orders them by page slug;
    `image_for(url)` fetches a figure's PNG (`render.ImageCache(...).png`)."""
    records = [r for r in pe.load_gold(gold_dir) if r.get("kind") == "figure"]
    if pages is not None:
        order = {slug: i for i, slug in enumerate(pages)}
        records = sorted(
            (r for r in records if r["page_slug"] in order),
            key=lambda r: (order[r["page_slug"]], r["index_on_page"]),
        )
    references = []
    for record in records:
        images = record.get("images") or []
        first = images[0] if images else {}
        url = first.get("url")
        alt = " ".join((first.get("alt") or "").split())
        picture = None
        if image_for is not None and url:
            try:
                picture = image_for(url)
            except Exception:  # an image that cannot be fetched or read is "no image"
                picture = None
        references.append(
            FigureReference(
                id=record["id"],
                number=record.get("figure_number"),
                caption=" ".join((record.get("caption") or "").split()),
                alt=alt,
                image_url=url,
                image=picture,
            )
        )
    return references


@dataclass(frozen=True)
class Binding:
    description: int  # index into the output's descriptions
    reference: str | None  # gold figure id, None when no rule matched
    by: str | None  # "number" or "caption"
    share: float = 0.0


def bind(
    descriptions: Sequence[Description], references: Sequence[FigureReference]
) -> list[Binding]:
    """Section 7.3's rule: the description's text or caption holds the gold figure's number
    ("Figure 12.3"), or at least half of the gold caption's word 4-grams."""
    bound = []
    for index, description in enumerate(descriptions):
        haystack = f"{description.caption or ''} {description.text}"
        words = norm_words(haystack)
        text = " ".join(words)
        grams = ngrams(words, 4)
        best: Binding | None = None
        for reference in references:
            if reference.number:
                phrase = " ".join(["figure", *norm_words(reference.number)])
                if f" {phrase} " in f" {text} ":
                    best = Binding(index, reference.id, "number", 1.0)
                    break
            ref_grams = ngrams(norm_words(reference.caption), 4)
            if not ref_grams:
                continue
            share = overlap(ref_grams, frozenset(grams))
            if share >= CAPTION_SHARE_MIN and (best is None or share > best.share):
                best = Binding(index, reference.id, "caption", share)
        bound.append(best or Binding(index, None, None))
    return bound


def to_png(image: bytes) -> bytes | None:
    """PNG bytes for the judge (it takes PNG only), the long side capped at 2,048 px;
    None when the bytes are not an image Pillow can read."""
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(image)) as opened:
            picture = opened.convert("RGB") if opened.mode not in {"RGB", "L"} else opened.copy()
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    if max(picture.size) > MAX_SIDE:
        picture.thumbnail((MAX_SIDE, MAX_SIDE))
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    return buffer.getvalue()


@dataclass(frozen=True)
class JudgeItem:
    """One description with everything the judge is given."""

    unit: str  # a stable id: "<output>#<description index>"
    source: str  # the description's source (alt, figure, details, ...)
    candidate: str
    caption: str | None
    reference_alt: str
    image: bytes  # PNG
    authored: bool = False


def judge_items(
    unit_prefix: str,
    descriptions: Sequence[Description],
    bindings: Sequence[Binding] = (),
    references: Sequence[FigureReference] = (),
) -> tuple[list[JudgeItem], dict[str, int]]:
    """The descriptions that can be judged (their image from their own HTML, else from the
    gold figure they bind to) and what could not be: `{"no_image": n, "unreadable": n}`."""
    by_id = {r.id: r for r in references}
    bound = {b.description: by_id.get(b.reference or "") for b in bindings}
    items: list[JudgeItem] = []
    skipped = Counter()
    for index, description in enumerate(descriptions):
        reference = bound.get(index)
        raw = description.image or (reference.image if reference else None)
        if raw is None:
            skipped["no_image"] += 1
            continue
        png = raw if raw.startswith(PNG_SIGNATURE) else to_png(raw)
        if png is None:
            skipped["unreadable"] += 1
            continue
        items.append(
            JudgeItem(
                unit=f"{unit_prefix}#{index}",
                source=description.source,
                candidate=description.text,
                caption=description.caption or (reference.caption if reference else None),
                reference_alt=reference.alt if reference else "",
                image=png,
                authored=description.authored,
            )
        )
    return items, dict(skipped)


def sample_items(items: Sequence[JudgeItem], k: int, seed: str) -> list[JudgeItem]:
    """A seeded sample of `k` items (all of them when k is at least as large), in the
    order given."""
    if k >= len(items):
        return list(items)
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    chosen = set(random.Random(int.from_bytes(digest[:8], "big")).sample(range(len(items)), k))
    return [item for index, item in enumerate(items) if index in chosen]


@dataclass
class Judged:
    unit: str
    source: str
    factual_error: bool | None  # None: the call failed
    conveys: bool | None = None
    score: int | None = None
    confidence: float | None = None
    outcome: str = "ok"
    cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit": self.unit,
            "source": self.source,
            "factual_error": self.factual_error,
            "conveys_key_information": self.conveys,
            "score": self.score,
            "confidence": self.confidence,
            "outcome": self.outcome,
            "cost_usd": self.cost_usd,
        }


@dataclass
class FigureJudgement:
    population: int  # descriptions in the output(s)
    judgeable: int  # of them with an image
    skipped: dict[str, int]
    sampled: int
    seed: str
    judged: list[Judged] = field(default_factory=list)
    prompt_version: str = ""
    model: str = ""

    @property
    def answered(self) -> list[Judged]:
        return [j for j in self.judged if j.factual_error is not None]

    def to_dict(self, *, units: bool = False) -> dict[str, Any]:
        answered = self.answered
        errors = sum(bool(j.factual_error) for j in answered)
        data: dict[str, Any] = {
            "population": self.population,
            "judgeable": self.judgeable,
            "skipped": self.skipped,
            "sampled": self.sampled,
            "seed": self.seed,
            "answered": len(answered),
            "failed_calls": len(self.judged) - len(answered),
            "factual_error": errors,
            "rate": errors / len(answered) if answered else None,
            "wilson95": pe.wilson_interval(errors, len(answered)),
            "census": self.sampled >= self.judgeable,
            "judge_cost_usd": round(sum(j.cost_usd for j in self.judged), 6),
            "judge_prompt": self.prompt_version,
            "judge_model": self.model,
            "by_source": {
                source: {
                    "answered": sum(1 for j in answered if j.source == source),
                    "factual_error": sum(
                        1 for j in answered if j.source == source and j.factual_error
                    ),
                }
                for source in sorted({j.source for j in answered})
            },
        }
        if units:
            data["units"] = [j.to_dict() for j in self.judged]
        return data


def judge_sample(
    items: Sequence[JudgeItem],
    *,
    population: int,
    skipped: dict[str, int],
    client: VisionClient,
    settings: Settings,
    k: int,
    seed: str,
) -> FigureJudgement:
    """Judge a seeded sample of `items`. A failed call is recorded and left unjudged;
    `BudgetExceeded` from the client propagates (the run stops, nothing is half-counted)."""
    sample = sample_items(items, k, seed)
    result = FigureJudgement(
        population=population,
        judgeable=len(items),
        skipped=skipped,
        sampled=len(sample),
        seed=seed,
        prompt_version=judge_mod.judge_prompt_version(),
        model=settings.main_model,
    )
    for item in sample:
        judgement = judge_mod.judge_alt(
            item.candidate,
            item.reference_alt,
            item.caption,
            client=client,
            settings=settings,
            image=item.image,
        )
        verdict = judgement.verdict
        result.judged.append(
            Judged(
                unit=item.unit,
                source=item.source,
                factual_error=None if verdict is None else verdict.factual_error,
                conveys=None if verdict is None else verdict.conveys_key_information,
                score=None if verdict is None else verdict.score,
                confidence=None if verdict is None else verdict.confidence,
                outcome=str(judgement.call.outcome),
                cost_usd=judgement.call.cost_usd,
            )
        )
    return result
