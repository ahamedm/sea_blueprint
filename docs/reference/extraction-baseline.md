# Extraction baseline — measured result

> **Reference.** Measured extraction output captured with `TODO.md` v1. Not a task; re-measure before quoting.

---

Model: `unsloth/Qwen3.5-4B-GGUF:Q4_K_M` via llama.cpp `:8080`
Input: `test_data/prd/sample_requirements.md` (REQ) / `test_data/arch/payment_platform_arch.md` (ARCH)

| | REQ-G | ARC-G (draft) |
|---|---|---|
| Triples | 68 | 61 |
| Nodes | 53 entities (16 with requirement ids) | 21 elements ⚠ |
| Edges | — | 26 connections, 6 `part_of` |
| Traceability to the other graph | — | captured as refs, not yet resolved (YB-005) |

⚠ 21 elements includes 7 technologies/patterns that regressed into elements —
see YB-007. The genuine architecture element count is 14, all correctly classified.

ARC-G containment now captured: 6 `part_of` triples (the 6 microservices → the
platform) plus `parent` on every container/datastore. The containment validator
flaggged 2 real gaps on first run (PostgreSQL and Valkey declared a parent with no
matching triple) — the deterministic-check pattern working as intended.

ARC-G element classification is correct across all 15 elements (verified:
Container / DataStore / ExternalSystem / DeploymentNode / SoftwareSystem).
Technologies are correctly predicates (`uses_technology: 14`) and deployment is
`deploys_on: 9`, not connections.
