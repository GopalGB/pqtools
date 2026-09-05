"""``Record.*`` builtins.

Split out of ``evaluate.py`` in the 0.5.0 architecture refactor (pure move,
zero behaviour change) - see PRD-0.5.0-builtins.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._shared import (
    _MISSING_FIELD_ERROR,
    _MISSING_FIELD_USE_NULL,
    EvalError,
    UnsupportedError,
    _arity,
    _field_name_list,
    _missing_field_mode,
    _require_list,
    _require_record,
    _require_str,
    _require_table,
)

if TYPE_CHECKING:
    from ..evaluate import _Ctx

# MissingField.Error / Ignore / UseNull now live in _shared.py: the Table.*
# family needs the same three values, and the note that used to sit here -
# that the bare identifiers could not resolve - is no longer true. _enums.py
# registers them, so `MissingField.UseNull` works in a query as written.


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


def _record_to_list(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Record.ToList", args, 1)
    return list(_require_record(args[0]).values())


def _record_field_values(args: list[Any], ctx: _Ctx) -> Any:
    # Record.FieldValues(record) as list - "a list of the field values",
    # in field order (learn.microsoft.com/en-us/powerquery-m/record-fieldvalues).
    # Same shape as Record.ToList; kept as its own entry because it is the
    # name the docs (and real queries) actually use.
    _arity("Record.FieldValues", args, 1)
    return list(_require_record(args[0]).values())


def _record_from_list(args: list[Any], ctx: _Ctx) -> Any:
    # Record.FromList(list, fields) - `fields` can be a list of names or a
    # record type. Record types parse as `TypePrimaryType`/`PrimitiveType`
    # nodes, which the evaluator does not implement at all outside type
    # ascription (see PRD-0.5.0-builtins.md's P0 type-system item, owned by
    # another module for this task) - a query passing a type value here
    # already fails before this function runs, so only the list-of-names
    # shape is reachable and implemented.
    _arity("Record.FromList", args, 2)
    values = _require_list(args[0])
    fields_arg = args[1]
    if not isinstance(fields_arg, list):
        raise UnsupportedError("Record.FromList: fields as a record type")
    names = [_require_str(name) for name in fields_arg]
    if len(names) != len(values):
        raise EvalError("Record.FromList: field count does not match value count")
    if len(set(names)) != len(names):
        raise EvalError("Record.FromList: field names must be unique")
    return dict(zip(names, values, strict=True))


def _record_combine(args: list[Any], ctx: _Ctx) -> Any:
    # On a field-name collision across records, the LAST record's value
    # wins (standard last-write-wins merge, matching dict.update order).
    _arity("Record.Combine", args, 1)
    result: dict[str, Any] = {}
    for record in _require_list(args[0]):
        result.update(_require_record(record))
    return result


def _record_select_fields(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Record.SelectFields", args, 2, 3)
    record = _require_record(args[0])
    names = _field_name_list(args[1])
    mode = _missing_field_mode(args[2] if len(args) == 3 else None)
    result: dict[str, Any] = {}
    for name in names:
        if name in record:
            result[name] = record[name]
        elif mode == _MISSING_FIELD_ERROR:
            raise EvalError(f"Record.SelectFields: no such field: {name}")
        elif mode == _MISSING_FIELD_USE_NULL:
            result[name] = None
        # Ignore: field is simply absent from the result.
    return result


def _normalize_pairs(value: Any, fn_name: str) -> list[list[Any]]:
    """Record.RenameFields/Record.TransformFields both accept EITHER a
    single {a, b} pair OR a list of such pairs - discriminate the same way
    real PQ does: a bare pair's first element is a field-name string and
    its second is NOT itself a list (a name), whereas a list-of-pairs has
    lists as its own elements.
    """
    if not isinstance(value, list):
        raise EvalError(f"{fn_name}: expected a list")
    if len(value) == 2 and isinstance(value[0], str) and not isinstance(value[1], list):
        return [value]
    pairs = []
    for item in value:
        if not (isinstance(item, list) and len(item) == 2):
            raise EvalError(f"{fn_name}: expected {{old, new}} pairs")
        pairs.append(item)
    return pairs


def _record_rename_fields(args: list[Any], ctx: _Ctx) -> Any:
    # Renaming preserves each field's ORIGINAL position (verified against
    # the MS docs worked example - a renamed field stays where it was, it
    # does not move to the end).
    _arity("Record.RenameFields", args, 2, 3)
    record = _require_record(args[0])
    pairs = _normalize_pairs(args[1], "Record.RenameFields")
    mode = _missing_field_mode(args[2] if len(args) == 3 else None)
    rename_map: dict[str, str] = {}
    for old_name, new_name in pairs:
        old_name = _require_str(old_name)
        new_name = _require_str(new_name)
        if old_name not in record:
            if mode == _MISSING_FIELD_ERROR:
                raise EvalError(f"Record.RenameFields: no such field: {old_name}")
            continue
        rename_map[old_name] = new_name
    return {rename_map.get(key, key): value for key, value in record.items()}


def _record_transform_fields(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Record.TransformFields", args, 2, 3)
    record = _require_record(args[0])
    pairs = _normalize_pairs(args[1], "Record.TransformFields")
    mode = _missing_field_mode(args[2] if len(args) == 3 else None)
    result = dict(record)
    for name, transform in pairs:
        name = _require_str(name)
        if name not in result:
            if mode == _MISSING_FIELD_ERROR:
                raise EvalError(f"Record.TransformFields: no such field: {name}")
            if mode == _MISSING_FIELD_USE_NULL:
                result[name] = None
            continue
        result[name] = ctx.invoke(transform, [result[name]], ctx)
    return result


def _record_to_table(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Record.ToTable", args, 1)
    record = _require_record(args[0])
    return [{"Name": name, "Value": value} for name, value in record.items()]


def _record_from_table(args: list[Any], ctx: _Ctx) -> Any:
    # Record.FromTable(table as table) as record - the exact inverse of
    # Record.ToTable. Verified against the docs' own worked example: a
    # 3-row {Name, Value} table -> [CustomerID = 1, Name = "Bob",
    # Phone = "123-4567"], field order following row order. "An error is
    # raised if the field names are not unique" (docs, verbatim).
    _arity("Record.FromTable", args, 1)
    table = _require_table(args[0])
    result: dict[str, Any] = {}
    for index, row in enumerate(table):
        if "Name" not in row or "Value" not in row:
            raise EvalError(
                f"Record.FromTable: row {index + 1} is missing a Name or Value field"
            )
        name = _require_str(row["Name"])
        if name in result:
            raise EvalError(f"Record.FromTable: field names are not unique: {name}")
        result[name] = row["Value"]
    return result


def _record_field_or_default(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Record.FieldOrDefault", args, 2, 3)
    record = args[0]
    if record is None:
        return None
    record = _require_record(record)
    name = _require_str(args[1])
    if name in record:
        return record[name]
    return args[2] if len(args) == 3 else None


def _record_field_count(args: list[Any], ctx: _Ctx) -> Any:
    _arity("Record.FieldCount", args, 1)
    return len(_require_record(args[0]))


def _record_reorder_fields(args: list[Any], ctx: _Ctx) -> Any:
    # Algorithm verified against BOTH MS docs worked examples (plain
    # reorder, and reorder-with-a-new-field-via-UseNull):
    #   - fields NOT named in fieldOrder keep their ORIGINAL numeric index
    #     in the (possibly longer, if UseNull added new fields) output.
    #   - all other output slots are filled, in increasing slot order, by
    #     the fieldOrder entries in the order fieldOrder gives them.
    _arity("Record.ReorderFields", args, 2, 3)
    record = _require_record(args[0])
    field_order = [_require_str(name) for name in _require_list(args[1])]
    mode = _missing_field_mode(args[2] if len(args) == 3 else None)

    original_keys = list(record.keys())
    listed_set = set(field_order)
    unlisted = [key for key in original_keys if key not in listed_set]

    listed_values: dict[str, Any] = {}
    kept_listed: list[str] = []
    for name in field_order:
        if name in record:
            listed_values[name] = record[name]
            kept_listed.append(name)
        elif mode == _MISSING_FIELD_ERROR:
            raise EvalError(f"Record.ReorderFields: no such field: {name}")
        elif mode == _MISSING_FIELD_USE_NULL:
            listed_values[name] = None
            kept_listed.append(name)
        # Ignore: the named-but-missing field is dropped entirely.

    total = len(unlisted) + len(kept_listed)
    original_index = {name: i for i, name in enumerate(original_keys)}
    unlisted_slots = {original_index[name] for name in unlisted}
    remaining_slots = [i for i in range(total) if i not in unlisted_slots]

    slots: list[str | None] = [None] * total
    for name in unlisted:
        slots[original_index[name]] = name
    for slot, name in zip(remaining_slots, kept_listed, strict=True):
        slots[slot] = name

    result: dict[str, Any] = {}
    for slot_name in slots:
        assert slot_name is not None
        result[slot_name] = (
            listed_values[slot_name]
            if slot_name in listed_values
            else record[slot_name]
        )
    return result


# The M-visible names this module owns. builtins/__init__.py merges every
# module's BUILTINS into one registry, so a new function is added HERE and
# nowhere else - no central file to edit, and no merge conflict when several
# families are implemented in parallel.
BUILTINS: dict[str, Any] = {
    "Record.Field": _record_field_builtin,
    "Record.FieldNames": _record_field_names,
    "Record.HasFields": _record_has_fields,
    "Record.AddField": _record_add_field,
    "Record.RemoveFields": _record_remove_fields,
    "Record.ToList": _record_to_list,
    "Record.FieldValues": _record_field_values,
    "Record.FromList": _record_from_list,
    "Record.Combine": _record_combine,
    "Record.SelectFields": _record_select_fields,
    "Record.RenameFields": _record_rename_fields,
    "Record.TransformFields": _record_transform_fields,
    "Record.ToTable": _record_to_table,
    "Record.FromTable": _record_from_table,
    "Record.FieldOrDefault": _record_field_or_default,
    "Record.FieldCount": _record_field_count,
    "Record.ReorderFields": _record_reorder_fields,
}
