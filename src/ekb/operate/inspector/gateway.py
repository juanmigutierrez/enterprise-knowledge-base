"""What the inspector shows of the model gateway (S7): read-only, and never a key or a prompt.

The panel reads the same cost log the gateway writes (``.cache/ekb/model-calls.jsonl``),
so every call made by ``ekb demo gateway`` and, from S8, by the pipeline appears
in the **Model gateway** tab. The log holds no prompt and no reply, so the panel
cannot show any document text, deleted or not. Of the keys it knows only whether
each is set (``ekb.app.inspector`` works that out; the panel never sees the environment).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from ekb.platform.gateway.config import GatewayConfig
from ekb.platform.gateway.gateway import utc_now
from ekb.platform.gateway.models import CallRecord
from ekb.platform.gateway.port import CostLog
from ekb.platform.gateway.status import ProviderStatus


@dataclass(frozen=True)
class GatewayPanel:
    config: GatewayConfig = field(default_factory=GatewayConfig)
    log: CostLog | None = None
    """``None``: no cost log to read (the panel shows the routes and no calls)."""
    providers: list[ProviderStatus] = field(default_factory=list)
    max_spend_usd: float = 0.0
    clock: Callable[[], datetime] = utc_now

    def records(self) -> list[CallRecord]:
        return self.log.records() if self.log is not None else []
