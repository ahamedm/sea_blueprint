# Extract from C4 Structurizr (model / DSL), not just prose

> **Design document** for `YB-006`. Status is tracked in that entry.
> Preserved verbatim from `TODO.md` v1 (YB-006).

---

### Why this matters more than it looks

Structurizr is a **structured architecture model**, not prose. Extracting from it
is a **deterministic mapping**, not an LLM inference problem — which removes the
entire class of errors we keep fighting:

- no C4 level guessing (levels are explicit in the model)
- no container/component misclassification (item from the arch draft)
- no invented elements or genericised names
- no hallucinated connections
- stable element IDs, so ARC-G ⇄ REQ-G joining (YB-005) becomes tractable —
  architecture-side references can carry real identifiers instead of paraphrases

Essentially: the prose path needs a 15-rule prompt and a contract validator to
approximate what Structurizr states outright. Prefer the structured source
wherever one exists.

### Inputs to support

- **Structurizr DSL** (`.dsl`) — text, needs a parser
- **Structurizr JSON export** — direct; the model is already a graph
  (people, softwareSystems, containers, components, relationships, views,
  and `properties` for arbitrary metadata like REQ ids)

JSON first — no parser to write, and it is the canonical interchange format.

### Mapping sketch

| Structurizr | ARC-G |
|---|---|
| `softwareSystem` | `SoftwareSystem` / `ExternalSystem` (per `location`) |
| `container` | `Container` (or `DataStore` by tag/technology) |
| `component` | `Component` |
| `person` | `Person` |
| `relationship` | `Connection` (+ `technology`, `description`) |
| `views` | `ArchitectureView` |
| `properties` | traceability slots — e.g. `properties["requirement"] = "FR-PM-001"` |

The `properties` field is the clean bridge for YB-005: it gives architecture
elements a place to carry requirement identifiers natively.

### Note

Structurizr exports also carry `documentation` and `decisions` (ADR) sections,
which map directly onto `ArchitectureDecision` — another reason to prefer it.
