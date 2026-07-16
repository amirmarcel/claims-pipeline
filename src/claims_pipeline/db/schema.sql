-- Postgres schema for the deterministic spine (SPEC.md §2).
--
-- Raw SQL, applied directly at startup/setup -- no migration framework this
-- session. See docs/adr/0009-raw-sql-schema-no-migration-framework.md for why,
-- and when to switch to one.

-- Keyed by claim_id: this key IS the idempotency mechanism (ADR-0007).
-- Reprocessing the same claim_id upserts the same row rather than adding a
-- new one.
CREATE TABLE IF NOT EXISTS claim_scores (
    claim_id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    cost_efficiency DOUBLE PRECISION NOT NULL,
    quality DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS claim_scores_provider_id_idx ON claim_scores (provider_id);

-- One row per provider. Recomputed in full from claim_scores on every
-- upsert (ADR-0007) -- never an incremented running total.
CREATE TABLE IF NOT EXISTS provider_scores (
    provider_id TEXT PRIMARY KEY,
    provider_score DOUBLE PRECISION NOT NULL,
    cost_efficiency DOUBLE PRECISION NOT NULL,
    quality DOUBLE PRECISION NOT NULL,
    claim_count INTEGER NOT NULL
);
