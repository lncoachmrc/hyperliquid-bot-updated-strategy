from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from hyperliquid_v2.storage.postgres import PostgresRepository
from hyperliquid_v2.supervisor.github import GitHubDraftPRClient
from hyperliquid_v2.supervisor.model import SupervisorProposalModel
from hyperliquid_v2.supervisor.policy import (
    SupervisorOutcome,
    SupervisorPolicyGate,
)


class SupervisorService:
    def __init__(
        self,
        repository: PostgresRepository,
        model: SupervisorProposalModel,
        github: GitHubDraftPRClient | None,
        gate: SupervisorPolicyGate | None = None,
    ) -> None:
        self.repository = repository
        self.model = model
        self.github = github
        self.gate = gate or SupervisorPolicyGate()

    async def close(self) -> None:
        await self.model.close()
        if self.github is not None:
            await self.github.close()

    async def run(self) -> dict[str, Any]:
        run_id = str(uuid4())
        metrics = await self._verified_metrics()
        await self.repository.save_supervisor_run(run_id, "analyzing", metrics)
        try:
            proposal, model_output = await self.model.propose(metrics)
            if proposal is None:
                result = {
                    "run_id": run_id,
                    "status": "no_change",
                    "model": model_output,
                    "merge_authorized": False,
                    "deploy_authorized": False,
                }
                await self.repository.save_supervisor_run(
                    run_id,
                    result["status"],
                    metrics,
                    model_output=model_output,
                    policy_output={"outcome": "NO_CHANGE"},
                )
                return result

            proposal = replace(
                proposal,
                comparable_samples=int(metrics["quant"]["completed"] or 0),
                out_of_sample_samples=int(metrics["quant"]["out_of_sample_samples"] or 0),
                affected_symbols=tuple(metrics["quant"]["affected_symbols"] or ()),
            )
            decision = self.gate.evaluate(proposal)
            policy_output = {
                "outcome": str(decision.outcome),
                "reasons": list(decision.reasons),
                "verified_proposal": asdict(proposal),
                "merge_authorized": False,
                "deploy_authorized": False,
            }
            if decision.outcome is not SupervisorOutcome.PROPOSE_PR:
                result = {
                    "run_id": run_id,
                    "status": "evidence_gate_rejected",
                    "policy": policy_output,
                    "merge_authorized": False,
                    "deploy_authorized": False,
                }
                await self.repository.save_supervisor_run(
                    run_id,
                    result["status"],
                    metrics,
                    model_output=model_output,
                    policy_output=policy_output,
                )
                return result

            replacement = model_output.get("replacement_policy")
            if not isinstance(replacement, dict):
                result = {
                    "run_id": run_id,
                    "status": "invalid_replacement_policy",
                    "policy": policy_output,
                    "merge_authorized": False,
                    "deploy_authorized": False,
                }
                await self.repository.save_supervisor_run(
                    run_id,
                    result["status"],
                    metrics,
                    model_output=model_output,
                    policy_output=policy_output,
                )
                return result

            if self.github is None:
                result = {
                    "run_id": run_id,
                    "status": "approved_but_github_not_configured",
                    "policy": policy_output,
                    "merge_authorized": False,
                    "deploy_authorized": False,
                }
                await self.repository.save_supervisor_run(
                    run_id,
                    result["status"],
                    metrics,
                    model_output=model_output,
                    policy_output=policy_output,
                )
                return result

            github_output = await self.github.create_policy_draft_pr(
                run_id,
                replacement,
                proposal.hypothesis,
                metrics,
            )
            result = {
                "run_id": run_id,
                "status": "draft_pr_created",
                "policy": policy_output,
                "github": github_output,
                "merge_authorized": False,
                "deploy_authorized": False,
            }
            await self.repository.save_supervisor_run(
                run_id,
                result["status"],
                metrics,
                model_output=model_output,
                policy_output=policy_output,
                github_output=github_output,
            )
            return result
        except Exception as exc:  # noqa: BLE001
            await self.repository.save_supervisor_run(
                run_id,
                "error",
                metrics,
                error_message=str(exc),
            )
            raise

    async def _verified_metrics(self) -> dict[str, Any]:
        metrics = await self.repository.supervisor_metrics()
        pool = self.repository._require_pool()
        verification = await pool.fetchrow(
            """
            WITH completed AS (
                SELECT symbol, observed_at,
                       NTILE(5) OVER (ORDER BY observed_at) AS fold
                FROM v2_quant_observations
                WHERE completed IS TRUE
            )
            SELECT COUNT(*) AS completed,
                   COUNT(*) FILTER (WHERE fold=5) AS oos,
                   ARRAY_AGG(DISTINCT symbol) AS symbols
            FROM completed;
            """
        )
        metrics["quant"]["completed"] = int(verification["completed"] or 0)
        metrics["quant"]["out_of_sample_samples"] = int(verification["oos"] or 0)
        metrics["quant"]["affected_symbols"] = list(verification["symbols"] or [])
        metrics["authority"] = {
            "editable_files": ["v2/config/experimental_policy.json"],
            "maximum_changes": 1,
            "merge_authorized": False,
            "deploy_authorized": False,
            "live_trading_authorized": False,
        }
        return _primitive(metrics)


def _primitive(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_primitive(item) for item in value]
    return value
