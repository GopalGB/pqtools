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

import datetime
from collections.abc import Callable
from typing import Any

from .builtins import BUILTINS
from .builtins._shared import (
    EvalError,
    UnsupportedError,
    _m_equal,
    _parse_numeric_literal,
    _require_int,
    _require_number,
    _type_name,
)
from .builtins._type import _PRIMITIVE_TYPES
from .core import ast as _parse_ast

_MAX_STEPS_DEFAULT = 1_000_000


# --------------------------------------------------------------------------
# Evaluation-wide state
# --------------------------------------------------------------------------


class _Scope:
    """A lexical scope: its own bindings, chained to an enclosing scope."""

    __slots__ = ("vars", "parent")

    def __init__(self, parent: _Scope | None) -> None:
        self.vars: dict[str, Any] = {}
        self.parent = parent

    def lookup(self, name: str) -> tuple[bool, Any]:
        scope: _Scope | None = self
        while scope is not None:
            if name in scope.vars:
                return True, scope.vars[name]
            scope = scope.parent
        return False, None

    def child(self) -> _Scope:
        return _Scope(self)


class _Thunk:
    """A lazy, memoised ``let`` binding value."""

    __slots__ = ("node", "scope", "value", "done", "active")

    def __init__(self, node: dict[str, Any], scope: _Scope) -> None:
        self.node = node
        self.scope = scope
        self.value: Any = None
        self.done = False
        self.active = False


class _Lambda:
    """A closure created by ``each ...`` or ``(params) => ...``."""

    __slots__ = ("params", "body", "scope")

    def __init__(self, params: list[str], body: dict[str, Any], scope: _Scope) -> None:
        self.params = params
        self.body = body
        self.scope = scope


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

    __slots__ = ("bindings", "budget", "invoke")

    def __init__(
        self,
        bindings: dict[str, Any],
        budget: _Budget,
        invoke: Callable[[Any, list[Any], _Ctx], Any],
    ) -> None:
        self.bindings = bindings
        self.budget = budget
        self.invoke = invoke


# Re-exported from _shared so callers have one import site for the evaluator's
# public surface; declared here so the re-export is explicit to type checkers.
__all__ = ["BUILTINS", "EvalError", "UnsupportedError", "evaluate"]


def evaluate(
    source: str,
    *,
    bindings: dict[str, Any] | None = None,
    max_steps: int = _MAX_STEPS_DEFAULT,
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
    ctx = _Ctx(resolved_bindings, _Budget(max_steps), _invoke)
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
    """The name held by an ``Identifier`` or ``GeneralizedIdentifier`` node."""
    return str(node["value"])


def _binop_parts(node: dict[str, Any]) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """``[left, operatorConstant, right]`` for a binary-operator node."""
    left, operator, right = _children(node)
    return left, str(operator["value"]), right


# --------------------------------------------------------------------------
# Literals
# --------------------------------------------------------------------------


def _parse_text_literal(token: str) -> str:
    if len(token) < 2 or token[0] != '"' or token[-1] != '"':
        raise EvalError("malformed text literal")
    return token[1:-1].replace('""', '"')


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
        raise EvalError("circular reference in let binding")
    value.active = True
    value.value = _eval(value.node, value.scope, ctx)
    value.done = True
    value.active = False
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
            child_scope.vars[name] = _Thunk(value_node, child_scope)
    return _eval(body, child_scope, ctx)


def _eval_if(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    condition_node, true_node, false_node = _semantic(node)
    condition = _eval(condition_node, scope, ctx)
    if not isinstance(condition, bool):
        raise EvalError(f"if condition must be logical, got {_type_name(condition)}")
    return _eval(true_node if condition else false_node, scope, ctx)


def _eval_record(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    (array_wrapper,) = _semantic(node)
    record: dict[str, Any] = {}
    for csv in _children(array_wrapper):
        (pair,) = _semantic(csv)
        name_node, value_node = _semantic(pair)
        record[_identifier_text(name_node)] = _eval(value_node, scope, ctx)
    return record


def _eval_list(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    (array_wrapper,) = _semantic(node)
    return [_eval(_semantic(csv)[0], scope, ctx) for csv in _children(array_wrapper)]


def _eval_function(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    parts = _semantic(node)
    if len(parts) == 3:
        raise UnsupportedError("type ascription (function return type)")
    parameter_list, body = parts
    (params_wrapper,) = _semantic(parameter_list)
    names: list[str] = []
    for csv in _children(params_wrapper):
        (parameter,) = _semantic(csv)
        parameter_children = _semantic(parameter)
        if len(parameter_children) != 1:
            raise UnsupportedError("type ascription (parameter type)")
        names.append(_identifier_text(parameter_children[0]))
    return _Lambda(names, body, scope)


def _eval_each(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    (body,) = _semantic(node)
    return _Lambda(["_"], body, scope)


def _eval_parenthesized(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    (inner,) = _semantic(node)
    return _eval(inner, scope, ctx)


def _eval_type_primary(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    # `type text`, `type number`, ... - the AST wraps a leaf `PrimitiveType`
    # node (whose `value` is the exact lowercase keyword) in `TypePrimaryType`.
    # Compound type shapes (`type table [...]`, `type [a = text]`, `type
    # {number}`, `type nullable text`, `type function ...`) wrap something
    # other than `PrimitiveType` here and are not modelled - see
    # builtins/_type.py's module docstring for why.
    (type_node,) = _semantic(node)
    if type_node.get("kind") != "PrimitiveType":
        raise UnsupportedError(
            f"type value: {type_node.get('kind')} (pqtools only supports the "
            "primitive M types - type text/number/date/datetime/"
            "datetimezone/time/duration/logical/any/none/binary)"
        )
    name = str(type_node["value"])
    type_value = _PRIMITIVE_TYPES.get(name)
    if type_value is None:
        raise UnsupportedError(f"type value: type {name}")
    return type_value


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


def _invoke(callee: Any, args: list[Any], ctx: _Ctx) -> Any:
    if isinstance(callee, _Lambda):
        if len(args) != len(callee.params):
            raise EvalError(
                f"function expects {len(callee.params)} argument(s), got {len(args)}"
            )
        child = callee.scope.child()
        for name, value in zip(callee.params, args, strict=True):
            child.vars[name] = value
        return _eval(callee.body, child, ctx)
    if callable(callee):
        result: Any = callee(args, ctx)
        return result
    raise EvalError(f"{_type_name(callee)} value is not a function")


def _list_index(base: Any, index: Any, optional: bool) -> Any:
    if not isinstance(base, list):
        if optional:
            return None
        raise EvalError(f"cannot index into a {_type_name(base)} value")
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


def _eval_try(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    handler_kind = node.get("handlerKind")
    parts = _semantic(node)
    if len(parts) == 1:
        raise UnsupportedError("try without otherwise")
    protected, handler = parts
    if handler_kind == "Catch":
        raise UnsupportedError("try ... catch ...")
    if handler_kind != "Otherwise":
        raise UnsupportedError(f"try handler: {handler_kind}")
    (otherwise_expr,) = _semantic(handler)
    try:
        return _eval(protected, scope, ctx)
    except UnsupportedError:
        raise
    except EvalError:
        return _eval(otherwise_expr, scope, ctx)


# --------------------------------------------------------------------------
# Operators
# --------------------------------------------------------------------------


def _eval_arithmetic(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    left_node, operator, right_node = _binop_parts(node)
    left = _eval(left_node, scope, ctx)
    right = _eval(right_node, scope, ctx)
    if operator == "&":
        if not isinstance(left, str) or not isinstance(right, str):
            raise EvalError(
                "& requires text on both sides "
                f"(got {_type_name(left)} and {_type_name(right)}; "
                "use Text.From to convert first)"
            )
        return left + right
    left_number = _require_number(left)
    right_number = _require_number(right)
    if operator == "+":
        return left_number + right_number
    if operator == "-":
        return left_number - right_number
    if operator == "*":
        return left_number * right_number
    if operator == "/":
        if right_number == 0:
            raise EvalError("division by zero")
        return left_number / right_number
    raise UnsupportedError(f"arithmetic operator: {operator}")


def _eval_equality(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    left_node, operator, right_node = _binop_parts(node)
    left = _eval(left_node, scope, ctx)
    right = _eval(right_node, scope, ctx)
    if operator == "=":
        return _m_equal(left, right)
    if operator == "<>":
        return not _m_equal(left, right)
    raise UnsupportedError(f"equality operator: {operator}")


def _eval_relational(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    left_node, operator, right_node = _binop_parts(node)
    left = _eval(left_node, scope, ctx)
    right = _eval(right_node, scope, ctx)

    # Temporal values are ordered in M (a date range filter is one of the most
    # common things a real query does), so they are comparable here. Each kind
    # only compares against its own kind: datetime is tested before date because
    # datetime.datetime subclasses datetime.date, and an aware/naive pair is a
    # datetimezone-vs-datetime mismatch that Python itself refuses to order.
    def _same_temporal(a: Any, b: Any) -> bool:
        if isinstance(a, datetime.datetime) or isinstance(b, datetime.datetime):
            return (
                isinstance(a, datetime.datetime)
                and isinstance(b, datetime.datetime)
                and (a.tzinfo is None) == (b.tzinfo is None)
            )
        return any(
            isinstance(a, kind) and isinstance(b, kind)
            for kind in (datetime.date, datetime.time, datetime.timedelta)
        )

    comparable = (
        not isinstance(left, bool)
        and not isinstance(right, bool)
        and (
            (isinstance(left, (int, float)) and isinstance(right, (int, float)))
            or (isinstance(left, str) and isinstance(right, str))
            or _same_temporal(left, right)
        )
    )
    if not comparable:
        raise EvalError(
            "relational operators require two numbers, two text values or two "
            "temporal values of the same kind "
            f"(got {_type_name(left)} and {_type_name(right)})"
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


def _eval_unary(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    operators_wrapper, operand_node = _children(node)
    value = _eval(operand_node, scope, ctx)
    operators = [str(item["value"]) for item in _children(operators_wrapper)]
    for operator in reversed(operators):
        if operator == "-":
            value = -_require_number(value)
        elif operator == "+":
            value = _require_number(value)
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
_CONNECTOR_NAMES = frozenset(
    {
        "Web.Contents",
        "Sql.Database",
        "Excel.Workbook",
        "OData.Feed",
        "SharePoint.Files",
        "SharePoint.Tables",
        "Odbc.DataSource",
        "Oracle.Database",
        "PostgreSQL.Database",
        "MySQL.Database",
        "Folder.Files",
        "Folder.Contents",
    }
)


def _is_connector(name: str) -> bool:
    return name in _CONNECTOR_NAMES or name.startswith("Binary.")


def _eval_identifier_expression(node: dict[str, Any], scope: _Scope, ctx: _Ctx) -> Any:
    children = _children(node)
    if len(children) != 1:
        # The `@name` outer-scope-reference form: an extra leading Constant
        # ("@") child. Only meaningful for recursive record self-reference,
        # which this evaluator does not implement.
        raise UnsupportedError("@ outer-scope identifier operator")
    name = _identifier_text(children[0])
    found, value = scope.lookup(name)
    if found:
        return _force(value, ctx)
    builtin = BUILTINS.get(name)
    if builtin is not None:
        return builtin
    if name == "#shared":
        raise UnsupportedError("#shared")
    if _is_connector(name):
        raise UnsupportedError(
            f"{name} is a connector - Power Query's Mashup Engine runs it "
            "(Fabric or PQTest is the host that can); pqtools evaluates only "
            "the transformation chain after you supply its result table with "
            "--bind"
        )
    raise UnsupportedError(f"unknown identifier: {name}")


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

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
}

_SIMPLE_UNSUPPORTED: dict[str, str] = {
    "AsExpression": "type ascription (as)",
    "IsExpression": "is-expression",
    "NullCoalescingExpression": "?? (null-coalescing operator)",
    "MetadataExpression": "meta",
    "NotImplementedExpression": "... (not-implemented placeholder)",
    "FieldProjection": "field projection (r[[a],[b]])",
    "RangeExpression": "a .. range",
    "ErrorRaisingExpression": "error ...",
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
