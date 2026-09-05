"""Run a Power Query M transformation chain locally, against caller data.

pandas does not run Excel's formulas; it replaces Excel's data connections
with your data, in Python. This module does the same for Power Query: a
real M query is a ``Source = <connector>(...)`` step followed by a chain of
``Table.*`` transformations. The connector step is Microsoft's proprietary
Mashup Engine and is never run here - we are not reimplementing it. But if
the caller supplies the source table (``--bind Source=data.csv`` on the
CLI, or the ``bindings`` argument here), the entire transformation chain
after it runs locally.

A TABLE is a ``list[dict[str, Any]]``: a list of records. A record is a
``dict[str, Any]``. A list is a ``list[Any]``. That is the whole data
model - keep it that simple.

Anything this module does not implement raises :class:`UnsupportedError`
naming the exact construct - never approximated, never guessed at. There is
no ``eval``, ``exec``, dynamic import, or input-driven attribute lookup
anywhere below: the AST is walked with a fixed dispatch table and every
builtin is a fixed, named Python function.
"""

from __future__ import annotations

import dataclasses as _dataclasses
import datetime
from collections.abc import Callable
from typing import Any

from . import core as _core
from .builtins import BUILTINS
from .builtins._shared import (
    EvalError,
    UnsupportedError,
    _m_equal,
    _numeric_quotient,
    _parse_numeric_literal,
    _require_int,
    _type_name,
)
from .builtins._type import _PRIMITIVE_TYPES, _classify, _MType
from .catalog import explain as _explain_documented
from .core import ast as _parse_ast
from .io import DENY_ALL, IOBlockedError, IOPolicy

_MAX_STEPS_DEFAULT = 1_000_000


# --------------------------------------------------------------------------
# Evaluation-wide state
# --------------------------------------------------------------------------


class _Scope:
    """A lexical scope: its own bindings, chained to an enclosing scope.

    ``excluded`` is M's exclusive/inclusive identifier rule, which this
    evaluator ignored until three of Microsoft's own worked examples were
    run against each other and turned out to need it. M's grammar names
    two forms of identifier reference: a plain `x` is EXCLUSIVE - it skips
    the binding currently being defined and resolves outward - and `@x` is
    INCLUSIVE, naming that binding itself. That is why a recursive
    function in M has to be written `@f`, and it is what makes all three
    of these documented results true at once:

        [List.Sum = List.Sum]                       -> the library function
        [x = List.Count([y]), y = [y] & {x}]        -> `x` is the sibling
        [length = length, list = ...length...]      -> the outer parameter

    Reading the first as a cycle, or the second as an unknown identifier,
    are the two ways to get this wrong; both were tried here first.
    """

    __slots__ = ("vars", "parent", "excluded")

    def __init__(
        self,
        parent: _Scope | None,
        variables: dict[str, Any] | None = None,
        excluded: str | None = None,
    ) -> None:
        self.vars: dict[str, Any] = {} if variables is None else variables
        self.parent = parent
        self.excluded = excluded

    def lookup(self, name: str, inclusive: bool = False) -> tuple[bool, Any]:
        scope: _Scope | None = self
        while scope is not None:
            if name in scope.vars and (inclusive or name != scope.excluded):
                return True, scope.vars[name]
            scope = scope.parent
        return False, None

    def child(self) -> _Scope:
        return _Scope(self)

    def excluding(self, name: str) -> _Scope:
        """This scope as seen from inside `name`'s own defining expression.

        `vars` is shared by reference, not copied, so a sibling bound after
        this view is created is still visible through it - which is what
        makes `[a = b, b = 1]` work regardless of the order they appear in.
        """
        return _Scope(self.parent, self.vars, name)


class _Thunk:
    """A lazy, memoised ``let`` binding or record field value.

    Records share this with ``let`` because M gives them the same rule: a
    field's expression can name its siblings, so the fields have to be
    bound before any of them is evaluated. ``name`` exists only so a cycle
    can be reported by the identifier that closes it.
    """

    __slots__ = ("node", "scope", "value", "done", "active", "name")

    def __init__(self, node: dict[str, Any], scope: _Scope, name: str = "") -> None:
        self.node = node
        self.scope = scope
        self.value: Any = None
        self.done = False
        self.active = False
        self.name = name


class _Lambda:
    """A closure created by ``each ...`` or ``(params) => ...``."""

    __slots__ = (
        "params",
        "body",
        "scope",
        "param_types",
        "return_type",
        "optionals",
    )

    def __init__(
        self,
        params: list[str],
        body: dict[str, Any],
        scope: _Scope,
        param_types: list[str | None] | None = None,
        return_type: str | None = None,
        optionals: list[bool] | None = None,
    ) -> None:
        self.params = params
        self.body = body
        self.scope = scope
        # `(x, optional y) => ...` may be called with one argument; the
        # missing one arrives as null. Every parameter was treated as
        # required, so a perfectly ordinary custom function - the shape the
        # Power Query UI writes whenever an author adds a default - failed
        # at the call site with an arity error.
        self.optionals = optionals or [False] * len(params)
        # Declared types are enforced, not decorative. M raises when an
        # argument does not match, and a query that relies on that error is
        # relying on a real guarantee - accepting anything here would turn a
        # caught type error into a wrong result further down the chain.
        self.param_types = param_types or [None] * len(params)
        self.return_type = return_type


class _Budget:
    """Bounds the total number of AST nodes visited in one evaluation."""

    __slots__ = ("remaining",)

    def __init__(self, max_steps: int) -> None:
        self.remaining = max_steps

    def tick(self) -> None:
        if self.remaining <= 0:
            raise EvalError(f"evaluation exceeded max_steps ({self.remaining!r} left)")
        self.remaining -= 1


class _Ctx:
    """Evaluation-wide, read-mostly state threaded through every call.

    ``invoke`` is dependency-injected here rather than the builtins package
    importing a module-level ``_invoke``. Builtins that take an M lambda
    (``Table.SelectRows``, ``List.Transform``, ``Table.AddColumn``, ...)
    need to call back into this module's ``_invoke`` to run it, but
    ``_invoke`` is evaluator-core code that lives in this module, and this
    module imports the builtin registry (``BUILTINS``) from
    ``pqtools.builtins`` - a straight import the other way would be a
    circular import (``evaluate`` -> ``builtins`` -> a family module ->
    ``evaluate``). Threading ``_invoke`` through ``_Ctx.invoke`` instead
    means ``pqtools.builtins`` never has to import ``pqtools.evaluate`` at
    all: a builtin calls ``ctx.invoke(callee, args, ctx)`` where it used to
    call the bare ``_invoke(callee, args, ctx)``.
    """

    __slots__ = ("bindings", "budget", "invoke", "io")

    def __init__(
        self,
        bindings: dict[str, Any],
        budget: _Budget,
        invoke: Callable[[Any, list[Any], _Ctx], Any],
        io: IOPolicy = DENY_ALL,
    ) -> None:
        self.bindings = bindings
        self.budget = budget
        self.invoke = invoke
        self.io = io


# Re-exported from _shared so callers have one import site for the evaluator's
# public surface; declared here so the re-export is explicit to type checkers.
__all__ = ["BUILTINS", "EvalError", "UnsupportedError", "evaluate"]


def evaluate(
    source: str,
    *,
    bindings: dict[str, Any] | None = None,
    max_steps: int = _MAX_STEPS_DEFAULT,
    io: IOPolicy = DENY_ALL,
) -> Any:
    """Evaluate an M transformation chain against caller-supplied data.

    `source` is parsed with the pinned Microsoft parser
    (:func:`pqtools.core.ast`) and the resulting tree is walked directly -
    never ``eval``/``exec``.

    `bindings` pre-populates names in the top-level scope. Precisely: if a
    ``let`` binding's name (at ANY nesting depth) is a key in `bindings`,
    the supplied value is used and the binding's right-hand-side expression
    is never evaluated at all. That is what lets
    ``--bind Source=data.csv`` replace a
    ``Source = Csv.Document(File.Contents(...))`` connector step without
    ever calling the connector.

    `max_steps` bounds the total number of AST nodes visited (including
    every iteration of every ``List.Transform``/``Table.SelectRows``/...
    callback), so a runaway or hostile query cannot hang the caller -
    :class:`EvalError` is raised instead once the budget is spent.
    """
    tree = _parse_ast(source)
    resolved_bindings = dict(bindings) if bindings else {}
    scope = _Scope(None)
    # Pre-populate the root scope directly too, not only `let` processing:
    # a query that references a bound name without ever binding it itself
    # (e.g. `Table.RowCount(Source)` with no enclosing `let Source = ...`)
    # must still resolve it.
    scope.vars.update(resolved_bindings)
    ctx = _Ctx(resolved_bindings, _Budget(max_steps), _invoke, io)
    return _eval(tree, scope, ctx)


# --------------------------------------------------------------------------
# AST navigation helpers
#
# js/bridge.js's `astView` prunes the parser's raw tree to {kind, value,
# literalKind, identifierContextKind, handlerKind, line, column, children}.
# Every "wrapper" node the real M grammar uses (ArrayWrapper, Csv,
# IdentifierPairedExpression, GeneralizedIdentifierPairedExpression, ...)
# survives that prune unchanged - only parser bookkeeping (id,
# attributeIndex, tokenRange, isLeaf) is dropped. These helpers walk that
# exact shape; see js/bridge.test.js's `astView` cases for the shapes they
# assume.
# --------------------------------------------------------------------------


def _children(node: dict[str, Any]) -> list[dict[str, Any]]:
    children: list[dict[str, Any]] = node.get("children", [])
    return children


def _semantic(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Real (non-keyword/punctuation) children, in source order."""
    return [child for child in _children(node) if child.get("kind") != "Constant"]


def _has_optional(node: dict[str, Any]) -> bool:
    """True if `node` carries a trailing ``?`` (optional field/item access)."""
    return any(
        child.get("kind") == "Constant" and child.get("value") == "?"
        for child in _children(node)
    )


def _identifier_text(node: dict[str, Any]) -> str:
    """The name held by an ``Identifier`` or ``GeneralizedIdentifier`` node.

    M's quoted-identifier form ``#"First Name"`` *is* the identifier
    ``First Name`` - the ``#"..."`` is syntax, not part of the name. The
    parser hands the raw token through, so it is unquoted here, at the single
    place every identifier is read.

    This was wrong until 0.6.1 and the failure was quiet in the worst way:
    step names round-tripped fine (``#"Changed Type"`` was defined and
    referenced with the same raw text, so it matched itself), while any
    reference to a real *column* whose name has a space - ``[#"First Name"]``
    against a header promoted from a CSV - looked up ``#"First Name"`` and
    reported "field not found". Column names with spaces are everywhere in
    real queries, so this hit nearly every one of them.
    """
    return _core.unquote_identifier(str(node["value"]))


def _binop_parts(node: dict[str, Any]) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """``[left, operatorConstant, right]`` for a binary-operator node."""
    left, operator, right = _children(node)
    return left, str(operator["value"]), right


# --------------------------------------------------------------------------
# Literals
# --------------------------------------------------------------------------


def _parse_text_literal(token: str) -> str:
    """Decode an M text literal, escapes included.

    Until 0.9.0 this returned the body with only `""` collapsed, so `#(lf)`
    arrived downstream as the six literal characters `#(lf)`. That is not a
    cosmetic gap: `Csv.Document(text, [Delimiter=","], "#(lf)")` and
    `Text.Split(x, "#(lf)")` are how M queries name a line feed, so the split
    silently never matched and the query returned one long row instead of
    failing. Same shape as the `#table` gap - an idiom every real query uses
    and no fixture did.

    The decoding itself moved to `core.decode_escapes` once it turned out
    that quoted IDENTIFIERS carry the same escapes and were not decoding
    them - see `core.unquote_identifier`. Two decoders, one of which did
    nothing, is the shape of bug this file keeps finding.
    """
    if len(token) < 2 or token[0] != '"' or token[-1] != '"':
        raise EvalError("malformed text literal")
    try:
        return _core.decode_escapes(token[1:-1], f"text literal {token}")
    except _core.ParseError as error:
        raise EvalError(str(error)) from error


def _eval_literal(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    kind = node["literalKind"]
    text = str(node["value"])
    if kind == "Numeric":
        try:
            return _parse_numeric_literal(text)
        except ValueError as error:
            raise EvalError(f"malformed numeric literal: {text}") from error
    if kind == "Text":
        return _parse_text_literal(text)
    if kind == "Logical":
        return text == "true"
    if kind == "Null":
        return None
    raise UnsupportedError(f"literal kind: {kind}")


# --------------------------------------------------------------------------
# Structural evaluation - let, if, record, list, function, invocation, try
# --------------------------------------------------------------------------


def _force(value: Any, ctx: _Ctx) -> Any:
    if not isinstance(value, _Thunk):
        return value
    if value.done:
        return value.value
    if value.active:
        where = f" in {value.name!r}" if value.name else ""
        raise EvalError(f"circular reference{where}")
    value.active = True
    try:
        value.value = _eval(value.node, value.scope, ctx)
    finally:
        # Cleared even when evaluation raises. Without the `finally` a
        # binding that failed once stayed marked in-progress forever, so the
        # NEXT reference to it reported "circular reference in let binding" -
        # a diagnosis with nothing to do with the actual problem, in a query
        # containing no cycle at all. Real Power BI queries hit this: an
        # "Enter Data" step binds one `_t` and names it in every column, so
        # a single unsupported binding turned into a phantom cycle on the
        # second column.
        value.active = False
    value.done = True
    return value.value


def _eval_let(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    array_wrapper, body = _semantic(node)
    child_scope = scope.child()
    for csv in _children(array_wrapper):
        (pair,) = _semantic(csv)
        key_node, value_node = _semantic(pair)
        name = _identifier_text(key_node)
        if name in ctx.bindings:
            child_scope.vars[name] = ctx.bindings[name]
        else:
            child_scope.vars[name] = _Thunk(
                value_node, child_scope.excluding(name), name
            )
    return _eval(body, child_scope, ctx)


def _eval_if(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    condition_node, true_node, false_node = _semantic(node)
    condition = _eval(condition_node, scope, ctx)
    if not isinstance(condition, bool):
        raise EvalError(f"if condition must be logical, got {_type_name(condition)}")
    return _eval(true_node if condition else false_node, scope, ctx)


def _eval_record(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    """`[a = 1, b = a + 1]` - a field expression can name its siblings.

    This used to evaluate each field in the ENCLOSING scope, so `b = a + 1`
    reported "unknown identifier: a". The behaviour is not a corner: it is
    what Microsoft's own `List.Generate` example is built on -

        each [x = List.Count([y]), y = [y] & {x}]

    where the bare `x` in the second field is the first field, and nothing
    else in scope is called `x`. That example ran here as an unknown
    identifier until the record grew a scope of its own.

    Records get `let`'s exact machinery rather than a second one: the same
    lazy `_Thunk`, so order does not matter (`[a = b, b = 1]` works), the
    same cycle detection, so `[a = b, b = a]` is an error rather than a
    hang, and the same exclusive-identifier rule, so `[List.Sum = List.Sum]`
    still names the library function rather than reporting a cycle.
    """
    (array_wrapper,) = _semantic(node)
    child_scope = scope.child()
    record: dict[str, Any] = {}
    for csv in _children(array_wrapper):
        (pair,) = _semantic(csv)
        name_node, value_node = _semantic(pair)
        name = _identifier_text(name_node)
        thunk = _Thunk(value_node, child_scope.excluding(name), name)
        child_scope.vars[name] = thunk
        record[name] = thunk
    # Forced here, not left lazy: a record is a value, and handing a caller
    # a half-evaluated one would leak thunks into every builtin.
    return {name: _force(value, ctx) for name, value in record.items()}


def _eval_list(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    (array_wrapper,) = _semantic(node)
    items: list[Any] = []
    for csv in _children(array_wrapper):
        element = _semantic(csv)[0]
        if element["kind"] == "RangeExpression":
            # `{1..5}` contributes five items, not one.
            items.extend(_range_values(element, scope, ctx))
        else:
            items.append(_eval(element, scope, ctx))
    return items


def _ascribed_type_name(node: dict[str, Any]) -> str | None:
    """The primitive type named by an `as <type>` clause, nullability kept.

    The parser models `as nullable number` as
    ``AsNullablePrimitiveType -> NullablePrimitiveType{Constant 'nullable',
    PrimitiveType 'number'}``, and this used to return None the moment it
    saw that Constant - "declared but not checked", on the reasoning that
    nullable "only adds null" and the checker should stay out of the way.

    That reasoning dropped the half of the declaration that does work.
    `nullable number` is number OR null, not "anything": M rejects
    `f("oops")` there, and so did every other ascription here, so
    `(x as nullable number) => x` was the one spelling that quietly
    accepted text. The name is now returned with a `nullable ` prefix and
    `_check_ascription` reads both halves.

    None still means "no check", and it is still the answer for a type this
    evaluator does not model. A parameter list is not the place to lose a
    query over a type the checker cannot yet read.
    """
    nullable = any(
        str(candidate.get("value", "")) == "nullable"
        for candidate in _descendants(node, "Constant")
    )
    for candidate in _descendants(node, "PrimitiveType"):
        name = str(candidate.get("value", ""))
        if name in _PRIMITIVE_TYPES:
            return f"nullable {name}" if nullable else name
    if node.get("kind") == "PrimitiveType":
        name = str(node.get("value", ""))
        if name in _PRIMITIVE_TYPES:
            return f"nullable {name}" if nullable else name
    return None


def _check_ascription(value: Any, declared: str | None, what: str) -> Any:
    """Enforce an `as <type>` declaration, as M does at call time.

    M raises when an argument does not match its declared type, and a query
    can legitimately rely on that error. Accepting anything here would turn a
    type error the author expected to catch into a wrong value further down.

    Three of the eighteen primitive type names need their own answer, and
    all three used to get the wrong one:

    - `nullable T` accepted ANYTHING, because it arrived here as None. It
      is T-or-null.
    - `none` accepted anything, because it was grouped with `any`. The two
      are opposites: `any` holds every value and `none` holds no value at
      all, so `(x as none) => x` can never be called successfully.
    - `null` REJECTED null, because the null branch fired before the
      declared name was read - `(x as null) => x` called with null said
      "expected null, got null".
    """
    if declared is None:
        return value
    nullable = declared.startswith("nullable ")
    base = declared[len("nullable ") :] if nullable else declared

    if value is None:
        if nullable or base in {"any", "null"}:
            return value
        raise EvalError(f"{what}: expected {declared}, got null")
    # Every path below has a non-null value in hand.
    if base in {"any", "anynonnull"}:
        return value
    if base == "none":
        raise EvalError(f"{what}: expected {declared}, and no value has type none")
    if base == "null":
        raise EvalError(
            f"{what}: expected {declared}, got {_classify(value) or 'a value'}"
        )
    actual = _classify(value)
    if actual is None:
        # Lists, records, tables and functions share one runtime shape here,
        # so there is nothing to check against. Silence beats a wrong verdict.
        return value
    if actual != base:
        raise EvalError(f"{what}: expected {declared}, got {actual}")
    return value


def _eval_function(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    parts = _semantic(node)
    return_type: str | None = None
    if len(parts) == 3:
        parameter_list, return_type_node, body = parts
        return_type = _ascribed_type_name(return_type_node)
    else:
        parameter_list, body = parts
    (params_wrapper,) = _semantic(parameter_list)
    names: list[str] = []
    types: list[str | None] = []
    optionals: list[bool] = []
    for csv in _children(params_wrapper):
        (parameter,) = _semantic(csv)
        parameter_children = _semantic(parameter)
        names.append(_identifier_text(parameter_children[0]))
        types.append(
            _ascribed_type_name(parameter_children[1])
            if len(parameter_children) > 1
            else None
        )
        # `optional` is a Constant sibling, which `_semantic` filters out -
        # which is exactly why it went unnoticed: the name and the declared
        # type both read correctly and only the flag was lost.
        optionals.append(
            any(
                child.get("kind") == "Constant" and child.get("value") == "optional"
                for child in _children(parameter)
            )
        )
    return _Lambda(names, body, scope, types, return_type, optionals)


def _eval_each(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    (body,) = _semantic(node)
    return _Lambda(["_"], body, scope)


def _eval_parenthesized(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    (inner,) = _semantic(node)
    return _eval(inner, scope, ctx)


def _field_specification_list(
    list_node: dict[str, Any], scope: _Scope, ctx: _Ctx, what: str
) -> tuple[tuple[str, ...], tuple[Any, ...] | None, tuple[bool, ...] | None, bool]:
    """The fields of a ``[A = text, optional B = number, ...]`` block.

    Returns (names, types, optional flags, is_open).

    Read from DIRECT children only. The previous version searched every
    descendant for a `FieldSpecification`, which silently flattened nested
    field types into the outer list: `type table [A = table [C = text]]`
    produced a TWO-column table, `A` and a phantom `C`. Nothing errored -
    `Table.ColumnNames` on it simply returned a column the query never
    declared, which is the worst way for a type system to be wrong.
    """
    names: list[str] = []
    types: list[Any] = []
    optionals: list[bool] = []
    is_open = False
    # Set when any field's declared type could not be modelled - see the
    # UnsupportedError branch below. The whole tuple is then dropped rather
    # than half-filled, because `field_types = None` is the state the
    # Type.* introspection functions already read as "not captured" and
    # refuse on; a tuple with a silent `any` in it would be a wrong answer.
    unknown = False
    for child in _children(list_node):
        # An open record is spelled `[A = text, ...]`; the marker is a
        # sibling of the field list, not a field.
        if child.get("kind") == "Constant" and str(child.get("value")) == "...":
            is_open = True
            continue
        if child.get("kind") != "ArrayWrapper":
            continue
        for csv in _children(child):
            for spec in _children(csv):
                if spec.get("kind") != "FieldSpecification":
                    continue
                name = None
                declared: Any = _PRIMITIVE_TYPES["any"]
                optional = False
                for part in _children(spec):
                    kind = part.get("kind")
                    if kind == "Constant" and str(part.get("value")) == "optional":
                        optional = True
                    elif kind == "GeneralizedIdentifier":
                        name = _identifier_text(part)
                    elif kind == "FieldTypeSpecification":
                        try:
                            declared = _type_value(
                                _type_operand(part), scope, ctx, what
                            )
                        except UnsupportedError:
                            # A field type this evaluator cannot model does
                            # not make the FIELD unknown - the name is right
                            # there. Real Power BI writes exactly this:
                            #
                            #   let _t = ((type text) meta [Serialized.Text
                            #             = true])
                            #   in  type table [ID = _t, ...]
                            #
                            # `meta` is refused here on purpose (there is no
                            # value wrapper for it), so the whole "Enter
                            # Data" query would fail on its first step if one
                            # unmodellable field type were fatal. Degrade to
                            # what was true before field types were read at
                            # all: names certain, types not captured. An
                            # EvalError - a genuinely wrong query, such as a
                            # misspelled type name - still propagates.
                            unknown = True
                            declared = _PRIMITIVE_TYPES["any"]
                if name is None:
                    continue
                names.append(name)
                # A field written without a type (`type [a]`) is `any` - the
                # grammar allows it and M's own default for an undeclared
                # field type is any, not "unknown".
                types.append(declared)
                optionals.append(optional)
    if unknown:
        return tuple(names), None, None, is_open
    return tuple(names), tuple(types), tuple(optionals), is_open


def _type_operand(node: dict[str, Any]) -> dict[str, Any]:
    """The one meaningful child of a wrapper node, skipping punctuation."""
    for child in _children(node):
        if child.get("kind") != "Constant":
            return child
    raise UnsupportedError(f"malformed type expression: {node.get('kind')}")


def _table_type_value(type_node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    """``type table [Name = ..., ...]`` or ``type table <rowType>``.

    This shape is not exotic: it is the second argument of the
    ``Table.FromRows(Json.Document(Binary.Decompress(...)), type table [...])``
    that Power BI writes for every "Enter Data" table, so refusing it stopped
    those queries on their first step.

    The named form - ``type table rowType``, where the row shape is a record
    type held in a variable - used to fall through to "no field
    specifications found" and produce a table type with ZERO columns. Not an
    error: a silently empty schema, which is how Type.ForRecord's own
    documented example failed.
    """
    for child in _children(type_node):
        kind = child.get("kind")
        if kind == "FieldSpecificationList":
            names, types, optionals, _ = _field_specification_list(
                child, scope, ctx, "type table"
            )
            return _MType(
                kind="table",
                display="type table",
                field_names=names,
                field_types=types,
                field_optional=optionals,
            )
        if kind == "Constant":
            continue
        # `type table <expression>`: the row type is named rather than spelled
        # out, so evaluate it and take its fields.
        row = _eval(child, scope, ctx)
        if not isinstance(row, _MType) or row.field_names is None:
            raise EvalError(
                "type table <name>: expected a record type naming its fields, "
                f"got {_type_name(row)}"
            )
        return _MType(
            kind="table",
            display="type table",
            field_names=row.field_names,
            field_types=row.field_types,
            field_optional=row.field_optional,
        )
    raise UnsupportedError("type table: no field list and no row type")


def _descendants(node: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    """Every descendant of ``node`` with the given kind, in document order."""
    found: list[dict[str, Any]] = []
    stack = [node]
    while stack:
        current = stack.pop(0)
        for child in _children(current):
            if child.get("kind") == kind:
                found.append(child)
            stack.append(child)
    return found


def _function_type_value(
    type_node: dict[str, Any], scope: _Scope, ctx: _Ctx, what: str
) -> Any:
    """``type function (a as number, optional b as text) as number``.

    Refused outright before, which is why Function.From's and
    Function.ScalarVector's own documented examples could not run: the
    failure was one AST node above the function under test, so implementing
    those functions correctly did not help.

    `Type.ForFunction` builds the same value from M-level arguments, so this
    reuses its display formatter rather than inventing a second spelling.
    """
    from .builtins._type import _function_type_display

    parameters: list[tuple[str, Any]] = []
    min_arity = 0
    return_type: Any = _PRIMITIVE_TYPES["any"]
    for child in _children(type_node):
        kind = child.get("kind")
        if kind == "ParameterList":
            # ParameterList -> ArrayWrapper -> Csv -> Parameter. The
            # ArrayWrapper level is easy to skip and the symptom is quiet:
            # zero parameters and a plausible-looking function type.
            for wrapper in _children(child):
                if wrapper.get("kind") != "ArrayWrapper":
                    continue
                for csv in _children(wrapper):
                    for parameter in _children(csv):
                        if parameter.get("kind") != "Parameter":
                            continue
                        name = ""
                        declared: Any = _PRIMITIVE_TYPES["any"]
                        optional = False
                        for part in _children(parameter):
                            part_kind = part.get("kind")
                            if (
                                part_kind == "Constant"
                                and part.get("value") == "optional"
                            ):
                                optional = True
                            elif part_kind == "Identifier":
                                name = _identifier_text(part)
                            elif part_kind == "AsType":
                                declared = _type_value(
                                    _type_operand(part), scope, ctx, what
                                )
                        parameters.append((name, declared))
                        if not optional:
                            # "min" is Type.ForFunction's own name for the
                            # count of REQUIRED parameters, so they agree.
                            min_arity += 1
        elif kind == "AsType":
            # The trailing `as T`, a sibling of the parameter list.
            return_type = _type_value(_type_operand(child), scope, ctx, what)
    return _MType(
        kind="function",
        display=_function_type_display(parameters, return_type),
        parameters=tuple(parameters),
        min_arity=min_arity,
        return_type=return_type,
    )


def _type_value(type_node: dict[str, Any], scope: _Scope, ctx: _Ctx, what: str) -> Any:
    """One M type expression -> one type value.

    Recursive, because type expressions nest: a table type's field can be a
    record type whose field is `nullable text`. Reading only the outermost
    layer is what let a nested field's names leak upward - see
    `_field_specification_list`.
    """
    kind = type_node.get("kind")
    if kind == "PrimitiveType":
        name = str(type_node["value"])
        type_value = _PRIMITIVE_TYPES.get(name)
        if type_value is None:
            raise UnsupportedError(f"type value: type {name}")
        return type_value
    if kind == "TableType":
        return _table_type_value(type_node, scope, ctx)
    if kind == "RecordType":
        names, types, optionals, is_open = _field_specification_list(
            _type_operand(type_node), scope, ctx, what
        )
        return _MType(
            kind="record",
            display="type record",
            field_names=names,
            field_types=types,
            field_optional=optionals,
            is_open=is_open,
        )
    if kind == "FunctionType":
        return _function_type_value(type_node, scope, ctx, what)
    if kind == "NullableType":
        base = _type_value(_type_operand(type_node), scope, ctx, what)
        if base.is_nullable:
            return base
        return _dataclasses.replace(
            base,
            display=f"type nullable {base.display.removeprefix('type ')}",
            is_nullable=True,
        )
    # Anything else is an ordinary expression standing in a type position -
    # which is not exotic, it is what Power BI itself writes. Every "Enter
    # Data" and "Changed Type" step spells its columns `type table [Sales =
    # Int64.Type, Name = text]`, mixing ascribable type VALUES (registered
    # builtins, parsed as identifiers) with grammar keywords in one list.
    # Reading only the keywords is what broke the real-workbook end-to-end
    # test the moment field types started being read at all.
    resolved = _eval(type_node, scope, ctx)
    if isinstance(resolved, _MType):
        return resolved
    raise UnsupportedError(
        f"{what}: {kind} is not a type shape pqtools models, and it does not "
        f"evaluate to a type value (got {_type_name(resolved)}). Supported: "
        "the primitive types, `type table [...]`, `type table <rowType>`, "
        "`type [...]` records, `nullable` over any of those, and any "
        "expression naming a type value such as Int64.Type"
    )


def _eval_type_primary(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    # `type text`, `type number`, ... - the AST wraps a leaf `PrimitiveType`
    # node (whose `value` is the exact lowercase keyword) in `TypePrimaryType`.
    (type_node,) = _semantic(node)
    return _type_value(type_node, scope, ctx, "type value")


def _eval_field_selector(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    # `[field]` on its own (not chained after another expression) is M's
    # "implicit target" shorthand for `_[field]` - only meaningful inside
    # an `each` lambda, where `_` names the current row/record.
    found, value = scope.lookup("_")
    if not found:
        raise EvalError("[field] shorthand used outside of an each expression")
    (name_node,) = _semantic(node)
    return _record_field_access(
        _force(value, ctx), _identifier_text(name_node), _has_optional(node)
    )


def _handler_takes_the_error(function: Any) -> bool:
    """Whether a `try ... catch` handler wants the error record passed in.

    Read off the closure's parameter list, never off a failed call. A
    builtin (or any other plain callable) is given the record, because it
    does its own arity check and reports it under its own name.
    """
    if not isinstance(function, _Lambda):
        return True
    required = sum(1 for optional in function.optionals if not optional)
    return required <= 1 <= len(function.params)


def _invoke(callee: Any, args: list[Any], ctx: _Ctx) -> Any:
    if isinstance(callee, _Lambda):
        required = sum(1 for flag in callee.optionals if not flag)
        total = len(callee.params)
        if not required <= len(args) <= total:
            expected = (
                f"{required}"
                if required == total
                else f"between {required} and {total}"
            )
            raise EvalError(f"function expects {expected} argument(s), got {len(args)}")
        # An omitted optional parameter is null inside the body, which is
        # how M spells "not supplied" - there is no separate missing-value
        # sentinel to distinguish it from an explicit null.
        supplied = list(args) + [None] * (total - len(args))
        child = callee.scope.child()
        for name, value, declared, is_optional in zip(
            callee.params, supplied, callee.param_types, callee.optionals, strict=True
        ):
            # `optional y as number` declares a NULLABLE number: the whole
            # point of the parameter is that it may be absent, so the
            # ascription must not reject the absence it exists to allow.
            if is_optional and value is None:
                declared = None
            child.vars[name] = _check_ascription(value, declared, f"argument {name!r}")
        return _check_ascription(
            _eval(callee.body, child, ctx), callee.return_type, "return value"
        )
    if callable(callee):
        result: Any = callee(args, ctx)
        return result
    raise EvalError(f"{_type_name(callee)} value is not a function")


def _match_row(base: list[Any], key: dict[str, Any], optional: bool) -> Any:
    """``table{[Field=value, ...]}`` - select the one row matching every field.

    This is how Power Query's own generated M navigates, and it is not a
    corner case: every query the Excel and Power BI UI writes against a
    workbook opens with

        Source = Excel.Workbook(File.Contents(path), null, true),
        Sheet  = Source{[Item="Colors", Kind="Sheet"]}[Data]

    Until now the selector reached `_require_int` and the query died with
    "expected a number, got record" - an error about the wrong thing, on the
    second line of nearly every real workbook query in existence. The suite
    missed it because fixtures written by hand reach for Table.SelectRows.

    A key matching several rows is an error even under `?`. `?` means "this
    key may be absent", not "pick one of the matches for me"; guessing there
    would return a different row as the data grows.
    """
    matches = [
        row
        for row in base
        if isinstance(row, dict)
        and all(
            name in row and _m_equal(row[name], value) for name, value in key.items()
        )
    ]
    if len(matches) == 1:
        return matches[0]
    rendered = ", ".join(f"{name}={value!r}" for name, value in key.items())
    if not matches:
        if optional:
            return None
        raise EvalError(f"no row matches [{rendered}]")
    raise EvalError(f"{len(matches)} rows match [{rendered}], expected exactly one")


def _list_index(base: Any, index: Any, optional: bool) -> Any:
    if not isinstance(base, list):
        if optional:
            return None
        raise EvalError(f"cannot index into a {_type_name(base)} value")
    if isinstance(index, dict):
        return _match_row(base, index, optional)
    position = _require_int(index)
    if -len(base) <= position < len(base):
        return base[position]
    if optional:
        return None
    raise EvalError(f"list index {position} is out of range")


def _record_field_access(base: Any, name: str, optional: bool) -> Any:
    # M's [Name] is defined on records AND on tables. On a table it projects the
    # column, yielding the list of that column's values - which is what makes
    # `each List.Sum([Amount])` work as a Table.Group aggregation, the single most
    # common form Power Query's UI writes. Tables are list[dict] here, so the table
    # case is checked first and a non-table list still falls through to the error.
    if isinstance(base, list) and all(isinstance(row, dict) for row in base):
        if not base:
            # An empty table has no columns to disprove, so an unknown column is
            # indistinguishable from an empty one. Real PQ keeps the schema and
            # would return an empty list; matching that beats erroring on a filter
            # that legitimately removed every row.
            return []
        if all(name in row for row in base):
            return [row[name] for row in base]
        if optional:
            return None
        raise EvalError(f"column not found in every row: {name}")
    if not isinstance(base, dict):
        if optional:
            return None
        raise EvalError(f"cannot select a field from a {_type_name(base)} value")
    if name in base:
        return base[name]
    if optional:
        return None
    raise EvalError(f"field not found: {name}")


def _eval_recursive(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    head, steps_wrapper = _children(node)
    base = _eval(head, scope, ctx)
    for step in _children(steps_wrapper):
        step_kind = step["kind"]
        if step_kind == "InvokeExpression":
            (args_wrapper,) = _semantic(step)
            args = [
                _eval(_semantic(csv)[0], scope, ctx) for csv in _children(args_wrapper)
            ]
            base = _invoke(base, args, ctx)
        elif step_kind == "ItemAccessExpression":
            (index_node,) = _semantic(step)
            base = _list_index(base, _eval(index_node, scope, ctx), _has_optional(step))
        elif step_kind == "FieldProjection":
            base = _project_fields(base, step, _has_optional(step))
        elif step_kind == "FieldSelector":
            (name_node,) = _semantic(step)
            base = _record_field_access(
                base, _identifier_text(name_node), _has_optional(step)
            )
        else:
            raise UnsupportedError(
                _SIMPLE_UNSUPPORTED.get(
                    step_kind, f"unsupported construct: {step_kind}"
                )
            )
    return base


def _error_record(error: EvalError) -> dict[str, Any]:
    """M's error record: what `try` exposes and what `catch` receives.

    Power Query builds this from the failing operation. The fields are fixed
    by the language (Reason/Message/Detail), so code that reads
    ``[Message]`` off a caught error keeps working.
    """
    supplied = getattr(error, "m_error_record", None)
    if isinstance(supplied, dict):
        # `error [Reason = "R", ...]` named its own fields; handing back the
        # generic shape would lose exactly what the author raised.
        return supplied
    return {
        "Reason": "Expression.Error",
        "Message": str(error),
        "Detail": None,
    }


def _eval_try(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    handler_kind = node.get("handlerKind")
    parts = _semantic(node)

    if len(parts) == 1:
        # Bare `try x` is a value, not a statement: a record saying whether
        # the expression failed. Refusing it used to force every caller into
        # `try x otherwise <sentinel>`, which cannot distinguish a real
        # sentinel value in the data from a failure.
        try:
            return {"HasError": False, "Value": _eval(parts[0], scope, ctx)}
        except (UnsupportedError, IOBlockedError):
            raise
        except EvalError as error:
            return {"HasError": True, "Error": _error_record(error)}

    protected, handler = parts
    if handler_kind not in {"Otherwise", "Catch"}:
        raise UnsupportedError(f"try handler: {handler_kind}")

    try:
        return _eval(protected, scope, ctx)
    except (UnsupportedError, IOBlockedError):
        # Neither is a data error, so neither may be swallowed. `try ...
        # otherwise 0` around a blocked Web.Contents would hand back 0 and
        # never mention that the connector was refused - the user would read
        # a policy decision as a value. IOBlockedError does not derive from
        # EvalError today, so this is belt and braces, but the property is
        # too important to leave resting on a base-class choice.
        raise
    except EvalError as error:
        if handler_kind == "Otherwise":
            (otherwise_expr,) = _semantic(handler)
            return _eval(otherwise_expr, scope, ctx)
        # Catch: the handler is a function of the error record. M allows the
        # zero-argument form too, for a handler that ignores the detail.
        #
        # Which form it is comes from the function's own signature. It used
        # to come from re-running the handler and searching the failure text
        # for "argument", which is a guess that fails in the worst possible
        # direction: a perfectly good one-argument handler whose own body
        # raises `argument 'x': expected number, got text` was then called
        # AGAIN with no arguments, and the caller was told "function expects
        # 1 argument(s), got 0" - the real error replaced by one about the
        # machinery that hid it. Verified before the fix, and pinned in
        # tests/test_language_features.py.
        (function_node,) = _semantic(handler)
        function = _eval(function_node, scope, ctx)
        record = _error_record(error)
        arguments = [record] if _handler_takes_the_error(function) else []
        return ctx.invoke(function, arguments, ctx)


# --------------------------------------------------------------------------
# Operators
#
# The operand tables below are Microsoft's own, transcribed from the M
# specification (learn.microsoft.com/en-us/powerquery-m/m-spec-operators)
# rather than inferred from what a query "probably" means.
#
# Until 0.10.0 every arithmetic operator called ``_require_number`` on both
# sides, so the language outside numbers was simply not here. Three of the
# consequences were ordinary queries, not corner cases:
#
#     [Amount] + [Fee]         raised the moment either cell was blank
#     DateTime.LocalNow() + #duration(0,1,0,0)
#                              could not run at all, though the reference
#                              writes half its temporal examples that way
#     [Total] / [Count]        raised on a zero count, where Power Query
#                              returns #infinity
#
# Each of those runs in Power Query and failed here, which is the direction
# of divergence this package exists to prevent.
# --------------------------------------------------------------------------

# A `time` has no date to carry into, so time arithmetic is done against a
# fixed anchor and the anchor must still be there afterwards.
_TIME_ANCHOR = datetime.date(2000, 1, 1)


def _is_number(value: Any) -> bool:
    # `bool` subclasses `int` in Python. A logical is not a number in M.
    return not isinstance(value, bool) and isinstance(value, (int, float))


def _is_duration(value: Any) -> bool:
    return isinstance(value, datetime.timedelta)


def _is_temporal(value: Any) -> bool:
    """The spec's "type datetime": date, datetime, datetimezone or time."""
    return isinstance(value, (datetime.date, datetime.time))


def _same_temporal_kind(left: Any, right: Any) -> bool:
    """Both values are the SAME one of date/datetime/datetimezone/time/duration.

    datetime is tested first because ``datetime.datetime`` subclasses
    ``datetime.date``, and an aware/naive pair is a datetimezone-vs-datetime
    mismatch that Python itself refuses to order.
    """
    if isinstance(left, datetime.datetime) or isinstance(right, datetime.datetime):
        return (
            isinstance(left, datetime.datetime)
            and isinstance(right, datetime.datetime)
            and (left.tzinfo is None) == (right.tzinfo is None)
        )
    return any(
        isinstance(left, kind) and isinstance(right, kind)
        for kind in (datetime.date, datetime.time, datetime.timedelta)
    )


def _time_as_duration(value: datetime.time) -> datetime.timedelta:
    return datetime.timedelta(
        hours=value.hour,
        minutes=value.minute,
        seconds=value.second,
        microseconds=value.microsecond,
    )


def _shift_temporal(value: Any, delta: datetime.timedelta) -> Any:
    """Offset a date/datetime/datetimezone/time, keeping its own type.

    "When adding a duration and a value of some type datetime, the resulting
    value is of that same type." For a `date` that means the sub-day part of
    the duration is dropped, which is what Python's `date + timedelta`
    already does.
    """
    if isinstance(value, datetime.time):
        moved = datetime.datetime.combine(_TIME_ANCHOR, value) + delta
        if moved.date() != _TIME_ANCHOR:
            raise EvalError(
                f"time arithmetic left the day: {value} offset by {delta} is "
                "not a time (M's time has no date to carry into)"
            )
        return moved.time()
    return value + delta


def _operand_error(operator: str, left: Any, right: Any) -> EvalError:
    return EvalError(
        f"operator {operator} is not defined for {_type_name(left)} and "
        f"{_type_name(right)}"
    )


def _op_add(left: Any, right: Any) -> Any:
    if _is_number(left) and _is_number(right):
        return left + right
    if _is_duration(left) and _is_duration(right):
        return left + right
    if _is_temporal(left) and _is_duration(right):
        return _shift_temporal(left, right)
    if _is_duration(left) and _is_temporal(right):
        return _shift_temporal(right, left)
    raise _operand_error("+", left, right)


def _op_subtract(left: Any, right: Any) -> Any:
    if _is_number(left) and _is_number(right):
        return left - right
    if _is_duration(left) and _is_duration(right):
        return left - right
    if _is_temporal(left) and _is_duration(right):
        return _shift_temporal(left, -right)
    if _is_temporal(left) and _is_temporal(right) and _same_temporal_kind(left, right):
        # "Duration between datetimes". Two aware datetimes subtract across
        # their offsets, which is the normalise-to-UTC rule the spec states.
        if isinstance(left, datetime.time):
            return _time_as_duration(left) - _time_as_duration(right)
        return left - right
    # `duration - datetime` has no row in the spec's table, and there is no
    # sensible value for "an hour minus Tuesday".
    raise _operand_error("-", left, right)


def _op_multiply(left: Any, right: Any) -> Any:
    if _is_number(left) and _is_number(right):
        return left * right
    if _is_duration(left) and _is_number(right):
        return left * right
    if _is_number(left) and _is_duration(right):
        return right * left
    raise _operand_error("*", left, right)


def _op_divide(left: Any, right: Any) -> Any:
    if _is_number(left) and _is_number(right):
        return _numeric_quotient(left, right)
    if _is_duration(left) and _is_number(right):
        if right == 0:
            # Unlike numbers, a duration has no infinity to divide into.
            raise EvalError("division of a duration by zero")
        return left / right
    if _is_duration(left) and _is_duration(right):
        if not right:
            raise EvalError("division of a duration by a zero duration")
        return left / right
    # `number / duration` has no row in the spec's table.
    raise _operand_error("/", left, right)


def _table_fields(value: list[Any]) -> list[str] | None:
    """The column names if this list reads as a table, else None."""
    if not value or not all(isinstance(item, dict) for item in value):
        return None
    return list(value[0])


def _concatenate(left: list[Any], right: list[Any]) -> list[Any]:
    """`x & y` over two lists - or two tables, which look identical here.

    A table in this evaluator is a list of records, so `{...} & {...}` is
    genuinely ambiguous. M concatenates two LISTS item by item, but
    concatenates two TABLES by taking the union of their columns and filling
    the gaps with null. The two readings agree unless the records carry
    different fields; where they differ they disagree about the answer, not
    merely about the type.

    Guessing returns a ragged list where a table was meant, or invents null
    cells where a plain list was meant. Both are wrong quietly, so this
    refuses and names the function that says which was intended.
    """
    left_fields = _table_fields(left)
    right_fields = _table_fields(right)
    if (
        left_fields is not None
        and right_fields is not None
        and left_fields != right_fields
    ):
        raise UnsupportedError(
            "& between two lists of records whose fields differ is ambiguous "
            "here: as lists M keeps the rows unchanged, as tables it unions "
            "the columns and fills the gaps with null, and this evaluator "
            "models both as a list of records. Write Table.Combine({x, y}) "
            "for the table meaning or List.Combine({x, y}) for the list one."
        )
    return [*left, *right]


def _op_combine(left: Any, right: Any) -> Any:
    if isinstance(left, str) and isinstance(right, str):
        return left + right
    if (
        isinstance(left, datetime.date)
        and not isinstance(left, datetime.datetime)
        and isinstance(right, datetime.time)
    ):
        # #date(2013,02,26) & #time(09,17,00) // #datetime(2013,02,26,09,17,00)
        return datetime.datetime.combine(left, right)
    if isinstance(left, dict) and isinstance(right, dict):
        # "If a field appears in both x and y, the value from y is used", and
        # the field order is x's followed by y's new fields - exactly what
        # dict unpacking gives.
        return {**left, **right}
    if isinstance(left, list) and isinstance(right, list):
        return _concatenate(left, right)
    if left is None or right is None:
        other = left if right is None else right
        # The table pairs null with text, date and time. A list, record or
        # table has nothing to absorb a null operand into, so those stay
        # errors rather than quietly evaporating into null.
        if other is None or isinstance(other, (str, datetime.date, datetime.time)):
            return None
    raise _operand_error("&", left, right)


_ARITHMETIC: dict[str, Callable[[Any, Any], Any]] = {
    "+": _op_add,
    "-": _op_subtract,
    "*": _op_multiply,
    "/": _op_divide,
}


def _eval_arithmetic(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    left_node, operator, right_node = _binop_parts(node)
    left = _eval(left_node, scope, ctx)
    right = _eval(right_node, scope, ctx)
    if operator == "&":
        # `&` has its own null rule - it accepts kinds arithmetic does not.
        return _op_combine(left, right)
    apply = _ARITHMETIC.get(operator)
    if apply is None:
        raise UnsupportedError(f"arithmetic operator: {operator}")
    if left is None or right is None:
        # Every row of the four arithmetic tables that pairs a value with
        # null yields null. `null + null` is not enumerated; null is chosen
        # over an error because the relational section states the rule in
        # general terms ("if either or both operands are null, the result is
        # the null value"), and because adding two blank cells is an
        # ordinary thing for a query to do.
        return None
    return apply(left, right)


def _eval_equality(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    left_node, operator, right_node = _binop_parts(node)
    left = _eval(left_node, scope, ctx)
    right = _eval(right_node, scope, ctx)
    if operator == "=":
        return _m_equal(left, right)
    if operator == "<>":
        return not _m_equal(left, right)
    raise UnsupportedError(f"equality operator: {operator}")


def _same_orderable_kind(left: Any, right: Any) -> bool:
    """The spec's operand rule for `<`, `>`, `<=` and `>=`.

    "The values ... must be a binary, date, datetime, datetimezone, duration,
    logical, number, null, text or time value", and "both operands must be
    the same kind of value or null".
    """
    if isinstance(left, bool) or isinstance(right, bool):
        # "Two logicals are compared such that true is considered to be
        # greater than false" - which is Python's own bool ordering.
        return isinstance(left, bool) and isinstance(right, bool)
    if _is_number(left) and _is_number(right):
        return True
    for kind in (str, bytes):
        # "Two binaries are compared byte by byte" - Python's bytes ordering,
        # exactly.
        if isinstance(left, kind) and isinstance(right, kind):
            return True
    return _same_temporal_kind(left, right)


def _eval_relational(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    left_node, operator, right_node = _binop_parts(node)
    left = _eval(left_node, scope, ctx)
    right = _eval(right_node, scope, ctx)
    if left is None or right is None:
        # "If either or both operands are null, the result is the null
        # value." Not false and not an error: `[Ship Date] < #date(...)` over
        # a blank cell is a null that the surrounding filter then treats as
        # not-true, which is how Power Query drops those rows.
        return None
    if not _same_orderable_kind(left, right):
        raise EvalError(
            "relational operators require two values of the same kind "
            "(binary, date, datetime, datetimezone, duration, logical, "
            f"number, text or time); got {_type_name(left)} and "
            f"{_type_name(right)}"
        )
    if operator == "<":
        return left < right
    if operator == "<=":
        return left <= right
    if operator == ">":
        return left > right
    if operator == ">=":
        return left >= right
    raise UnsupportedError(f"relational operator: {operator}")


def _eval_logical(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    left_node, operator, right_node = _binop_parts(node)
    left = _eval(left_node, scope, ctx)
    if not isinstance(left, bool):
        raise EvalError(f"and/or require logical operands, got {_type_name(left)}")
    if operator == "and" and not left:
        return False
    if operator == "or" and left:
        return True
    right = _eval(right_node, scope, ctx)
    if not isinstance(right, bool):
        raise EvalError(f"and/or require logical operands, got {_type_name(right)}")
    if operator == "and":
        return left and right
    if operator == "or":
        return left or right
    raise UnsupportedError(f"logical operator: {operator}")


def _op_plus(value: Any) -> Any:
    """Unary `+` over number, duration and null.

    The spec's prose says "if the result of evaluating x is not a number
    value, then an error ... is raised", but its own table for this operator
    lists duration and null, and its own example is
    `+ #duration(0,1,30,0) // #duration(0,1,30,0)`. The table and the example
    agree with each other, so they win over the sentence.
    """
    if value is None or _is_number(value) or _is_duration(value):
        return value
    raise EvalError(f"unary + is not defined for {_type_name(value)}")


def _op_negate(value: Any) -> Any:
    if value is None:
        return None
    if _is_number(value) or _is_duration(value):
        return -value
    raise EvalError(f"unary - is not defined for {_type_name(value)}")


def _eval_unary(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    operators_wrapper, operand_node = _children(node)
    value = _eval(operand_node, scope, ctx)
    operators = [str(item["value"]) for item in _children(operators_wrapper)]
    for operator in reversed(operators):
        if operator == "-":
            value = _op_negate(value)
        elif operator == "+":
            value = _op_plus(value)
        elif operator == "not":
            if not isinstance(value, bool):
                raise EvalError(
                    f"not requires a logical value, got {_type_name(value)}"
                )
            value = not value
        else:
            raise UnsupportedError(f"unary operator: {operator}")
    return value


# --------------------------------------------------------------------------
# Identifiers - scope, builtins, and the honest boundary for connectors
# --------------------------------------------------------------------------

# Sources that genuinely need Microsoft's Mashup Engine: credentials, a
# network identity, driver-specific type mapping, or query folding into a
# remote engine. File.Contents and Csv.Document used to sit here too - they
# were removed once implemented natively (builtins/_connectors.py), because
# reading a local CSV needs none of the above.
# Connectors that are still refused. Everything previously in this set that
# pqtools can actually open now lives in builtins/_sources.py; what remains
# needs an auth flow (OAuth device code, tenant consent) that a library cannot
# complete without becoming a credential store.
_CONNECTOR_NAMES = frozenset(
    {
        "SharePoint.Files",
        "SharePoint.Tables",
        "SharePoint.Contents",
    }
)


def _is_connector(name: str) -> bool:
    # Binary.* is no longer refused wholesale: Binary.FromText/ToText/
    # Decompress/Length are implemented (builtins/_connectors.py) because the
    # inline "Enter Data" table Power BI writes depends on them. Anything else
    # in that namespace falls through to the plain unknown-identifier error,
    # which names it just as clearly.
    return name in _CONNECTOR_NAMES


def _eval_identifier_expression(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    children = _children(node)
    # `@name` carries an extra leading Constant ("@") and is M's INCLUSIVE
    # identifier reference: it names the binding currently being defined,
    # which is how a recursive function refers to itself. A plain `name` is
    # the EXCLUSIVE form and skips that binding - see `_Scope`.
    inclusive = any(
        child.get("kind") == "Constant" and child.get("value") == "@"
        for child in children
    )
    name = _identifier_text(children[-1])
    found, value = scope.lookup(name, inclusive)
    if found:
        return _force(value, ctx)
    builtin = BUILTINS.get(name)
    if builtin is not None:
        return builtin
    if name in {"#shared", "#sections"}:
        # Both hand back the *document's* members, so they need a whole
        # section document rather than the expression being evaluated.
        # "unknown identifier" would read like a typo; it is a scope limit.
        raise UnsupportedError(
            f"{name} needs the enclosing section document, which this "
            "expression evaluator does not have - use `pq list FILE` to see "
            "a container's members, or `pq eval FILE --member NAME` to run one"
        )
    if _is_connector(name):
        raise UnsupportedError(
            f"{name} is a connector - Power Query's Mashup Engine runs it "
            "(Fabric or PQTest is the host that can); pqtools evaluates only "
            "the transformation chain after you supply its result table with "
            "--bind"
        )
    documented = _explain_documented(name)
    if documented is not None:
        # A real M function we do not implement. Saying "unknown identifier"
        # here is the same error a typo gives, which leaves the reader unable
        # to tell a misspelling from a genuine gap.
        raise UnsupportedError(documented)
    raise UnsupportedError(f"unknown identifier: {name}")


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Core language constructs
#
# These were all `UnsupportedError` until 0.10.0, and they are not exotic:
# 23 of Microsoft's own worked examples use `..` alone. They were invisible
# because no fixture in this repo was written by someone reading the M
# reference - a library author writes `{1, 2, 3}`, the reference writes
# `{1..10}`.
# --------------------------------------------------------------------------


def _range_values(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> list[Any]:
    """`{1..5}` and `{"a".."z"}` - the range operator inside a list literal.

    Both forms appear in the reference: `List.Range({1..10}, 6)` for numbers
    and `Text.Select("a,b;c", {"a".."z"})` for characters. Inclusive at both
    ends.
    """
    start_node, end_node = _semantic(node)
    start = _eval(start_node, scope, ctx)
    end = _eval(end_node, scope, ctx)
    if isinstance(start, str) and isinstance(end, str):
        if len(start) != 1 or len(end) != 1:
            raise EvalError(
                "a text range needs a single character on each side "
                f"(got {start!r}..{end!r})"
            )
        low, high, as_text = ord(start), ord(end), True
    elif _is_number(start) and _is_number(end):
        if int(start) != start or int(end) != end:
            raise EvalError(f"a numeric range needs whole numbers (got {start}..{end})")
        low, high, as_text = int(start), int(end), False
    else:
        raise EvalError(
            "a .. range needs two numbers or two single characters "
            f"(got {_type_name(start)} and {_type_name(end)})"
        )
    values: list[Any] = []
    for code in range(low, high + 1):
        # Each element costs a step, so a runaway range meets the same budget
        # that guards every other loop here instead of the heap.
        ctx.budget.tick()
        values.append(chr(code) if as_text else code)
    return values


_TYPE_KINDS = {
    "text",
    "number",
    "logical",
    "date",
    "datetime",
    "datetimezone",
    "time",
    "duration",
    "binary",
}


def _named_type(node: dict[str, Any]) -> tuple[str | None, bool]:
    """(primitive type name, nullable) from an `is`/`as` right-hand side."""
    nullable = any(
        str(child.get("value", "")) == "nullable"
        for child in _descendants(node, "Constant")
    )
    for candidate in _descendants(node, "PrimitiveType"):
        return str(candidate.get("value")), nullable
    if node.get("kind") == "PrimitiveType":
        return str(node.get("value")), nullable
    return None, nullable


def _conforms(value: Any, node: dict[str, Any], operator: str) -> bool:
    """M's `is` test: does `value` conform to the named primitive type?"""
    name, nullable = _named_type(node)
    if name is None:
        raise UnsupportedError(
            f"{operator} against a non-primitive type "
            "(only the primitive and nullable-primitive types are modelled "
            "here, which is the same set the M spec defines these operators "
            "over)"
        )
    if value is None:
        return nullable or name in ("null", "any")
    if name == "any":
        return True
    if name == "none":
        return False
    if name == "null":
        return False
    if name == "record":
        return isinstance(value, dict)
    if name == "function":
        return isinstance(value, _Lambda) or callable(value)
    if name in ("list", "table"):
        if not isinstance(value, list):
            return False
        if value and all(isinstance(item, dict) for item in value):
            # A table here IS a list of records, so the two answers are the
            # same object. Guessing would make `x is table` and `x is list`
            # both authoritative about something this model cannot see.
            raise UnsupportedError(
                f"{operator} list/table against a list of records: this "
                "evaluator models an M table as exactly that, so the two "
                "are indistinguishable here"
            )
        return name == "list"
    if name in _TYPE_KINDS:
        return _classify(value) == name or (
            name == "binary" and isinstance(value, bytes)
        )
    raise UnsupportedError(f"{operator} against `type {name}`")


def _eval_is(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    value_node, type_node = _semantic(node)
    return _conforms(_eval(value_node, scope, ctx), type_node, "is")


def _eval_as(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    """`x as number` - ascription, which CHECKS rather than converts.

    "Is compatible primitive/nullable primitive type or error". It does not
    coerce: `"1" as number` is an error, not 1.
    """
    value_node, type_node = _semantic(node)
    value = _eval(value_node, scope, ctx)
    if _conforms(value, type_node, "as"):
        return value
    name, nullable = _named_type(type_node)
    declared = f"nullable {name}" if nullable else str(name)
    raise EvalError(
        f"{_type_name(value)} value does not conform to `type {declared}` "
        "(`as` checks a type, it does not convert - use the matching "
        "X.From/X.FromText to convert)"
    )


def _eval_coalesce(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    """`x ?? y` - null-coalescing, short-circuiting on a non-null left."""
    left_node, right_node = _semantic(node)
    left = _eval(left_node, scope, ctx)
    return _eval(right_node, scope, ctx) if left is None else left


def _eval_error_raising(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    """`error "..."` / `error [Reason=..., Message=..., Detail=...]`.

    The raised value has to be catchable by `try`, and the record form has to
    survive into `catch (e) => e[Reason]`, so the payload rides on the
    exception and `_error_record` prefers it over the generic shape.
    """
    (payload_node,) = _semantic(node)
    payload = _eval(payload_node, scope, ctx)
    if isinstance(payload, dict):
        message = payload.get("Message")
        error = EvalError(str(message) if message is not None else "error")
        # Carried on the exception rather than in the message, so `catch`
        # can hand back the author's own record unchanged.
        #
        # It used to be reprojected onto exactly Reason/Message/Detail, which
        # silently DELETED every other field the author raised. Error.Record
        # builds six - Message.Format, Message.Parameters and ErrorCode as
        # well - and its own documented output shows all six surviving into
        # `try`'s Error field, so the projection was throwing away half of a
        # documented result. Reason still defaults, because M supplies one
        # when the author does not.
        record = dict(payload)
        record.setdefault("Reason", "Expression.Error")
        error.m_error_record = record  # type: ignore[attr-defined]
        raise error
    raise EvalError("error" if payload is None else str(payload))


def _project_fields(base: Any, node: dict[str, Any], optional: bool) -> Any:
    """`r[[a],[b]]` - keep only the named fields, in the order named.

    On a table (a list of records here) M reads the same syntax as column
    selection, which is the same operation applied row-wise.
    """
    names = [
        _identifier_text(_semantic(selector)[0])
        for csv in _children(_semantic(node)[0])
        for selector in [_semantic(csv)[0]]
    ]

    def pick(record: dict[str, Any]) -> dict[str, Any]:
        picked: dict[str, Any] = {}
        for name in names:
            if name in record:
                picked[name] = record[name]
            elif optional:
                picked[name] = None
            else:
                raise EvalError(f"field not found: {name}")
        return picked

    if isinstance(base, dict):
        return pick(base)
    if isinstance(base, list) and all(isinstance(row, dict) for row in base):
        return [pick(row) for row in base]
    raise EvalError(
        f"field projection needs a record or a table, got {_type_name(base)}"
    )


_HANDLERS: dict[str, Callable[[dict[str, Any], _Scope, _Ctx], Any]] = {
    "LiteralExpression": _eval_literal,
    "IdentifierExpression": _eval_identifier_expression,
    "LetExpression": _eval_let,
    "IfExpression": _eval_if,
    "RecordExpression": _eval_record,
    "ListExpression": _eval_list,
    "FunctionExpression": _eval_function,
    "EachExpression": _eval_each,
    "ParenthesizedExpression": _eval_parenthesized,
    "FieldSelector": _eval_field_selector,
    "TypePrimaryType": _eval_type_primary,
    "RecursivePrimaryExpression": _eval_recursive,
    "ErrorHandlingExpression": _eval_try,
    "ArithmeticExpression": _eval_arithmetic,
    "EqualityExpression": _eval_equality,
    "RelationalExpression": _eval_relational,
    "LogicalExpression": _eval_logical,
    "UnaryExpression": _eval_unary,
    "IsExpression": _eval_is,
    "AsExpression": _eval_as,
    "NullCoalescingExpression": _eval_coalesce,
    "ErrorRaisingExpression": _eval_error_raising,
}

_SIMPLE_UNSUPPORTED: dict[str, str] = {
    "MetadataExpression": "meta - attaching metadata that a later "
    "Value.Metadata call could read back needs a value wrapper this "
    "evaluator's flat data model does not have, and passing the value "
    "through would make every later Value.Metadata call lie",
    "NotImplementedExpression": "... (not-implemented placeholder)",
    "RangeExpression": "a .. range outside a list literal "
    '(it is only meaningful as `{1..5}` or `{"a".."z"}`)',
    "Section": "a section document - pass --member NAME to evaluate one shared member",
    "SectionMember": "a section member outside of --member handling",
}


def _eval(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    ctx.budget.tick()
    kind = node["kind"]
    handler = _HANDLERS.get(kind)
    if handler is not None:
        return handler(node, scope, ctx)
    message = _SIMPLE_UNSUPPORTED.get(kind)
    if message is not None:
        raise UnsupportedError(message)
    raise UnsupportedError(f"unsupported construct: {kind}")
