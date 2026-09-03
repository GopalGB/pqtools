Report security issues privately. Never include credentials or customer data.

# File-write boundary

`mquery --write` takes a final identity, size, timestamp, and content snapshot
of the source immediately before the atomic replacement (`os.replace`). That
final snapshot check is the guarantee against lost updates - a change landing
between the check and the replace is a microsecond window on every OS. The
advisory lock (`fcntl`/`msvcrt`) is best-effort serialisation of cooperating
`mquery` processes only, not a correctness guarantee: the lock file is removed
after use, so a waiting process and a freshly started one can end up locking
different inodes. Use source control or external exclusive ownership when
multiple programs may edit the same query simultaneously.
