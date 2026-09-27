#!/usr/bin/env python3
"""
Sanity check — is the platform actually wired end to end?

WHY THIS EXISTS. Three faults in a row produced the SAME symptom — "ingestion fails,
no triples" — from three different layers: a credential chosen for the wrong endpoint
(a LAN IP counted as hosted, so the paid key went to a local server), a sampling flag
from the wrong provider (silently ignored, so every call burned its output budget on
reasoning until the wall-clock cancel), and a request that outlives its client. Each was
found by hand, one probe at a time. This is that probing, kept and ordered, so the next
failure names its own layer instead of looking like the last one.

THE STAGES ARE ORDERED BY WHAT FAILS FIRST, cheapest first:

  config    which endpoint, which credential SOURCE, which extra params, any mismatch
  endpoint  reachable, the configured model is actually served, tools accepted, and
            reasoning genuinely off — one call that exercises all four
  extract   the real profile over the real document: passes, triples, wall clock
  route     POST a document through the real /ingest route and read the graph back
  journal   the run-event endpoint answers, and says so when it has nothing yet

Usage:
  .venv/bin/python scripts/sanity_check.py                    # config + endpoint
  .venv/bin/python scripts/sanity_check.py --stages all       # everything (minutes)
  .venv/bin/python scripts/sanity_check.py --stages extract --doc architecture
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.agent_config import (  # noqa: E402
    choose_api_key,
    endpoint_is_local,
    extra_params_mismatch,
    load_environment,
)

OK = "PASS"
BAD = "FAIL"
SKIP = "SKIP"

REQUIREMENTS_DOC = "test_data/prd/payment_platform_brief.md"
ARCHITECTURE_DOC = "test_data/arch/payment_platform_arch.md"
SCRATCH_STORE = "data/scratch/sanity-store"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _post(url: str, payload: dict, key: str, timeout: int) -> Tuple[int, dict]:
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
    request.add_header("Authorization", f"Bearer {key}")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": exc.read()[:200].decode(errors="replace")}
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": f"{type(exc).__name__}: {exc}"}


def _get(url: str, key: str, timeout: int) -> Tuple[int, dict]:
    request = urllib.request.Request(url)
    request.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": exc.read()[:200].decode(errors="replace")}
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------


def stage_config(strict: bool = True) -> List[Tuple[str, str, str]]:
    """Which endpoint, which credential, which sampling flags — the layer that broke
    the 401 and, separately, the reasoning/`cancel` failure."""
    env = load_environment()
    base_url = env.get_openai_base_url()
    results: List[Tuple[str, str, str]] = []

    results.append((
        "endpoint configured", OK if base_url else BAD,
        f"{base_url or '(none — falls back to DEFAULT_MODEL_PROVIDER)'} "
        f"({'local' if base_url and endpoint_is_local(base_url) else 'hosted'})",
    ))
    if not base_url:
        return results

    key, source = choose_api_key(base_url, env)
    results.append((
        "credential chosen", OK if key else BAD,
        f"{source} (len={len(key)}, value never printed)",
    ))
    results.append((
        "model id configured", OK if env.local_model_id else BAD,
        env.local_model_id or "(none)",
    ))
    mismatch = extra_params_mismatch(base_url, env.model_extra_params)
    results.append((
        "sampling flags match the endpoint", BAD if mismatch else OK,
        mismatch or f"{env.model_extra_params or '{}'}",
    ))
    inner = (env.model_extra_params or {}).get("extra_body") or {}
    # The flag can sit directly in `extra_body` or nested under `chat_template_kwargs`,
    # and which is correct depends on the server — so this stage only checks that SOME
    # reasoning switch is configured. The endpoint stage is the one that proves the
    # server honours it, by measuring `reasoning_content`.
    template_kwargs = inner.get("chat_template_kwargs") or {}
    disables = (
        "enable_thinking" in template_kwargs
        or "reasoning_effort" in inner
        or "thinking" in inner
    )
    results.append((
        "reasoning is addressed", OK if disables else BAD,
        "a flag is set; the endpoint stage proves whether the server honours it"
        if disables else
        "no flag — a reasoning model will spend its output budget on chain-of-thought "
        "and can hit the wall-clock cancel",
    ))
    return results


def stage_endpoint(timeout: int = 120) -> List[Tuple[str, str, str]]:
    """One call that proves four things at once: auth, the model id, tool support, and
    that reasoning is genuinely off. Each of those has caused a silent empty run."""
    env = load_environment()
    base_url = env.get_openai_base_url().rstrip("/")
    if not base_url:
        return [("endpoint reachable", SKIP, "no OpenAI-compatible endpoint configured")]
    key, _source = choose_api_key(base_url, env)
    results: List[Tuple[str, str, str]] = []

    status, body = _get(f"{base_url}/models", key, 30)
    served = [m.get("id") for m in (body.get("data") or [])] if status == 200 else []
    results.append((
        "endpoint answers /models", OK if status == 200 else BAD,
        f"HTTP {status}" + (f", serves {served[:4]}" if served else f" {body.get('error','')}"),
    ))
    if status != 200:
        return results

    wanted = env.local_model_id
    if served and wanted:
        results.append((
            "configured model is served", OK if wanted in served else BAD,
            wanted if wanted in served else f"{wanted!r} not in {served}",
        ))

    extra = (env.model_extra_params or {}).get("extra_body") or {}
    tools = [{"type": "function", "function": {
        "name": "emit", "description": "emit the answer",
        "parameters": {"type": "object", "properties": {"ok": {"type": "boolean"}},
                       "required": ["ok"]}}}]
    started = time.time()
    status, body = _post(
        f"{base_url}/chat/completions",
        {"model": wanted, "messages": [{"role": "user", "content": "Call emit with ok=true."}],
         "max_tokens": 128, "temperature": 0, "tools": tools, "tool_choice": "required",
         **extra},
        key, timeout,
    )
    elapsed = time.time() - started
    if status != 200 or "choices" not in body:
        results.append(("structured (tool) call works", BAD, f"HTTP {status} {body.get('error','')}"))
        return results

    message = body["choices"][0]["message"]
    reasoning = message.get("reasoning_content") or ""
    results.append((
        "structured (tool) call works", OK if message.get("tool_calls") else BAD,
        f"tool_calls={bool(message.get('tool_calls'))} in {elapsed:.1f}s",
    ))
    results.append((
        "reasoning actually suppressed", OK if not reasoning else BAD,
        f"{len(reasoning)} chars of reasoning_content"
        + ("" if not reasoning else " — every call will burn its output budget on it"),
    ))
    return results


def stage_extract(doc_type: str, timeout: int = 1800) -> List[Tuple[str, str, str]]:
    """The real profile over the real document. This is the stage that takes minutes,
    which is itself the finding: extraction runs inside the request."""
    path = REQUIREMENTS_DOC if doc_type == "requirements" else ARCHITECTURE_DOC
    if not Path(path).exists():
        return [(f"{doc_type} extraction", SKIP, f"{path} not found")]

    from agents.architecture_extraction import create_architecture_extraction_agent
    from agents.knowledge_extraction import create_knowledge_extraction_agent

    agent = (create_knowledge_extraction_agent() if doc_type == "requirements"
             else create_architecture_extraction_agent())
    document = Path(path).read_text()
    started = time.time()
    result = agent.run({"document": document, "document_type": doc_type})
    elapsed = time.time() - started
    meta = result.metadata or {}
    passes = meta.get("passes") or []

    results: List[Tuple[str, str, str]] = [
        (f"{doc_type} run succeeds", OK if result.success else BAD,
         f"{elapsed:.0f}s, path={meta.get('extraction_path')}, errors={result.errors or '[]'}"),
    ]
    failed = [p for p in passes if p.get("outcome") == "failed"]
    empty = [p for p in passes if p.get("outcome") == "empty"]
    produced = sum(p.get("triples_produced") or 0 for p in passes)
    results.append((
        f"{doc_type} passes produce triples", OK if produced and not failed else BAD,
        f"{len(passes)} pass(es), {produced} triples, {len(empty)} empty, {len(failed)} failed"
        + (f" — {failed[0].get('error','')[:80]}" if failed else ""),
    ))
    # The wall-clock budget is per call and cancellation is checked BETWEEN turns, so a
    # slow call is the thing that silently turns into "no triples".
    #
    # NOT every profile records `elapsed` on its pass records — the requirements profile
    # builds its records without one — so a zero must be reported as UNMEASURED rather
    # than passing. A check that passes because the number is missing is worse than no
    # check: it is exactly the false assurance that let the cancellations go unnoticed.
    budget = agent.config.structured_timeout_seconds
    elapsed_values = [p.get("elapsed") or 0 for p in passes]
    slowest = max(elapsed_values) if elapsed_values else 0
    if not any(elapsed_values):
        results.append((
            f"{doc_type} clear of the {budget}s wall-clock budget", SKIP,
            "this profile records no per-pass elapsed, so the budget cannot be judged "
            "from the run record — check the log for 'wall-clock budget — cancelled'",
        ))
    else:
        results.append((
            f"{doc_type} clear of the {budget}s wall-clock budget",
            OK if slowest < budget * 0.6 else BAD,
            f"slowest pass {slowest:.0f}s ({slowest / budget:.0%} of budget)",
        ))
    return results


def stage_route() -> List[Tuple[str, str, str]]:
    """Through the real route, because the route is where the save happens — and where a
    cancelled request loses everything, since nothing is written until the end."""
    import io

    from app import create_app

    root = Path(SCRATCH_STORE)
    for stale in (root / "working.json", root / "index.json"):
        stale.unlink(missing_ok=True)

    client = create_app(store_root=str(root)).test_client()
    document = b"""# Requirements
FR-PM-001 The platform must accept and validate payment requests.
NFR-PS-001 Authorization latency must stay under 500ms.
"""
    started = time.time()
    response = client.post(
        "/ingest",
        data={"document": (io.BytesIO(document), "sanity.md"), "type": "requirements",
              "initiative_id": "INIT-MVP-001", "revision_label": "sanity"},
        content_type="multipart/form-data", follow_redirects=False,
    )
    elapsed = time.time() - started
    with client.session_transaction() as session:
        flashes = session.get("_flashes", []) or []

    ok = response.status_code == 302 and any(c == "success" for c, _ in flashes)
    detail = f"HTTP {response.status_code} in {elapsed:.0f}s; " + "; ".join(
        m[:120] for _c, m in flashes) or "no flash"
    results = [("POST /ingest completes", OK if ok else BAD, detail)]

    from core.workspace import load_workspace

    graph = load_workspace(root).open_store().load_working().graph
    results.append((
        "the graph actually gained facts", OK if graph.assertions else BAD,
        f"{len(graph.assertions)} assertions, {len(graph.nodes)} nodes, {len(graph.runs)} run(s)",
    ))
    return results


def stage_journal() -> List[Tuple[str, str, str]]:
    """The read side of the run journal. Expected to have NOTHING today: `/ingest` does
    not attach a sink yet. Asserting the empty answer is the point — it distinguishes
    "not wired" from "broken", which is the difference the next change has to make."""
    from app import create_app

    client = create_app(store_root=SCRATCH_STORE).test_client()
    response = client.get("/api/runs/run_sanity/events")
    payload = response.get_json() or {}
    ok = response.status_code == 200 and payload.get("events") == []
    return [(
        "run-event endpoint answers", OK if ok else BAD,
        f"HTTP {response.status_code}, events={len(payload.get('events') or [])}, "
        f"terminal={payload.get('terminal')} — empty is expected until /ingest attaches a sink",
    )]


STAGES = {
    "config": stage_config,
    "endpoint": stage_endpoint,
    "extract": stage_extract,
    "route": stage_route,
    "journal": stage_journal,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stages", default="config,endpoint",
                        help="comma-separated, or 'all'. "
                             f"one of: {', '.join(STAGES)}")
    parser.add_argument("--doc", default="architecture",
                    choices=["requirements", "architecture"])
    args = parser.parse_args()

    wanted = list(STAGES) if args.stages == "all" else [
        s.strip() for s in args.stages.split(",") if s.strip()
    ]
    unknown = [s for s in wanted if s not in STAGES]
    if unknown:
        parser.error(f"unknown stage(s): {unknown}; known: {list(STAGES)}")

    print(f"SEA sanity check — stages: {', '.join(wanted)}\n")
    results: List[Tuple[str, str, str]] = []
    for name in wanted:
        print(f"-- {name} " + "-" * (58 - len(name)))
        stage = STAGES[name]
        stage_results = stage(args.doc) if name == "extract" else stage()
        for label, status, detail in stage_results:
            print(f"  [{status}] {label}\n         {detail}")
        results.extend(stage_results)

    failed = [r for r in results if r[1] == BAD]
    skipped = [r for r in results if r[1] == SKIP]
    print(f"\n{len(results) - len(failed) - len(skipped)} passed, "
          f"{len(failed)} failed, {len(skipped)} skipped")
    if failed:
        print("\nfirst failure to investigate: " + failed[0][0])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
