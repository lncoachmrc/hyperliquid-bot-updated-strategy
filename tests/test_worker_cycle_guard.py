import pytest

from worker import cycle_delay_for_recent_persisted_cycle


def test_no_previous_cycle_allows_immediate_run():
    assert cycle_delay_for_recent_persisted_cycle(None, 120) == 0.0


def test_disabled_gap_allows_immediate_run():
    assert cycle_delay_for_recent_persisted_cycle(10.0, 0) == 0.0


def test_recent_cycle_waits_only_remaining_guard_interval():
    assert cycle_delay_for_recent_persisted_cycle(21.0, 120) == pytest.approx(99.0)


def test_cycle_at_minimum_gap_is_allowed():
    assert cycle_delay_for_recent_persisted_cycle(120.0, 120) == 0.0


def test_clock_skew_negative_age_fails_closed_to_full_gap():
    assert cycle_delay_for_recent_persisted_cycle(-5.0, 120) == pytest.approx(120.0)
