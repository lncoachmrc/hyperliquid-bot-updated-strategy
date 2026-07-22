from datetime import datetime, timezone

from forecaster import normalized_target_time


def test_normalized_targets_are_exact_offsets_not_clock_boundaries():
    generated = datetime(2026, 7, 22, 10, 7, 31, tzinfo=timezone.utc)
    assert (normalized_target_time(generated, 15) - generated).total_seconds() == 900
    assert (normalized_target_time(generated, 60) - generated).total_seconds() == 3600
    assert normalized_target_time(generated, 15).minute == 22
    assert normalized_target_time(generated, 60).minute == 7
