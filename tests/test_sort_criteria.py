"""``Table.Sort`` accepts every shape Microsoft's own reference page uses.

The parser rejected Example 2 - a query copied verbatim out of the docs did
not run here. It read ``{"OrderID", Order.Descending}`` as a request to sort
by two columns, one of which was named ``1``.

There were two copies of the criteria parser (``Table.Sort`` and
``Table.MaxN``/``MinN``), they had the same defect, and fixing one would have
left the other. They are one function now, so this file exercises the shapes
through all three entry points.

The table below is the exact nine-row fixture from
learn.microsoft.com/en-us/powerquery-m/table-sort, and each expected ordering
is that page's own printed output.
"""

from __future__ import annotations

import pytest

from pqtools import UnsupportedError, evaluate

# The docs' fixture, as a #table so the literal stays readable.
ORDERS = """#table(
    {"OrderID", "CustomerID", "Item", "Price"},
    {
        {1, 1, "Fishing rod", 100.0},
        {2, 1, "1 lb. worms", 5.0},
        {3, 2, "Fishing net", 25.0},
        {4, 3, "Fish tazer", 200.0},
        {5, 3, "Bandaids", 2.0},
        {6, 1, "Tackle box", 20.0},
        {7, 5, "Bait", 3.25},
        {8, 5, "Fishing Rod", 100.0},
        {9, 6, "Bait", 3.25}
    }
)"""


def order_ids(criteria: str) -> list[int]:
    rows = evaluate(f"Table.Sort({ORDERS}, {criteria})")
    return [row["OrderID"] for row in rows]


def test_example_1_a_single_column_in_a_list() -> None:
    """`{"OrderID"}` - one name, wrapped, ascending."""
    assert order_ids('{"OrderID"}') == [1, 2, 3, 4, 5, 6, 7, 8, 9]


def test_example_2_the_bare_pair() -> None:
    """`{"OrderID", Order.Descending}` - the shape that used to fail.

    Not wrapped in an outer list. It is unambiguous even though it looks like
    a two-column list: column names are text, so a number in second position
    can only be an Order.
    """
    assert order_ids('{"OrderID", Order.Descending}') == [9, 8, 7, 6, 5, 4, 3, 2, 1]


def test_example_3_a_pair_and_a_bare_name_mixed() -> None:
    """`{{"CustomerID", Order.Ascending}, "OrderID"}` - the docs' output order."""
    assert order_ids('{{"CustomerID", Order.Ascending}, "OrderID"}') == [
        1,
        2,
        6,
        3,
        4,
        5,
        7,
        8,
        9,
    ]


def test_a_bare_column_name_needs_no_list_at_all() -> None:
    assert order_ids('"OrderID"') == [1, 2, 3, 4, 5, 6, 7, 8, 9]


def test_two_bare_names_are_still_two_columns() -> None:
    """The disambiguation, from the other side.

    `{"a", "b"}` must not be read as column `a` with order `b`; the bare-pair
    rule is keyed on the second element being a NUMBER. Getting this wrong
    silently sorts by one column instead of two, which no error would reveal.
    """
    # CustomerID ascending, then Item as text: "1 lb. worms" < "Fishing rod"
    # < "Tackle box" inside customer 1.
    assert order_ids('{"CustomerID", "Item"}') == [2, 1, 6, 3, 5, 4, 7, 8, 9]


def test_the_wrapped_pair_still_works() -> None:
    # Power Query's own UI emits this form; the fix must not have traded one
    # accepted shape for another.
    assert order_ids('{{"OrderID", Order.Descending}}') == [9, 8, 7, 6, 5, 4, 3, 2, 1]


def test_a_logical_is_not_an_order() -> None:
    """`true` is not `Order.Descending`.

    In Python `bool` subclasses `int`, so an implementation that only checks
    "is the second element a number" accepts `{"Col", true}` and sorts
    descending. M has no such coercion.
    """
    with pytest.raises(UnsupportedError):
        evaluate(f'Table.Sort({ORDERS}, {{"OrderID", true}})')


def test_an_out_of_range_order_is_rejected() -> None:
    # Order.Ascending = 0 and Order.Descending = 1. There is no 7.
    with pytest.raises(UnsupportedError, match="Order.Ascending or Order.Descending"):
        evaluate(f'Table.Sort({ORDERS}, {{{{"OrderID", 7}}}})')


# --------------------------------------------------------------------------
# The same parser, reached through Table.MaxN / Table.MinN
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        ('Table.MaxN({}, {{"OrderID", Order.Descending}}, 2)', [1, 2]),
        ('Table.MinN({}, {{"OrderID", Order.Descending}}, 2)', [9, 8]),
        ('Table.MaxN({}, "OrderID", 2)', [9, 8]),
        ('Table.MinN({}, "OrderID", 2)', [1, 2]),
    ],
)
def test_maxn_and_minn_share_the_parser(call: str, expected: list[int]) -> None:
    """The second copy of the bug lived here.

    A descending criterion inverts what "biggest" means, so MaxN with
    Order.Descending returns the smallest OrderIDs - the same behaviour
    Table.Sort would give followed by taking the first rows.
    """
    rows = evaluate(call.format(ORDERS))
    assert [row["OrderID"] for row in rows] == expected
