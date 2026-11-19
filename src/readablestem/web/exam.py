"""The words for the exam choice (`Job.exam_mode`), said once.

The upload form's box, the job page, the documents list, the review header, the record and
its CSV and the share page all print these, so the choice reads the same wherever the
document is handled. The student's page and files never say it.
"""

from __future__ import annotations

import re
from typing import NamedTuple


class ExamWords(NamedTuple):
    choice: str  # the upload form's box
    on: str  # a document converted as an exam or quiz
    off: str  # a document converted normally
    short: str  # the mark on a list row or a header
    change: str  # the link text for the control that changes it
    file_hint: str  # under the upload form's box when a chosen file's name reads as an exam
    title_hint: str  # on the job page of a document converted normally whose name reads as one
    title_ask: str  # what the person can do about it, said where they can


EXAM_WORDS = ExamWords(
    choice="Exam or quiz: describe figures without giving answers",
    on="Exam or quiz: figure descriptions do not give answers.",
    off="Not an exam or quiz: figures are described normally.",
    short="Exam or quiz",
    change="Change the exam or quiz choice",
    file_hint="A file name says “{word}”. Check the box above if students will be tested with it.",
    title_hint="The title or file name says “{word}”.",
    title_ask="If students will be tested with this document, change the choice.",
)

# The words that make a title or file name read as an exam or quiz, as whole words. "final"
# and "test" are left out on purpose: "..._FINAL.pdf" is a final draft and "test" is a draft
# label ("final exam" is caught by "exam"). Homework, worksheets and problem sets are not exams.
NAME_WORDS = ("exam", "exams", "quiz", "quizzes", "midterm", "midterms")


def words(exam_mode: bool) -> str:
    """The sentence for a document's choice."""
    return EXAM_WORDS.on if exam_mode else EXAM_WORDS.off


def word_in(*names: str) -> str:
    """The first exam word in a title or file name, "" when none reads as an exam. Whole words,
    any case; a camel-case join, a digit, `_`, `-` and `.` all break a word ("MidtermExam",
    "quiz-03", "exam2"). static/app.js reads a chosen file's name the same way, with `NAME_WORDS`
    from the field's data attribute. It is a hint: it never turns the choice on."""
    for name in names:
        spaced = re.sub(r"[^A-Za-z]+", " ", re.sub(r"([a-z])([A-Z])", r"\1 \2", name)).lower()
        for found in spaced.split():
            if found in NAME_WORDS:
                return found
    return ""
