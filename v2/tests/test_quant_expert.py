from hyperliquid_v2.quant_expert.evidence import ComparableObservation, QuantExpert


def observation(net: float, *, green: bool = True, negative: bool = False):
    return ComparableObservation(
        setup_family="breakout_retest",
        return_15m_pct=net * 0.2,
        return_60m_pct=net * 0.6,
        return_180m_pct=net,
        mfe_r=max(net, 0.3),
        mae_r=min(-0.1, net),
        realized_net_r=net,
        reached_green=green,
        finished_negative=negative,
    )


def test_quant_expert_refuses_operational_status_below_30_samples():
    evidence = QuantExpert().build("breakout_retest", [observation(0.1)] * 29)
    assert evidence.operational is False
    assert evidence.evidence_quality == "insufficient"
    assert "fewer_than_30_comparable_samples" in evidence.limitations


def test_quant_expert_becomes_operational_only_at_configured_threshold():
    values = [observation(0.2)] * 30 + [observation(-0.1, negative=True)] * 20
    evidence = QuantExpert(minimum_operational_samples=50).build("breakout_retest", values)
    assert evidence.operational is True
    assert evidence.comparable_samples == 50
    assert evidence.expected_net_value_r > 0
    assert evidence.green_to_red_rate == 0.4
