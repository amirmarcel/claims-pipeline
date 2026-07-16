# 0004 — KEDA queue-depth autoscaling over CPU-based HPA

**Status:** Accepted

## Context

Worker capacity should track the amount of work waiting, so that a burst drains
quickly and an idle system costs little. The question is what signal drives scaling.

The default Kubernetes Horizontal Pod Autoscaler scales on CPU (or memory) utilization.
For queue-consuming workers this is an indirect proxy: a backlog of thousands of
messages may sit behind workers that are I/O-bound and barely registering CPU, so the
HPA under-scales exactly when the backlog is largest. CPU utilization is a symptom of
load, not a measure of pending work.

## Decision

Workers scale on **queue depth** using KEDA. The scaler reads the number of messages
available (and in flight) on the source SQS queue and sets the desired replica count
from that directly. Scaling responds to the actual backlog, and KEDA can scale the
consumer deployment to zero when a queue is empty.

## Consequences

Replica count tracks pending work, which is the signal that matters for a
queue-consuming system: a burst produces a proportional scale-up, and the queue
drains while end-to-end latency stays roughly flat. This behavior is the headline
thing the load test is designed to demonstrate — queue depth, replica count, and
latency captured together. We accept a dependency on KEDA as an added cluster
component, and the need to tune the target depth-per-replica and cooldown so the
system does not thrash on short spikes.
