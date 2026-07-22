from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath


class SupervisorOutcome(StrEnum):
    NO_CHANGE = "NO_CHANGE"
    PROPOSE_PR = "PROPOSE_PR"
    REJECT = "REJECT"


IMMUTABLE_PREFIXES = (
    "v2/config/immutable_invariants.json",
    "v2/src/hyperliquid_v2/execution/",
    ".github/workflows/production-",
)


@dataclass(frozen=True)
class OptimizationProposal:
    hypothesis: str
    changed_files: tuple[str, ...]
    comparable_samples: int
    out_of_sample_samples: int
    expected_net_improvement_r: float
    drawdown_delta_pct: float
    depends_on_single_trade: bool
    affected_symbols: tuple[str, ...]
    tests: tuple[str, ...]


@dataclass(frozen=True)
class PolicyDecision:
    outcome: SupervisorOutcome
    reasons: tuple[str, ...]


class SupervisorPolicyGate:
    """Allow an autonomous supervisor to propose a PR, never merge or deploy it."""

    def __init__(self, minimum_samples: int = 50, minimum_oos_samples: int = 20) -> None:
        self.minimum_samples = minimum_samples
        self.minimum_oos_samples = minimum_oos_samples

    def evaluate(self, proposal: OptimizationProposal) -> PolicyDecision:
        reasons: list[str] = []
        if len(proposal.changed_files) != 1:
            reasons.append("one_causal_change_per_proposal_required")
        if proposal.comparable_samples < self.minimum_samples:
            reasons.append("insufficient_comparable_samples")
        if proposal.out_of_sample_samples < self.minimum_oos_samples:
            reasons.append("insufficient_out_of_sample_samples")
        if proposal.expected_net_improvement_r <= 0:
            reasons.append("no_positive_expected_net_improvement")
        if proposal.drawdown_delta_pct > 0.10:
            reasons.append("material_drawdown_deterioration")
        if proposal.depends_on_single_trade:
            reasons.append("result_depends_on_single_trade")
        if len(set(proposal.affected_symbols)) < 2:
            reasons.append("result_not_cross_symbol")
        if not proposal.tests:
            reasons.append("tests_required")
        if any(_is_immutable(path) for path in proposal.changed_files):
            reasons.append("immutable_boundary_violation")

        if reasons:
            return PolicyDecision(SupervisorOutcome.NO_CHANGE, tuple(reasons))
        return PolicyDecision(
            SupervisorOutcome.PROPOSE_PR,
            ("evidence_gate_passed_human_review_required",),
        )


def _is_immutable(path: str) -> bool:
    normalized = PurePosixPath(path).as_posix()
    return any(normalized.startswith(prefix) for prefix in IMMUTABLE_PREFIXES)
