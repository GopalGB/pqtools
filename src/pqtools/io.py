"""The capability gate for connectors that leave the machine.

Reading a local CSV needs no permission: the caller named the file. A query
that calls ``Web.Contents`` or ``Sql.Database`` is different, because *the
query* names the destination, and the query often came from a workbook
somebody else wrote. Running it unasked would turn ``pq eval report.pbix``
into an SSRF primitive and, with a URL built by string concatenation, an
exfiltration one.

pandas does not have this problem - you type ``read_sql`` yourself, so the
destination is always yours. Here the M source supplies it, so the
permission has to come from somewhere else: the caller, per invocation.

Hence: network and database access are **off by default** and refused with an
error naming the flag that would enable them. Turning them on is one word
(``--allow-net``), so this is a speed bump for the honest case and a wall for
the hostile one. ``hosts`` narrows it further when the caller knows exactly
where a query should be allowed to reach.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .core import MQueryError


class IOBlockedError(MQueryError):
    """Raised when a source is blocked by policy rather than by capability.

    Deliberately distinct from ``UnsupportedError``: "pqtools cannot do this"
    and "you did not permit this" need different fixes, and collapsing them
    sends people looking for a missing feature when they need a flag. It still
    derives from ``MQueryError`` so the CLI reports it as an error line rather
    than a traceback.
    """

    code = "M_IO_BLOCKED"


@dataclass(frozen=True)
class IOPolicy:
    """What a single evaluation is permitted to reach.

    The default is the safe one. Every field has to be turned on explicitly,
    because a default that reaches the network is a default that surprises
    somebody exactly once, expensively.
    """

    allow_net: bool = False
    allow_db: bool = False
    allow_private: bool = False
    hosts: frozenset[str] = field(default_factory=frozenset)
    timeout: float = 30.0

    def check_net(self, url: str, *, what: str) -> None:
        """Permit an HTTP(S) fetch, or explain which flag would."""
        if not self.allow_net:
            raise IOBlockedError(
                f"{what} is blocked: this query reaches the network and network "
                "access is off by default. Re-run with --allow-net (or "
                "IOPolicy(allow_net=True)) if you trust this query's URLs."
            )
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"}:
            raise IOBlockedError(
                f"{what}: only http and https URLs are allowed, got "
                f"{parts.scheme or 'no'} scheme. A file:// or other scheme here "
                "is how a network fetch turns into a local-file read."
            )
        if not parts.hostname:
            raise IOBlockedError(f"{what}: no host in URL {url!r}")
        if self.hosts and parts.hostname.lower() not in self.hosts:
            allowed = ", ".join(sorted(self.hosts))
            raise IOBlockedError(
                f"{what}: host {parts.hostname!r} is not in the allowed set "
                f"({allowed}). Add it with --allow-host."
            )
        if not self.allow_private:
            self._reject_internal(parts.hostname, what)

    @staticmethod
    def _reject_internal(hostname: str, what: str) -> None:
        """Refuse loopback, link-local and private addresses.

        `--allow-net` means "this query may fetch from the internet", not
        "this query may probe my machine and my network". The address that
        matters most is 169.254.169.254: on every major cloud that is the
        instance metadata service, and reading it hands over the host's
        credentials. A workbook someone emailed you is exactly the vehicle
        for that request.

        Resolution happens here rather than being left to urllib so that a
        hostname pointing at an internal address is caught too - blocking
        only literal IPs would be defeated by one DNS record. This is not a
        complete defence against a rebinding attack (the name is resolved
        again by the actual fetch), which is why --allow-host exists for the
        cases that need certainty.
        """
        try:
            infos = socket.getaddrinfo(hostname, None)
        except socket.gaierror:
            return  # Unresolvable: the fetch will fail on its own merits.
        for info in infos:
            address = ipaddress.ip_address(info[4][0])
            if (
                address.is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_reserved
                or address.is_multicast
            ):
                raise IOBlockedError(
                    f"{what}: {hostname} resolves to the internal address "
                    f"{address}, which is blocked. Cloud metadata endpoints and "
                    "internal services live there. Pass allow_private=True "
                    "(--allow-private-net) if you meant to reach it."
                )

    def check_db(self, *, what: str) -> None:
        """Permit a database connection, or explain which flag would."""
        if not self.allow_db:
            raise IOBlockedError(
                f"{what} is blocked: this query opens a database connection and "
                "database access is off by default. Re-run with --allow-db (or "
                "IOPolicy(allow_db=True)) if you trust this query."
            )


DENY_ALL = IOPolicy()

__all__ = ["DENY_ALL", "IOBlockedError", "IOPolicy"]
