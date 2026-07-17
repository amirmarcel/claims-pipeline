"""Render the Session 7 load-test CSV (benchmark/monitor_scaling.py) into the
headline autoscaling graphs committed to docs/images/.

Three stacked panels, shared x-axis, one figure -- queue depth, replica
count, and processing throughput are one story (burst -> scale-up -> drain
-> scale-down, ADR-0004) and read better together than as separate charts.
Not part of the application; a benchmark-only reporting tool.
"""

from __future__ import annotations

import argparse
import csv

import matplotlib.pyplot as plt

# Categorical palette slots 1 (blue) and 6 (orange) -- consistent series
# identity (validation = blue, scoring = orange) across all three panels.
VALIDATION_COLOR = "#2a78d6"
SCORING_COLOR = "#eb6834"
GRID_COLOR = "#d8d8d5"
TEXT_COLOR = "#3a3a37"


def _load(path: str) -> list[dict[str, float]]:
    with open(path, newline="") as f:
        return [{k: float(v) for k, v in row.items()} for row in csv.DictReader(f)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--out", required=True, help="output PNG path")
    parser.add_argument("--title", default="KEDA queue-depth autoscaling under a generator burst")
    args = parser.parse_args(argv)

    rows = _load(args.csv)
    t = [r["elapsed_s"] for r in rows]

    fig, axes = plt.subplots(3, 1, figsize=(10, 9), sharex=True)
    fig.suptitle(args.title, fontsize=13, color=TEXT_COLOR, fontweight="bold")

    ax = axes[0]
    ax.plot(
        t,
        [r["validation_q_depth"] for r in rows],
        color=VALIDATION_COLOR,
        lw=2,
        label="validation-q",
    )
    ax.plot(t, [r["scoring_q_depth"] for r in rows], color=SCORING_COLOR, lw=2, label="scoring-q")
    ax.set_ylabel("queue depth\n(messages)", color=TEXT_COLOR)
    ax.legend(loc="upper right", frameon=False)
    ax.set_title("Queue depth", loc="left", color=TEXT_COLOR, fontsize=10)

    ax = axes[1]
    ax.step(
        t,
        [r["validation_worker_replicas"] for r in rows],
        where="post",
        color=VALIDATION_COLOR,
        lw=2,
        label="validation-worker",
    )
    ax.step(
        t,
        [r["scoring_worker_replicas"] for r in rows],
        where="post",
        color=SCORING_COLOR,
        lw=2,
        label="scoring-worker",
    )
    ax.set_ylabel("ready replicas", color=TEXT_COLOR)
    ax.set_ylim(0, 6)
    ax.legend(loc="upper right", frameon=False)
    ax.set_title("KEDA-managed replica count", loc="left", color=TEXT_COLOR, fontsize=10)

    ax = axes[2]
    ax.plot(
        t,
        [r["throughput_claims_per_s"] for r in rows],
        color="#008300",
        lw=1.5,
    )
    ax.fill_between(t, [r["throughput_claims_per_s"] for r in rows], color="#008300", alpha=0.15)
    ax.set_ylabel("claims scored / s", color=TEXT_COLOR)
    ax.set_xlabel("elapsed seconds", color=TEXT_COLOR)
    ax.set_title(
        "End-to-end throughput (claim_scores writes)", loc="left", color=TEXT_COLOR, fontsize=10
    )

    for ax in axes:
        ax.grid(True, color=GRID_COLOR, lw=0.7)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color(GRID_COLOR)
        ax.spines["bottom"].set_color(GRID_COLOR)
        ax.tick_params(colors=TEXT_COLOR)

    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(args.out, dpi=150, facecolor="white")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
