# 0008 — Local-first on LocalStack, then EKS

**Status:** Accepted

## Context

The system depends on managed cloud services (SNS, SQS) and ultimately runs on a
Kubernetes cluster with autoscaling. Building against real cloud from the first commit
makes the inner development loop slow and costly, and it entangles application
correctness with cloud provisioning — so a bug in the scoring logic and a bug in an
IAM policy show up the same way: "it didn't work."

## Decision

Development is local-first. The full pipeline — topic, queues, workers, API, Postgres
— runs on LocalStack and Postgres under docker-compose, exercising the same messaging
topology and the same application code that will run in the cloud. Cloud and cluster
concerns (EKS, KEDA, real SNS/SQS, IAM) are layered on **after** the pipeline is
correct and fully tested locally.

The application code talks to AWS through the standard clients pointed at an endpoint,
so the only difference between local and cloud is configuration, not code.

## Consequences

Correctness is proven cheaply and fast before any cloud dependency exists, so a
complete, testable artifact exists at the local milestone even if the cloud layer is
never reached. Failures are isolated to the layer that caused them: application bugs
surface locally, infrastructure bugs surface only when the infrastructure is added.
The residual risk is local/cloud drift — LocalStack is not a perfect emulator — so the
same integration tests are run against real services once the cloud layer lands, to
catch anything the emulator hid.
