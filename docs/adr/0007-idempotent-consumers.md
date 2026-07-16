# 0007 — Idempotent consumers via `claim_id`

**Status:** Accepted

## Context

The queues deliver at least once (ADR-0001, ADR-0002). A message can be redelivered
after a visibility-timeout expiry, and a dead-lettered message can be deliberately
replayed (SPEC §5). Both mean the same claim can be processed more than once. If
processing is not idempotent, a redelivery double-counts a claim into a provider's
aggregate and corrupts the ranking — and replay, a feature we want, becomes unsafe.

## Decision

Every consumer is idempotent on `claim_id`. Concretely, the scoring worker upserts
the claim keyed by `claim_id` and recomputes the provider aggregate from the stored
set of claims rather than incrementally adding to a running total. Processing a
`claim_id` that is already stored is a no-op with respect to the aggregate. The
validation worker's forwarding is likewise safe to repeat.

## Consequences

Redelivery and replay are both safe by construction: reprocessing any claim converges
to the same state. This is what makes dead-letter replay (a first-class feature)
usable rather than dangerous, and it is asserted directly by a Tier 1 test — feed a
duplicate `claim_id`, assert the aggregate is unchanged. The cost is that scoring
recomputes a provider's aggregate from its stored claims rather than mutating a
counter, which is a deliberate trade of a little work for a strong invariant.

Recomputing from stored claims is also what makes the aggregate safe under
*concurrent* scoring-worker replicas processing claims for the same provider, not
just sequential redelivery. A plain `SELECT AVG(...)` followed by an upsert would
otherwise be a lost-update race: two transactions under READ COMMITTED can each
read a snapshot that doesn't include the other's not-yet-committed insert, and
whichever commits last overwrites the correct aggregate with a stale one. To close
this, `_recompute_provider_aggregate` takes a `pg_advisory_xact_lock` keyed on a
hash of `provider_id` before reading and upserting, serializing all recomputation
for a given provider across concurrent transactions regardless of snapshot
visibility. The lock is released automatically at transaction end.
