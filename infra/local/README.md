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
the topic), and the `scoring-q` / `validation-dlq` SQS queues (SPEC.md §2).

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
provider distribution; `burst` and `failure_injection` are not implemented yet.

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
| Postgres   | 5432 |

## Credentials

LocalStack accepts any AWS credentials. Postgres: user `claims`, password `claims`,
database `claims_pipeline`.
