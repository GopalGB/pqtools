# mquery-toolkit

Unofficial offline tooling for Power Query M source. It is not an M runtime and
is not affiliated with or endorsed by Microsoft.

`mquery` delegates syntax and formatting to pinned Microsoft packages:
`@microsoft/powerquery-parser` 2.0.0 and
`@microsoft/powerquery-formatter` 1.0.0. The core remains credential-free and
never performs network requests.

## Commands

`mquery parse FILE` and `dependencies FILE` emit deterministic JSON. `check FILE`
prints human-readable diagnostics by default and `--json` emits stable objects.
Rule IDs are `M_PARSE_ERROR` for syntax, `M001` duplicate binding, `M002` dynamic
`Web.Contents`, `M003` credential-like literal, `M004` unreachable binding,
`M005` unresolved unqualified reference, and `M006` source inventory.

`rename FILE --old NAME --new NAME` is intentionally limited
to one ordinary top-level `let` binding. It refuses quoted identifiers, records,
lambdas, non-ASCII source, and ambiguous shapes. `replace-source FILE --source
SOURCE` replaces complete validated source only.

All edits produce a unified diff by default. Add `--write` for an atomic replace
that preserves UTF-8, newline convention, final-newline state, and mode while
rejecting symlinks and hardlinks. A cross-platform advisory lock serializes
other `mquery` writers, and a final snapshot detects changes made before the
replacement check. Operating systems do not provide a portable mandatory lock,
so a non-cooperating program can still race after that final check.

## Local runtime

Node 22.23.2 is required. For this local build, the verified user-space runtime
is `.tools/node-v22.23.2-darwin-arm64/bin/node`; set `MQUERY_NODE` to another
Node 22 executable on a different supported host. Node, package manager, and
Microsoft package versions are pinned in `package-lock.json`.

Fabric and PQTest are optional adapters. Fabric accepts only a caller-provided
token through an injected transport. PQTest accepts only a user-installed
Windows executable at version 2.155.2 and never downloads a binary.
