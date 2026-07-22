from hyperliquid_v2.supervisor.policy import (
    OptimizationProposal,
    SupervisorOutcome,
    SupervisorPolicyGate,
)


def proposal(**overrides):
    values = dict(
        hypothesis="lower exit giveback without reducing continuation capture",
        changed_files=("v2/config/experiment_exit_policy.json",),
        comparable_samples=80,
        out_of_sample_samples=30,
        expected_net_improvement_r=0.08,
        drawdown_delta_pct=-0.02,
        depends_on_single_trade=False,
        affected_symbols=("BTC", "ETH", "SOL"),
        tests=("walk_forward", "cost_stress", "no_lookahead"),
    )
    values.update(overrides)
    return OptimizationProposal(**values)


def test_supervisor_can_only_propose_pr_after_evidence_gate():
    result = SupervisorPolicyGate().evaluate(proposal())
    assert result.outcome is SupervisorOutcome.PROPOSE_PR


def test_supervisor_refuses_multiple_changes():
    result = SupervisorPolicyGate().evaluate(
        proposal(changed_files=("v2/a.py", "v2/b.py"))
    )
    assert result.outcome is SupervisorOutcome.NO_CHANGE
    assert "one_causal_change_per_proposal_required" in result.reasons


def test_supervisor_refuses_immutable_file_change():
    result = SupervisorPolicyGate().evaluate(
        proposal(changed_files=("v2/config/immutable_invariants.json",))
    )
    assert result.outcome is SupervisorOutcome.NO_CHANGE
    assert "immutable_boundary_violation" in result.reasons
