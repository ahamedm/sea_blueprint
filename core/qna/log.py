"""
The CAN'T ANSWER log — the candidate queue for the registry, and nothing more.

Every question the vocabulary could not answer is appended as one JSON line, with
enough context to replay it: the question, what the router matched or nearly matched,
which graph and revision it was asked against, and who asked. This is deliberately NOT
a queue that promotes itself. Promotion stays a code-plus-test change to
`ontology/catalogues/questions.yaml`, reviewed like any other, because auto-registration
would let the model's own guesses about what is answerable become the roadmap — and the
whole design rests on the vocabulary being a declaration rather than an output.

One line per event, appended, so a crash mid-write costs a line rather than the file.
The location is the caller's: it belongs under the store root so it follows
`SEA_DATA_DIR` and does not mix workspaces, which is why this module takes a path
instead of knowing one.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: Relative to a store root. Dot-prefixed because it is a working record, not a scope.
LOG_DIRNAME = ".qna"
LOG_FILENAME = "unanswered.jsonl"


def default_log_path(store_root: str | Path) -> Path:
    """`<store_root>/.qna/unanswered.jsonl` — beside the store it describes."""
    return Path(store_root) / LOG_DIRNAME / LOG_FILENAME


@dataclass(frozen=True)
class UnansweredEvent:
    question: str
    #: WHICH kind of silence this was. The distinction is the reason the log exists:
    #: `no_named_question` is demand for a registry entry, `substrate_absent` is demand
    #: for the work that would put data in the graph, and `out_of_scope` is neither.
    #: Recording only the question would make those indistinguishable in the replay.
    state: str = ""
    scope_id: str = ""
    ref: str = "working"
    initiative_id: str = ""
    matched_intent: str = ""
    confidence: float = 0.0
    nearest_entries: Tuple[Dict[str, Any], ...] = ()
    asked_by: str = ""
    at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["nearest_entries"] = [dict(n) for n in self.nearest_entries]
        payload["at"] = self.at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        return payload


def log_unanswered(path: str | Path, event: UnansweredEvent) -> Optional[Path]:
    """Append one event. Returns where it went, or None if it could not be written.

    A failure to log must not fail the answer: the reader asked a question and deserves
    the response either way. It must not be SILENT either, hence the return value — the
    caller decides whether an unwritable log is worth surfacing.
    """
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        return target
    except OSError:
        return None


def read_unanswered(path: str | Path) -> List[Dict[str, Any]]:
    """Every recorded event, for a test or a future pusher. A malformed line is kept as
    a finding rather than dropped: it means a writer wrote something unexpected."""
    target = Path(path)
    if not target.is_file():
        return []
    events: List[Dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            events.append({"malformed": line})
    return events
