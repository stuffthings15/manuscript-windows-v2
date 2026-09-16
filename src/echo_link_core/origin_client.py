"""Shared constructor for a client pinned to one validated HTTPS origin.

subscription.py's EntitlementClient and update_check.py's UpdateClient both
need exactly this: validate the origin through the caller's own fail-closed
rule, then hold it and an optional injected opener for tests. Extracted here
after tests/test_source_discipline.py's duplicate-body guard caught the two
__init__ methods as byte-for-byte the same shape - each module keeps its own
origin-validation logic and its own error type; only the constructor plumbing
is shared, so this file makes no decision about what a valid origin is.
"""

from __future__ import annotations

from typing import Any, Callable


class OriginBoundClient:
    """A subclass sets ``_validate_origin`` to its own fail-closed validator."""

    _validate_origin: Callable[[str], str]

    def __init__(self, origin: str, *, opener: Callable[..., Any] | None = None) -> None:
        self.origin = self._validate_origin(origin)
        self._opener = opener


__all__ = ["OriginBoundClient"]
