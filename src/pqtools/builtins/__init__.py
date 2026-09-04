"""Assembles the M builtin-function registry from the per-family modules.

Split out of ``evaluate.py`` in the 0.5.0 architecture refactor (pure move,
zero behaviour change) - see PRD-0.5.0-builtins.md's "Architecture change"
section. Each family module owns one M namespace (``Table.*``, ``Text.*``,
``List.*``, ``Record.*``, ``Number.*``/``Logical.*``/``Json.*``); this
module just wires them into one dict keyed by the M-visible name, exactly
as the single ``BUILTINS`` dict at the bottom of ``evaluate.py`` did before
the split.

``evaluate.py`` imports ``BUILTINS`` from here (``from .builtins import
BUILTINS``) and re-exports it, so ``pqtools.evaluate.BUILTINS`` - documented
in README.md as the source of truth for the supported-builtins list -
keeps working unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from . import (
    _datetime,
    _enums,
    _list,
    _number,
    _record,
    _table,
    _table_join,
    _table_shape,
    _text,
    _type,
)

if TYPE_CHECKING:
    from ..evaluate import _Ctx

_Builtin = Callable[[list[Any], "_Ctx"], Any]

# Each family module owns its own BUILTINS dict; this merges them. A duplicate
# M name across two modules is a bug (two implementations of one function, and
# whichever imported last would silently win), so it is rejected loudly here
# rather than resolved by import order.
_MODULES = (
    _enums,
    _text,
    _number,
    _list,
    _record,
    _table,
    _type,
    _table_join,
    _table_shape,
    _datetime,
)

BUILTINS: dict[str, _Builtin] = {}
for _module in _MODULES:
    _clash = BUILTINS.keys() & _module.BUILTINS.keys()
    if _clash:
        raise RuntimeError(
            f"duplicate builtin(s) {sorted(_clash)} in {_module.__name__}"
        )
    BUILTINS.update(_module.BUILTINS)

__all__ = ["BUILTINS"]
