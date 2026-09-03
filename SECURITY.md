# Security policy

## Reporting a vulnerability

Report suspected vulnerabilities privately through GitHub's private vulnerability
reporting on this repository: **Security -> Report a vulnerability**. Please do not
open a public issue for a security problem, and never include credentials, tokens,
or customer data in a report.

Expect an acknowledgement within a few days. Include the version, the platform, and
the smallest input that reproduces the problem.

## Scope

`mquery-toolkit` reads and rewrites Power Query M source files. The core makes no
network requests and handles no credentials. The optional adapters do:

- **Fabric** takes a bearer token supplied by the caller and an injected HTTP
  transport. It never acquires, stores, or logs a credential, refuses any non-HTTPS
  or cross-origin redirect, and caps the response it will decode.
- **PQTest** runs a Windows executable the user already installed, at a pinned
  version. It never downloads a binary.

## File-write boundary

`mquery --write` snapshots the file (identity, size, timestamp, content), applies the
transform, then re-checks that snapshot immediately before the atomic replace. **That
re-check is the guarantee against a lost update** - a change between the final check
and `os.replace` is a microsecond window on every operating system. After the replace,
the parent directory is `fsync`'d so the rename itself is durable.

The advisory lock taken during a write is best-effort serialisation between
cooperating `mquery` processes, not a correctness guarantee: the lock file is removed
after use, so a waiter and a fresh process can end up holding different inodes, and no
portable mandatory lock exists against a program that ignores advisory locks. If
several programs may edit the same query at once, use source control or external
exclusive ownership.

Writes refuse symlinks, hard-linked files, and anything that is not a regular file.
Input is capped at 10 MiB and the Node subprocess at 30 seconds.
