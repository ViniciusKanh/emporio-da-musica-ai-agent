"""Token accounting, per-provider budgets and quota-error detection.

* ``CallUsage`` is filled by a provider while it answers one message (it is updated after every
  API request, so tokens spent before a failure are still counted).
* ``UsageMeter`` aggregates usage per provider for a session and enforces an optional token budget
  per provider. The budget is checked *before* each answer, so it can be exceeded by at most one
  answer; when it is reached the agent skips that provider (e.g. Gemini -> local model -> deterministic).
* ``is_quota_error`` recognises "out of tokens / rate limited" failures (HTTP 429, RESOURCE_EXHAUSTED)
  so the UI can explain why the fallback was used.

Counts come from the providers' own responses (Gemini ``usage_metadata``, OpenAI-compatible ``usage``);
when an endpoint does not report usage the request is counted and the tokens stay at zero.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass

PROVIDER_LABELS = {"gemini": "Gemini", "local": "Modelo local"}


class TokenBudgetExceeded(RuntimeError):
    """The configured token budget for a provider was reached in this session."""


@dataclass
class CallUsage:
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def add(self, prompt: int | None, completion: int | None, total: int | None = None) -> None:
        prompt, completion = int(prompt or 0), int(completion or 0)
        self.requests += 1
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += int(total) if total else prompt + completion


@dataclass
class ProviderUsage(CallUsage):
    answers: int = 0
    errors: int = 0
    quota_errors: int = 0


class UsageMeter:
    def __init__(self, budgets: dict[str, int | None] | None = None):
        self._lock = threading.Lock()
        self.budgets: dict[str, int | None] = dict(budgets or {})
        self._data: dict[str, ProviderUsage] = {}

    def _get(self, provider: str) -> ProviderUsage:
        return self._data.setdefault(provider, ProviderUsage())

    def record(self, provider: str, call: CallUsage, ok: bool, quota_error: bool = False) -> None:
        with self._lock:
            usage = self._get(provider)
            usage.requests += call.requests
            usage.prompt_tokens += call.prompt_tokens
            usage.completion_tokens += call.completion_tokens
            usage.total_tokens += call.total_tokens
            usage.answers += int(ok)
            usage.errors += int(not ok)
            usage.quota_errors += int(quota_error)

    def remaining(self, provider: str) -> int | None:
        budget = self.budgets.get(provider)
        if not budget:
            return None
        with self._lock:
            return max(budget - self._get(provider).total_tokens, 0)

    def check(self, provider: str) -> None:
        if self.remaining(provider) == 0:
            raise TokenBudgetExceeded(f"token budget of {self.budgets[provider]} reached for {provider}")

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            return {name: {**asdict(u), "budget": self.budgets.get(name)} for name, u in self._data.items()}

    def reset(self) -> None:
        with self._lock:
            self._data.clear()


def is_quota_error(exc: BaseException) -> bool:
    """True for budget/quota/rate-limit failures (the cases where switching provider makes sense)."""
    if isinstance(exc, TokenBudgetExceeded):
        return True
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code == 429:
        return True
    text = f"{exc} {getattr(exc, 'status', '')}".lower()
    return any(k in text for k in ("429", "resource_exhausted", "quota", "rate limit", "too many requests"))
