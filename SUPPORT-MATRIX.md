# Support matrix - pqtools 0.10.0

**This file is the authoritative current statement of what pqtools supports.**
Where any other document disagrees with it, this one is right and the other is
stale. `ops/STATUS.md` and `.planning/m-inventory.md` are historical records of
earlier releases and are marked as such; they are kept for provenance, not for
reference.

Every number here is produced from the live registry and the checked-in
fixtures, and `tests/test_support_matrix.py` fails if any of them drifts.

## The three numbers that are not the same number

The single most misleading thing that can be said about this package is "86%
compatible". That figure is a count of **names**, and a name is the cheapest of
the three things a caller depends on.

| Measure | Count | What it does and does not tell you |
|---|---|---|
| Names in Microsoft's function reference | 635 | The denominator. |
| ... of those, registered here | 547 | **86.1% of NAMES.** The name resolves. Nothing more. |
| ... of those, refused by name with a reason | 88 | An honest refusal, not a gap in the catalog. |
| Total registry entries | 640 | 554 callables + 86 enum/type values. Larger than 547 because enum values (`Order.Ascending = 0`) are registry entries that are not function names. |
| Signatures verified against the reference | 547 | Arity **and** nullability, per builtin, from `tests/fixtures/m-signatures.json`. |
| Enum values verified against the reference | 73 | The actual numbers, from `tests/fixtures/m-enum-values.json`. |

**Semantic compatibility is not measured, and is not claimed.** The closest
evidence is Microsoft's own worked examples:

| Worked examples | Count |
|---|---|
| Harvested from the reference | 786 |
| ... whose printed Output is itself evaluable M | 165 |
| ... of those, reproducing their documented value exactly | 141 |

**141 of 165** is the closest thing to a semantic measure this package has. The remaining 24 are individually accounted
for in `tests/test_doc_examples.py` - 3 recorded divergences and 21 examples
that cannot be compared here, each with its reason. The other 621 examples have
no printed output to check against, so they prove only that the expression runs.

That is the honest ceiling of what is known. It is not 86%, and no number in
this file should be quoted as "compatibility" without the word it belongs to.

**This package does not implement Microsoft's Mashup Engine and does not claim
compatibility with it.** Anything that requires the engine itself raises a typed
error naming the construct.

## Connectors

A query's `Source` step names where the data comes from. These run here.

| Source | Status | Needs |
|---|---|---|
| `Csv.Document`, `File.Contents`, `Json.Document`, `Lines.*` | works | nothing |
| `Folder.Files`, `Folder.Contents` | works | nothing |
| `Excel.Workbook` | works | `pip install 'pqtools[excel]'` |
| `Web.Contents` | works | `--allow-net` |
| `OData.Feed` | works, follows `@odata.nextLink` | `--allow-net` |
| `Sql.Database` | works | `--allow-db` + `pqtools[sql]` |
| `Odbc.Query`, `Odbc.DataSource` | works | `--allow-db` + `pqtools[sql]` |
| `PostgreSQL.Database` | works | `--allow-db` + `pqtools[postgres]` |
| `MySQL.Database` | works | `--allow-db` + `pqtools[mysql]` |
| `Oracle.Database` | works, `[Query=...]` only | `--allow-db` + `pqtools[oracle]` |
| `SharePoint.*`, `Web.Headers` | refuses, by name | - |

**No query folding.** `Table.SelectRows` after a database source filters
locally; it does not become a `WHERE` clause. Same rows, more bytes.

**`Sql.Database` navigation is lazy, and the laziness is visible.** Each row
of the navigation table carries a `Data` field that is a `pqtools.DeferredTable`,
not a list: the `SELECT *` runs only when that row is selected, so reading one
table does not read every other table in the catalog.

- In M, selecting it reads it: `Source{[Schema="dbo", Item="Orders"]}[Data]`.
- From Python, `evaluate()` hands you the object; call `.read()` on it.
- Every other way of touching it - `len`, `iter`, `==`, `bool` - raises a
  typed error rather than answering. A lazy value that returned "empty" on a
  path nobody anticipated would be the silent wrong answer this package
  exists to refuse.
- `pq eval` refuses to print a navigation table for the same reason: printing
  it would mean running one query per catalog entry. Select the item first.

### Connector options, honoured vs refused

`Sql.Database` documents twelve options. Silently discarding one is the defect
class this table exists to prevent, so each is either implemented or refused by
name.

| Option | Sql.Database | Odbc.* | PostgreSQL / MySQL / Oracle |
|---|---|---|---|
| `Query` | honoured | honoured | honoured (required - navigation is refused) |
| `CommandTimeout` | honoured (duration) | honoured | refused by name |
| `ConnectionTimeout` | honoured (duration) | honoured | refused by name |
| `MultiSubnetFailover` | honoured (sets `MultiSubnetFailover=Yes` and `ApplicationIntent=ReadOnly`) | n/a | n/a |
| `HierarchicalNavigation` | `false` only; `true` refused by name | n/a | refused by name |
| `SqlCompatibleWindowsAuth` | n/a | `true` only | n/a |
| `CreateNavigationProperties`, `NavigationPropertyNameGenerator`, `MaxDegreeOfParallelism`, `UnsafeTypeConversions`, `ContextInfo`, `OmitSRID`, `EnableCrossDatabaseFolding` | refused by name | n/a | refused by name |

The capability difference between the families is real and deliberate: the two
timeouts map onto mechanisms `pyodbc` documents, and no equivalent was
implemented for the DB-API drivers rather than guessed at. Every unimplemented
option refuses by name in every family, so the *policy* is uniform even where
the capability is not.

**Credentials are read from the environment only** - `PQTOOLS_SQL_USER`,
`PQTOOLS_SQL_PASSWORD`, `PQTOOLS_PG_*`, `PQTOOLS_MYSQL_*`, `PQTOOLS_ORACLE_*`.
`[Username=...]` and `[Password=...]` in an M file are **refused by name**, and
neither is a documented Power Query option in the first place.

## Why a name can be refused

The 88 documented-but-refused names each carry one of these reasons, recorded in
`pqtools.catalog.REASONS` and printed with the refusal:

| Reason | Meaning |
|---|---|
| `engine` | Evaluated by the Mashup Engine itself, not the M standard library. |
| `connector` | Needs vendor credentials, an OAuth identity, or a proprietary driver. |
| `folding` | Exists to control query folding into a remote engine. |
| `culture` | Culture-sensitive; only the invariant/en-US culture is implemented. A wrong separator turns `1.234` into `1234`, so another culture is refused rather than approximated. |
| `fuzzy` | Approximate matching whose similarity algorithm Microsoft does not document precisely enough to reproduce. |
| `model` | Reads a Power BI data model a standalone evaluator has no access to. |
| `shape` | A format that could be parsed, but whose resulting table shape is not documented. |
| `internal` | Marked "intended for internal use only" in Microsoft's own reference. |
| `notyet` | A real, implementable function that is simply not implemented yet. |

Only `notyet` is a gap in this package. The other eight are statements about
what a standalone M evaluator can know.

## Implemented names with a narrowed branch

A name in the registry is not a promise that every branch of its documented
behaviour is implemented. Where a branch is not, the function **refuses that
input by name** rather than returning a value from a branch that does apply -
a wrong value with nothing in it to say so is the failure this package exists
to avoid.

| Name | Branch not implemented | What the input does instead |
|---|---|---|
| `Value.FromText` | `datetime` and `duration`. The page's return union is "number, logical, null, datetime, duration, or text", but it publishes no invariant-culture rule for either - its only datetime example passes `"de-DE"`. | Text that reads as a date, time, datetime or duration is **refused by name**, pointing at `Date.FromText` / `Time.FromText` / `DateTime.FromText` / `Duration.FromText`, which each state their format. The `number`, `logical`, `null` and `text` branches are unaffected: `"12345.6789"`, `"25.4%"`, `"true"`, `""` and `"hello world"` all behave as documented. |

What counts as "reads as" is not invented for this refusal: it is exactly
what this package's own `Date.FromText`, `Time.FromText`, `DateTime.FromText`
and `Duration.FromText` accept with no format argument - ISO 8601 plus the
en-US patterns they ground on Microsoft's own examples (`"Apr 8, 2022"`,
`"10:12:31am"`) - asked, not re-implemented. Text those parsers reject stays
text: `Value.FromText("P1D")` is `"P1D"` and `Value.FromText("24:00")` is
`"24:00"`, because `Duration.FromText` is an error for both.

## Refactoring scope

`pq rename` renames exactly **one unquoted top-level `let` binding**.

Its guard is **textual and whole-file**, which is stricter than it may sound: a
`#"` quoted identifier, a `[` (record literal *or* field access), a `=>`, or any
non-ASCII character **anywhere in the source - including inside a string literal
or a comment** - refuses the whole rename. Nested `let` scopes are refused too.

This is deliberate over-refusal. A rename that is right most of the time is
worse than one that declines, because the failure is a silently altered query.
Narrowing the guard requires binding-aware analysis of the parse tree, not a
smaller set of forbidden characters.

The refusal names **which of the four it found and at what line and column**,
so the scope above is something you can act on rather than four things to hunt
for by hand across a file. `pq explain M_RENAME_REFUSED` says the same in plain
English. That is the only thing that changed: the decision is held to the
original expression, source by source, by
`test_the_rename_guard_refuses_exactly_what_it_always_did`.

## Getting the rows out

`to_pandas()`, `to_arrow()`, `to_parquet()` (and `pq eval --to parquet`).
`pandas` and `pyarrow` are optional extras; with neither installed the package
imports and every other verb works, and the export functions refuse by name
with the install command.

The type map is one M type per column, and a column whose values do not share
one M type is refused rather than widened to `object`. What survives:

| M | pandas | Arrow |
|---|---|---|
| logical | `boolean` (nullable) | `bool` |
| number, all integral | `Int64` (nullable) | `int64` |
| number, any fractional | `float64` | `double` |
| text | `object` | `string` |
| binary | `object` | `binary` |
| date | `object` (`datetime.date`) | `date32` |
| time | `object` (`datetime.time`) | `time64[us]` |
| datetime | `datetime64[us]` | `timestamp[us]` |
| datetimezone | `datetime64[us, tz]` | `timestamp[us, tz]` |
| duration | `timedelta64[us]` | `duration[us]` |

The nullable `Int64` matters: pandas' default integer column cannot hold a
null, so a naive export turns `1, null, 3` into `1.0, NaN, 3.0` and silently
changes the type of every row to get one null in.

This table is the same at both ends of the declared dependency range -
`pandas>=2`, `pyarrow>=14` - and not only on whichever version happens to be
installed. Verified by running the export suite against pandas 2.3.3 /
pyarrow 14.0.2 as well as 3.0.5 / 25.0.1; the two dtype tables are identical
and all 71 tests pass on each. The three native dtypes are coerced
explicitly rather than inferred, which is what makes that true: pandas 2
infers nanosecond resolution where pandas 3 infers microsecond.

A `datetimezone` column at a non-UTC offset lands as
`datetime64[us, UTC+05:30]`, and that interpolated form parses identically on
both ends of the range - the tz is derived from `utcoffset()`, so a column
mixing `UTC` and `Europe/London` in January is one column, not `object`. The
measurement is
[evidence/export-dtypes-across-the-declared-range-2026-09-07.txt](evidence/export-dtypes-across-the-declared-range-2026-09-07.txt),
re-run against this tree; the 2026-09-06 version of that file reported a
count taken before the change it was certifying.

**Opening a file is an `OSError`, not an `MQueryError`.** Everything this
package *refuses* raises a typed `MQueryError` subclass, and the CLI renders
it with an `M_*` code. A file that will not open is different: `pqtools.open`,
`update_file`, `read_sections` and `PqFile` let the `FileNotFoundError` /
`PermissionError` through unwrapped, because the OS's own reason is the true
one and reporting a missing file as `M_SAFE_WRITE_REFUSED: writes require a
regular, non-symlink, single-link file` told the reader nothing true. A
library caller that catches only `MQueryError` must also catch `OSError`. The
CLI already does, and renders it as `M_IO_ERROR`. The genuine write-safety
refusals - a symlink (including one swapped in after the check, which
`O_NOFOLLOW` catches), a non-regular file, more than one hard link - stay
`SafeWriteError`.

**Refused, by name, rather than exported:** an unread lazy table (select it
first), a record, a nested list or table (expand it first), a `type` value, a
function value, ragged rows, a column mixing two M types, an integer past the
64-bit range, a `datetimezone` column mixing UTC offsets, and - on the pandas
path only - a number column holding both `null` and `#nan`, which `float64`
represents identically. Arrow keeps a real null bitmap and so accepts that
last one.

## Tool-level gaps, named

The sections above are about M. These three are about the tool, and they are
listed for the same reason: a gap a user can discover by being wrong is worse
than one stated here. Each is a typed refusal today, not a silent partial
answer.

**Query-to-query dependencies.** `dependencies()` reports the builtin
functions a query calls. It does **not** report which other queries in a
multi-query file a query references. Asking it for that returns the builtin
list, which is a true answer to a different question - so read it as "what
does this call", never as "what does this depend on". A real answer needs
binding-aware analysis of the parse tree, the same thing `pq rename`'s guard
is deliberately over-strict for want of; approximating it would produce a
dependency graph that is right most of the time, and a graph that is wrong
about one edge is worse than no graph, because nothing in the output says
which edge.

**Fabric and PQTest results.** Both adapters exist and both are reachable as
adapters. `pq eval` does not route through them, and the Fabric adapter
returns the service's raw Arrow bytes rather than decoding them to rows.
Decoding is refused rather than guessed: the payload is a versioned binary
format from a service this package cannot test against offline, and a decode
that is subtly wrong produces plausible rows, which is the one failure this
package promises not to have.

**`.pbip` / TMDL write-back.** `.pbip` projects are readable. Writing to them
raises `SafeWriteError`. TMDL round-trip fidelity - preserving everything the
format carries that this package does not model - is a separate piece of work,
and a partial write to a project file is a corrupted project.

## What enforces this file

| Claim | Enforced by |
|---|---|
| Every registered name is in Microsoft's reference | `tests/test_catalog.py` |
| Arity and nullability, per builtin | `tests/test_documented_signatures.py` |
| Enum numbers, per `*-type` page | `tests/test_documented_enum_values.py` |
| Worked examples reproduce documented output | `tests/test_doc_examples.py` |
| The counts in this file | `tests/test_support_matrix.py` |
| The README's builtin list and count | `tests/test_readme_builtins.py` |
| Connector options honoured or refused | `tests/test_sql_options.py` |
| Credentials environment-only, values escaped | `tests/test_sql_credentials.py` |
