"""Errors of the proof command. Each carries the exit code the CLI returns for it."""

from __future__ import annotations


class ProofError(RuntimeError):
    """Something the user has to fix before the proof can run. Exit code 2."""

    code = 2


class PinMismatch(ProofError):
    """A hash or pin does not match: the gold, the preregistration or a bundle file is not
    the one the registry names. Nothing is run. Exit code 3."""

    code = 3


class BundleError(ProofError):
    """The recorded run is missing, damaged or was made by other code. Exit code 3."""

    code = 3


class Drift(ProofError):
    """Replaying the recorded answers gives other numbers than the recorded run. The
    published numbers no longer come from this checkout. Exit code 4."""

    code = 4


class ReplayMiss(ProofError):
    """The pipeline asked for a model answer the bundle does not hold. Exit code 5."""

    code = 5


class Incomplete(ProofError):
    """The run did not finish every section of every arm. Exit code 1."""

    code = 1
