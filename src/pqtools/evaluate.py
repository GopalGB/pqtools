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

import json as _json
import math
from collections.abc import Callable
from typing import Any

from .core import MQueryError
from .core import ast as _parse_ast

_MAX_STEPS_DEFAULT = 1_000_000


class EvalError(MQueryError):
    code = "M_EVAL_ERROR"


class UnsupportedError(EvalError):
    code = "M_EVAL_UNSUPPORTED"


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
    """Evaluation-wide, read-mostly state threaded through every call."""

    __slots__ = ("bindings", "budget")

    def __init__(self, bindings: dict[str, Any], budget: _Budget) -> None:
        self.bindings = bindings
        self.budget = budget


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
    ctx = _Ctx(resolved_bindings, _Budget(max_steps))
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


def _parse_numeric_literal(token: str) -> int | float:
    text = token.strip()
    lowered = text.lower()
    if lowered == "#infinity":
        return math.inf
    if lowered == "#nan":
        return math.nan
    if lowered.startswith("0x"):
        return int(text, 16)
    if any(marker in text for marker in (".", "e", "E")):
        return float(text)
    return int(text)


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
# Value helpers shared by operators and builtins
# --------------------------------------------------------------------------


def _require_number(value: Any) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvalError(f"expected a number, got {_type_name(value)}")
    return value


def _require_str(value: Any) -> str:
    if not isinstance(value, str):
        raise EvalError(f"expected text, got {_type_name(value)}")
    return value


def _require_int(value: Any) -> int:
    number = _require_number(value)
    if isinstance(number, float):
        if not number.is_integer():
            raise EvalError("expected a whole number")
        return int(number)
    return number


def _require_list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise EvalError(f"expected a list, got {_type_name(value)}")
    return value


def _require_record(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvalError(f"expected a record, got {_type_name(value)}")
    return value


def _require_table(value: Any) -> list[dict[str, Any]]:
    rows = _require_list(value)
    for row in rows:
        if not isinstance(row, dict):
            raise EvalError("expected a table (a list of records)")
    return rows


def _field_name_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [_require_str(item) for item in value]
    raise EvalError("expected a field name or a list of field names")


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "logical"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "record"
    return type(value).__name__


def _m_equal(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _m_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _m_equal(left[key], right[key]) for key in left
        )
    return False


def _format_number(value: int | float) -> str:
    if isinstance(value, int):
        return str(value)
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    if value.is_integer():
        return str(int(value))
    return str(value)


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
    comparable = (
        not isinstance(left, bool)
        and not isinstance(right, bool)
        and (
            (isinstance(left, (int, float)) and isinstance(right, (int, float)))
            or (isinstance(left, str) and isinstance(right, str))
        )
    )
    if not comparable:
        raise EvalError(
            "relational operators require two numbers or two text values "
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

_CONNECTOR_NAMES = frozenset(
    {
        "Web.Contents",
        "Sql.Database",
        "File.Contents",
        "Excel.Workbook",
        "Csv.Document",
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
    if name in _ORDER_ENUM:
        return _ORDER_ENUM[name]
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
# Builtins
#
# Each function takes `(args, ctx)` and returns the M value. `_arity` turns
# an unsupported argument-count variant (an optional parameter this module
# does not implement) into a typed, named UnsupportedError rather than a
# best-effort guess.
# --------------------------------------------------------------------------

_Builtin = Callable[[list[Any], _Ctx], Any]


def _arity(name: str, args: list[Any], low: int, high: int | None = None) -> None:
    ceiling = low if high is None else high
    if not (low <= len(args) <= ceiling):
        raise UnsupportedError(f"{name} with {len(args)} argument(s)")


def _text_from(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.From", args, 1)
    value = args[0]
    if value is None:
        return None
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return _format_number(value)
    if isinstance(value, str):
        return value
    raise EvalError(f"Text.From: unsupported value type: {_type_name(value)}")


def _text_upper(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.Upper", args, 1)
    return _require_str(args[0]).upper()


def _text_lower(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.Lower", args, 1)
    return _require_str(args[0]).lower()


def _text_length(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.Length", args, 1)
    return len(_require_str(args[0]))


def _text_combine(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.Combine", args, 1, 2)
    texts = _require_list(args[0])
    separator = _require_str(args[1]) if len(args) == 2 else ""
    return separator.join(_require_str(item) for item in texts)


def _text_contains(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.Contains", args, 2)
    return _require_str(args[1]) in _require_str(args[0])


def _text_replace(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.Replace", args, 3)
    return _require_str(args[0]).replace(_require_str(args[1]), _require_str(args[2]))


def _text_split(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.Split", args, 2)
    return _require_str(args[0]).split(_require_str(args[1]))


def _text_start(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.Start", args, 2)
    text = _require_str(args[0])
    count = _require_int(args[1])
    if count < 0:
        raise EvalError("Text.Start: count must not be negative")
    return text[:count]


def _text_end(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.End", args, 2)
    text = _require_str(args[0])
    count = _require_int(args[1])
    if count < 0:
        raise EvalError("Text.End: count must not be negative")
    return text[len(text) - count :] if count else ""


def _text_trim(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Text.Trim", args, 1, 2)
    text = _require_str(args[0])
    if len(args) == 2:
        return text.strip(_require_str(args[1]))
    return text.strip()


def _number_from(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Number.From", args, 1)
    value = args[0]
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return _parse_numeric_literal(value.strip())
        except ValueError as error:
            raise EvalError(f"Number.From: not a number: {value!r}") from error
    raise EvalError(f"Number.From: unsupported value type: {_type_name(value)}")


def _number_round(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Number.Round", args, 1, 2)
    value = _require_number(args[0])
    digits = _require_int(args[1]) if len(args) == 2 else 0
    return round(value, digits)


def _number_abs(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Number.Abs", args, 1)
    return abs(_require_number(args[0]))


def _list_count(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Count", args, 1)
    return len(_require_list(args[0]))


def _list_sum(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Sum", args, 1)
    total: int | float = 0
    for item in _require_list(args[0]):
        total = total + _require_number(item)
    return total


def _list_max(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Max", args, 1, 2)
    items = _require_list(args[0])
    if not items:
        return args[1] if len(args) == 2 else None
    try:
        return max(items)
    except TypeError as error:
        raise EvalError("List.Max: values are not comparable") from error


def _list_min(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Min", args, 1, 2)
    items = _require_list(args[0])
    if not items:
        return args[1] if len(args) == 2 else None
    try:
        return min(items)
    except TypeError as error:
        raise EvalError("List.Min: values are not comparable") from error


def _list_average(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Average", args, 1)
    items = _require_list(args[0])
    if not items:
        return None
    numbers = [_require_number(item) for item in items]
    return sum(numbers) / len(numbers)


def _list_transform(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Transform", args, 2)
    transform = args[1]
    return [_invoke(transform, [item], ctx) for item in _require_list(args[0])]


def _list_select(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Select", args, 2)
    predicate = args[1]
    result = []
    for item in _require_list(args[0]):
        keep = _invoke(predicate, [item], ctx)
        if not isinstance(keep, bool):
            raise EvalError("List.Select: predicate must return a logical value")
        if keep:
            result.append(item)
    return result


def _list_first(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.First", args, 1, 2)
    items = _require_list(args[0])
    if items:
        return items[0]
    return args[1] if len(args) == 2 else None


def _list_last(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Last", args, 1, 2)
    items = _require_list(args[0])
    if items:
        return items[-1]
    return args[1] if len(args) == 2 else None


def _list_reverse(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Reverse", args, 1)
    return list(reversed(_require_list(args[0])))


def _list_sort(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Sort", args, 1)
    try:
        return sorted(_require_list(args[0]))
    except TypeError as error:
        raise EvalError("List.Sort: values are not comparable") from error


def _list_contains(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Contains", args, 2)
    return any(_m_equal(item, args[1]) for item in _require_list(args[0]))


def _list_distinct(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Distinct", args, 1)
    result: list[Any] = []
    for item in _require_list(args[0]):
        if not any(_m_equal(item, seen) for seen in result):
            result.append(item)
    return result


def _list_range(args: list[Any], ctx: _Ctx) -> Any:
    _arity("List.Range", args, 2, 3)
    items = _require_list(args[0])
    offset = _require_int(args[1])
    if offset < 0:
        raise EvalError("List.Range: offset must not be negative")
    if len(args) == 3:
        count = _require_int(args[2])
        if count < 0:
            raise EvalError("List.Range: count must not be negative")
        return items[offset : offset + count]
    return items[offset:]


def _record_field_builtin(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Record.Field", args, 2)
    record = _require_record(args[0])
    name = _require_str(args[1])
    if name not in record:
        raise EvalError(f"Record.Field: no such field: {name}")
    return record[name]


def _record_field_names(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Record.FieldNames", args, 1)
    return list(_require_record(args[0]).keys())


def _record_has_fields(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Record.HasFields", args, 2)
    record = _require_record(args[0])
    return all(name in record for name in _field_name_list(args[1]))


def _record_add_field(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Record.AddField", args, 3)
    record = _require_record(args[0])
    name = _require_str(args[1])
    if name in record:
        raise EvalError(f"Record.AddField: field already exists: {name}")
    result = dict(record)
    result[name] = args[2]
    return result


def _record_remove_fields(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Record.RemoveFields", args, 2)
    record = _require_record(args[0])
    names = _field_name_list(args[1])
    for name in names:
        if name not in record:
            raise EvalError(f"Record.RemoveFields: no such field: {name}")
    return {key: value for key, value in record.items() if key not in names}


def _table_from_records(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.FromRecords", args, 1)
    return list(_require_table(args[0]))


def _table_to_records(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.ToRecords", args, 1)
    return list(_require_table(args[0]))


def _table_row_count(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.RowCount", args, 1)
    return len(_require_table(args[0]))


def _table_column_names(args: list[Any], ctx: _Ctx) -> Any:
    # A table here carries no schema beyond its rows (spec: "a TABLE is a
    # list of dicts"), so an empty table has no column names to report -
    # not a guess, the necessary consequence of that data model.
    _arity("Table.ColumnNames", args, 1)
    table = _require_table(args[0])
    return list(table[0].keys()) if table else []


def _table_select_rows(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.SelectRows", args, 2)
    predicate = args[1]
    result = []
    for row in _require_table(args[0]):
        keep = _invoke(predicate, [row], ctx)
        if not isinstance(keep, bool):
            raise EvalError("Table.SelectRows: predicate must return a logical value")
        if keep:
            result.append(row)
    return result


def _table_select_columns(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.SelectColumns", args, 2)
    names = _field_name_list(args[1])
    result = []
    for row in _require_table(args[0]):
        for name in names:
            if name not in row:
                raise EvalError(f"Table.SelectColumns: no such column: {name}")
        result.append({name: row[name] for name in names})
    return result


def _table_remove_columns(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.RemoveColumns", args, 2)
    names = _field_name_list(args[1])
    result = []
    for row in _require_table(args[0]):
        for name in names:
            if name not in row:
                raise EvalError(f"Table.RemoveColumns: no such column: {name}")
        result.append({key: value for key, value in row.items() if key not in names})
    return result


def _column_pairs(value: Any, what: str) -> list[tuple[str, Any]]:
    """``{old, new}`` or ``{{old1, new1}, {old2, new2}, ...}``."""

    def is_pair(item: Any) -> bool:
        return isinstance(item, list) and len(item) == 2 and isinstance(item[0], str)

    if not isinstance(value, list):
        raise EvalError(f"{what}: expected a {{column, value}} pair or a list of them")
    if is_pair(value):
        return [(value[0], value[1])]
    pairs: list[tuple[str, Any]] = []
    for item in value:
        if not is_pair(item):
            raise EvalError(
                f"{what}: expected a {{column, value}} pair or a list of them"
            )
        pairs.append((item[0], item[1]))
    return pairs


def _table_rename_columns(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.RenameColumns", args, 2)
    table = _require_table(args[0])
    pairs = _column_pairs(args[1], "Table.RenameColumns")
    mapping: dict[str, str] = {}
    for old, new in pairs:
        if not isinstance(new, str):
            raise EvalError("Table.RenameColumns: new column name must be text")
        if table and old not in table[0]:
            raise EvalError(f"Table.RenameColumns: no such column: {old}")
        mapping[old] = new
    return [
        {mapping.get(key, key): value for key, value in row.items()} for row in table
    ]


def _table_add_column(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.AddColumn", args, 3)
    table = _require_table(args[0])
    name = _require_str(args[1])
    generator = args[2]
    if table and name in table[0]:
        raise EvalError(f"Table.AddColumn: column already exists: {name}")
    result = []
    for row in table:
        new_row = dict(row)
        new_row[name] = _invoke(generator, [row], ctx)
        result.append(new_row)
    return result


def _table_transform_columns(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.TransformColumns", args, 2)
    table = _require_table(args[0])
    pairs = _column_pairs(args[1], "Table.TransformColumns")
    result = []
    for row in table:
        new_row = dict(row)
        for name, transform in pairs:
            if name not in new_row:
                raise EvalError(f"Table.TransformColumns: no such column: {name}")
            new_row[name] = _invoke(transform, [new_row[name]], ctx)
        result.append(new_row)
    return result


# Order.Ascending / Order.Descending are M enum constants (0 and 1). Power Query's
# own UI emits `Table.Sort(t, {{"Col", Order.Ascending}})`, so without these the most
# common real-world sort is unusable.
_ORDER_ENUM = {"Order.Ascending": 0, "Order.Descending": 1}


def _table_sort(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.Sort", args, 2)
    table = _require_table(args[0])
    spec = args[1]
    # Accepted shapes, all of which Power Query itself emits:
    #   "Col"                                  one column, ascending
    #   {"A", "B"}                             several columns, ascending
    #   {{"Col", Order.Descending}}            column with an explicit direction
    #   {{"A", Order.Ascending}, {"B", Order.Descending}}
    keys: list[tuple[str, bool]] = []
    entries = [spec] if isinstance(spec, str) else spec
    if not isinstance(entries, list):
        raise UnsupportedError(f"Table.Sort with a {type(spec).__name__} sort spec")
    for entry in entries:
        if isinstance(entry, str):
            keys.append((entry, False))
        elif (
            isinstance(entry, list)
            and 1 <= len(entry) <= 2
            and isinstance(entry[0], str)
        ):
            if len(entry) == 1:
                keys.append((entry[0], False))
            elif entry[1] in (0, 1):
                keys.append((entry[0], entry[1] == 1))
            else:
                raise UnsupportedError(
                    "Table.Sort direction must be Order.Ascending or Order.Descending"
                )
        else:
            raise UnsupportedError(
                "Table.Sort entries must be a column name or "
                '{"Column", Order.Ascending}'
            )
    for name, _ in keys:
        if table and name not in table[0]:
            raise EvalError(f"Table.Sort: no such column: {name}")
    rows = list(table)
    try:
        # Stable sort, least significant key first, so mixed directions work.
        for name, descending in reversed(keys):
            rows.sort(key=lambda row: row[name], reverse=descending)
    except TypeError as error:
        raise EvalError("Table.Sort: values are not comparable") from error
    return rows


def _table_first_n(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.FirstN", args, 2)
    table = _require_table(args[0])
    count = _require_int(args[1])
    if count < 0:
        raise EvalError("Table.FirstN: count must not be negative")
    return table[:count]


def _table_last_n(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.LastN", args, 2)
    table = _require_table(args[0])
    count = _require_int(args[1])
    if count < 0:
        raise EvalError("Table.LastN: count must not be negative")
    return table[len(table) - count :] if count else []


def _table_distinct(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Table.Distinct", args, 1, 2)
    table = _require_table(args[0])
    if len(args) == 2:
        names = _field_name_list(args[1])
        seen: list[tuple[Any, ...]] = []
        result = []
        for row in table:
            key = tuple(row.get(name) for name in names)
            if key not in seen:
                seen.append(key)
                result.append(row)
        return result
    result = []
    for row in table:
        if not any(_m_equal(row, other) for other in result):
            result.append(row)
    return result


def _json_document(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Json.Document", args, 1)
    text = _require_str(args[0])
    try:
        return _json.loads(text)
    except _json.JSONDecodeError as error:
        raise EvalError(f"Json.Document: invalid JSON: {error}") from error


def _logical_from(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Logical.From", args, 1)
    value = args[0]
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        raise EvalError(f"Logical.From: not a logical value: {value!r}")
    raise EvalError(f"Logical.From: unsupported value type: {_type_name(value)}")


BUILTINS: dict[str, _Builtin] = {
    "Text.From": _text_from,
    "Text.Upper": _text_upper,
    "Text.Lower": _text_lower,
    "Text.Length": _text_length,
    "Text.Combine": _text_combine,
    "Text.Contains": _text_contains,
    "Text.Replace": _text_replace,
    "Text.Split": _text_split,
    "Text.Start": _text_start,
    "Text.End": _text_end,
    "Text.Trim": _text_trim,
    "Number.From": _number_from,
    "Number.Round": _number_round,
    "Number.Abs": _number_abs,
    "List.Count": _list_count,
    "List.Sum": _list_sum,
    "List.Max": _list_max,
    "List.Min": _list_min,
    "List.Average": _list_average,
    "List.Transform": _list_transform,
    "List.Select": _list_select,
    "List.First": _list_first,
    "List.Last": _list_last,
    "List.Reverse": _list_reverse,
    "List.Sort": _list_sort,
    "List.Contains": _list_contains,
    "List.Distinct": _list_distinct,
    "List.Range": _list_range,
    "Record.Field": _record_field_builtin,
    "Record.FieldNames": _record_field_names,
    "Record.HasFields": _record_has_fields,
    "Record.AddField": _record_add_field,
    "Record.RemoveFields": _record_remove_fields,
    "Table.FromRecords": _table_from_records,
    "Table.ToRecords": _table_to_records,
    "Table.RowCount": _table_row_count,
    "Table.ColumnNames": _table_column_names,
    "Table.SelectRows": _table_select_rows,
    "Table.SelectColumns": _table_select_columns,
    "Table.RemoveColumns": _table_remove_columns,
    "Table.RenameColumns": _table_rename_columns,
    "Table.AddColumn": _table_add_column,
    "Table.TransformColumns": _table_transform_columns,
    "Table.Sort": _table_sort,
    "Table.FirstN": _table_first_n,
    "Table.LastN": _table_last_n,
    "Table.Distinct": _table_distinct,
    "Json.Document": _json_document,
    "Logical.From": _logical_from,
}


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
    "TypePrimaryType": "a type value (type ...)",
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
