---
id: ADR-0034
title: "The payment pack's hard cases — an operation class, closed state sets, and a list that was already implemented"
status: accepted
date: 2026-09-29
area: "`ontology/domains/payment_processing.yaml`, `ontology/README.md`, `tests/test_domain_pack.py`"
related: ["YB-011", "YB-055", "ADR-0032", "ADR-0017"]
---

# ADR-0034 — The payment pack's hard cases

> **Record.** A review of the payment domain pack against a proposed list of
> concepts: Chargeback, Reconciliation, Refund/PartialRefund, Dispute, Mandate,
> Payout, BankAccount, Wallet, Issuer, Acquirer. The verification outcome is worth
> recording on its own, and four modelling decisions came out of it.

## 1. The verification: the list was already implemented

Every concept proposed **already existed** in `payment_processing.yaml`. Nothing was
missing, and the amendment is therefore not an addition of concepts but a repair of
four of them. Verified against the schema, not the README (whose own header warns its
tables "have drifted from the schemas"):

| Proposed | Where it was | State found |
|---|---|---|
| `Chargeback` | class | complete — reason category, amount, representment deadline |
| `Reconciliation` | class | **nothing to point at** |
| `Refund` / `PartialRefund` | class + `is_partial` + `PARTIALLY_REFUNDED` | complete |
| `Dispute` | class | **one free-text slot**, no escalation link |
| `Mandate` | class | **free-text state**, one-way account link |
| `Payout` | class | **no destination** |
| `BankAccount`, `Wallet` | classes | `Wallet` was a **stub** |
| `Issuer`, `Acquirer` | classes | **stubs with no attributes at all** |

The useful generalisation: declaration is not capability. Five of the ten were
present and inert, and a reader of the README could not tell which — the same defect
class as `container_type` being `required: true` and produced by nothing (ADR-0032).
A class with no slots can be named and nothing recorded about it.

## 2. Operations could not name the payment they concerned

**The structural gap.** Every operation inherited the root's role slots — `merchant`,
`cardholder`, `acquirer`, `issuer`, `instrument`, `scheme`, `gateway`,
`lifecycle_state`, `payment_method`, `direction` — and there was **no `payment`
slot**. Each operation was therefore an island: `Refund`, `Chargeback` and `Dispute`
could say who and what instrument, and not which payment. "Which refunds belong to
this payment?" was unanswerable.

`Chargeback`'s own schema made the inconsistency explicit: its `instrument` slot is
documented as *"Optional here — the dispute is against the payment, not the
instrument"*, and there was no slot for the payment.

**The fix is an abstract `PaymentOperation`** (is_a `PaymentDomainConcept`) carrying
`payment -> Payment` and `authorization -> Authorization`, with `Authorization`,
`Capture`, `Refund`, `Chargeback` and `Dispute` re-parented under it.

**Why an intermediate class and not a slot on the root.** The root is the natural
home for shared slots — it already carries roles that do not apply to everything —
and putting `payment` there is the smaller diff. It is rejected because `Payment`,
`Merchant`, `Card` and `Wallet` all inherit from that root, so the schema would
declare that a payment concerns a payment. An abstract intermediate makes the
category honest: an operation is something done *against* a payment.

**Why Settlement, Payout, Reconciliation and Mandate are excluded**, which is the
part a later reader would otherwise "tidy up":

| Class | What it acts on | Its own link |
|---|---|---|
| `Settlement` | a batch of payments | `payments -> Payment` (multivalued) |
| `Payout` | a destination account | `destination -> PaymentInstrument` |
| `Reconciliation` | settlements | `reconciles -> Settlement` |
| `Mandate` | an account | `account -> BankAccount` |

Settlement and Payout are money movements in their own right rather than operations
against one payment; Reconciliation operates over settlements; a Mandate is an
authority, not an act. Forcing them under `PaymentOperation` to make the hierarchy
look uniform would have given each a `payment` slot that is usually wrong.

## 3. PartialRefund is deliberately NOT a class

The proposed list wrote "`Refund` / `PartialRefund`". A partial refund is not a
different thing from a refund — it is a refund whose amount does not exhaust the
capture. That is `Refund.is_partial` plus the `PARTIALLY_REFUNDED` lifecycle state,
both of which already existed.

Adding `PartialRefund` as a sibling class would put two nodes on one fact and give
the coverage census two ways to report the same gap. `test_a_partial_refund_is_a_refund_and_not_a_second_class`
pins the decision so it is not "fixed" later by duplication.

## 4. Closed sets became enums

`mandate_state` and `dispute_state` were free-text strings, in a pack that uses enums
for every other closed set. `MandateState` (ACTIVE / SUSPENDED / CANCELLED / EXPIRED)
and `DisputeState` (RAISED / UNDER_REVIEW / RESOLVED_MERCHANT / RESOLVED_CARDHOLDER /
ESCALATED) replace them.

This is the ADR-0032 argument applied to the domain layer: the question a direct-debit
control turns on is *"was this collection made against an active mandate?"*, which is
a yes/no, and a free-text state cannot answer it. A closed enum also lets the JSON
schema constrain the decoder rather than leaving a string the model can spell four
ways.

## 5. Version bumped, and the test that made that hard

The pack moved `0.1.0 -> 0.2.0`, because the pack id is recorded in assertion
provenance and *"a pack without a version cannot be told apart from an edited one"*
(`core/ontology.py`). One test had hardcoded `payment_processing@0.1.0` as the
expected **live** pack id and failed. Its stated intent — "it must name the pack AND
its version" — is about the shape, not the number, so it now derives the version from
the loaded pack. A literal there fails on every legitimate vocabulary bump, which is
a change this file is expected to have.

The cost is visible and correct: graphs extracted under 0.1.0 now warn that the
vocabulary may have changed, which is exactly what the version field is for. Tests
that *simulate* a graph recorded under `@0.1.0` are left alone — that is now a
genuine historical record rather than a copy of the current value.

## What was not done

- **No new `ConceptAttribute`/`ConceptRelationship` work.** Those slots are still
  `inlined_as_list` and still have 0 instances (YB-055), so the domain model's
  attribute layer remains unpopulated. Adding slots to a pack whose attributes
  cannot be emitted would deepen the gap this record is about.
- **No worked-example change.** The pack's prose example teaches *shape*
  (clause-objects, comma lists, frameworks-as-concepts), and the dispute coverage
  point is already made where it belongs — in `Chargeback`'s description, which
  states that a design handling authorisation and capture but not chargebacks is a
  coverage finding. Extending the example would grow every requirements prompt when
  the pack is active, against YB-007.
- **No `scheme` widening.** The root's `scheme` ranges over `CardScheme`, which a
  direct-debit authority is not, so `Mandate.collection_scheme` is a plain string
  (BACS, SEPA, ACH). Widening `CardScheme` would break the comparability that makes
  it an enum; a second scheme enum is a larger change than this review justified.

## Verification

`tests/test_domain_pack.py` gains eight tests, of which the load-bearing ones are the
regression lists — the operations, parties and instruments the census depends on are
named once, so a concept that disappears is a failing test rather than a quiet gap in
a report. The others pin the decisions above: the operation hierarchy (including who
is *not* in it), the dispute's escalation link, the closed state sets, and
`PartialRefund` staying a boolean.

The pack loads with `unresolved_parents == []` and `unresolved_ranges == []`, which
`test_every_pack_reference_resolves` already asserts — so every new range is proved to
land on a pack class, a base class, or an enum.
