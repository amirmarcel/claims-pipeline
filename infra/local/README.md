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

This creates the `claims.raw` SNS topic and the `validation.q` SQS queue, subscribed
to the topic (SPEC.md §2).

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
