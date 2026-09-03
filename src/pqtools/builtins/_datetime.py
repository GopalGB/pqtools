"""Placeholder for the 0.5.0 builtin expansion - see PRD-0.5.0-builtins.md.

Owned by exactly one implementer. Add functions here and register them in this
module's own BUILTINS dict; builtins/__init__.py merges every module's dict, so
there is no central registry file to edit and no cross-family merge conflict.
"""

from __future__ import annotations

from typing import Any

BUILTINS: dict[str, Any] = {}
