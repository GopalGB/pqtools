"""A wrong-arity call to a connector is an arity error under ANY policy.

The database connectors used to check the I/O policy BEFORE `_arity`. With
database access denied - the default - every wrong-arity call was reported
as M_IO_BLOCKED, so a query that passed one argument where the reference
requires two was told to pass `--allow-db`, and the arity probe in
test_documented_signatures.py had to run with everything allowed just to see
past the policy. That probe now enforces arity-first, but only for the
builtins it executes, which are the helper-registered ones. These have a
LITERAL arity the static scan reads, so nothing else would notice them
going back to policy-first. This does.
"""

from __future__ import annotations

import pytest

from pqtools import evaluate
from pqtools.builtins._shared import UnsupportedError


@pytest.mark.parametrize(
    "call",
    [
        "Sql.Database()",
        "Oracle.Database()",
        "Odbc.Query()",
        "Odbc.DataSource()",
        # helper-registered, covered by the probe too; listed so the property
        # is stated in one place for every database connector
        "PostgreSQL.Database()",
        "MySQL.Database()",
    ],
)
def test_wrong_arity_is_an_arity_error_not_a_policy_refusal(call: str) -> None:
    # No `io=`: the default policy denies databases. `_arity`'s message is
    # "<name> with 0 argument(s)"; a policy-first connector raises
    # IOBlockedError instead, which is not an UnsupportedError and so fails.
    with pytest.raises(UnsupportedError, match=r"with 0 argument"):
        evaluate(call)
