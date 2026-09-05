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
    _binaryformat,
    _connectors,
    _datetime,
    _enums,
    _list,
    _misc,
    _number,
    _record,
    _sources,
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
    _connectors,
    _sources,
    _text,
    _number,
    _list,
    _record,
    _table,
    _type,
    _table_join,
    _table_shape,
    _datetime,
    _misc,
    _binaryformat,
)


def _registered_names(module: object) -> list[str]:
    """Every M name a module's source registers, repeats included.

    Reading the dict is not enough: a module that registers a name twice has
    already lost the first entry by the time the dict exists. That is a real
    failure mode, not a hypothetical - a later `Table.Range` silently replaced
    an older one that had an out-of-bounds check, and the cross-module check
    below could not see it because both registrations were in the same file.

    Parsed rather than pattern-matched. A regex over the source also matches
    the record-field keys in `_sources.py`'s `{"Name": ..., "Data": ...}`
    rows, which are data, not registrations - a check that reports those is a
    check people learn to ignore.
    """
    import ast
    import inspect

    try:
        tree = ast.parse(inspect.getsource(module))  # type: ignore[arg-type]
    except (OSError, TypeError, SyntaxError):  # pragma: no cover - not from source
        return list(module.BUILTINS)  # type: ignore[attr-defined]

    names: list[str] = []

    def collect(node: ast.AST | None) -> None:
        if isinstance(node, ast.Dict):
            names.extend(
                key.value
                for key in node.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            )

    def is_builtins(node: ast.AST) -> bool:
        return isinstance(node, ast.Name) and node.id == "BUILTINS"

    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and is_builtins(node.target):
            collect(node.value)
        elif isinstance(node, ast.Assign) and any(map(is_builtins, node.targets)):
            collect(node.value)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "update"
            and is_builtins(node.func.value)
        ):
            for argument in node.args:
                collect(argument)
    return names


BUILTINS: dict[str, _Builtin] = {}
for _module in _MODULES:
    _own = _registered_names(_module)
    _self_clash = sorted({n for n in _own if _own.count(n) > 1})
    if _self_clash:
        raise RuntimeError(
            f"{_module.__name__} registers {_self_clash} more than once; the "
            "later definition silently replaces the earlier one"
        )
    _clash = BUILTINS.keys() & _module.BUILTINS.keys()
    if _clash:
        raise RuntimeError(
            f"duplicate builtin(s) {sorted(_clash)} in {_module.__name__}"
        )
    BUILTINS.update(_module.BUILTINS)

__all__ = ["BUILTINS"]
