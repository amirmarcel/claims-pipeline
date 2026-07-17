"""Shared worker helpers.

`touch_heartbeat` exists solely so a Kubernetes liveness probe has something
to check for a long-poll worker with no HTTP surface (infra/k8s/*.yaml). It
is unconditional -- called on every poll cycle regardless of environment, no
branching on where it's running -- so it does not touch ADR-0008's
config-only-difference boundary. As a host process (Sessions 1-5) it is an
inert file write; only the container/K8s deployment gives the file meaning.
"""

from __future__ import annotations

import time
from pathlib import Path

HEARTBEAT_PATH = Path("/tmp/claims-pipeline-worker-heartbeat")


def touch_heartbeat() -> None:
    try:
        HEARTBEAT_PATH.write_text(str(time.time()))
    except OSError:
        # Never let heartbeat bookkeeping take down the poll loop.
        pass
