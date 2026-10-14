# Copyright (c) 2020 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# -*- encoding: utf-8 -*-
# @Author: SWHL
# @Contact: liekkaskono@163.com
#
# Vendored from RapidTable 3.0.2 (https://github.com/RapidAI/RapidTable, Apache-2.0;
# licence in rapid_table-LICENSE.txt, notice in README.md next to this file):
# rapid_table/table_structure/pp_structure/pre_process.py (TablePreprocess),
# rapid_table/table_structure/pp_structure/post_process.py (TableLabelDecode) and
# rapid_table/table_matcher/main.py (TableMatch.decode_one_logic_points), the pre- and
# post-processing around the SLANet-plus ONNX model.
#
# Changed for ReadableSTEM (2026-10-09):
# - the resize uses Pillow instead of OpenCV (cv2.resize), so OpenCV is not needed;
# - one image at a time, already in BGR channel order (as RapidTable passes it);
# - the cell boxes keep their order and count (RapidTable drops all-zero boxes, which
#   shifts every later box onto the wrong cell); an all-zero box is reported as None;
# - the logic-point decoder reads spans with a bounded integer parse;
# - type hints and names; the HTML matcher, OCR and the engine wrapper are not used.
"""SLANet-plus pre- and post-processing (from RapidTable 3.0.2, Apache-2.0)."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from PIL import Image

MAX_LEN = 488  # SLANet-plus input side (pixels)
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
_MAX_SPAN = 60  # a colspan or rowspan larger than any table we write is noise


def preprocess(image: np.ndarray) -> tuple[np.ndarray, list[float]]:
    """(1, 3, 488, 488) float32 input and the shape list [h, w, ratio, ratio, 488, 488].

    `image` is H x W x 3 uint8, BGR. The long side is scaled to 488 pixels, the image is
    normalised and padded with zeros at the bottom and right (TablePreprocess)."""
    h, w = image.shape[:2]
    ratio = MAX_LEN / (max(h, w) * 1.0)
    resize_h, resize_w = max(1, int(h * ratio)), max(1, int(w * ratio))
    resized = _resize(image, resize_w, resize_h)
    normalised = (resized.astype(np.float32) * (1 / 255.0) - _MEAN) / _STD
    padded = np.zeros((MAX_LEN, MAX_LEN, 3), dtype=np.float32)
    padded[:resize_h, :resize_w, :] = normalised
    return padded.transpose((2, 0, 1))[np.newaxis, ...], [h, w, ratio, ratio, MAX_LEN, MAX_LEN]


def _resize(image: np.ndarray, width: int, height: int) -> np.ndarray:
    """Bilinear resize sampled at pixel centres without smoothing first, as OpenCV's
    cv2.resize(INTER_LINEAR) does (the model was trained on that). Pillow's own resize
    filters a downscaled image first, which blurs small text; its affine transform with
    bilinear sampling does not."""
    h, w = image.shape[:2]
    scale = (w / width, 0.0, 0.0, 0.0, h / height, 0.0)
    resized = Image.fromarray(image).transform(
        (width, height), Image.Transform.AFFINE, scale, resample=Image.Resampling.BILINEAR
    )
    return np.asarray(resized)


class TableLabelDecode:
    """Structure tokens and cell boxes from the model's two outputs."""

    def __init__(self, characters: Sequence[str]) -> None:
        tokens = list(characters)
        # merge_no_span_structure=True: "<td></td>" is one token, a bare "<td>" is not.
        if "<td></td>" not in tokens:
            tokens.append("<td></td>")
        if "<td>" in tokens:
            tokens.remove("<td>")
        self.characters = ["sos", *tokens, "eos"]
        self.end_index = len(self.characters) - 1
        self.ignored = {0, self.end_index}
        self.td_tokens = {"<td>", "<td", "<td></td>"}

    def __call__(
        self,
        bbox_preds: np.ndarray,
        structure_probs: np.ndarray,
        shape_list: Sequence[float],
        image_shape: tuple[int, int],
    ) -> tuple[list[str], list[list[float] | None], float]:
        """(structure tokens, one box per cell token or None, mean token probability).

        Boxes are [x0, y0, x1, y1, x2, y2, x3, y3] in the original image's pixels."""
        structure_idx = structure_probs.argmax(axis=2)[0]
        structure_max = structure_probs.max(axis=2)[0]
        tokens: list[str] = []
        boxes: list[list[float] | None] = []
        scores: list[float] = []
        for idx in range(len(structure_idx)):
            char_idx = int(structure_idx[idx])
            if idx > 0 and char_idx == self.end_index:
                break
            if char_idx in self.ignored or char_idx >= len(self.characters):
                continue
            text = self.characters[char_idx]
            if text in self.td_tokens:
                boxes.append(self._box(bbox_preds[0, idx].astype(np.float64), shape_list))
            tokens.append(text)
            scores.append(float(structure_max[idx]))
        boxes = [self._rescale(b, image_shape) for b in boxes]
        return tokens, boxes, (sum(scores) / len(scores) if scores else 0.0)

    @staticmethod
    def _box(bbox: np.ndarray, shape_list: Sequence[float]) -> list[float] | None:
        if not np.any(bbox):
            return None  # a placeholder box (RapidTable's filter_blank_bbox)
        h, w = shape_list[0], shape_list[1]
        out = bbox.copy()
        out[0::2] *= w
        out[1::2] *= h
        return [float(v) for v in out]

    @staticmethod
    def _rescale(box: list[float] | None, image_shape: tuple[int, int]) -> list[float] | None:
        """SLANet-plus boxes come out relative to the padded square (rescale_cell_bboxes)."""
        if box is None:
            return None
        h, w = image_shape
        ratio = min(MAX_LEN / h, MAX_LEN / w)
        w_ratio, h_ratio = MAX_LEN / (w * ratio), MAX_LEN / (h * ratio)
        return [v * (w_ratio if i % 2 == 0 else h_ratio) for i, v in enumerate(box)]


def _span(token: str) -> int:
    digits = "".join(c for c in token.split("=", 1)[-1] if c.isdigit())[:3]
    return max(1, min(_MAX_SPAN, int(digits))) if digits else 1


def logic_points(tokens: Sequence[str]) -> list[tuple[int, int, int, int]]:
    """[row_start, row_end, col_start, col_end] of each cell token, in token order
    (TableMatch.decode_one_logic_points)."""
    points: list[tuple[int, int, int, int]] = []
    row = col = 0
    occupied: set[tuple[int, int]] = set()
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "<tr>":
            col = 0
        elif token == "</tr>":
            row += 1
        elif token.startswith("<td"):
            colspan = rowspan = 1
            j = i
            if token != "<td></td>":
                j += 1
                while j < len(tokens) and not tokens[j].startswith(">"):
                    if "colspan=" in tokens[j]:
                        colspan = _span(tokens[j])
                    elif "rowspan=" in tokens[j]:
                        rowspan = _span(tokens[j])
                    j += 1
            i = j
            while (row, col) in occupied:
                col += 1
            points.append((row, row + rowspan - 1, col, col + colspan - 1))
            for r in range(row, row + rowspan):
                for c in range(col, col + colspan):
                    occupied.add((r, c))
            col += colspan
        i += 1
    return points


def header_row_count(tokens: Sequence[str]) -> int:
    """Rows inside the model's <thead> (0 when it predicts none)."""
    rows = 0
    inside = False
    for token in tokens:
        if token == "<thead>":
            inside = True
        elif token == "</thead>":
            return rows
        elif token == "</tr>" and inside:
            rows += 1
    return 0
