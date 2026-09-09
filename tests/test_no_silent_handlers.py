"""Every `except` handler in `src/` that can finish without raising.

PRD acceptance 6.6 reads: *"No new silent path - the round-9 AST sweep over
exception handlers that return without raising must still find nothing new."*
That sweep was a one-off analysis. Nothing re-ran it, so the criterion had no
standing check and could only be met by remembering to look - and by round 49
`cli.py` had grown from the nine handlers round 9 recorded to eleven.

**What this test does and does not claim.** It does not certify that any
handler here is correct. It pins the SET, so adding one forces a person to
look at it and decide, and to move the pin deliberately. That is the whole
mechanism the criterion assumes.

The two that appeared since round 9 were checked when this pin was written:
both are in `_add_parse_refusal`, which *builds* a `ParseError` for its caller
to throw - its only call site is `raise _add_parse_refusal(...) from error`
(`cli.py:431`). An AST walk cannot see that the raise happens one frame up, so
a returning handler is not the same thing as a swallowed error.

Of the other nine `cli.py` shapes, **four** are the batch loops round 9
checked by execution: `_run_parse_batch`, `_run_dependencies_batch`,
`_run_check_batch` and `_run_format_batch` all exit 2 with one broken file
among good ones. The remaining five are not batch loops, and are named here
so a reader is not pointed at the wrong four: `_container_backup`
(`FileExistsError`, the retry that walks to the next free backup name),
`_run_list` twice, `_run_show`, and `main`. The pin's value is that each
entry was read before it was pinned, so the sentence describing them has to
match the entries.

Counts, not a set: a second identical handler added to the same function is
exactly the change a set would hide.
"""

from __future__ import annotations

import ast
import collections
import pathlib
from collections.abc import Sequence

_Function = ast.FunctionDef | ast.AsyncFunctionDef

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "pqtools"

# (module relative to src/pqtools, enclosing function, exception expression)
# -> how many handlers of that shape may finish without raising.
_PINNED: dict[tuple[str, str, str], int] = {
    (
        "builtins/__init__.py",
        "_registered_names",
        "(OSError, TypeError, SyntaxError)",
    ): 1,
    ("builtins/_datetime.py", "_coerce_date_or_datetime", "ValueError"): 1,
    ("builtins/_datetime.py", "_parse_iso_date", "ValueError"): 2,
    ("builtins/_datetime.py", "_parse_iso_datetime", "ValueError"): 2,
    ("builtins/_datetime.py", "_parse_iso_time", "ValueError"): 1,
    ("builtins/_datetime.py", "_try_patterns", "(EvalError, UnsupportedError)"): 1,
    ("builtins/_number.py", "_is_temporal_text", "(EvalError, UnsupportedError)"): 1,
    ("builtins/_number.py", "_is_temporal_text", "EvalError"): 1,
    ("builtins/_number.py", "_number_acos", "ValueError"): 1,
    ("builtins/_number.py", "_number_asin", "ValueError"): 1,
    ("builtins/_number.py", "_number_cosh", "OverflowError"): 1,
    ("builtins/_number.py", "_number_exp", "OverflowError"): 1,
    ("builtins/_number.py", "_number_sinh", "OverflowError"): 1,
    ("builtins/_number.py", "_value_from_text", "ValueError"): 1,
    ("builtins/_table_shape.py", "_table_pivot", "ValueError"): 1,
    ("cli.py", "_add_parse_refusal", "ParseError"): 2,
    ("cli.py", "_container_backup", "FileExistsError"): 1,
    ("cli.py", "_run_check_batch", "(OSError, UnicodeDecodeError, MQueryError)"): 1,
    (
        "cli.py",
        "_run_dependencies_batch",
        "(OSError, UnicodeDecodeError, MQueryError)",
    ): 1,
    ("cli.py", "_run_format_batch", "(OSError, UnicodeDecodeError, MQueryError)"): 1,
    ("cli.py", "_run_list", "(OSError, UnicodeDecodeError, MQueryError)"): 1,
    ("cli.py", "_run_list", "MQueryError"): 1,
    ("cli.py", "_run_parse_batch", "(OSError, UnicodeDecodeError, MQueryError)"): 1,
    ("cli.py", "_run_show", "(OSError, UnicodeDecodeError, MQueryError)"): 1,
    ("cli.py", "main", "(OSError, UnicodeDecodeError, MQueryError)"): 1,
    ("containers.py", "_locate", "(UnicodeDecodeError, LookupError)"): 1,
    ("containers.py", "_locate", "ContainerError"): 2,
    ("containers.py", "_locate", "binascii.Error"): 1,
    ("containers.py", "_read_pbip", "(OSError, MQueryError, UnicodeDecodeError)"): 1,
    ("containers.py", "_read_pbip", "ValueError"): 1,
    ("core.py", "_final_component_is_a_symlink", "OSError"): 1,
    ("core.py", "check", "ParseError"): 1,
    ("core.py", "read", "(OSError, ValueError)"): 1,
    ("core.py", "unquote_identifier", "ParseError"): 1,
    ("core.py", "write", "OSError"): 1,
    ("evaluate.py", "_eval_try", "EvalError"): 2,
    ("evaluate.py", "_field_specification_list", "UnsupportedError"): 1,
    ("fabric.py", "_validate_arrow", "StopIteration"): 1,
    ("io.py", "_reject_internal", "socket.gaierror"): 1,
}


def _exits_by_raising(body: list[ast.stmt]) -> bool:
    """True when this handler's last statement raises on every path out.

    Deliberately shallow, and shallow in the SAFE direction: anything it
    cannot prove is reported, so a handler joins the pin and someone reads it.

    `with` and `try` were originally followed into their bodies, which was
    unsound the wrong way. A `with` can suppress - `contextlib.suppress` is
    already used twice in `cli.py` - and a `try` has handlers of its own, so
    a trailing `raise` inside either can be swallowed entirely. Measured: a
    handler ending `with contextlib.suppress(Exception): raise ParseError(...)`
    and one ending `try: raise ... except ...: pass` were both classified as
    raising, and both would have dropped out of the pin invisibly. They are
    now reported like any other unprovable tail.
    """
    if not body:
        return False
    last = body[-1]
    if isinstance(last, ast.Raise):
        return True
    if isinstance(last, ast.If):
        return (
            _exits_by_raising(last.body)
            and bool(last.orelse)
            and _exits_by_raising(last.orelse)
        )
    return False


def _enclosing_function(functions: Sequence[_Function], line: int) -> str:
    """The innermost function containing `line`; latest start line wins."""
    best: _Function | None = None
    for function in functions:
        end = function.end_lineno or function.lineno
        if function.lineno <= line <= end:
            if best is None or function.lineno > best.lineno:
                best = function
    return best.name if best is not None else "<module>"


def _sweep() -> dict[tuple[str, str, str], int]:
    found: collections.Counter[tuple[str, str, str]] = collections.Counter()
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        functions: list[_Function] = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if _exits_by_raising(node.body):
                continue
            found[
                (
                    str(path.relative_to(_SRC)),
                    _enclosing_function(functions, node.lineno),
                    ast.unparse(node.type) if node.type else "bare except",
                )
            ] += 1
    return dict(found)


def test_the_sweep_finds_something_to_sweep() -> None:
    """Guard the guard: a walk that finds nothing would pass vacuously."""
    found = _sweep()
    assert len(found) > 30, found
    assert any(module == "cli.py" for module, _, _ in found), sorted(found)


def test_no_new_exception_handler_finishes_without_raising() -> None:
    """PRD 6.6. A handler added here is not necessarily wrong - it is
    necessarily unreviewed, and this is where it gets read."""
    found = _sweep()
    added = {key: count for key, count in found.items() if count > _PINNED.get(key, 0)}
    removed = {
        key: count for key, count in _PINNED.items() if count > found.get(key, 0)
    }
    assert not added, f"new or multiplied silent-capable handlers: {sorted(added)}"
    assert not removed, (
        "handlers disappeared - good, but move the pin in the same commit: "
        f"{sorted(removed)}"
    )


def test_every_diagnostic_is_built_with_an_explicit_code() -> None:
    """`Diagnostic.code` defaults to `M000`, which no table documents.

    Today it is unreachable: all eight construction sites pass a code - seven
    positionally as the fourth argument in `core.py`, and one by keyword in
    `cli.py`'s `_run_check_batch`. That is a fact about the current call
    sites, not a property of the type: `Diagnostic(file, line, column)`
    compiles and would ship an undocumented `M000` at severity "error", which
    is precisely the unnamed refusal this package promises not to produce.

    Pinned here rather than by removing the default, because the default is
    load-bearing for `Diagnostic()` in tests and changing a frozen dataclass's
    signature is a wider change than the hazard warrants. This makes the call
    sites the thing that is checked.

    The first version of this test read `core.py` ALONE while its docstring
    certified "all eight construction sites". The eighth is `cli.py:781`, and
    changing it to drop the code left this test green - the guard was not
    running where the defect could live. It now walks every module under
    `src/pqtools`, matches attribute-style calls (`core.Diagnostic(...)`) as
    well as bare ones, and refuses to count a `*args` splat as four
    positionals.
    """
    codeless = []
    sites = []
    for path in sorted(_SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            called = node.func
            name = (
                called.id
                if isinstance(called, ast.Name)
                else called.attr
                if isinstance(called, ast.Attribute)
                else None
            )
            if name != "Diagnostic":
                continue
            sites.append(f"{path.relative_to(_SRC)}:{node.lineno}")
            by_keyword = any(keyword.arg == "code" for keyword in node.keywords)
            # `file, line, column, code` - the fourth positional IS the code.
            # A `*args` splat is one AST node of unknown length, so it cannot
            # be counted towards four.
            positional = [arg for arg in node.args if not isinstance(arg, ast.Starred)]
            by_position = len(positional) >= 4 and len(positional) == len(node.args)
            if not (by_keyword or by_position):
                codeless.append(f"{path.relative_to(_SRC)}:{node.lineno}")
    # Non-vacuity, for the same reason the handler sweep has one: this test
    # matches on the NAME `Diagnostic`, so renaming the class, moving it, or
    # wrapping construction in a factory yields zero sites and `not codeless`
    # passes while asserting nothing. Measured: renaming all 37 occurrences
    # in `core.py` and `cli.py` left this test reporting `1 passed`.
    assert len(sites) >= 8, (
        f"expected at least the 8 known Diagnostic construction sites, found "
        f"{len(sites)}: {sites} - if the class was renamed or wrapped, this "
        "test is no longer looking at anything"
    )
    assert not codeless, (
        f"Diagnostic built without a code at {codeless} - it would default to "
        "M000, which no documented table lists"
    )


def test_the_docstring_accounts_for_every_pinned_cli_handler() -> None:
    """The prose above has to name the same functions the pin holds.

    Round 50 caught it claiming "the other nine are the batch loops
    `parse`, `dependencies`, `check` and `format`" when only four of the nine
    are those loops - `_container_backup`, `_run_list` (twice), `_run_show`
    and `main` are not batch loops at all. The pin's value is that a reader
    trusts each entry was read before it was pinned, and the docstring was
    pointing that reader at the wrong four.

    A sentence cannot be trusted to stay true as the pin moves, so it is
    checked instead of restated - the same move `test_evidence_captures.py`
    makes for the closeout's counts.
    """
    docstring = __doc__ or ""
    functions = sorted(
        {function for module, function, _ in _PINNED if module == "cli.py"}
    )
    unnamed = [f"`{name}`" for name in functions if f"`{name}`" not in docstring]
    assert not unnamed, (
        f"pinned in cli.py but not named in the module docstring: {unnamed}"
    )

    # The batch-loop claim specifically: exactly the `_run_*_batch` entries.
    batch = sorted(name for name in functions if name.endswith("_batch"))
    assert len(batch) == 4, batch
    assert "**four** are the batch loops" in docstring, (
        "the docstring no longer states how many of the pinned cli.py "
        f"handlers are batch loops; there are {len(batch)}: {batch}"
    )
