from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any

import httpx


class GitHubDraftPRClient:
    """Least-privilege client: create a branch, replace one policy file, open a draft PR."""

    policy_path = "v2/config/experimental_policy.json"

    def __init__(self, token: str, repository: str, base_branch: str = "main") -> None:
        self.token = token
        self.repository = repository
        self.base_branch = base_branch
        self.api = f"https://api.github.com/repos/{repository}"
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(45, connect=10),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def create_policy_draft_pr(
        self,
        run_id: str,
        replacement_policy: dict[str, Any],
        hypothesis: str,
        evidence_summary: dict[str, Any],
    ) -> dict[str, Any]:
        _validate_policy(replacement_policy)
        base_ref = await self._get(f"/git/ref/heads/{self.base_branch}")
        base_sha = base_ref["object"]["sha"]
        branch = (
            "v2-supervisor/"
            + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            + "-"
            + run_id[:8]
        )
        await self._post(
            "/git/refs",
            {"ref": f"refs/heads/{branch}", "sha": base_sha},
        )
        existing = await self._get(
            f"/contents/{self.policy_path}?ref={self.base_branch}"
        )
        content = json.dumps(replacement_policy, indent=2, sort_keys=True) + "\n"
        await self._put(
            f"/contents/{self.policy_path}",
            {
                "message": f"V2 Supervisor experiment: {hypothesis[:72]}",
                "content": base64.b64encode(content.encode()).decode(),
                "sha": existing["sha"],
                "branch": branch,
            },
        )
        body = (
            "## Shadow-only Supervisor proposal\n\n"
            f"**Run:** `{run_id}`\n\n"
            f"**Hypothesis:** {hypothesis}\n\n"
            "This draft PR changes exactly one experimental configuration file. "
            "It does not authorize merge, deployment, live trading, execution changes, "
            "risk-cap changes or secret access.\n\n"
            "### Evidence snapshot\n```json\n"
            + json.dumps(evidence_summary, indent=2, default=str)[:12000]
            + "\n```\n"
        )
        pr = await self._post(
            "/pulls",
            {
                "title": f"V2 Supervisor experiment: {hypothesis[:70]}",
                "head": branch,
                "base": self.base_branch,
                "body": body,
                "draft": True,
                "maintainer_can_modify": True,
            },
        )
        return {
            "branch": branch,
            "number": pr.get("number"),
            "url": pr.get("html_url"),
            "draft": True,
            "merge_authorized": False,
            "deploy_authorized": False,
        }

    async def _get(self, path: str) -> dict[str, Any]:
        response = await self.client.get(self.api + path)
        response.raise_for_status()
        return response.json()

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self.client.post(self.api + path, json=payload)
        response.raise_for_status()
        return response.json()

    async def _put(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self.client.put(self.api + path, json=payload)
        response.raise_for_status()
        return response.json()


def _validate_policy(policy: dict[str, Any]) -> None:
    if set(policy) - {"guardian", "opportunity", "supervisor_history"}:
        raise ValueError("replacement policy contains unauthorized top-level keys")
    guardian = policy.get("guardian")
    opportunity = policy.get("opportunity")
    if not isinstance(guardian, dict) or not isinstance(opportunity, dict):
        raise ValueError("replacement policy requires guardian and opportunity objects")
    allowed_guardian = {
        "min_mfe_for_dynamic_floor_r",
        "exhaustion_reversal_margin",
        "minimum_profit_retention",
    }
    allowed_opportunity = {
        "max_distance_ema_atr",
        "min_reward_risk",
        "min_volume_ratio",
    }
    if set(guardian) - allowed_guardian or set(opportunity) - allowed_opportunity:
        raise ValueError("replacement policy attempts to change unauthorized parameters")
    ranges = {
        "min_mfe_for_dynamic_floor_r": (0.30, 1.50),
        "exhaustion_reversal_margin": (-0.20, 0.40),
        "minimum_profit_retention": (0.15, 0.80),
        "max_distance_ema_atr": (0.40, 2.00),
        "min_reward_risk": (1.10, 3.00),
        "min_volume_ratio": (0.50, 2.00),
    }
    for group in (guardian, opportunity):
        for key, value in group.items():
            low, high = ranges[key]
            numeric = float(value)
            if not low <= numeric <= high:
                raise ValueError(f"{key} outside the bounded experimental range")
