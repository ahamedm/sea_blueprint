"""
Design drafts — a proposal that has been made but not yet applied.

WHY THIS EXISTS
---------------
`/changes/discard` empties the whole working set, so a design draft merged straight
into it could not be undone selectively. Applying a proposal is therefore a
deliberate second step: a run writes a draft here, the page renders it, and only an
explicit Apply merges it into the working set and commits a revision.

That also gives the property YB-035 asked for — re-running does not disturb the
previous draft — for free: a new run writes a new file and the old one is still
there to compare against or discard.

WHAT THIS IS NOT
----------------
It is not `YB-018`'s review-batch model. A batch is a unit of the review gate with
its own progress, exclusions and promotion rules; this is a staging file with an
Apply button. Building the smaller thing first is deliberate: a draft that lands in
the existing review gate as ordinary unreviewed assertions needs no new gate
semantics, and the batch item stays free to design them properly.

Files are disposable. `discard` deletes one; nothing else reads the directory.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .model import KnowledgeGraph, utc_now
from .serialise import graph_from_dict, graph_to_dict


def _new_draft_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"draft_{stamp}_{uuid.uuid4().hex[:4]}"


@dataclass
class DesignDraft:
    """A proposal on disk, with what produced it."""

    id: str
    created_at: str = ""
    label: str = ""
    initiative_id: str = ""
    base_ref: str = ""
    run_id: str = ""
    completeness: str = ""
    domain_pack: str = ""
    counts: Dict[str, int] = field(default_factory=dict)
    caveats: List[str] = field(default_factory=list)
    findings: List[Dict[str, Any]] = field(default_factory=list)
    pattern_resolutions: List[Dict[str, Any]] = field(default_factory=list)
    # The exact prompt input the design ran against. Kept because "what did the
    # model actually see" is the first question asked of a surprising proposal.
    digest: str = ""

    @property
    def unresolved_patterns(self) -> List[Dict[str, Any]]:
        return [r for r in self.pattern_resolutions if not r.get("resolved")]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DesignDraft":
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in (data or {}).items() if k in known})

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "label": self.label,
            "initiative_id": self.initiative_id,
            "base_ref": self.base_ref,
            "run_id": self.run_id,
            "completeness": self.completeness,
            "domain_pack": self.domain_pack,
            "counts": dict(self.counts),
            "caveats": list(self.caveats),
            "findings": list(self.findings),
            "pattern_resolutions": list(self.pattern_resolutions),
            "digest_chars": len(self.digest),
        }


class DesignDraftStore:
    """Reads and writes design drafts beside the working set."""

    DIRNAME = "drafts"

    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)

    @property
    def directory(self) -> Path:
        return self.root / self.DIRNAME

    def ensure(self) -> "DesignDraftStore":
        self.directory.mkdir(parents=True, exist_ok=True)
        return self

    def _path(self, draft_id: str) -> Path:
        return self.directory / f"{draft_id}.json"

    def _write_atomic(self, path: Path, text: str) -> None:
        self.ensure()
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)  # atomic: a crash never leaves a half-written draft

    # -- write ------------------------------------------------------------

    def save(
        self,
        graph: KnowledgeGraph,
        *,
        label: str = "",
        initiative_id: str = "",
        base_ref: str = "",
        run_id: str = "",
        completeness: str = "",
        domain_pack: str = "",
        caveats: Optional[List[str]] = None,
        findings: Optional[List[Dict[str, Any]]] = None,
        pattern_resolutions: Optional[List[Dict[str, Any]]] = None,
        digest: str = "",
    ) -> DesignDraft:
        draft = DesignDraft(
            id=_new_draft_id(),
            created_at=utc_now(),
            label=label,
            initiative_id=initiative_id,
            base_ref=base_ref,
            run_id=run_id,
            completeness=completeness,
            domain_pack=domain_pack,
            counts=dict(graph.stats()),
            caveats=list(caveats or []),
            findings=list(findings or []),
            pattern_resolutions=list(pattern_resolutions or []),
            digest=digest,
        )
        payload = {"draft": draft.to_dict(), "graph": graph_to_dict(graph)}
        self._write_atomic(self._path(draft.id), json.dumps(payload, indent=2))
        return draft

    # -- read -------------------------------------------------------------

    def get(self, draft_id: str) -> Optional[DesignDraft]:
        data = self._read(self._path(draft_id))
        return DesignDraft.from_dict(data.get("draft") or {}) if data else None

    def load(self, draft_id: str) -> Tuple[DesignDraft, KnowledgeGraph]:
        """The draft and its graph. Raises KeyError when it is not there."""
        data = self._read(self._path(draft_id))
        if not data:
            raise KeyError(f"no such design draft: {draft_id}")
        return (
            DesignDraft.from_dict(data.get("draft") or {}),
            graph_from_dict(data.get("graph") or {}),
        )

    def list(self) -> List[DesignDraft]:
        """Every draft, newest first. A malformed file is skipped, not fatal."""
        self.ensure()
        drafts: List[DesignDraft] = []
        for path in sorted(self.directory.glob("*.json"), reverse=True):
            data = self._read(path)
            if not data:
                continue
            try:
                drafts.append(DesignDraft.from_dict(data.get("draft") or {}))
            except Exception:                                        # noqa: BLE001
                continue
        return drafts

    def latest(self) -> Optional[DesignDraft]:
        drafts = self.list()
        return drafts[0] if drafts else None

    # -- delete -----------------------------------------------------------

    def discard(self, draft_id: str) -> bool:
        path = self._path(draft_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def _read(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
