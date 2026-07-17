# Session 7 load-test report: KEDA queue-depth autoscaling

**Status:** captured from a real run, not asserted. See `docs/images/session7_autoscaling_run.png`
for the graph and `session7_scaling_run.csv` in this directory for the raw samples.

## Environment

- Real local Kubernetes via `kind` (not a simulation): one control-plane node, `kindest/node:v1.36.1`.
- KEDA v2 installed via `helm install keda kedacore/keda --namespace keda` (the `kedacore/keda` chart, current as of 2026-07).
- LocalStack 3.8 (SNS/SQS only) and Postgres 16, both host-level `docker-compose` containers attached to the kind Docker network (`infra/k8s/connect-local-services.sh`), exactly the ADR-0013 topology.
- `validation-worker` / `scoring-worker` Deployments (`infra/k8s/10-*.yaml`, `11-*.yaml`) with `minReplicaCount: 1`, `maxReplicaCount: 5`, `queueLength: 50` per `infra/k8s/20-keda-scaledobjects.yaml` (ADR-0014).
- Generator run from the host (not in-cluster), publishing to LocalStack SNS on `localhost:4566`.

## Reproduction

```sh
python -m claims_pipeline.generator \
  --rate 10 --duration 50 \
  --burst-rate 250 --burst-offset 10 \
  --seed 43 --endpoint-url http://localhost:4566
```

10s at 10 events/s (100 events), then a step to 250 events/s for the remaining 40s
(10,000 events) -- 10,100 events total, deterministic for seed 43 (`GeneratorConfig.event_count`).

Monitoring, run concurrently from generator start:

```sh
python benchmark/monitor_scaling.py --interval 2 --drain-idle-samples 60 \
  --out benchmark/reports/session7_scaling_run.csv
python benchmark/plot_scaling.py --csv benchmark/reports/session7_scaling_run.csv \
  --out docs/images/session7_autoscaling_run.png
```

## Results

| metric | value |
|---|---|
| events published | 10,100 |
| events processed (claim_scores rows) | 10,100 (100%) |
| peak `validation-q` depth | 8,782 messages (t=122s) |
| peak `scoring-q` depth | 950 messages (t=267s) |
| `validation-worker` replicas | 1 -> 5 at t=39s, held at 5 until t=372s, back to 1 by t=499s |
| `scoring-worker` replicas | 1 -> 5 at t=81s, oscillated 2-5 while `validation-worker` fed it, back to 1 by t=511s |
| peak throughput observed | 236 claims/s (a late catch-up burst as `validation-q` finally cleared) |
| total wall-clock (burst start to fully drained + scaled back to 1/1) | ~511s |

The graph (`docs/images/session7_autoscaling_run.png`) shows the three series
together: `validation-q` depth climbs sharply once the burst starts, both
Deployments scale to `maxReplicaCount=5` in response, the backlog drains, and
both replica counts step back down to 1 through the HPA's 30s scale-down
stabilization window once their respective queues are empty -- the ADR-0004
story (burst -> proportional scale-up -> drain -> latency/backlog recovers)
captured from a genuine kind+KEDA run.

## Lessons learned

1. **A synchronous, one-call-at-a-time publish loop cannot reach a stress-test
   burst rate.** The generator's `publish_claims` originally issued each
   `sns.publish` call and slept for the pacing interval before the next --
   fine at low rates, but at `--burst-rate 250` the per-call network
   round-trip to LocalStack (not the sleep interval) dominated, capping real
   throughput near 85-90 events/s regardless of the configured rate. A first
   attempt at this load test (seed 42, not committed) never built a backlog
   large enough to trigger a second replica for exactly this reason. Fixed
   by publishing from a small thread pool (`ThreadPoolExecutor`,
   `DEFAULT_PUBLISH_CONCURRENCY = 20`) so the configured schedule, not HTTP
   latency, paces the run -- see `src/claims_pipeline/generator/publisher.py`.
2. **SNS -> SQS fan-out lag under LocalStack.** `validation-q` depth kept
   climbing for roughly a minute after the generator's publish loop returned
   (all 10,100 `sns.publish` calls acknowledged). LocalStack's in-memory
   SNS/SQS emulation does not fan a burst out to the subscribed queue
   instantaneously under load -- a residual local/cloud fidelity gap in the
   same family ADR-0008 and ADR-0013 already flag (emulator behavior, not
   real AWS behavior, near the ceiling of what LocalStack was asked to do
   here). Real SNS/SQS would very likely show materially lower fan-out
   latency at this volume.
3. **`scoring-worker` throughput did not scale linearly with replica count.**
   Around t=230-250s, throughput dropped to single digits/s briefly even
   with 5 `scoring-worker` replicas running. `upsert_claim_and_recompute`
   (ADR-0007, ADR-0009) takes a per-provider advisory lock and recomputes
   that provider's full aggregate on every claim; with `provider_pool_size`
   in the tens, concurrent replicas land on the same provider often enough
   that lock contention -- not queue depth -- became the throughput ceiling
   for short stretches. This is a real characteristic of the current
   idempotency design under high worker concurrency and a low-cardinality
   provider pool, not a KEDA or Kubernetes artifact; worth a note for anyone
   tuning `queueLength` against a production provider distribution, since a
   higher provider cardinality would reduce lock contention and let
   additional replicas contribute more linearly.
4. **`minReplicaCount: 1` delegates scale-down pacing to the HPA, not KEDA's
   own `cooldownPeriod`.** KEDA warns on `kubectl apply` if `cooldownPeriod`/
   `pollingInterval` are set alongside `minReplicaCount > 0` -- they're inert.
   The behavior actually observed here (30s from empty-queue to a replica
   drop) comes from `spec.advanced.horizontalPodAutoscalerConfig.behavior
   .scaleDown.stabilizationWindowSeconds: 30` on each `ScaledObject`, which
   overrides the Kubernetes HPA's own 300s (5-minute) default. See ADR-0014.
