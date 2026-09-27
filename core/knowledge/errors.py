"""
Store-level failures, kept in their own module so every backend can import them
without importing a backend.

`store_api` imports `Revision`/`Snapshot` from `store`, so `store` cannot import
`store_api` back — a guarded write still has to be able to raise `StoreConflict`.
One tiny module with no imports of its own is the way out.
"""

from __future__ import annotations

from typing import Optional


class StoreError(Exception):
    """Base class for store failures."""


class StoreConflict(StoreError):
    """A guarded write lost the race, or was attempted where it cannot be guarded.

    Not a bug and not a retry hint on its own: the caller re-reads, recomputes and
    tries again. Raised rather than swallowed because a silent overwrite is the
    defect this whole contract exists to prevent.
    """

    def __init__(self, message: str, expected: Optional[int] = None,
                 found: Optional[int] = None):
        self.expected = expected
        self.found = found
        super().__init__(message)
