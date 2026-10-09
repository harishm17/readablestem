"""The two optional questions after a download, stored as usage events (`web.events`).

"Will you post this for students?" (yes, no, not sure) and "About how many minutes did
this save?" (a whole number) are the beta's measure of whether the output reaches
students and what it is worth (FEATURE-REVIEW "Beta 6"). Both are optional, asked once
per document, and never block anything: the download has already happened.

Where they appear (`templates/_after_download.html`, under the download link): in the
page but hidden until the download link is used (app.js shows them then); without
JavaScript, or on a later visit, they show once the server has recorded a download. Each
person answers once per document; answers are only a code and a number, never text, so
there is no free-text box (anything else goes to the footer's Feedback link).

FEEDBACK_QUESTIONS_ENABLED=false, or EVENTS_ENABLED=false (nowhere to keep the answers),
turns them off.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from a11y_stem.config import Settings
from a11y_stem.web import events

WILL_POST_CHOICES = (("yes", "Yes"), ("no", "No"), ("not_sure", "Not sure"))
# Longer than any course document takes to make accessible by hand, so an answer above
# it is a typo, not an estimate.
MAX_MINUTES = 2000
MINUTES_FIELD = "minutes-saved"
WILL_POST_FIELD = "will-post-yes"  # the first radio button: where an error summary points


def enabled(settings: Settings) -> bool:
    return settings.feedback_questions_enabled and settings.events_enabled


@dataclass
class AfterDownload:
    """What `_after_download.html` shows for one job and person."""

    ask: bool = False  # the questions are on the page
    hidden: bool = True  # ... but hidden until the download link is used (app.js)
    answered: bool = False
    choices: tuple[tuple[str, str], ...] = WILL_POST_CHOICES


def after_download(
    session: Session, settings: Settings, user_id: str, job_id: str
) -> AfterDownload:
    if not enabled(settings):
        return AfterDownload()
    if events.answered(session, user_id, job_id):
        return AfterDownload(answered=True)
    return AfterDownload(ask=True, hidden=not events.downloaded(session, user_id, job_id))


@dataclass
class Answers:
    will_post: str | None = None
    minutes: int | None = None

    @property
    def empty(self) -> bool:
        return self.will_post is None and self.minutes is None


@dataclass
class FieldError:
    field_id: str
    message: str


def parse(will_post: str, minutes: str) -> tuple[Answers, list[FieldError]]:
    """The form's two fields, each optional. Errors name what to type instead."""
    errors: list[FieldError] = []
    answers = Answers()
    if will_post in dict(WILL_POST_CHOICES):
        answers.will_post = will_post
    elif will_post:
        errors.append(FieldError(WILL_POST_FIELD, "Choose yes, no or not sure."))
    typed = minutes.strip().removesuffix("minutes").removesuffix("min").strip()
    if typed:
        if typed.isdigit() and int(typed) <= MAX_MINUTES:
            answers.minutes = int(typed)
        else:
            errors.append(
                FieldError(
                    MINUTES_FIELD,
                    f"Enter the minutes as a whole number from 0 to {MAX_MINUTES}, like 30. "
                    "Or leave it empty.",
                )
            )
    return answers, errors


def record(session: Session, user_id: str, job_id: str, answers: Answers) -> None:
    """The answers given as events, in the caller's transaction."""
    if answers.will_post is not None:
        session.add(events.make(user_id, events.WILL_POST, job_ref=job_id, code=answers.will_post))
    if answers.minutes is not None:
        session.add(
            events.make(user_id, events.MINUTES_SAVED, job_ref=job_id, value=answers.minutes)
        )
