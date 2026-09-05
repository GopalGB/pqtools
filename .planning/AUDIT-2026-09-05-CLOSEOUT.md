# Audit closeout - AUDIT-2026-09-05

Baseline `c7ffc5f` (0.10.0). Four commits, `c7ffc5f..a4f5735`. No push, no tag,
no publish, no deploy, nobody contacted. No other MAX HQ project touched.

Companion to [AUDIT-2026-09-05.md](AUDIT-2026-09-05.md), which is preserved
unchanged as the statement of the problem.

**Read the scope limits at the bottom before quoting anything here.** Everything
verified below is LOCAL. No live database, Fabric tenant, Windows PQTest, native
Excel or Power BI refresh was exercised, and no credentials were used.

## Dispositions

| # | Finding | Disposition | Commit |
|---|---|---|---|
| 1 | HIGH - unsafe workbook backup destination | FIXED | `2e27afd` |
| 2 | HIGH - OData pagination silently truncates | FIXED | `f4fdeac` |
| 3 | HIGH - SQL navigation eagerly reads every table | FIXED | `f4fdeac` |
| 4 | MEDIUM - SQL options silently ignored | FIXED | `f4fdeac` |
| 5 | MEDIUM - credential policy differs from documentation | FIXED (code changed, not docs) | `f4fdeac` |
| 6 | MEDIUM - compatibility gates miss defects | FIXED, and wider than reported | `58f439c` |
| 7 | Requirements and documentation reconciliation | DONE, and wider than reported | `a4f5735` |

Every fix was **reproduced end to end before being changed** and
**positive-controlled after**: the defect was reintroduced and the new tests
were watched going red, then restored. Red counts are given per finding.

### 1. Backup destination - FIXED

`src/pqtools/cli.py`. `_container_backup` wrote `<name>.bak` with a plain
write, so it followed a symlink at that path, overwrote a real backup, and did
not stop the edit when it failed.

Now `os.open` with `O_CREAT|O_EXCL|O_NOFOLLOW`, preserving existing backups by
moving to `.bak.1`...`.bak.99`, fsyncing, carrying the source mode over, and
raising before `containers.write_sections` so a failed backup leaves the
container untouched.

`tests/test_container_backup_safety.py`, 11 tests over both callers
(`format --write`, `add --write`), temporary files only, no real user file.
Positive control: 8 of 11 red.

### 2. OData pagination - FIXED

`src/pqtools/builtins/_sources.py`. `OData.Feed` read page one and returned it
as the whole feed.

Now follows `@odata.nextLink` and the v3 `odata.nextLink`, absolute or relative
(`urljoin`), applying `policy.check_net` to every request **and every redirect**,
bounded by a `seen` cycle check, `ctx.budget.tick()`, and a 200-page ceiling.
Every bound raises a typed `EvalError` rather than returning a prefix. A
malformed page raises instead of surfacing a `JSONDecodeError`.

`tests/test_odata_pagination.py`, 13 tests against a local HTTP server.
Includes a next link to a blocked host, asserting the second request was never
made. Positive control: 11 of 13 red.

### 3. SQL navigation - FIXED

`_sources.py`, `builtins/_shared.py`, `evaluate.py`. `Sql.Database` navigation
ran `SELECT *` against every table in the catalog before the query said which
one it wanted.

Each row's `Data` is now a `_DeferredRows` that reads on selection, on its own
short-lived connection. It **refuses loudly on every consumption path except its
own** (`__iter__`, `__len__`, `__getitem__`, `__contains__`, `__eq__`,
`__bool__`, and `__hash__ = None`), so a path this change missed is a typed
error rather than a silently empty table. `_require_list`, `_require_table` and
`_record_field_access` force it.

Scope was checked rather than assumed: PostgreSQL, MySQL and Oracle refuse
navigation without `[Query=...]` and `Odbc.DataSource` returns rows with no
`Data` field, so none of them had this defect.

`tests/test_sql_navigation.py`, 10 tests on a mock `pyodbc`, asserting
connections opened equals connections closed. Positive control: 6 of 10 red.
A full sequential suite was run after this change specifically because
`_require_list` is a hot path: **3780 passed**.

### 4. SQL options - FIXED

`_sources.py`. `CommandTimeout`, `HierarchicalNavigation` and
`MultiSubnetFailover` were popped off the options record and discarded.
`Odbc.Query`, in the same file, had validated and applied the identical
`CommandTimeout` all along, so the two connector families disagreed about one
documented option.

Microsoft's page documents **twelve** options, not the four the code knew about.
Now: `CommandTimeout` and `ConnectionTimeout` validated as durations and applied
to the catalog connection **and the deferred read** (a timeout that stopped at
the catalog would bound the one query that was never the slow one);
`MultiSubnetFailover` writes both properties its page documents, including the
easily-missed `ApplicationIntent=ReadOnly`, and refuses to combine with an
explicit `ConnectionString`; `HierarchicalNavigation` accepts its documented
default `false` and refuses `true` by name rather than inventing a grouped
table shape the page does not specify. The remaining seven refuse by name, as
they already did.

`tests/test_sql_options.py`, 26 tests. Positive control: 12 of 26 red - and the
14 that stayed green are exactly the cases the defect never touched.

### 5. Credentials - FIXED IN CODE, NOT PAPERED OVER IN DOCS

`_sources.py`. Two defects, verified before either was changed.

**Policy.** The README states twice, as a security property, that credentials
come from the environment and not from the M file. `_credentials` read
`[Username=...]`/`[Password=...]` and **preferred them over** the environment.
Neither is a documented Power Query option - the connector pages list no
credential options at all. Both are now refused by name, before any connection,
naming the variable to set instead. **The code was changed to match the
documentation, not the reverse.**

**Escaping - this one is a security defect, not a correctness one.** Values were
interpolated into the ODBC connection string raw. A password of
`x;Encrypt=no` did not produce a wrong password; it produced an extra
connection-string **keyword**, and that one turns TLS off. Server, database and
user were injectable the same way, and a password legitimately containing `;` or
`}` could not be expressed at all. Values are now brace-quoted with `}` doubled,
at **both** sites that build a connection string - the record form of
`Odbc.Query`/`Odbc.DataSource` had the identical hole and is fixed in the same
pass.

`tests/test_sql_credentials.py`, 15 tests. They parse the connection string the
way a driver does, because a substring assertion cannot tell `PWD={a;b}` from
`PWD=a;b` - which is the entire difference the fix makes. Every credential is a
dummy literal; no real credential was read or inspected. Positive control: 10 of
15 red.

### 6. Compatibility gates - FIXED, AND THE GAP WAS WIDER THAN REPORTED

**Arity gate.** `tests/test_documented_signatures.py` reads the source for a
literal `_arity("Name", args, ...)`. A builtin registered through a helper writes
`_arity(name, args, ...)` with `name` as a variable, matches nothing, and drops
out of the comparison silently.

The audit named two connectors. The real number is **82 of 554 callable builtins
(15% of the registry) had never had their arity compared to their page.**

Among them, `PostgreSQL.Database` and `MySQL.Database` accepted a one-argument
call where both pages require two, then connected with `database=""` - which
PostgreSQL resolves to the user's default database. A different database,
silently, on a call the signature forbids. Fixed to `2..3`.

The gate now asks the builtins the text cannot describe directly: call with k
arguments and see whether `_arity` is what refuses. All 82 are compared, and a
new test fails if any documented builtin is covered by neither route.
Positive control: reintroducing the arity gave exactly 2 failures, by name.

**Worked-example gate.** `test_a_documented_example_produces_its_documented_output`
returned when the usage did not evaluate, and again when the printed Output was
not parseable M. Both silent; **21 examples took those exits**, and the only
backstop was a match floor of 110 against an actual **141** - 31 examples of
slack.

All 21 are now recorded exemptions with reasons (6 culture-specific, 3
Mashup-Engine-only, 4 fuzzy matching, 3 genuinely unimplemented functions, 2
needing a file or credentials, 2 defects in Microsoft's own pages, 1 output that
is prose). A case that recovers **fails and asks to be removed**, so the list can
only shrink. The floor is now the measured 141. Positive control run in both
directions: removing an exemption fails, and fabricating one for a working case
fails.

No test was weakened or removed. Both gates check strictly more than before.

### 7. Documentation reconciliation - DONE, AND WIDER THAN REPORTED

Three documents described three different releases at once, and nothing checked
any of them.

Verified by execution before a word was changed, which found **three stale claims
the audit had not named**: `??` and field projection (`r[[a],[b]]`) are listed as
unsupported and both run; `RoundingMode.*`, `TextEncoding.*` and
`BinaryEncoding.*` are described as "deliberately unregistered - their numeric
values could not be verified" while all thirteen are registered with verified
numbers. `#shared` and `meta` do still refuse; those claims were true and were
kept.

- **`SUPPORT-MATRIX.md` (new)** is the single authoritative statement: the three
  counts that are not the same number, the connector table, per-option behaviour
  for all twelve `Sql.Database` options, the credential rule, the nine refusal
  categories, and the rename scope.
- **The framing was the biggest defect.** "pqtools implements 547 of the 635
  functions (86%)" is a count of NAMES, and `llms.txt` exists to be quoted by
  assistants, so that sentence propagated furthest. It now reads "registers 547
  of the 635 names - 86% of NAMES, which is not a measure of semantic
  compatibility and should not be quoted as one", generated by
  `scripts/sync_builtin_list.py` so it cannot be hand-edited back.
- **The three numbers, separated:** 547 of 635 names registered (86.1% of names);
  547 signatures verified for arity and nullability; 73 enum values verified;
  141 of Microsoft's worked examples reproduce their documented output exactly.
  **Semantic compatibility is not measured and is not claimed.** Mashup Engine
  compatibility is explicitly disclaimed.
- **`ops/STATUS.md`** (0.1.0, former package name, 60 tests) and
  **`.planning/m-inventory.md`** (a path that no longer exists) are preserved
  and labelled HISTORICAL at the top, pointing at the matrix.
- **Rename scope** documented as what it is: a **textual, whole-file** guard, so
  a `[`, `=>`, `#"` or non-ASCII character anywhere - including inside a string
  literal or a comment - refuses the whole rename. **The guard was not loosened.**
  Narrowing it needs binding-aware analysis of the parse tree, which was not
  attempted and would need its own regression proof.

Enforcement, because prose is what drifted: `tests/test_support_matrix.py` checks
every count against the live registry, checks each connector row against the
registry (the exact check the stale README paragraph would have failed for four
releases), and fails if either historical file loses its banner.
`tests/test_catalog.py` now also asserts the "% of NAMES" qualifier, so the
misleading framing cannot return while the number stays right.

## Changed paths

Production code: `src/pqtools/cli.py`, `src/pqtools/builtins/_sources.py`,
`src/pqtools/builtins/_shared.py`, `src/pqtools/evaluate.py`.

Gates and tests: `tests/test_documented_signatures.py`, `tests/test_doc_examples.py`,
`tests/test_catalog.py`, plus new `tests/test_container_backup_safety.py`,
`test_odata_pagination.py`, `test_sql_navigation.py`, `test_sql_options.py`,
`test_sql_credentials.py`, `test_support_matrix.py`.

Documentation: `SUPPORT-MATRIX.md` (new), `README.md`, `llms.txt`, `CLAUDE.md`,
`scripts/sync_builtin_list.py`, `ops/STATUS.md`, `.planning/m-inventory.md`.

## Round 2 - the exact `claude-opus-5` review of the fixes

`bash ~/.codex/skills/claude-review/bin/review.sh c7ffc5f..HEAD`, with
`ANTHROPIC_API_KEY` unset so it bills the logged-in plan. The wrapper refuses
any model substitution. **Verdict: FIX-FIRST. 2 HIGH, 3 MEDIUM, 2 LOW.**

Every finding was reproduced before being changed and positive-controlled
after. All seven are fixed in `e1e90f3`. **Two of the HIGHs were regressions
the audit fixes themselves introduced** - worth stating plainly, because it is
the argument for the review step existing.

| Severity | Finding | Status |
|---|---|---|
| HIGH | OData paging carried the caller's `Authorization` to a host the SERVER chose | FIXED |
| HIGH | the 256 MiB response cap became a per-PAGE cap, so the feed total was unbounded | FIXED |
| MEDIUM | a sub-second timeout truncated to `0`, which pyodbc reads as NO timeout | FIXED |
| MEDIUM | the ODBC record form escaped values but not KEYS, so keys were still injectable | FIXED |
| MEDIUM | a deferred navigation value reached the CLI as a bare `AttributeError` traceback | FIXED |
| LOW | README and llms.txt still promised the backup is exactly `<file>.bak` | FIXED |
| LOW | the new arity probe caught `BaseException` and guessed where it could not reach | FIXED |

### The two that were mine

**The credential hop.** `_odata_feed` carried `page_options` - including the
caller's `Headers` - to every next link, and that URL comes from
`@odata.nextLink`, which the remote server writes. Under plain `--allow-net`
with no host allowlist, `check_net` permits it, so the network policy was not
what stopped this; nothing was. Reproduced with two local origins: the second
origin logged `Bearer`. My own code comment said "the headers (which may carry
auth) and the timeout travel on" - the risk was written down and shipped
anyway, which is the part worth remembering.

Cross-origin hops now carry only `Accept`. Same-origin paging is unchanged, so
authenticated paging inside one service still works, and both cases are
pinned by tests.

**The cap that stopped being a cap.** `_MAX_RESPONSE_BYTES` is enforced inside
`_http_fetch`, per response. Before paging existed, a feed was one response and
genuinely bounded at 256 MiB. Following up to 200 of them turned a hard limit
into that limit times two hundred. The running total is now bounded by the same
constant.

### The one worth naming separately

`pq eval 'Sql.Database("s","d")'` ended in a bare Python `AttributeError`
traceback - the failure shape `CLAUDE.md` says this package exists to prevent -
because `_print`'s JSON fallback called `.as_dict()` on the deferred value.
`--format csv` was worse: `csv.DictWriter` would have written the placeholder
into a data cell, where a table nobody read is indistinguishable from a value
somebody measured. That is the silent-wrong-answer class the whole audit is
about, introduced by the fix for finding 3.

Forcing the value at the CLI was considered and rejected: that is exactly the
read-every-table behaviour finding 3 removed. Both paths now refuse in the
typed way and name the step to write instead.

### Round 2b - the sibling the review did not name

The reviewer found the credential leak on the `@odata.nextLink` hop. Asking
whether the *other* way a request can change hosts had the same defect found
that it did: a plain **HTTP 302** forwarded the caller's headers too, through
`_http_fetch` - which every network connector shares, so this was
`Web.Contents`'s defect as much as `OData.Feed`'s. Pre-existing, not
introduced by the paging work.

Reproduced: a server answering 302 to a second local origin received
`Authorization: Bearer ...` in full. `_PolicyRedirectHandler` already
re-checked the policy on every hop, which answers a different question -
"may this request be made", not "may this server be told the caller's secret".

Cross-origin redirects now drop the standard credential headers **and every
header the query supplied**, because a credential does not have to be called
`Authorization` and `X-API-Key` matches no fixed list. `Accept` is kept.
Same-origin redirects are unchanged and have their own test, because a fix
that broke ordinary authenticated services would otherwise have looked like a
pass. Fixed in `a3faea2`.

## Scope limits - what was NOT verified

Local completion is reported separately from live verification on purpose.
**Everything above was verified locally, against mock drivers, local HTTP
servers, temporary files and checked-in fixtures.** None of the following was
exercised, and no result here should be read as evidence about any of them:

- **No live database.** Every SQL test injects a mock `pyodbc` (or asserts the
  refusal before a driver is imported). The connection strings and timeouts
  are asserted as *built* and *passed*, not as accepted by SQL Server,
  PostgreSQL, MySQL or Oracle. `MultiSubnetFailover=Yes` and
  `ApplicationIntent=ReadOnly` are written into the string because the page
  says they should be; no cluster confirmed the failover behaviour.
- **No credentials were used or inspected.** Every credential in the tests is
  a dummy literal written for the test.
- **No Fabric tenant, no Windows PQTest, no native Excel, no Power BI
  refresh.** The container work was verified by rewriting fixtures and by
  read-only `pq list` / `pq eval` against the local samples; Excel itself has
  not opened a rewritten workbook on this machine, which the README already
  states and which remains true.
- **No live OData service.** Paging, cycles, the page ceiling, the byte
  ceiling and the credential-stripping are all verified against local HTTP
  servers on `127.0.0.1`.
- **One platform, one interpreter.** macOS on this machine. CI declares three
  operating systems and Python 3.11-3.13; nothing here speaks to the other
  two OSes or the other interpreter versions.

## Residual limitations, stated rather than closed

1. **The backup has a narrow non-atomic window.** `_container_backup` creates
   the sidecar with `O_CREAT|O_EXCL|O_NOFOLLOW` and fsyncs it, so the *create*
   is atomic, an existing backup is never clobbered, a symlink is never
   followed, and a failure raises before the container is touched. But if the
   process is `SIGKILL`ed mid-write, a short `.bak` can remain. No data is
   lost - the container is still untouched at that point, and the next run
   moves to `.bak.1` - so the consequence is a confusing stray file, not
   destruction. Closing it fully would mean writing to a temp file and
   `os.link`ing it into place, which trades the window for a dependency on
   hardlink support. Not done; recorded here as a deliberate choice.
2. **`CommandTimeout`/`ConnectionTimeout` are not implemented for
   PostgreSQL, MySQL or Oracle.** They refuse by name, which is compliant,
   but the capability differs from `Sql.Database` and `Odbc.*`. The three
   drivers express these differently enough that implementing them without a
   live server to test against would be guessing.
3. **`HierarchicalNavigation = true` is refused, not implemented.**
   Microsoft's page does not specify the shape of the schema-grouped table,
   and inventing one is the defect class this package's gates exist to catch.
4. **`Table.RemoveRowsWithErrors` and `Table.ReplaceErrorValues` are not
   implemented.** They refuse in the typed way and are recorded as genuine
   gaps in `tests/test_doc_examples.py`, not as documentation defects.
5. **Semantic compatibility is still unmeasured.** 141 of Microsoft's worked
   examples reproducing their documented output is the strongest evidence
   available here, and it is not a compatibility percentage. See
   `SUPPORT-MATRIX.md`.
6. **`semgrep` was not run** - it is not installed on this machine. `gitleaks`
   was run over the branch range and found nothing; `pip-audit` reports no
   known vulnerabilities.
7. **`pq rename` remains narrow by design.** It refuses on most real Power
   Query, because a record literal or field access anywhere in the file stops
   it. The guard was documented, not loosened; narrowing it correctly needs
   binding-aware analysis of the parse tree and its own regression proof.

