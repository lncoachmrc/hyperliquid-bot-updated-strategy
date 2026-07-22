from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Protocol

from hyperliquid_v2.supervisor.policy import (
    OptimizationProposal,
    PolicyDecision,
    SupervisorOutcome,
    SupervisorPolicyGate,
)


class AuditSource(Protocol):
    def build_daily_evidence(self) -> dict: ...


class ProposalModel(Protocol):
    def propose(self, evidence: dict) -> OptimizationProposal | None: ...


@dataclass(frozen=True)
class SupervisorRun:
    started_at: str
    outcome: SupervisorOutcome
    reasons: tuple[str, ...]
    proposal: dict | None
    merge_authorized: bool = False
    deploy_authorized: bool = False


def run_supervisor(
    audit_source: AuditSource,
    proposal_model: ProposalModel,
    gate: SupervisorPolicyGate,
) -> SupervisorRun:
    evidence = audit_source.build_daily_evidence()
    proposal = proposal_model.propose(evidence)
    if proposal is None:
        return SupervisorRun(
            started_at=datetime.now(timezone.utc).isoformat(),
            outcome=SupervisorOutcome.NO_CHANGE,
            reasons=("no_falsifiable_improvement_hypothesis",),
            proposal=None,
        )

    decision: PolicyDecision = gate.evaluate(proposal)
    return SupervisorRun(
        started_at=datetime.now(timezone.utc).isoformat(),
        outcome=decision.outcome,
        reasons=decision.reasons,
        proposal=asdict(proposal),
        merge_authorized=False,
        deploy_authorized=False,
    )
