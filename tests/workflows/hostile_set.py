"""The hostile-input set: files made to hurt the app, and how each must end.

Every file comes from a builder (`builders/hostile.py`, `builders/hostile_sources.py` and a
few of the other modules). A case states one of three ends:

- `refused`: the upload is refused, and the log line `upload_rejected` carries this code;
- `held`: the file is kept for choosing its pages, and nothing is queued;
- `ends`: a job is queued and ends `done`, or `failed` with `code`.

Whatever the end, it comes inside `seconds` and `megabytes`, and the strings the builder
planted stay where they can do nothing: a string of `never` is in no output at all, and a
string of `as_text` is only ever text.

`open` names the checks an open finding makes false today, by the name of the test's check
(`ends`, `bound`, `next`, `planted`). The mark is strict: the fix removes it.

`test_wf49_hostile_input.py` runs the set. A reader the app gains (a second PDF parser, a
direct Word reader, a notebook reader) adds its cases here in the change that adds it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from builders.hostile import MARKUP_AS_TEXT, MARKUP_NEVER

# The start of the sentence each injection file addresses to the model.
TO_THE_MODEL = ("Note to the AI model",)
TO_THE_MODEL_EXAM = ("Instruction for the AI model",)

# What a queued job may take. A plain two-page file takes about 3 s and peaks near 450 MB
# with the layout models loaded (measured November 11, 2026).
JOB_S = 30.0
JOB_MB = 600
# What an upload may take to be refused or held: it is answered inside a web request.
UPLOAD_S = 10.0
# The start of the host's own /etc/passwd: a source that reads the file would carry it.
HOST_FILE = "root:x:0:0"


@dataclass(frozen=True)
class Case:
    file: str
    refused: str = ""
    held: bool = False
    ends: str = ""
    code: str | None = None
    seconds: float = JOB_S
    megabytes: int = JOB_MB
    never: tuple[str, ...] = ()
    as_text: tuple[str, ...] = ()
    # Model answers the conversion asks for and the recording does not hold.
    unanswered: int = 0
    # A LaTeX source: run once on each sandbox the machine can start.
    source: bool = False
    # The builder takes long: run under the `slow` marker.
    slow: bool = False
    open: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        stated = [bool(self.refused), self.held, bool(self.ends)]
        if stated.count(True) != 1:
            raise ValueError(f"{self.file}: a case states exactly one end")


def refused(file: str, code: str, **more: object) -> Case:
    return Case(file, refused=code, seconds=UPLOAD_S, **more)  # type: ignore[arg-type]


def done(file: str, **more: object) -> Case:
    return Case(file, ends="done", **more)  # type: ignore[arg-type]


def failed(file: str, code: str, **more: object) -> Case:
    return Case(file, ends="failed", code=code, **more)  # type: ignore[arg-type]


CASES: tuple[Case, ...] = (
    # ------------------------------------------------------------ size: pages and pictures
    refused("page-14400.pdf", "page_too_large"),
    refused("image-20000.pdf", "image_too_large"),
    refused("image-65000.pdf", "image_too_large", slow=True),
    refused("page-4999-image-20000.pdf", "image_too_large"),
    # The file of F-503: it held the only worker for 694.5 s and 770 MB before the pixel
    # budget, which now refuses it at the door.
    refused("shared-image-100-pages.pdf", "image_too_large"),
    # Each page's picture asks a model twice. Nothing is recorded for a gray square, and a
    # live job stops asking at its spending limit.
    done("shared-image-under-limit-100-pages.pdf", unanswered=200),
    done("pages-100.pdf"),
    Case("pages-500.pdf", held=True, seconds=UPLOAD_S),
    refused("pages-3000.pdf", "too_many_pages", slow=True),
    # ---------------------------------------------------------------- structure of the PDF
    refused("password.pdf", "encrypted"),
    refused("not-a-pdf.pdf", "not_pdf"),
    refused("png-named.pdf", "not_pdf"),
    refused("empty.pdf", "empty"),
    # What the instructor is told about a cut-off file is WF19's (F-505); here it only has
    # to end.
    done("truncated.pdf"),
    done("xref-damaged.pdf"),
    done("page-tree-deep.pdf"),
    # One page that inflates to 256 MB of content: refused by the content budget before
    # anything parses it (F-559; it took 7 s of the upload request and 24 s of a worker).
    refused("stream-bomb.pdf", "content_too_large"),
    # ----------------------------------------------- content that is data and never a program
    done(
        "javascript.pdf",
        never=("ZS2-JS-MARKER", "ZS2-LAUNCH-MARKER", "evil.example", "cmd.exe"),
    ),
    done("markup.pdf", never=MARKUP_NEVER, as_text=MARKUP_AS_TEXT),
    # Text on the page that tells a model what to write. That the model was not steered is
    # read from its answers, which are not recorded yet: two a file (docs/eval.md).
    done("injection-visible.pdf", as_text=TO_THE_MODEL, unanswered=2),
    done("injection-white.pdf", as_text=TO_THE_MODEL, unanswered=2),
    done("injection-exam.pdf", as_text=TO_THE_MODEL_EXAM, unanswered=2),
    # ------------------------------------------------------------------------------- zips
    refused("zip-traversal.zip", "source_unsafe_zip"),
    refused("zip-absolute.zip", "source_unsafe_zip"),
    refused("zip-backslash.zip", "source_unsafe_zip"),
    refused("zip-symlink.zip", "source_unsafe_zip"),
    refused("zip-duplicate.zip", "source_unsafe_zip"),
    refused("zip-bomb.zip", "source_zip_limits"),
    refused("zip-lying-header.zip", "source_bad_zip"),
    refused("nested.zip", "source_unsupported_file"),
    # ------------------------------------------------------------------------------ LaTeX
    refused("hostile-input-host-file.tex", "source_unsafe"),
    refused("hostile-shell.tex", "source_unsafe"),
    failed("hostile-input-built-name.tex", "source_missing_file", source=True, never=(HOST_FILE,)),
    failed("hostile-read-built-name.tex", "source_undefined", source=True, never=(HOST_FILE,)),
    failed("hostile-grows.tex", "source_failed", source=True),
    failed("hostile-loop.tex", "source_failed", source=True),
)
