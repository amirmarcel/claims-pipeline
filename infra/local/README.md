# Local rig

LocalStack (SNS + SQS) and Postgres, for local-first development per ADR-0008. Same
messaging topology as the cloud target; only the endpoint configuration differs.

## Bring it up

```sh
docker compose -f infra/local/docker-compose.yml up -d
```

Wait for both containers to report healthy:

```sh
docker compose -f infra/local/docker-compose.yml ps
```

Both `localstack` and `postgres` should show `(healthy)`.

Then provision the SNS topic and SQS queue (idempotent, safe to re-run):

```sh
./infra/local/provision.sh
```

This creates the `claims-raw` SNS topic, the `validation-q` SQS queue (subscribed to
the topic), and the `scoring-q` / `validation-dlq` / `scoring-dlq` SQS queues
(SPEC.md §2), plus the redrive policies (`maxReceiveCount=3`,
`validation-q` -> `validation-dlq`, `scoring-q` -> `scoring-dlq`) that move a
repeatedly-failing message to its dead-letter queue -- see
`docs/adr/0010-sqs-native-redrive-and-visibility-backoff.md`.

## Apply the Postgres schema

```sh
python -c "from claims_pipeline.db import repository as r; r.apply_schema(r.connect())"
```

Creates `claim_scores` and `provider_scores` (raw SQL DDL, `src/claims_pipeline/db/schema.sql`
— see `docs/adr/0009-raw-sql-schema-no-migration-framework.md`). Safe to re-run.

## Run the load generator

Publish deterministic synthetic claim events to `claims-raw` (SPEC.md §6):

```sh
python -m claims_pipeline.generator --rate 5 --count 100 --seed 1
```

Defaults to `LOCALSTACK_ENDPOINT_URL` (or `http://localhost:4566`) and region
`us-east-1`; override with `--endpoint-url` / `--region`. Use `--duration` instead of
`--count` to publish for a fixed number of seconds. v1 supports only the `uniform`
provider distribution; `burst` is not implemented yet.

Inject failure modes (SPEC.md §6) with `--failure-injection MODE=FRACTION`
(repeatable):

```sh
python -m claims_pipeline.generator --rate 5 --count 100 --seed 1 \
  --failure-injection malformed=0.05 --failure-injection duplicate=0.05
```

Modes: `invalid-but-parseable` (business-invalid -> `validation-dlq`),
`malformed` (undecodable -> poison, redriven to the matching DLQ), `duplicate`
(reuses an earlier `claim_id` -- exercises idempotency, ADR-0007).

## Inspect and replay dead letters

```sh
python -m claims_pipeline.replay --dlq validation-dlq --dry-run
python -m claims_pipeline.replay --dlq scoring-dlq --source-queue scoring-q
```

`--dry-run` lists what's on a DLQ with a derived reason, without consuming
anything. Omitting it replays every message back onto `--source-queue`;
`--limit` and repeatable `--claim-id` narrow the selection. Safe because
consumers are idempotent on `claim_id` (ADR-0007) -- see
`docs/adr/0010-sqs-native-redrive-and-visibility-backoff.md` for why a truly
malformed message can be inspected but not usefully replayed byte-for-byte.

## Confirm health directly

```sh
curl http://localhost:4566/_localstack/health
docker exec claims-pipeline-postgres pg_isready -U claims -d claims_pipeline
```

## Tear it down

```sh
docker compose -f infra/local/docker-compose.yml down
```

Add `-v` to also drop the Postgres named volume (all data is lost):

```sh
docker compose -f infra/local/docker-compose.yml down -v
```

## Ports

| service    | port |
|------------|------|
| LocalStack | 4566 |
| Postgres   | 5433 |

Postgres binds host port 5433 (container port stays 5432 internally) so the rig
doesn't collide with a developer's existing native Postgres listening on the
standard 5432. Connect with `psql -h localhost -p 5433 -U claims claims_pipeline`.

## Credentials

LocalStack accepts any AWS credentials. Postgres: user `claims`, password `claims`,
database `claims_pipeline`. Override the connection string with
`CLAIMS_PIPELINE_DATABASE_URL` (defaults to
`postgresql://claims:claims@localhost:5433/claims_pipeline`).
