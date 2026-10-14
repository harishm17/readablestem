"""Import alias: `a11y_stem` is the old name of the `readablestem` package.

ReadableSTEM was called a11y-stem until October 2026. The registered proof files (`proof/`) are
kept byte for byte and import `a11y_stem.*`, so this module makes every `a11y_stem.X` import
resolve to the very same module object as `readablestem.X` (no second copy, so classes and
enums compare equal). New code imports `readablestem`.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import sys
from types import ModuleType

import readablestem

_OLD = __name__
_NEW = "readablestem"


class _AliasFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(_OLD + "."):
            return importlib.machinery.ModuleSpec(fullname, self)
        return None

    def create_module(self, spec):
        return importlib.import_module(_NEW + spec.name[len(_OLD) :])

    def exec_module(self, module: ModuleType) -> None:
        """The real module already ran under its new name."""


if not any(isinstance(f, _AliasFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _AliasFinder())

__version__ = readablestem.__version__
__path__ = readablestem.__path__


def __getattr__(name: str):
    return getattr(readablestem, name)
