"""pipeline.table_model: where the SLANet-plus file lives, the checked download, the
command that fetches it, the Docker build step, and the vendored pre- and
post-processing around the model (RapidTable 3.0.2)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import httpx
import numpy as np
import pytest

from readablestem.pipeline import table_model
from readablestem.pipeline.vendor import rapid_table

ROOT = Path(__file__).resolve().parents[1]
FAKE = b"slanet-plus stand-in"


@pytest.fixture
def pinned_to_fake(monkeypatch):
    monkeypatch.setattr(table_model, "MODEL_SHA256", hashlib.sha256(FAKE).hexdigest())


def client(body: bytes = FAKE, status: int = 200, seen: list | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        return httpx.Response(status, content=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_the_pin_is_the_file_rapidtable_3_0_2_ships():
    assert table_model.MODEL_SHA256.startswith("d57a942a") and table_model.MODEL_SHA256.endswith(
        "514b"
    )
    assert re.fullmatch(r"[0-9a-f]{64}", table_model.MODEL_SHA256)
    assert table_model.MODEL_URL.startswith("https://")
    assert table_model.MODEL_URL.endswith("/slanet-plus.onnx")


def test_the_model_directory_comes_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("TABLE_MODEL_DIR", str(tmp_path / "m"))
    assert table_model.model_path() == tmp_path / "m" / "slanet-plus.onnx"
    monkeypatch.delenv("TABLE_MODEL_DIR")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    assert table_model.model_dir() == tmp_path / "cache" / "readablestem" / "models"


def test_fetch_installs_a_file_with_the_pinned_checksum(tmp_path, pinned_to_fake):
    seen: list[str] = []
    path = table_model.fetch(tmp_path, client=client(seen=seen))
    assert path.read_bytes() == FAKE and seen == [table_model.MODEL_URL]
    assert path.stat().st_mode & 0o777 == 0o644  # readable by the app user in the image
    table_model.fetch(tmp_path, client=client(seen=seen))  # already there: no download
    assert len(seen) == 1


def test_fetch_refuses_a_different_file_and_leaves_nothing_behind(tmp_path, pinned_to_fake):
    with pytest.raises(table_model.ModelFileError, match="checksum mismatch"):
        table_model.fetch(tmp_path, client=client(b"something else"))
    assert list(tmp_path.iterdir()) == []


def test_fetch_reports_http_errors_and_oversized_downloads(tmp_path, pinned_to_fake, monkeypatch):
    with pytest.raises(table_model.ModelFileError, match="HTTP 404"):
        table_model.fetch(tmp_path, client=client(status=404))
    monkeypatch.setattr(table_model, "_MAX_BYTES", 4)
    with pytest.raises(table_model.ModelFileError, match="larger"):
        table_model.fetch(tmp_path, client=client())
    assert list(tmp_path.iterdir()) == []


def test_the_command_installs_a_local_copy(tmp_path, pinned_to_fake, capsys):
    source = tmp_path / "downloaded.onnx"
    source.write_bytes(FAKE)
    assert table_model.main(["--dir", str(tmp_path / "models"), "--from", str(source)]) == 0
    assert (tmp_path / "models" / "slanet-plus.onnx").read_bytes() == FAKE
    source.write_bytes(b"tampered")
    assert table_model.main(["--dir", str(tmp_path / "other"), "--from", str(source)]) == 1
    assert "checksum mismatch" in capsys.readouterr().err


def test_the_image_fetches_the_model_and_points_the_app_at_it():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "python -m readablestem.pipeline.table_model --dir /app/models" in dockerfile
    assert "TABLE_MODEL_DIR=/app/models" in dockerfile
    makefile = (ROOT / "Makefile").read_text()
    assert "table-model:" in makefile and "readablestem.pipeline.table_model" in makefile


def test_the_vendored_code_keeps_its_licence_and_notice():
    vendor = ROOT / "src" / "readablestem" / "pipeline" / "vendor"
    head = (vendor / "rapid_table.py").read_text().splitlines()[:20]
    assert any("Licensed under the Apache License, Version 2.0" in line for line in head)
    assert any("PaddlePaddle Authors" in line for line in head)
    assert "Apache License" in (vendor / "rapid_table-LICENSE.txt").read_text()
    assert "RapidTable 3.0.2" in (vendor / "rapid_table-NOTICE.txt").read_text()
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text()
    for name in ("pymupdf-layout", "onnxruntime", "SLANet-plus", "RapidTable"):
        assert name in notices


# ---------------------------------------------------------------- vendored processing


def test_preprocess_scales_the_long_side_and_pads():
    image = np.full((100, 244, 3), 255, dtype=np.uint8)
    pixels, shape = rapid_table.preprocess(image)
    assert pixels.shape == (1, 3, 488, 488) and pixels.dtype == np.float32
    assert shape[:2] == [100, 244] and shape[2] == pytest.approx(2.0)
    assert np.all(pixels[0, :, 200:, :] == 0)  # padding below the 200-pixel-high image


def test_logic_points_follow_spans_and_occupied_slots():
    tokens = [
        "<thead>", "<tr>", "<td", ' rowspan="2"', ">", "</td>", "<td", ' colspan="2"', ">",
        "</td>", "</tr>", "<tr>", "<td></td>", "<td></td>", "</tr>", "</thead>",
        "<tbody>", "<tr>", "<td></td>", "<td></td>", "<td></td>", "</tr>", "</tbody>",
    ]  # fmt: skip
    assert rapid_table.logic_points(tokens) == [
        (0, 1, 0, 0),
        (0, 0, 1, 2),
        (1, 1, 1, 1),
        (1, 1, 2, 2),
        (2, 2, 0, 0),
        (2, 2, 1, 1),
        (2, 2, 2, 2),
    ]
    assert rapid_table.header_row_count(tokens) == 2
    assert rapid_table.header_row_count(["<tbody>", "<tr>", "<td></td>", "</tr>"]) == 0


def test_a_hostile_span_is_bounded():
    tokens = ["<tr>", "<td", ' colspan="99999999"', ">", "</td>", "</tr>"]
    assert rapid_table.logic_points(tokens) == [(0, 0, 0, 59)]


def test_the_decoder_keeps_one_box_per_cell_in_order():
    characters = ["<thead>", "</thead>", "<tbody>", "</tbody>", "<tr>", "</tr>", "<td>"]
    decode = rapid_table.TableLabelDecode(characters)
    index = {name: i for i, name in enumerate(decode.characters)}
    sequence = ["<tr>", "<td></td>", "<td></td>", "</tr>", "eos"]
    probs = np.zeros((1, len(sequence), len(decode.characters)), dtype=np.float32)
    for step, name in enumerate(sequence):
        probs[0, step, index[name]] = 1.0
    boxes = np.zeros((1, len(sequence), 8), dtype=np.float32)
    boxes[0, 2] = [0.5, 0.0, 1.0, 0.0, 1.0, 0.5, 0.5, 0.5]  # the first cell's box is all zero
    tokens, cells, score = decode(boxes, probs, [100, 200, 2.44, 2.44, 488, 488], (100, 200))
    assert tokens == ["<tr>", "<td></td>", "<td></td>", "</tr>"]
    assert cells[0] is None and cells[1] is not None  # not shifted onto the wrong cell
    assert score == pytest.approx(1.0)
