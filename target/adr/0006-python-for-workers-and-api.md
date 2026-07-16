# 0006 — Python for workers and the API

**Status:** Accepted

## Context

The workers and API need a language with mature AWS messaging clients, a
straightforward HTTP framework, ergonomic data-shaping for the synthetic generator and
the scoring function, and a clean path to a language-model client for the explanation
layer. The surrounding data ecosystem this kind of system lives in — orchestration and
transformation tooling — is predominantly Python.

## Decision

Workers and the API are written in Python, with type hints enforced under a type
checker. The scoring function is plain, pure Python so it reads as an obvious,
verifiable computation.

## Consequences

The messaging clients, HTTP layer, generator, scoring logic, and model client are all
first-class and idiomatic, which keeps the code legible and the review surface small.
Python's runtime performance is not a concern here because throughput is a function of
horizontal worker scaling (ADR-0004), not single-process speed. Type hints plus the
checker recover much of the safety that a statically typed language would provide by
default.
