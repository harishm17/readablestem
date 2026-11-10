"""Where the review screen's marks go on a page image (web.marks): a frame that hugs each
item, and a clickable area of at least 24 CSS px that overlaps no other mark's, also on
a dense page (an inline equation on every line, accepted items shown)."""

from __future__ import annotations

from readablestem.web import marks

LETTER = (612.0, 792.0)


def dense_page() -> dict[str, tuple[float, float, float, float]]:
    """A handout column as the LaTeX handout has it: a heading with an inline equation
    on the line under it and another just under that one (the real boxes), then an
    inline equation on every line 12 pt apart, some two to a line, a display equation,
    and the second column."""
    boxes = {
        "h1": (57.6, 286.69, 143.15, 301.04),
        "e11": (125.6, 311.94, 134.3, 321.9),
        "e14": (133.1, 322.6, 145.9, 331.7),
    }
    for line in range(10):
        x = 70.0 + (line * 97) % 200
        y = 336.0 + 12 * line
        boxes[f"i{line}"] = (x, y, x + 9 + (line % 2) * 14, y + 9.5)
        if line % 3 == 0:
            boxes[f"j{line}"] = (x + 40, y, x + 52, y + 9.5)
    boxes["d1"] = (150.0, 470.0, 260.0, 490.0)
    boxes["r1"] = (330.0, 312.0, 342.0, 321.5)  # right column, same line as e11
    return boxes


def covers_middle(mark: marks.Mark, side: float) -> bool:
    for axis in (0, 1):
        lo, hi = mark.element[axis], mark.element[axis + 2]
        keep = min((hi - lo) / 2, side / 4)
        centre = (lo + hi) / 2
        if not (
            mark.link[axis] <= centre - keep + 1e-6 and mark.link[axis + 2] >= centre + keep - 1e-6
        ):
            return False
    return True


def test_on_a_dense_page_no_two_clickable_areas_overlap():
    placed = marks.place(dense_page(), LETTER)
    assert marks.overlapping(placed) == []
    side = marks.min_target_pt(LETTER[0])
    for mark in placed.values():
        width, height = mark.link[2] - mark.link[0], mark.link[3] - mark.link[1]
        assert width >= side - 1e-6 and height >= side - 1e-6, mark.element_id
        assert covers_middle(mark, side), mark.element_id  # clicking the item opens it


def test_24_css_px_at_the_smallest_size_the_page_is_drawn():
    side = marks.min_target_pt(LETTER[0])
    assert side * marks.MIN_PAGE_PX / LETTER[0] >= marks.MIN_TARGET_PX
    # A landscape page is drawn at fewer pixels per point: bigger areas in points.
    assert marks.min_target_pt(792.0) > side


def test_a_mark_at_the_edge_of_the_page_stays_on_it():
    placed = marks.place({"corner": (0.0, 0.0, 4.0, 4.0)}, LETTER)
    x0, y0, _, _ = placed["corner"].link
    assert x0 >= 0 and y0 >= 0
