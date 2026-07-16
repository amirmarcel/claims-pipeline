# 0009 — Raw SQL schema, no migration framework (yet)

**Status:** Accepted

## Context

The deterministic spine needs two Postgres tables: `claim_scores` and
`provider_scores` (SPEC.md §2). A migration framework like Alembic earns its keep
when a schema has evolution history to manage — multiple environments moving through
an ordered sequence of changes, rollback support, drift detection. At this session's
scope there is no history yet: two tables, defined once, with no prior version to
migrate from.

## Decision

Schema is defined as raw SQL DDL (`src/claims_pipeline/db/schema.sql`), applied
directly (`CREATE TABLE IF NOT EXISTS ...`) by the persistence layer at
startup/setup. No Alembic, no migration tooling, this session.

## Consequences

One file is the entire schema surface, readable top to bottom, with no tooling
dependency or migration-chain bookkeeping for two tables that don't yet change shape.
The trade-off is deliberate and time-bound: the moment the schema needs to *evolve*
under running data — an added column with a backfill, a constraint change against
live rows, multiple deployed versions needing an upgrade path — raw
`CREATE TABLE IF NOT EXISTS` stops being sufficient and Alembic (or an equivalent) is
the right next tool. That is a future session's decision to make when the need
actually appears, not a default to reach for now.
