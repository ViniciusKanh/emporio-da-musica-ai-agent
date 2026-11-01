"""Public facade: ``EmporioAgent.ask(message, session_id)``.

Provider chain
--------------
Each answer tries an ordered chain of providers and stops at the first grounded answer:

* ``gemini``  - Gemini with native function calling over the read-only tools;
* ``local``   - any OpenAI-compatible endpoint (Ollama, LM Studio, llama.cpp...), optionally used as the
  fallback when Gemini fails (quota / rate limit / token budget / network / ungrounded answer);
* ``offline`` - the deterministic engine, always last. Used when no provider is configured or all of them fail.

The chain comes from the environment (``Settings``) and can be overridden per session from the UI
(``configure_session``), so an API key typed in the browser only affects that visitor's session and is kept
in memory only. The provider actually used and the tokens spent are reported in every ``AgentResponse``.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path

from .business_rules import BusinessRules
from .catalog import CatalogService
from .config import Settings
from .conversation import ConversationState
from .data_store import DataStore
from .guardrails import inspect_message, redact
from .offline_engine import OfflineEngine
from .orders import OrderService
from .policies import PolicyKnowledgeBase
from .router import IntentRouter
from .tools import ToolBox
from .usage import PROVIDER_LABELS, CallUsage, UsageMeter, is_quota_error

logger = logging.getLogger(__name__)
MAX_SESSIONS = 500


@dataclass
class AgentResponse:
    text: str
    mode: str  # "gemini" | "local" | "offline"
    intents: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    handoff: bool = False
    warnings: list[str] = field(default_factory=list)
    handoff_summary: str | None = None  # case note for the human team (not shown to the customer)
    provider_label: str = "Deterministic engine"
    usage: dict | None = None  # tokens spent on this answer, per provider attempt
    fallback_used: bool = False


@dataclass(frozen=True)
class ProviderConfig:
    """Provider choice for one session (from the UI) or for the whole app (from the environment)."""

    primary: str = "auto"  # auto | gemini | local | offline
    gemini_api_key: str | None = field(default=None, repr=False)
    gemini_model: str = "gemini-3.5-flash"
    local_base_url: str | None = None
    local_model: str = "qwen2.5:7b-instruct"
    local_api_key: str | None = field(default=None, repr=False)
    local_timeout: float = 120.0
    fallback_to_local: bool = True
    gemini_token_budget: int | None = None
    local_token_budget: int | None = None

    @classmethod
    def from_settings(cls, s: Settings) -> "ProviderConfig":
        return cls(primary=s.llm_mode, gemini_api_key=s.gemini_api_key, gemini_model=s.gemini_model,
                   local_base_url=s.local_base_url, local_model=s.local_model, local_api_key=s.local_api_key,
                   local_timeout=s.local_timeout, fallback_to_local=s.fallback_to_local,
                   gemini_token_budget=s.gemini_token_budget, local_token_budget=s.local_token_budget)

    def budgets(self) -> dict[str, int | None]:
        return {"gemini": self.gemini_token_budget, "local": self.local_token_budget}


class EmporioAgent:
    def __init__(self, settings: Settings | None = None, llm=None):
        self.settings = settings or Settings.from_env()
        self.rules = BusinessRules()
        self.store = DataStore(self.settings.data_dir)
        self.catalog = CatalogService(self.store, self.rules)
        self.store.report.issues.extend(self.catalog.data_findings())
        self.orders = OrderService(self.store)
        self.policies = PolicyKnowledgeBase(self.settings.policy_path, self.rules)
        self.store.report.issues.extend(self._manual_vs_data_findings())
        self.router = IntentRouter(self.catalog)
        self._sessions: dict[str, ConversationState] = {}
        self.clock = None  # optional callable returning the current store time (tests / reproducible examples)
        self._lock = threading.Lock()
        self.config = ProviderConfig.from_settings(self.settings)
        if llm is not None:
            self.providers = [llm]
        else:
            self.providers = self.build_providers(self.config, strict=True)
        if self.policies.missing_rule_anchors:
            logger.warning("Business rules not found in policy text: %s", self.policies.missing_rule_anchors)

    @classmethod
    def from_paths(cls, data_dir: str | Path, policy_path: str | Path | None = None, llm_mode: str = "offline",
                   llm=None) -> "EmporioAgent":
        return cls(Settings.from_env(data_dir=data_dir, policy_path=policy_path, llm_mode=llm_mode), llm=llm)

    def _manual_vs_data_findings(self) -> list[str]:
        """Claims of the manual that the data snapshot does not support (reported, never repeated to customers)."""
        import re

        text = " ".join(" ".join(c.text.split()) for c in self.policies.chunks)
        found = re.search(r"mais de (\d+) instrumentos", text)
        total = len(self.catalog.products)
        if found and total < int(found.group(1)):
            return [f"manual §1 says the catalog has more than {found.group(1)} instruments; the snapshot has {total} "
                    "products - the agent always quotes the data, not the manual's figure"]
        return []

    # ------------------------------------------------------------ providers
    def build_providers(self, config: ProviderConfig, strict: bool = False) -> list:
        """Ordered provider chain for a configuration. ``strict`` raises when the requested provider is missing
        its settings (used at start-up so a wrong .env fails loudly); the UI uses ``strict=False`` plus
        ``describe_config`` to show what is missing."""
        name = self.settings.assistant_name
        rounds = self.settings.max_tool_rounds

        def gemini():
            from .llm import GeminiAgent  # optional dependency

            return GeminiAgent(api_key=config.gemini_api_key, model=config.gemini_model, max_tool_rounds=rounds,
                               assistant_name=name)

        def local():
            from .llm_openai import OpenAICompatibleAgent

            return OpenAICompatibleAgent(base_url=config.local_base_url, model=config.local_model,
                                         api_key=config.local_api_key, max_tool_rounds=rounds,
                                         timeout=config.local_timeout, assistant_name=name)

        primary = config.primary
        has_gemini, has_local = bool(config.gemini_api_key), bool(config.local_base_url)
        if primary == "offline":
            return []
        if primary == "gemini" and not has_gemini and strict:
            raise RuntimeError("EMPORIO_LLM_MODE=gemini but GEMINI_API_KEY is not set")
        if primary == "local" and not has_local and strict:
            raise RuntimeError("EMPORIO_LLM_MODE=local but LOCAL_LLM_BASE_URL is not set")
        chain = []
        if primary in {"auto", "gemini"} and has_gemini:
            chain.append(gemini())
        if has_local and (primary == "local" or (primary == "auto" and not has_gemini)
                          or (primary in {"auto", "gemini"} and config.fallback_to_local)):
            chain.append(local())
        return chain

    @property
    def llm(self):
        """Primary provider of the default chain (None in deterministic mode)."""
        return self.providers[0] if self.providers else None

    @property
    def mode(self) -> str:
        return chain_mode(self.providers)

    @property
    def model_label(self) -> str:
        return chain_label(self.providers)

    def configure_session(self, session_id: str, config: ProviderConfig | None) -> list:
        """Override the provider chain for one session (None restores the app defaults)."""
        state = self.session(session_id)
        if config is None:
            state.llm_config, state.providers, state.usage = None, None, None
            return self.providers
        providers = self.build_providers(config)
        state.llm_config, state.providers = config, providers
        meter = state.usage if isinstance(state.usage, UsageMeter) else UsageMeter()
        meter.budgets = config.budgets()
        state.usage = meter
        return providers

    def session_providers(self, session_id: str) -> list:
        state = self.session(session_id)
        return state.providers if state.providers is not None else self.providers

    def session_usage(self, session_id: str) -> UsageMeter:
        state = self.session(session_id)
        if not isinstance(state.usage, UsageMeter):
            config = state.llm_config if isinstance(state.llm_config, ProviderConfig) else self.config
            state.usage = UsageMeter(config.budgets())
        return state.usage

    def with_settings(self, **changes) -> Settings:
        return replace(self.settings, **changes)

    # ------------------------------------------------------------ sessions
    def session(self, session_id: str = "default") -> ConversationState:
        with self._lock:
            if session_id not in self._sessions:
                if len(self._sessions) >= MAX_SESSIONS:
                    self._sessions.pop(next(iter(self._sessions)))
                self._sessions[session_id] = ConversationState()
            return self._sessions[session_id]

    def reset(self, session_id: str = "default", keep_settings: bool = False) -> None:
        """Forget the conversation. ``keep_settings`` keeps the session's provider configuration and token
        counters (used by the UI "Nova conversa" button)."""
        with self._lock:
            old = self._sessions.pop(session_id, None)
            if keep_settings and old is not None:
                fresh = ConversationState()
                fresh.llm_config, fresh.providers, fresh.usage = old.llm_config, old.providers, old.usage
                self._sessions[session_id] = fresh

    def _toolbox(self) -> ToolBox:
        return ToolBox(self.catalog, self.orders, self.policies, self.rules)

    # ---------------------------------------------------------------- ask
    def ask(self, message: str, session_id: str = "default") -> AgentResponse:
        state = self.session(session_id)
        state.turns += 1
        logger.info("session=%s turn=%d message=%s", session_id, state.turns, redact(message or "")[:200])
        tools = self._toolbox()
        engine = OfflineEngine(tools, self.router, self.rules, assistant_name=self.settings.assistant_name)
        if self.clock is not None:
            engine.clock = tools.clock = self.clock
        warnings: list[str] = []
        usage_report: dict[str, dict] = {}
        meter = self.session_usage(session_id)

        guard = inspect_message(message or "")
        response: AgentResponse | None = None
        if not guard.flagged and not guard.action_request:
            for index, provider in enumerate(self.session_providers(session_id)):
                name = getattr(provider, "name", "gemini")
                label = PROVIDER_LABELS.get(name, name)
                call = CallUsage()
                try:
                    meter.check(name)
                    result = provider.respond(message, state, tools, usage=call)
                except Exception as exc:  # provider/network/quota errors must never break the chat
                    quota = is_quota_error(exc)
                    meter.record(name, call, ok=False, quota_error=quota)
                    reason = "token budget / quota exhausted" if quota else type(exc).__name__
                    warnings.append(f"LLM unavailable: {label} ({reason}); trying the next option")
                    logger.warning("LLM error (%s): %s", name, exc)
                    tools.reset_trace()
                    continue
                finally:
                    if call.requests:
                        usage_report[name] = vars(call)
                if result.grounded:
                    meter.record(name, call, ok=True)
                    response = AgentResponse(result.text, name, result.intents, [vars(c) for c in tools.trace],
                                             result.sources, result.handoff, warnings,
                                             provider_label=provider_label(provider), usage=usage_report or None,
                                             fallback_used=index > 0)
                    break
                meter.record(name, call, ok=False)
                warnings.append(f"LLM answer failed the grounding check ({label}); trying the next option")
                logger.warning("grounding check failed (%s): %s", name, result.grounding_issues)
                tools.reset_trace()
        if response is None:
            if warnings:
                warnings.append("deterministic answer returned instead")
            offline = engine.respond(message, state)
            response = AgentResponse(offline.text, "offline", offline.intents, [vars(c) for c in tools.trace],
                                     offline.sources, offline.handoff, warnings, offline.handoff_summary,
                                     provider_label="Deterministic engine", usage=usage_report or None,
                                     fallback_used=bool(warnings))
        if not guard.flagged:
            self._remember(state, message, response.text)
        return response

    @staticmethod
    def _remember(state, message: str, answer: str) -> None:
        """Append the turn to the provider-neutral transcript (e-mails tokenised, bounded size)."""
        from .guardrails import PiiVault

        if state.pii_vault is None:
            state.pii_vault = PiiVault()
        state.transcript.append(("user", state.pii_vault.tokenize(message or "")))
        state.transcript.append(("assistant", answer))
        del state.transcript[:-40]


def provider_label(provider) -> str:
    return f"{getattr(provider, 'label', 'Gemini')} · {getattr(provider, 'model', '')}"


def chain_mode(providers: list) -> str:
    return getattr(providers[0], "name", "gemini") if providers else "offline"


def chain_label(providers: list) -> str:
    if not providers:
        return "Deterministic engine (no LLM)"
    return " → ".join(provider_label(p) for p in providers)
