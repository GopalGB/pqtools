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
evidence is Microsoft's own worked examples: of 786 harvested examples, 165
print an output that is itself evaluable M, and **141 of those 165 reproduce
their documented value exactly**. The remaining 24 are individually accounted
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
