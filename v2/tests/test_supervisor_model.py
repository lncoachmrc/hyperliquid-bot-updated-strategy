import pytest

from hyperliquid_v2.supervisor.github import _validate_policy
from hyperliquid_v2.supervisor.model import _schema


def valid_policy():
    return {
        "guardian": {
            "min_mfe_for_dynamic_floor_r": 0.5,
            "exhaustion_reversal_margin": 0.0,
            "minimum_profit_retention": 0.25,
        },
        "opportunity": {
            "max_distance_ema_atr": 1.2,
            "min_reward_risk": 1.5,
            "min_volume_ratio": 0.9,
        },
        "supervisor_history": [],
    }


def test_supervisor_schema_requires_single_replacement_policy():
    schema = _schema()
    assert "replacement_policy" in schema["required"]
    assert schema["additionalProperties"] is False


def test_supervisor_policy_accepts_only_bounded_experimental_parameters():
    _validate_policy(valid_policy())
    policy = valid_policy()
    policy["opportunity"]["max_distance_ema_atr"] = 9.0
    with pytest.raises(ValueError, match="outside"):
        _validate_policy(policy)


def test_supervisor_policy_rejects_risk_or_execution_keys():
    policy = valid_policy()
    policy["risk"] = {"max_risk_fraction": 1.0}
    with pytest.raises(ValueError, match="unauthorized"):
        _validate_policy(policy)
