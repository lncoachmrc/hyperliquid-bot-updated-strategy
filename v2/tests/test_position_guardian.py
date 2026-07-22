from hyperliquid_v2.domain.models import PositionPhase
from hyperliquid_v2.position_guardian.optimal_exit import assess_exit, dynamic_profit_floor
from hyperliquid_v2.position_guardian.state_machine import GuardianTelemetry, transition


def telemetry(**overrides):
    values = dict(
        current_r=0.7,
        mfe_r=1.0,
        mae_r=-0.2,
        round_trip_cost_r=0.08,
        continuation_probability=0.35,
        reversal_probability=0.65,
        price_velocity=0.1,
        price_acceleration=-0.4,
        sell_aggression=0.65,
        thesis_valid=True,
        closed=False,
    )
    values.update(overrides)
    return GuardianTelemetry(**values)


def test_exhaustion_is_event_driven_not_time_driven():
    assert transition(PositionPhase.EXPANSION, telemetry()) is PositionPhase.EXHAUSTION


def test_dynamic_profit_floor_tightens_when_continuation_falls():
    loose = dynamic_profit_floor(telemetry(continuation_probability=0.8, reversal_probability=0.2))
    tight = dynamic_profit_floor(telemetry(continuation_probability=0.2, reversal_probability=0.8))
    assert loose is not None and tight is not None
    assert tight > loose


def test_exit_review_when_hold_value_is_dominated_during_exhaustion():
    assessment = assess_exit(
        PositionPhase.EXHAUSTION,
        telemetry(current_r=0.72),
        expected_remaining_upside_r=0.15,
        expected_giveback_r=0.5,
        exit_cost_r=0.03,
    )
    assert assessment.close_review is True
    assert assessment.reason in {
        "dynamic_profit_floor_breached",
        "exhaustion_and_close_value_dominates",
    }


def test_thesis_invalidation_is_hard_exit():
    assessment = assess_exit(
        PositionPhase.THESIS_INVALIDATED,
        telemetry(thesis_valid=False, current_r=-0.1),
        expected_remaining_upside_r=1.0,
        expected_giveback_r=0.1,
        exit_cost_r=0.03,
    )
    assert assessment.hard_exit is True
    assert assessment.close_review is True
