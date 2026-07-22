from __future__ import annotations

from hyperliquid_v2.runtime.settings import Settings
from hyperliquid_v2.runtime.shadow_service import ShadowService
from hyperliquid_v2.storage.operational import OperationalPostgresRepository


class OperationalShadowService(ShadowService):
    """ShadowService wired to the net-outcome-aware operational repository."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.repository = OperationalPostgresRepository(settings.database_url)
