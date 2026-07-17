# Local Kubernetes (kind)

The API and both workers run on a real local Kubernetes cluster via
[kind](https://kind.sigs.k8s.io/) (Kubernetes-in-Docker), against the same
LocalStack + Postgres rig `infra/local/` already uses -- see ADR-0013 for why
kind-for-local + EKS-as-artifact is the chosen strategy, and ADR-0008 for why
this is possible at all: the app talks to AWS/Postgres through standard
clients pointed at an endpoint, so this deployment differs from
Sessions 1-5's host-process rig only in *where that endpoint is reached from*
-- no application code differs.

## Prerequisites

`kind`, `kubectl`, `docker` on the host. Verify:

```sh
kind version && kubectl version --client && docker version
```

## 1. Bring up the local rig (LocalStack + Postgres)

Same as `infra/local/README.md`:

```sh
docker compose -f infra/local/docker-compose.yml up -d
./infra/local/provision.sh
python -c "from claims_pipeline.db import repository as r; r.apply_schema(r.connect())"
```

## 2. Create the kind cluster

```sh
kind create cluster --config infra/k8s/kind-cluster.yaml
```

Single control-plane node; `extraPortMappings` exposes the API's NodePort
(30080) on `localhost:8000` so the host can reach it without a
`kubectl port-forward` process running.

## 3. Build and load the image

```sh
docker build -t claims-pipeline:local .
kind load docker-image claims-pipeline:local --name claims-pipeline
```

One shared image for the API and both workers, switched by `command` per
Deployment -- see the `Dockerfile` header comment for the tradeoff against
three separate images.

## 4. Connect the cluster to LocalStack + Postgres (the fiddly part)

LocalStack and Postgres run as host-level Docker containers outside the kind
cluster's pod network. Run:

```sh
./infra/k8s/connect-local-services.sh
```

This attaches both containers to the `kind` Docker network so kind's node --
and therefore pods -- can reach them directly, creates selector-less
Services + Endpoints (`localstack`, `postgres`) in the `claims-pipeline`
namespace pointing at those container IPs, and patches CoreDNS with a
rewrite rule so the `sqs.<region>.localhost.localstack.cloud` hostname
LocalStack embeds in every `QueueUrl` response resolves in-cluster to the
`localstack` Service instead of the real public DNS record (which points at
127.0.0.1 -- correct for a host process, wrong from inside a pod). Full
reasoning is in the script's header comment. Re-run it any time the
LocalStack/Postgres containers are recreated (their IP on the `kind` network
can change).

## 5. Create the secret

Never committed in plaintext -- see `02-secret.example.yaml` for the shape,
created here imperatively:

```sh
kubectl create secret generic claims-pipeline-secrets \
  --namespace claims-pipeline \
  --from-literal=database-url="postgresql://claims:claims@postgres.claims-pipeline.svc.cluster.local:5432/claims_pipeline" \
  --from-literal=anthropic-api-key="$ANTHROPIC_API_KEY"
```

(`$ANTHROPIC_API_KEY` from your `.env` -- `export $(grep ANTHROPIC_API_KEY .env)`
or source it however you normally do. A placeholder value works for
everything except the explanation endpoint.)

## 6. Apply the manifests

```sh
kubectl apply -f infra/k8s/00-namespace.yaml \
  -f infra/k8s/01-config.yaml \
  -f infra/k8s/02-serviceaccount.yaml \
  -f infra/k8s/10-validation-worker.yaml \
  -f infra/k8s/11-scoring-worker.yaml \
  -f infra/k8s/12-api.yaml
```

## 7. Verify pods are healthy

```sh
kubectl -n claims-pipeline get pods
```

All three should reach `1/1 Running` within ~15s. `validation-worker` and
`scoring-worker` have no HTTP surface, so their liveness probe reads a
heartbeat file the poll loop touches every cycle
(`src/claims_pipeline/workers/__init__.py:touch_heartbeat`); the `api` pod's
probes are a real read of `GET /providers/ranking?limit=1`.

## 8. Run the pipeline end to end

```sh
python -m claims_pipeline.generator --rate 20 --count 30 --seed 42 \
  --endpoint-url http://localhost:4566
curl "http://localhost:8000/providers/ranking?limit=10"
curl "http://localhost:8000/providers/<provider_id>/explanation"
```

The ranking should populate within a few seconds as the in-cluster workers
consume `validation-q` -> `scoring-q` and write to Postgres.

## 9. The parity smoke: run the existing test suite against this deployment

This is the ADR-0008 payoff. **No test code changes** -- point the existing
suite at the same LocalStack/Postgres endpoints the kind pods use, from the
host:

```sh
LOCALSTACK_ENDPOINT_URL=http://localhost:4566 \
CLAIMS_PIPELINE_DATABASE_URL=postgresql://claims:claims@127.0.0.1:5433/claims_pipeline \
pytest -q -k "not live"
```

**Scale the in-cluster workers to 0 first** (`kubectl -n claims-pipeline scale
deploy validation-worker scoring-worker --replicas=0`) if you're running this
back-to-back with step 8 above -- the reliability tests (`test_reliability_e2e.py`)
spin up their own in-process worker calls against the same queues, and having
the deployed pods *also* polling concurrently causes message-stealing races
between the two consumers (observed directly this session: two tests failed
under contention, passed clean with the deployment scaled down). This is
queue-consumer contention, not a kind- or K8s-specific issue -- the same race
would occur running two host-process workers against the same queue.
Afterward: `kubectl -n claims-pipeline scale deploy validation-worker scoring-worker --replicas=1`.

## Teardown

```sh
kind delete cluster --name claims-pipeline
docker compose -f infra/local/docker-compose.yml down
```

`connect-local-services.sh`'s `docker network connect` and the CoreDNS patch
live inside the kind cluster's own control plane, so deleting the cluster
removes them too -- nothing to separately undo on the LocalStack/Postgres
containers.

## 10. KEDA autoscaling (Session 7)

```sh
helm repo add kedacore https://kedacore.github.io/charts
helm install keda kedacore/keda --namespace keda --create-namespace
kubectl -n keda wait --for=condition=Available deployment --all --timeout=120s
kubectl apply -f infra/k8s/20-keda-scaledobjects.yaml
```

Creates a `TriggerAuthentication` (LocalStack's `test`/`test` static
credentials -- real AWS/EKS would use pod identity against the same
per-workload IRSA role instead, see `infra/eks/README.md`) and a
`ScaledObject` per worker Deployment, scaling on `ApproximateNumberOfMessages`
for `validation-q`/`scoring-q` respectively. Target depth, replica bounds,
and scale-down pacing are ADR-0014's flagged tuning values, validated
against one real load-test run below, not a production traffic shape.

```sh
kubectl -n claims-pipeline get scaledobject
kubectl -n claims-pipeline get hpa   # KEDA creates one HPA per ScaledObject
```

## 11. Load test (Session 7)

```sh
python -m claims_pipeline.generator \
  --rate 10 --duration 50 --burst-rate 250 --burst-offset 10 \
  --seed 43 --endpoint-url http://localhost:4566
```

Run `benchmark/monitor_scaling.py` concurrently to capture queue
depth/replica count/throughput, and `benchmark/plot_scaling.py` on the
resulting CSV for the graph -- see `benchmark/reports/session7_load_test_report.md`
for the full reproduction steps and the results of the run this repo commits.

**A standalone-consumer gotcha this session hit**: anything that manually
`receive_message`s from `validation-q`/`scoring-q` outside the deployed
workers (a debugging script, `benchmark/trace_one_claim.py`) races the
in-cluster pods for the same messages. Scaling the Deployments to 0 isn't
enough by itself once a `ScaledObject` exists -- KEDA's underlying HPA
enforces `minReplicaCount` and recreates the pod. Pause the `ScaledObject`s
first:

```sh
kubectl -n claims-pipeline annotate scaledobject \
  validation-worker-scaledobject scoring-worker-scaledobject \
  autoscaling.keda.sh/paused="true" --overwrite
kubectl -n claims-pipeline scale deploy validation-worker scoring-worker --replicas=0
# ...run the standalone consumer...
kubectl -n claims-pipeline annotate scaledobject \
  validation-worker-scaledobject scoring-worker-scaledobject \
  autoscaling.keda.sh/paused-
```
