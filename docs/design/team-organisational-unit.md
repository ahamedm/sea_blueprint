# Team / Organisational Unit construct (PARKED)

> **Design document** for `YB-008`. Status is tracked in that entry.
> Preserved verbatim from `TODO.md` v1 (YB-008).

---

### Why it matters

Ownership is the dimension that makes `SoftwareSystemClass` *mean* something.
Today ownership is `ArchitectureElement.owner: str` and
`Application.team_ownership: str` — free text, unqueryable, unvalidatable.

With a Team construct these become possible:

- "Which systems does the Payments team own?" — a real EA question today unanswered
- Cross-cutting vs domain ownership becomes **derived** from the owning team's
  remit, rather than asserted per system
- `ENTERPRISE_TECHNOLOGY_PLATFORM` stops being a label and becomes a consequence
  of being owned by a cross-cutting function
- Vendor/origin analysis can be paired with ownership: who is accountable for the
  single-vendor dependencies?

### Shape when picked up

A `Team` (or `OrganisationalUnit`) class in `enterprise_structure.yaml`, alongside
`Stakeholder`, with: id, name, remit (`DOMAIN` | `CROSS_CUTTING` | `PLATFORM` |
`SHARED_SERVICES`), parent unit (for nested orgs), and external references (HR/org
registry — the `ExternallyReferenced` mixin already exists).

Then `ArchitectureElement.managed_by`, `EnterpriseConstruct.owner`, and
`Application.team_ownership` become references rather than strings.

### Why parked

Adds a construct and a dimension of change to three layers for a benefit that is
real but not blocking. The `system_class` enum carries the cross-cutting signal
adequately for now; this makes it rigorous later.

### Note the pattern

This is the third "give it a proper class rather than filtering it" change —
after `TechnologyStack` and `Platform`. Excluding noise keeps failing; classifying
it keeps working.
