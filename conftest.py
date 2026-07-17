"""Pytest bootstrap: load .env into the environment before tests collect,
so the live-model guardrail tests can read ANTHROPIC_API_KEY (and the infra
tests can read their DSN / endpoint) without a manual `source .env`.

The stubbed/deterministic guardrail assertions do NOT need .env — they run
keyless in CI. This only enables the live layer when a .env is present.
"""

from pathlib import Path


def _load_dotenv() -> None:
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        import os

        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())
        return

    load_dotenv(env_path)


_load_dotenv()
