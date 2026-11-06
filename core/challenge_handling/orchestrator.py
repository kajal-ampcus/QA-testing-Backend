"""Small adapter registry; engine code never needs per-provider branches."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from .models import Challenge, ChallengeResult, ChallengeStatus, ChallengeStrategy

Handler = Callable[[Challenge], Awaitable[bool]]


class ChallengeOrchestrator:
    def __init__(self) -> None:
        self._handlers: dict[ChallengeStrategy, Handler] = {}

    def register(self, strategy: ChallengeStrategy, handler: Handler) -> None:
        self._handlers[strategy] = handler

    async def handle(self, challenge: Challenge, strategy: ChallengeStrategy) -> ChallengeResult:
        started = time.monotonic()
        if strategy == ChallengeStrategy.BLOCK:
            return ChallengeResult(ChallengeStatus.BLOCKED, challenge, strategy, detail="No authorized strategy is configured.")
        if strategy == ChallengeStrategy.HUMAN_INTERVENTION:
            return ChallengeResult(ChallengeStatus.MANUAL_INTERVENTION_REQUIRED, challenge, strategy, detail="Complete the challenge in the preserved browser session.")
        handler = self._handlers.get(strategy)
        if handler is None:
            return ChallengeResult(ChallengeStatus.UNSUPPORTED, challenge, strategy, detail="Configured strategy has no adapter.")
        try:
            verified = await handler(challenge)
        except TimeoutError:
            return ChallengeResult(ChallengeStatus.TIMEOUT, challenge, strategy, duration_ms=int((time.monotonic() - started) * 1000))
        except Exception as exc:  # adapters intentionally return no secrets in errors
            return ChallengeResult(ChallengeStatus.FAILED, challenge, strategy, detail=type(exc).__name__, duration_ms=int((time.monotonic() - started) * 1000))
        return ChallengeResult(ChallengeStatus.SUCCESS if verified else ChallengeStatus.FAILED, challenge, strategy, verified=verified, attempts=1, duration_ms=int((time.monotonic() - started) * 1000))
