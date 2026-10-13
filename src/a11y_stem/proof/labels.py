"""Stage: the human labels (preregistration 7.8), which do not exist yet.

Three label sets are the author's alone (no model labels for him): whether a wrong equation
changes the meaning, whether the figure judge agrees with him (Cohen's kappa), and the
audit of the invented-content rule. Each is made on a sheet with no system name, after the
run. Until the files exist this stage prints that fact and no number.
"""

from __future__ import annotations

from a11y_stem.proof import stages
from a11y_stem.proof.bundle import BundleWriter
from a11y_stem.proof.report import Section, StageResult, bullets
from a11y_stem.proof.stages import RecordContext, StageContext

TITLE = "Human labels and judge agreement"


class LabelsStage:
    name = "human_labels"
    title = TITLE

    def record(self, ctx: RecordContext, writer: BundleWriter) -> None:
        return None

    def run(self, ctx: StageContext) -> StageResult:
        section = Section(
            TITLE,
            [
                "**Not done.** The labels are written by the author, blind to the system, "
                "after the run (preregistration 7.8). None exist, so this report shows no "
                "number from them. Until they do:",
                bullets(
                    [
                        "whether a wrong equation changes the meaning is not known: every "
                        "wrong equation is counted as wrong, and the scorer's ceiling on a "
                        "hand-written sample is stated in the registered addendum;",
                        "the figure judge (a model) is not calibrated against human labels, "
                        "so its numbers are agreement with that judge and nothing more;",
                        "the invented-content rule's precision and recall are not measured, "
                        "so its rates include paraphrase it cannot tell from invention.",
                    ]
                ),
            ],
        )
        return StageResult("not_wired", [section], [f"{TITLE}: no labels exist yet"])


stages.register(LabelsStage())
