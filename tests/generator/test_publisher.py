from claims_pipeline.generator.config import BurstConfig
from claims_pipeline.generator.publisher import _interval_for_index


def test_interval_without_burst_is_constant() -> None:
    assert _interval_for_index(0, rate=10.0, burst=None) == 1.0 / 10.0
    assert _interval_for_index(99, rate=10.0, burst=None) == 1.0 / 10.0


def test_interval_steps_at_burst_cutover() -> None:
    # rate=10/s, burst kicks in at offset=5s -> cutover index = 50
    burst = BurstConfig(offset=5.0, rate=50.0)
    assert _interval_for_index(49, rate=10.0, burst=burst) == 1.0 / 10.0
    assert _interval_for_index(50, rate=10.0, burst=burst) == 1.0 / 50.0
    assert _interval_for_index(200, rate=10.0, burst=burst) == 1.0 / 50.0
