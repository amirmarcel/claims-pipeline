# 0005 — Synthetic data only; de-identification as a design concern

**Status:** Accepted

## Context

The domain is healthcare claims, which in reality carry protected health information.
This project must never handle real PHI. At the same time, privacy-aware handling is
a genuine property of a system like this and worth demonstrating in the design.

There is a trap here. Building a PHI *detection/redaction* stage and putting its
accuracy at the center invites evaluation of that stage's machine-learning quality —
regexes, named-entity recognition, edge cases in what counts as an identifier —
instead of evaluation of the distributed system. That trades the signal the project
is meant to show (event-driven architecture, reliability, scale) for a graded NLP
problem that is not the point.

## Decision

1. **All data is synthetic.** Claim events are generated. No real PHI ever enters the
   system, in any environment.
2. **`patient_ref` is an opaque, PHI-shaped reference**, not real identifying data.
   It exists so the design can treat patient identity as something to be handled
   carefully — minimized, not logged in the clear, not propagated past the stage that
   needs it — without any real identifier being present.
3. **De-identification is treated as a design concern, not a scored capability.** The
   pipeline demonstrates *where* PHI-shaped fields are minimized and contained; it
   does not stake its evaluation on the accuracy of an identifier-detection model. No
   eval tier grades redaction quality.

## Consequences

The system shows privacy-aware handling as an architectural property — data
minimization, containment to the stage that needs it, no clear-text PHI in logs —
which is the durable, transferable signal. It deliberately does not present a
redaction algorithm as a headline artifact, so no reviewer is drawn into grading
NLP heuristics. If a real deployment later needed production de-identification, that
would be a dedicated component with its own accuracy evals — a separate decision.
