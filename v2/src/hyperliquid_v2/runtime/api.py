from __future__ import annotations

import hmac
import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Header, HTTPException

from hyperliquid_v2.runtime.settings import Settings
from hyperliquid_v2.runtime.shadow_service import ShadowService
from hyperliquid_v2.supervisor.github import GitHubDraftPRClient
from hyperliquid_v2.supervisor.model import SupervisorProposalModel
from hyperliquid_v2.supervisor.service import SupervisorService

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
LOGGER = logging.getLogger("hyperliquid_v2")

settings: Settings | None = None
shadow: ShadowService | None = None
supervisor: SupervisorService | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global settings, shadow, supervisor
    settings = Settings.from_env()
    shadow = ShadowService(settings)
    await shadow.start()
    proposal_model = SupervisorProposalModel(
        settings.supervisor_provider,
        settings.supervisor_model,
    )
    github = (
        GitHubDraftPRClient(
            settings.github_token,
            settings.github_repository,
            settings.github_base_branch,
        )
        if settings.github_token
        else None
    )
    supervisor = SupervisorService(
        shadow.repository,
        proposal_model,
        github,
    )
    LOGGER.info("Hyperliquid V2 operational shadow service started")
    try:
        yield
    finally:
        if supervisor is not None:
            await supervisor.close()
        if shadow is not None:
            await shadow.stop()
        LOGGER.info("Hyperliquid V2 operational shadow service stopped")


app = FastAPI(
    title="Hyperliquid Bot V2 Shadow",
    version="0.2.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict:
    if shadow is None or settings is None:
        raise HTTPException(status_code=503, detail="starting")
    status = shadow.runtime_status()
    return {
        "status": "ok",
        "mode": "shadow",
        "live_trading_enabled": False,
        "runtime": status,
    }


@app.get("/status")
async def status() -> dict:
    if shadow is None:
        raise HTTPException(status_code=503, detail="starting")
    return {
        "runtime": shadow.runtime_status(),
        "services": await shadow.repository.status(),
    }


@app.post("/supervisor/run")
async def run_supervisor(
    x_supervisor_token: str | None = Header(default=None),
) -> dict:
    if settings is None or supervisor is None:
        raise HTTPException(status_code=503, detail="starting")
    if not settings.supervisor_token:
        raise HTTPException(
            status_code=503,
            detail="V2_SUPERVISOR_TOKEN is not configured",
        )
    if not x_supervisor_token or not hmac.compare_digest(
        x_supervisor_token,
        settings.supervisor_token,
    ):
        raise HTTPException(status_code=401, detail="invalid supervisor token")
    return await supervisor.run()


if __name__ == "__main__":
    uvicorn.run(
        "hyperliquid_v2.runtime.api:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )
