"""Figure descriptions: binding to gold figures, the sampled judge, its cost and interval."""

from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from readablestem.core.budget import BudgetExceeded, InMemorySpendGuard, JobBudget
from readablestem.eval.invented import figures, read
from readablestem.models.fake import FakeVisionClient
from test_eval_support import settings

NO_ERROR = {"conveys_key_information": True, "factual_error": False, "score": 4, "confidence": 0.8}
ERROR = {"conveys_key_information": True, "factual_error": True, "score": 2, "confidence": 0.7}


def image_bytes(fmt: str = "PNG", color: str = "red") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 5), color).save(buffer, format=fmt)
    return buffer.getvalue()


def reference(number, caption, alt="", image=None, ident=None):
    return figures.FigureReference(
        ident or f"openstax/physics/p/fig-{number}", number, caption, alt, None, image
    )


def description(text, caption=None, image=None, source="alt", authored=False):
    return read.Description(text, source, 1, caption, image, authored)


# ------------------------------------------------------------------ binding


def test_a_description_binds_by_figure_number_or_by_half_the_caption():
    refs = [
        reference("2.10", "The diagram shows a straight-line graph of position against time."),
        reference("2.11", "A graph of position versus time for the drive to school is shown."),
    ]
    by_number = description("A line rising.", caption="Figure 2.11 Something else entirely.")
    by_caption = description(
        "A line rising.",
        caption="The diagram shows a straight-line graph of position against time.",
    )
    neither = description(
        "A line rising.", caption="Figure 9.9 Unrelated caption words here today."
    )
    bound = figures.bind([by_number, by_caption, neither], refs)
    assert [(b.reference, b.by) for b in bound] == [
        (refs[1].id, "number"),
        (refs[0].id, "caption"),
        (None, None),
    ]


def test_figure_2_1_does_not_bind_to_figure_2_10():
    refs = [reference("2.1", "Caption one words here and more words."), reference("2.10", "Other")]
    [one, ten] = figures.bind(
        [description("x", caption="Figure 2.1 Foo."), description("x", caption="Figure 2.10 Foo.")],
        refs,
    )
    assert (one.reference, ten.reference) == (refs[0].id, refs[1].id)


# ------------------------------------------------------------------ items


def test_images_come_from_the_html_or_the_bound_gold_figure_and_become_png():
    jpeg = image_bytes("JPEG")
    refs = [
        reference(
            "1.1", "Caption words one two three four five.", alt="Gold alt", image=image_bytes()
        )
    ]
    descriptions = [
        description("From the HTML.", image=jpeg),
        description("From gold.", caption="Figure 1.1 x"),
        description("No image anywhere."),
        description("Garbage bytes.", image=b"not an image"),
    ]
    bound = figures.bind(descriptions, refs)
    items, skipped = figures.judge_items("out", descriptions, bound, refs)
    assert [i.unit for i in items] == ["out#0", "out#1"]
    assert all(i.image.startswith(figures.PNG_SIGNATURE) for i in items)
    assert items[1].reference_alt == "Gold alt" and items[0].reference_alt == ""
    assert items[1].caption == "Figure 1.1 x"
    assert skipped == {"no_image": 1, "unreadable": 1}


def test_a_large_image_is_capped_and_a_non_image_is_refused():
    big = io.BytesIO()
    Image.new("RGB", (3000, 1000), "blue").save(big, format="PNG")
    png = figures.to_png(big.getvalue())
    assert png is not None
    with Image.open(io.BytesIO(png)) as opened:
        assert max(opened.size) <= figures.MAX_SIDE
    assert figures.to_png(b"nope") is None


def test_the_sample_is_seeded_ordered_and_a_census_when_k_is_large():
    items = [
        figures.JudgeItem(f"o#{i}", "alt", f"text {i}", None, "", image_bytes()) for i in range(20)
    ]
    a = figures.sample_items(items, 5, "seed one")
    assert a == figures.sample_items(items, 5, "seed one")
    assert a != figures.sample_items(items, 5, "seed two")
    assert [i.unit for i in a] == sorted((i.unit for i in a), key=lambda u: int(u.split("#")[1]))
    assert (
        len(figures.sample_items(items, 20, "x")) == len(figures.sample_items(items, 99, "x")) == 20
    )


# ------------------------------------------------------------------ the judge


def make_items(n):
    return [
        figures.JudgeItem(
            f"o#{i}", "alt", f"A graph number {i}.", "Figure 1.1", "ref", image_bytes(color=c)
        )
        for i, c in zip(
            range(n), ["red", "green", "blue", "black", "white", "gray"] * 5, strict=False
        )
    ]


def test_judge_sample_counts_factual_errors_with_an_interval_and_the_cost():
    items = make_items(6)
    calls = iter([ERROR, NO_ERROR, NO_ERROR, ERROR, NO_ERROR, NO_ERROR])
    client = FakeVisionClient(responders={"judge": lambda request: next(calls)})
    result = figures.judge_sample(
        items,
        population=9,
        skipped={"no_image": 3},
        client=client,
        settings=settings(),
        k=6,
        seed="s",
    )
    data = result.to_dict()
    assert (data["population"], data["judgeable"], data["sampled"], data["answered"]) == (
        9,
        6,
        6,
        6,
    )
    assert data["factual_error"] == 2 and data["rate"] == pytest.approx(1 / 3)
    low, high = data["wilson95"]
    assert low < 1 / 3 < high
    assert data["census"] is True and data["skipped"] == {"no_image": 3}
    assert data["judge_cost_usd"] > 0
    assert data["judge_model"] == "gpt-6.1-sol" and data["judge_prompt"].startswith("judge_figure-")
    assert data["by_source"]["alt"] == {"answered": 6, "factual_error": 2}
    assert json.dumps(data)


def test_the_judge_sees_the_image_and_never_who_wrote_the_text():
    items = make_items(2)
    client = FakeVisionClient(defaults={"judge": NO_ERROR})
    figures.judge_sample(
        items, population=2, skipped={}, client=client, settings=settings(), k=2, seed="s"
    )
    assert len(client.requests) == 2
    for request, item in zip(client.requests, items, strict=True):
        assert request.images == [item.image]
        assert "<candidate>\nA graph number" in (request.context_text or "")
        assert "ReadableSTEM" not in (request.context_text or "") + request.instructions
        assert "ReadableSTEM" not in (request.context_text or "") + request.instructions


def test_a_failed_call_is_left_unjudged_and_counted():
    client = FakeVisionClient(
        defaults={"judge": NO_ERROR}, fail_models={"gpt-6.1-sol": "api_error"}
    )
    result = figures.judge_sample(
        make_items(3), population=3, skipped={}, client=client, settings=settings(), k=3, seed="s"
    )
    data = result.to_dict()
    assert (data["answered"], data["failed_calls"]) == (0, 3)
    assert data["rate"] is None and data["wilson95"] is None


def test_a_sample_smaller_than_the_population_is_not_a_census():
    client = FakeVisionClient(defaults={"judge": NO_ERROR})
    result = figures.judge_sample(
        make_items(10), population=10, skipped={}, client=client, settings=settings(), k=4, seed="s"
    )
    assert result.to_dict()["census"] is False and result.sampled == 4
    assert len(client.requests) == 4


def test_the_spend_cap_stops_the_run():
    budget = JobBudget(InMemorySpendGuard(cap_usd=0.0001), max_usd=0.0001, max_calls=10)
    client = FakeVisionClient(defaults={"judge": NO_ERROR}, budget=budget)
    with pytest.raises(BudgetExceeded):
        figures.judge_sample(
            make_items(3),
            population=3,
            skipped={},
            client=client,
            settings=settings(),
            k=3,
            seed="s",
        )


# ------------------------------------------------------------------ gold references


def test_references_load_in_page_order_with_images_from_a_fetcher(tmp_path):
    folder = tmp_path / "openstax" / "physics"
    folder.mkdir(parents=True)
    for slug, number in (("2-2-b", "2.2"), ("2-1-a", "2.1")):
        record = {
            "kind": "figure",
            "id": f"openstax/physics/{slug}/fig-000",
            "page_slug": slug,
            "index_on_page": 0,
            "figure_number": number,
            "caption": f"Caption {number}",
            "images": [{"alt": f"Alt {number}", "url": f"https://openstax.org/{slug}"}],
        }
        (folder / f"{slug}.jsonl").write_text(json.dumps(record))
    fetched = []

    def fetch(url):
        fetched.append(url)
        if url.endswith("a"):
            raise OSError("down")
        return b"png-bytes"

    refs = figures.load_references(tmp_path, ["2-2-b", "2-1-a"], image_for=fetch)
    assert [r.number for r in refs] == ["2.2", "2.1"]
    assert [r.image for r in refs] == [b"png-bytes", None]  # a failed fetch is "no image"
    assert refs[0].alt == "Alt 2.2" and refs[0].caption == "Caption 2.2"
