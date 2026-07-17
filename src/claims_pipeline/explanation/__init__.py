"""The confined explanation layer (SPEC.md §4, ADR-0003).

This package is the only part of the codebase that calls the language
model. It receives a fixed grounded_facts envelope, computed deterministically
from `provider_scores` before the model is invoked, and returns prose
describing those facts. It has no database access and cannot influence
scoring or ranking.
"""
