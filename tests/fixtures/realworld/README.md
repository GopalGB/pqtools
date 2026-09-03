# Real-world goal fixtures (pqtools 0.5.0)

These four scenarios are the terminal condition for `PRD-0.5.0-builtins.md`
("Done means: the four reference queries in `tests/fixtures/realworld/`
evaluate to correct results"). They are wired up by `tests/test_realworld.py`.

Each scenario directory has:

- one or two `*.csv` files - the data the CLI would receive via
  `--bind NAME=file.csv`
- `query.pq` - the M query, written the way Power Query's own UI emits it
- `expected.json` - the output table, worked out independently of pqtools

None of these currently pass. They are marked
`@pytest.mark.xfail(strict=False)` in the test module, each naming the exact
builtin the evaluator dies on today. **Do not weaken a fixture to make it
pass** - when pqtools implements the missing builtins, someone flips the
markers to strict (or removes them) as the real regression gate.

## Why the CSVs look "double-headered"

Power Query's `Csv.Document` does **not** promote headers by default - it
returns a table with generic column names (`Column1`, `Column2`, ...) and
the file's real header row as the *first data row*. The UI's "From CSV"
wizard always follows it with a `Table.PromoteHeaders` step to fix that up.
So every fixture CSV here has that literal shape:

```
Column1,Column2,Column3,...      <- the row csv.DictReader (and Csv.Document) treats as the header
RealHeader1,RealHeader2,...      <- becomes the FIRST DATA ROW - this is what Table.PromoteHeaders promotes
1001,Alice Chen,...              <- actual data starts here
```

This matters because `pq eval --bind Source=file.csv` loads the file with
`csv.DictReader` (see `cli.py::_load_binding`) - it does not know or care
that the query later calls `Table.PromoteHeaders`. If the CSV's first row
were the *real* headers, `Table.PromoteHeaders` would incorrectly promote
the first real data row instead. Structuring the CSV this way is what makes
`--bind Source=...` an honest stand-in for what `Csv.Document` itself
would have returned.

## Comparison rules the test uses (and why)

`tests/test_realworld.py::_values_equal` is deliberately tolerant of a few
representation choices that are implementation details, not correctness:

- **Floats are compared with `math.isclose`** (`rel_tol=abs_tol=1e-9`), not
  `==`. `560.0 * 1.08 == 604.8` is `False` in IEEE-754 double arithmetic
  (`560.0 * 1.08` is `604.8000000000001`) even though `604.8` is the
  mathematically exact answer - expected values are the numbers a human
  doing the arithmetic gets, not whatever bit pattern Python's float
  multiplication happens to produce.
- **Dates in `expected.json` are ISO strings** (`"2024-01-15"`). A real
  implementation will likely represent `type date` values as Python
  `datetime.date` objects, not strings; the comparison accepts either an
  exact string match or `actual.isoformat() == expected`.

Neither of these loosens what's being asserted about *values* - only how a
correct value may be represented.

---

## 1. `01_clean_and_type/` - clean-and-type a CSV import

The single most common Power Query UI output: import a CSV, promote its
headers, fix column types, filter, rename, add a calculated column, sort.

**Functions used:** `Csv.Document`, `File.Contents` (never executed - the
connector step, skipped via `--bind Source=...`), `Table.PromoteHeaders`,
`Table.TransformColumnTypes`, `Int64.Type`, `type text`, `type number`,
`type date`, `Table.SelectRows`, `Table.RenameColumns`, `Table.AddColumn`,
`Table.Sort`, `Order.Descending`.

**M syntax source:** the `Csv.Document`/`Table.PromoteHeaders`/
`Table.TransformColumnTypes` three-step shape (including the
`[Delimiter=",", Columns=N, Encoding=1252, QuoteStyle=QuoteStyle.None]`
options record on `Csv.Document`) was cross-checked against a real
generated-code example on Chris Webb's BI Blog, "Improve Power Query
performance on CSV files containing Date columns" (2025-02-09,
blog.crossjoin.co.uk) - the exact shape Power Query's Text/CSV import
wizard produces.

**Data:** 8 orders across 4 regions, amounts spanning both sides of the
`> 100` filter threshold (including one order at exactly `100.00`, which
the filter must exclude since the query is `> 100`, not `>= 100`).

**Expected output derivation:** filter (`Amount > 100`) and sort
(`Amount` descending) were applied by hand to the 8 source rows, leaving 5.
`AmountWithTax` (`Amount * 1.08`) was computed with plain Python float
arithmetic (`python3 -c "print(430.25*1.08)"` etc., not pqtools) and the
two rows whose product isn't exactly representable in binary floating point
(`560.00*1.08` and `310.40*1.08`) were confirmed by hand: `560 * 1.08 =
604.80` and `310.40 * 1.08 = 335.232` exactly in decimal arithmetic; the
extra float-precision digits Python prints (`604.8000000000001`,
`335.23199999999997`) are a representation artifact, which is exactly why
the test compares floats with a tolerance instead of exact equality.

**Verified failure (pre-0.5.0 evaluator, 2026-09-03):**
```
UnsupportedError: unknown identifier: Table.TransformColumnTypes
```
`Table.Sort`, `Table.AddColumn`, `Table.RenameColumns`, and
`Table.SelectRows` are all already implemented, so the evaluator walks the
whole `let` chain backwards from `#"Sorted Rows"` before hitting the first
real gap at `#"Changed Type" = Table.TransformColumnTypes(...)`.

---

## 2. `02_group_and_aggregate/` - group and aggregate

The pandas-`groupby`-equivalent: `Table.Group` with four aggregations
(sum, average, count, max) over a category key.

**Functions used:** `Csv.Document`, `File.Contents`, `Table.PromoteHeaders`,
`Table.TransformColumnTypes`, `Table.Group`, `List.Sum`, `List.Average`,
`Table.RowCount`, `List.Max`, `Int64.Type`, `type number`.

**M syntax source:** the `Table.Group(table, {"Key"}, {{"Name", each
<agg>([Col]), type}, ...})` shape - including the key argument being a
*list* (`{"Region"}`) even for a single grouping column, which is what
Power Query's own Group By dialog emits, not a bare string - was
cross-checked against TrumpExcel's "Table.Group Function in Power Query M
(9 Examples)" and a Microsoft Fabric Community generated-code example using
`Table.Group(#"Changed Type", {"Type"}, {{"Date1", each List.Min([Date]),
type nullable datetime}, ...})`.

**Data:** 10 transactions across 3 regions, **deliberately interleaved**
(East, West, East, North, West, East, North, West, East, North) rather than
sorted/grouped by region. This is the actual "group edge case": `Table.Group`
defaults to `GroupKind.Global` (confirmed via Microsoft's own
`GroupKind.Type` docs and independent community sources), which groups
*all* matching rows regardless of position - `GroupKind.Local` would only
merge *consecutive* runs of the same key and would silently produce extra,
wrong groups on this same interleaved data. The fixture pins the Global
behavior a naive/incorrect Local-style implementation would fail.

**Expected output derivation:** grouped and summed by hand per region
(East: 50+75+300+25=450, avg 112.5, count 4, max 300; West: 200+120+60=380,
avg 380/3=126.666..., count 3, max 200; North: 30+45+150=225, avg 75, count
3, max 150). Row order (East, West, North) is the order each region *first
appears* in the source data - confirmed via SpreadsheetPlanet's
`Table.Group` documentation ("group rows appear in first-appearance order,
matching when each key first shows up in the source"), not alphabetical
and not sorted by any aggregate.

**Verified failure (pre-0.5.0 evaluator, 2026-09-03):**
```
UnsupportedError: unknown identifier: Table.Group
```
`Table.Group` is the query's own final step, so the evaluator fails
resolving its callee before ever evaluating `#"Changed Type"` underneath.

---

## 3. `03_merge_two_tables/` - merge two tables

The standard PQ merge: `Table.NestedJoin` (the primitive the UI's "Merge
Queries" dialog actually emits) followed by `Table.ExpandTableColumn`, with
an explicit `JoinKind`, and a genuinely non-matching row so join semantics
are exercised, not just a lookup that always hits.

**Functions used:** `Csv.Document`, `File.Contents` (used *twice*, once per
source table), `Table.PromoteHeaders`, `Table.TransformColumnTypes`,
`Table.NestedJoin`, `JoinKind.LeftOuter`, `Table.ExpandTableColumn`.

Two CSVs, two independent `--bind` names (`Source` for the left/`orders`
table, `CustomersSource` for the right/`customers` table) - both are
connector-fed steps in the one query file, which is what `pq eval --bind
NAME=path` (repeatable) is for.

**M syntax source:** the `Table.NestedJoin(left, {key}, right, {key},
"NewCol", JoinKind.LeftOuter)` + `Table.ExpandTableColumn(joined, "NewCol",
{cols}, {cols})` two-step shape was cross-checked against SpreadsheetPlanet's
`Table.NestedJoin` examples (`Table.NestedJoin(Orders, {"SKU"}, Products,
{"SKU"}, "ProductData", JoinKind.LeftOuter)` +
`Table.ExpandTableColumn(Joined, "ProductData", {"Product", "Price"},
{"Product", "Price"})`).

**Data:** 6 orders referencing 4 possible customers. `CustomerID=1` and
`CustomerID=2` each appear on two different orders (duplicate-key matches -
both orders must expand to the *same* customer row, independently).
`CustomerID=99` appears on exactly one order and does not exist in the
customers table at all (the non-match row `LeftOuter` must keep and expand
to nulls). `CustomerID=4` (David Wu) exists only in the customers table and
has no orders - under `LeftOuter` from the orders side, he simply never
appears in the output; that's not a bug, it's what distinguishes
`LeftOuter` from `FullOuter`.

**Expected output derivation:** joined by hand, row-by-row, on `CustomerID`.
The one subtlety that isn't obvious from the M source and is worth pinning
explicitly: per Power Query's documented `Table.NestedJoin` +
`Table.ExpandTableColumn` behavior (confirmed via mrexcel.com community
discussion of exactly this pattern), an unmatched row under `LeftOuter`
gets an **empty nested table** (0 rows), not a `null` in the join column -
and `Table.ExpandTableColumn` turns that empty nested table into `null` in
*each* expanded column for that row, while still keeping the row itself.
So `OrderID=5005` (`CustomerID=99`) is expected to survive as one row with
`CustomerName: null, Tier: null` - it must not be dropped, and the
`CustomerData` intermediate value for that row must not error out as
though the join failed.

**Verified failure (pre-0.5.0 evaluator, 2026-09-03):**
```
UnsupportedError: unknown identifier: Table.ExpandTableColumn
```
This is the non-obvious one: `Table.ExpandTableColumn` is the query's
*last* step, so the evaluator resolves (and fails on) its callee before it
ever evaluates its first argument - the `#"Merged Queries"` step calling
`Table.NestedJoin`, which is equally unimplemented. Once
`Table.ExpandTableColumn` lands, `Table.NestedJoin` becomes the next wall
for this specific fixture, not the first one; implementing builtins in the
PRD's stated priority order (P1 lists `Table.NestedJoin` before
`Table.ExpandTableColumn`) will avoid this particular reordering trap.

---

## 4. `04_unpivot_and_dates/` - unpivot wide data + dates

A wide per-category-column table gets unpivoted to long form, plus real
date decomposition (`type date`, then `Date.Year` / `Date.MonthName`) - the
other extremely common GUI-generated shape (wide monthly/categorical
exports need reshaping before they're usable).

**Functions used:** `Csv.Document`, `File.Contents`, `Table.PromoteHeaders`,
`Table.TransformColumnTypes` (`type date` on `OrderDate`, `type number` on
the three category columns), `Table.UnpivotOtherColumns`,
`Table.SelectRows`, `Table.AddColumn`, `Date.Year`, `Date.MonthName`,
`Int64.Type`, `type text`.

**M syntax source:** `Table.UnpivotOtherColumns(table, {pivotCols},
"Attribute", "Value")` was cross-checked against Microsoft's own
`query-docs` GitHub source and a documented "wide to long" example that
keeps exactly two columns (`{"OrderDate", "Country"}`) and unpivots
everything else - this fixture's `{"Region", "OrderDate"}` pivot-column
list mirrors that shape directly (keep the dimension columns, unpivot the
rest). The `Table.AddColumn(prev, "Year", each Date.Year([Col]),
Int64.Type)` / `..."Month Name", each Date.MonthName([Col]), type text)`
shape (including the 4-argument form with the trailing type, and the
"Inserted Year" / "Inserted Month Name" step-naming convention) matches
what Power Query's Add Column > Date > Year / Month > Name of Month
transforms generate - cross-checked via a Power BI community "date table"
generated-code example using the identical
`Table.AddColumn(#"Changed Type", "Year", each Date.Year([Date]),
Int64.Type)` pattern.

**Data:** 5 rows (Region, OrderDate, and three category revenue columns:
Electronics/Clothing/Home), spanning two different years and five
different months, with several `0` revenue cells to exercise the
"drop zero/blank unpivoted rows" filter that's near-universal in real
unpivot-then-filter queries.

**Expected output derivation:** unpivoted by hand, one output row per
(source row × non-zero category), in row-major then source-column order
(Electronics, Clothing, Home) - 15 raw unpivoted combinations, 6 dropped
for zero revenue, 9 remaining. `Date.Year`/`Date.MonthName` were read off
each `OrderDate` by hand (e.g. `2024-07-09` → year `2024`, month name
`"July"`).

**Verified failure (pre-0.5.0 evaluator, 2026-09-03):**
```
UnsupportedError: unknown identifier: Table.UnpivotOtherColumns
```
`Table.AddColumn` (used twice, for `Year` and `Month Name`) is already
implemented, so the evaluator walks past both of those steps and dies on
`#"Unpivoted Other Columns"` underneath - `Date.Year` and `Date.MonthName`
(P2 in the PRD) are never even reached by the current evaluator.

---

## How the "verified failure" sections were produced

Each was captured by loading the fixture the same way `pq eval --bind
NAME=file.csv` does (`pqtools.cli._load_binding`, i.e. `csv.DictReader`)
and calling `pqtools.evaluate.evaluate(query_text, bindings=...)` directly
against the pre-0.5.0 evaluator, then recording the exact exception raised.
This is not a guess - `tests/test_realworld.py` asserts the identical
`UnsupportedError` today (verified with `pytest --runxfail`, which forces
the xfail-marked tests to run and shows the real traceback instead of
being swallowed).
