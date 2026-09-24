# C4 notation parser — deterministic extraction from structured architecture sources

> **Design document** for `YB-012`. Status is tracked in that entry.
> Preserved verbatim from `TODO.md` v1 (YB-012).

---

### What is missing

Architects don't write prose markdown for architecture. They write **C4 notation** in PlantUML, Structurizr DSL, or Mermaid. These are **structured, parseable, and already contain the C4 elements with their metadata.**

We're currently using LLM extraction on prose for something that's already structured. That's the mismatch.

**Example — PlantUML:**
```plantuml
@startuml
!include https://raw.githubusercontent.com/plantuml-stdlib/C4-PlantUML/master/C4_Container.puml

Person(user, "Customer", "A customer of the bank")
System_Boundary(banking, "Banking") {
    Container(web, "Web Bank", "JavaScript and Angular", "Allows customers to check accounts")
    Container(db, "Database", "Oracle", "Stores customer information")
}
Rel(user, web, "Uses", "HTTPS")
Rel(web, db, "Reads from", "JDBC")
@enduml
```

**Example — Structurizr DSL:**
```structurizr
workspace {
    model {
        user = person "Customer"
        bankingSystem = softwareSystem "Banking" {
            webapp = container "Web Bank" "Allows customers to check accounts" "JavaScript and Angular"
            database = container "Database" "Stores customer information" "Oracle"
        }
        user -> webapp "Uses" "HTTPS"
        webapp -> database "Reads from" "JDBC"
    }
}
```

Both are **deterministic, parseable, and already typed.** They're not prose — they're domain-specific languages with precise semantics.

### Why this matters

**Current flow:**
```
prose MD → LLM extraction → canonical graph
```

**Proposed flow:**
```
C4 notation → deterministic parser → canonical graph
prose MD → LLM extraction → canonical graph (fallback only)
```

**Key benefits:**

1. **Deterministic** — no LLM non-determinism for architecture. Same input → same output, every time.
2. **Complete** — all elements and connections captured. No silent drops like we've seen with responsibilities and monitoring platforms.
3. **Fast** — parsing is milliseconds, not minutes. No 80-second calls.
4. **Accurate** — technology, description, relationships all preserved from the source.
5. **Testable** — parser output is deterministic, easy to verify with unit tests.

This directly addresses the YB-007 problem (prompt dilution causing silent drops) by eliminating the LLM step for architecture entirely.

### Implementation options

**Option A: Add a C4 parser module**

Create `agents/extraction/c4_parser.py` that handles multiple C4 dialects:

```python
class C4Parser:
    def parse_plantuml(self, text: str) -> KnowledgeGraph:
        # Parse PlantUML syntax
        # Map Person → Node(kind="Person")
        # Map Container → Node(kind="Container")
        # Map Rel → Assertion(predicate="connects_to")
        # Preserve technology, description, etc.
        
    def parse_structurizr(self, text: str) -> KnowledgeGraph:
        # Parse Structurizr DSL or JSON
        
    def parse_mermaid(self, text: str) -> KnowledgeGraph:
        # Parse Mermaid C4 syntax
```

**Pros:**
- Clean separation of concerns
- Can support multiple C4 dialects
- Deterministic and testable

**Cons:**
- Need to implement parsers for each dialect
- PlantUML parsing is non-trivial (need ANTLR grammar or plantuml CLI)

**Option B: Use existing tools**

**PlantUML:**
- Use `plantuml` CLI to export to JSON/XML, then parse
- Or use an existing Python PlantUML parser

**Structurizr:**
- Use Structurizr CLI to export to JSON
- Or parse the DSL directly (simpler syntax)

**Mermaid:**
- Parse directly (simpler syntax)
- Or use mermaid-cli to export

**Pros:**
- Leverage existing tooling
- Faster to implement

**Cons:**
- External dependencies
- May not preserve all metadata

**Option C: Hybrid approach**

- Use existing tools where available (Structurizr JSON export)
- Implement custom parsers for others (PlantUML, Mermaid)
- Fall back to LLM extraction if parsing fails

**Pros:**
- Best of both worlds
- Robust (fallback to LLM)

**Cons:**
- More complex

### Canonical mapping

The parser needs to map C4 elements to the canonical model:

| C4 Element | Canonical Model |
|---|---|
| `Person` | `Node(kind="Person", label=...)` |
| `SoftwareSystem` | `Node(kind="SoftwareSystem", label=...)` |
| `Container` | `Node(kind="Container", label=..., attributes={"technology": ..., "description": ...})` |
| `Component` | `Node(kind="Component", label=...)` |
| `Rel` | `Assertion(subject=..., predicate="connects_to", object=..., attributes={"description": ..., "technology": ...})` |

**Metadata preservation:**
- Technology → `Node.attributes["technology"]` or `Assertion.attributes["technology"]`
- Description → `Node.attributes["description"]` or `Assertion.attributes["description"]`
- Tags → `Node.tags`
- Boundaries → containment relationships

### Integration with existing pipeline

The C4 parser should produce a `KnowledgeGraph` directly, bypassing the extraction agent:

```python
# Current flow
agent = create_architecture_extraction_agent()
result = agent.run({"document": prose_md, ...})

# New flow
parser = C4Parser()
graph = parser.parse_plantuml(plantuml_text)
# or
graph = parser.parse_structurizr(structurizr_text)
```

Or integrate into the agent:

```python
class ArchitectureExtractionAgent:
    def run(self, input_data):
        document = input_data["document"]
        
        # Detect format
        if self._is_plantuml(document):
            parser = C4Parser()
            return parser.parse_plantuml(document)
        elif self._is_structurizr(document):
            parser = C4Parser()
            return parser.parse_structurizr(document)
        else:
            # Fall back to LLM extraction
            return super().run(input_data)
```

### Testing strategy

1. **Parser tests** — deterministic, fast
   - Input: PlantUML/Structurizr/Mermaid text
   - Output: KnowledgeGraph
   - Assert: correct nodes, assertions, metadata

2. **Integration tests** — C4 → canonical → RDF
   - Input: C4 file
   - Output: RDF graph
   - Assert: correct triples, assertion resources

3. **Sample files** — need real C4 examples
   - PlantUML C4 diagram
   - Structurizr DSL workspace
   - Mermaid C4 diagram

### Trade-offs

**What we gain:**
- Deterministic architecture extraction
- No silent drops
- Fast (milliseconds vs minutes)
- Complete metadata preservation

**What we lose:**
- Flexibility (can't extract from arbitrary prose)
- Need to support multiple C4 dialects

**What stays the same:**
- Requirements extraction still uses LLM (requirements are typically prose)
- The canonical model, validators, RDF emission — all unchanged

### Recommendation

**Implement the C4 parser as the primary path for architecture extraction.**

1. Start with **Structurizr JSON** (easiest to parse, already structured)
2. Add **PlantUML** support (most common C4 notation)
3. Add **Mermaid** support (growing in popularity)
4. Keep LLM extraction as fallback for prose docs

This addresses the fundamental mismatch: we're using LLM extraction on something that's already structured. The C4 parser makes architecture extraction deterministic, complete, and fast.

### Implementation priority

This should be **critical** — not because it's blocking the current workflow, but because it's the right way to do architecture extraction. The current LLM-based approach works, but it's solving the wrong problem.

**Effort:** Medium. Structurizr JSON parsing is straightforward. PlantUML parsing is more complex but doable. Mermaid is simpler.

**Timeline:** Can be done in parallel with the review gate work, since it's independent.
