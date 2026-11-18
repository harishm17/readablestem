"""The words for the exam choice (`Job.exam_mode`), said once.

The upload form's box, the job page, the documents list, the review header, the record and
its CSV and the share page all print these, so the choice reads the same wherever the
document is handled. The student's page and files never say it.
"""

from __future__ import annotations

from typing import NamedTuple


class ExamWords(NamedTuple):
    choice: str  # the upload form's box
    on: str  # a document converted as an exam or quiz
    off: str  # a document converted normally
    short: str  # the mark on a list row or a header
    change: str  # the link text for the control that changes it


EXAM_WORDS = ExamWords(
    choice="Exam or quiz: describe figures without giving answers",
    on="Exam or quiz: figure descriptions do not give answers.",
    off="Not an exam or quiz: figures are described normally.",
    short="Exam or quiz",
    change="Change the exam or quiz choice",
)


def words(exam_mode: bool) -> str:
    """The sentence for a document's choice."""
    return EXAM_WORDS.on if exam_mode else EXAM_WORDS.off
