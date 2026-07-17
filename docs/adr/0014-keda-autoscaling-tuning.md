# 0014 — KEDA autoscaling tuning: target depth, replica bounds, scale-down pacing

**Status:** Accepted (values flagged for review — see Consequences)

## Context

ADR-0004 decided *what* signal drives scaling (SQS queue depth, not CPU) but
deliberately left the tuning knobs — target depth per replica, replica bounds,
and scale-down pacing — as a judgment call for the session that actually runs
a load test against them, since they can't be chosen well without observing
real behavior. This session (`infra/k8s/20-keda-scaledobjects.yaml`) is that
session.

## Decision

**`queueLength: "50"`** — the KEDA `aws-sqs-queue` scaler's target messages
per replica (`ApproximateNumberOfMessages` on `validation-q` / `scoring-q`,
read directly from LocalStack SQS, which reports queue depth the same way
real AWS does). Too low thrashes on ordinary jitter; too loose lets a real
backlog sit unaddressed. 50 was chosen before any run existed, as a
plausible middle value for a Deployment with no CPU-bound work in its
message-handling loop; validated after the fact against the load test below.

**`minReplicaCount: 1`, `maxReplicaCount: 5`** per worker. `minReplicaCount=1`
(not 0) keeps a warm pod always ready to receive the load test's opening
messages — scale-to-zero is a real KEDA capability (and arguably more
realistic for a genuinely bursty workload) but was set aside for this
session so the graphs don't have to explain a cold-start gap at t=0.
`maxReplicaCount=5` is enough headroom to show a multi-step scale-up curve
on a laptop-sized kind cluster without over-provisioning.

**`spec.advanced.horizontalPodAutoscalerConfig.behavior.scaleDown
.stabilizationWindowSeconds: 30`** on each `ScaledObject`. This is the one
correction this session made to its own first draft: with `minReplicaCount >
0`, KEDA delegates scaling entirely to the Kubernetes HPA it creates
underneath, and warns on `kubectl apply` that `cooldownPeriod`/
`pollingInterval` (the two knobs that looked like the obvious place to set
this) are inert in that mode. The HPA's own default scale-down
stabilization is 300 seconds (5 minutes) — appropriate for production
noise-avoidance, impractical for a load test meant to show the "queue
drains, replicas step back down" half of the story in one sitting. 30
seconds was chosen as short enough to observe within a load-test run while
still averaging over several HPA sync cycles (not reacting to a single
noisy sample).

## Evidence: what these values actually produced

`benchmark/reports/session7_load_test_report.md` and
`docs/images/session7_autoscaling_run.png` — a real kind+KEDA run,
`--rate 10 --burst-rate 250 --burst-offset 10 --duration 50` (10,100 events,
seed 43):

- `validation-worker`: 1 -> 5 replicas by t=39s (peak `validation-q` depth
  reached 8,782 at t=122s — the scaler was still correctly climbing toward
  `maxReplicaCount` while the backlog was still growing, not saturating
  early), held at 5 through the drain, back to 1 by t=499s.
- `scoring-worker`: 1 -> 5 by t=81s, then oscillated 2-5 for roughly 150s as
  `validation-worker`'s output rate and Postgres write contention (see the
  load-test report's lessons-learned #3) both varied `scoring-q`'s depth,
  back to 1 by t=511s.
- No thrashing observed at the 30s scale-down window — replica counts step
  down monotonically once a queue empties, no visible flapping back up.

This is real evidence the *scale-down* pacing (the ADR's one correction) is
sound. It is much weaker evidence that `queueLength: 50` itself is
well-tuned: the burst was large enough (peak depth 8,782, ~176x the target)
that almost any reasonable target in the 10-200 range would have driven both
Deployments to `maxReplicaCount` regardless. This run proves the mechanism
works end-to-end; it does not prove 50 is the *right* number for a
production traffic shape, which this session never had reason to model.

## Consequences

**Flagged for review, not a closed decision.** `queueLength`, the replica
bounds, and the 30s stabilization window are real judgment calls (ADR-0004
named exactly this risk: "too sensitive -> thrash; too loose -> slow
drain") made against one synthetic burst shape, not a production traffic
profile. Three things a human should sanity-check before treating these as
final:

1. Whether 50 messages/replica matches the actual per-replica processing
   capacity this system will see in practice — this session's worker
   throughput was itself contended by Postgres advisory-lock behavior under
   a small provider pool (load-test report, lesson 3), which a different
   `provider_pool_size` would change.
2. Whether `minReplicaCount: 1` (always-warm) or `0` (scale-to-zero, cost
   savings, cold-start latency) is the right trade-off for the target
   deployment — this session chose 1 for graph legibility, not because 0 is
   wrong.
3. Whether 30s scale-down stabilization is too aggressive for a production
   traffic pattern with more frequent, smaller bursts than this session's
   single synthetic one — the value was picked to fit inside one load-test
   sitting, not against a production noise profile.

The EKS side (`infra/eks/README.md`, "Deferred (Session 7)") documents KEDA
as a cluster add-on applied separately from the Terraform-managed SNS/SQS/
IAM/RDS resources, not part of that module — this ADR's values are the
starting point for that install's `ScaledObject`s too, subject to the same
review.
