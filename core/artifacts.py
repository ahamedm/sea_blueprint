"""
The artifact store: the input bytes a run reads, addressed by their raw content.

WHY THIS EXISTS AT ALL
----------------------
A background run executes in a *different process* from the request that started
it, so an uploaded document cannot stay request-scoped: the worker would have
nothing to read. The bytes are therefore written before the job is enqueued, and a
job carries the digest rather than the document.

This is also the first place raw documents persist on disk. Today the record keeps
only `document_hash` (a hash of the *decoded text*) — the bytes themselves are
never stored. That makes retention, quota and deletion part of the feature rather
than an afterthought, which is why they are named here.

WHY THE HASH IS OVER BYTES, NOT TEXT
------------------------------------
`core.knowledge.ingest._document_hash` hashes the decoded, newline-normalised text
and truncates to 16 hex chars. It is a good provenance fingerprint for a *run* and
a bad artifact address: two different byte streams that decode to the same text are
different objects, and a truncation short enough to be a filename is not what you
want addressing a content store. So the artifact digest is a full SHA-256 over the
raw bytes, and `document_hash` keeps its own job.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import List, Optional

__all__ = ["ArtifactStore", "ArtifactError", "ARTIFACT_DIRNAME"]

ARTIFACT_DIRNAME = "artifacts"

#: A digest is hex and nothing else. Validated before it is ever joined to a path,
#: because a digest arriving from a URL or a job record must not be able to escape
#: the store with `..` or an absolute path.
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

DEFAULT_MAX_BYTES = 8 * 1024 * 1024
"""Per-artifact cap. The upload route has its own 4 MB `MAX_CONTENT_LENGTH`, which
Flask enforces for multipart bodies only — a fetched document bypasses it, so the
store enforces a bound of its own rather than trusting every caller."""


class ArtifactError(RuntimeError):
    """The artifact store could not honour the request."""


class ArtifactStore:
    """Content-addressed bytes under a scope's directory.

        store = ArtifactStore(base / "artifacts")
        digest = store.put(document_bytes)      # 64 hex chars
        text = store.get(digest).decode("utf-8")
    """

    def __init__(self, root: str | Path, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self.root = Path(root)
        self.max_bytes = max_bytes
        self.root.mkdir(parents=True, exist_ok=True)

    # -- addressing --------------------------------------------------------

    @staticmethod
    def digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def path(self, digest: str) -> Path:
        """Where a digest lives, after validating it is a digest.

        Sharded two characters deep so one directory does not accumulate every
        document a workspace has ever read.
        """
        if not isinstance(digest, str) or not _DIGEST_RE.match(digest):
            raise ArtifactError(f"not a content digest: {digest!r}")
        return self.root / digest[:2] / digest

    # -- writes ------------------------------------------------------------

    def put(self, data: bytes, *, max_bytes: Optional[int] = None) -> str:
        """Store `data`, returning its digest. Storing the same bytes twice is one
        artifact — which is what makes a retry or a re-delivery cheap."""
        if not isinstance(data, (bytes, bytearray)):
            raise ArtifactError(f"artifacts are bytes, got {type(data).__name__}")
        cap = self.max_bytes if max_bytes is None else max_bytes
        if len(data) > cap:
            raise ArtifactError(
                f"artifact is {len(data)} bytes, over the {cap} byte cap"
            )
        digest = self.digest(bytes(data))
        target = self.path(digest)
        if target.exists():
            return digest
        target.parent.mkdir(parents=True, exist_ok=True)
        # Atomic, and never partial: a crash leaves a temp file, not a half-written
        # document that a later run would read as real.
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(bytes(data))
            os.replace(tmp, target)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return digest

    def get(self, digest: str) -> bytes:
        target = self.path(digest)
        if not target.exists():
            raise ArtifactError(f"artifact {digest} is not in this store")
        return target.read_bytes()

    # -- housekeeping ------------------------------------------------------

    def exists(self, digest: str) -> bool:
        try:
            return self.path(digest).exists()
        except ArtifactError:
            return False

    def digests(self) -> List[str]:
        if not self.root.exists():
            return []
        found: List[str] = []
        for shard in sorted(self.root.iterdir()):
            if not shard.is_dir():
                continue
            for entry in sorted(shard.iterdir()):
                if entry.is_file() and _DIGEST_RE.match(entry.name):
                    found.append(entry.name)
        return found

    def total_bytes(self) -> int:
        return sum(self.path(d).stat().st_size for d in self.digests())

    def delete(self, digest: str) -> bool:
        """Remove one artifact. Returns whether it was there.

        Callers own the reference question: this store cannot know whether a job
        still names the digest, which is why sweeping is by *artifact* age with a
        grace period rather than by job age (a job-age sweep can delete bytes a
        live job is still going to read).
        """
        target = self.path(digest)
        if not target.exists():
            return False
        target.unlink()
        try:
            target.parent.rmdir()  # tidy an empty shard; harmless if not empty
        except OSError:
            pass
        return True
