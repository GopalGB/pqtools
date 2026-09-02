Report security issues privately. Never include credentials or customer data.

# File-write boundary

`mquery --write` uses an advisory lock plus a final identity, size, timestamp,
and content snapshot before atomic replacement. This prevents lost updates
between cooperating `mquery` processes. It cannot prevent a different program
that ignores advisory locks from writing in the interval after the final check.
Use source control or external exclusive ownership when multiple programs may
edit the same query simultaneously.
