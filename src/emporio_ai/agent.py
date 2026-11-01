"""Public facade: ``EmporioAgent.ask(message, session_id)``.

Mode selection
--------------
* ``gemini``  - Gemini with native function calling over the read-only tools;
* ``offline`` - deterministic engine (no LLM). Used when no API key is configured,
  when the provider fails, or when the LLM answer fails the grounding check.
The mode actually used for every answer is reported in ``AgentResponse.mode``.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
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


class EmporioAgent:
    def __init__(self, settings: Settings | None = None, llm=None):
        self.settings = settings or Settings.from_env()
        self.rules = BusinessRules()
        self.store = DataStore(self.settings.data_dir)
        self.catalog = CatalogService(self.store, self.rules)
        self.store.report.issues.extend(self.catalog.data_findings())
        self.orders = OrderService(self.store)
        self.policies = PolicyKnowledgeBase(self.settings.policy_path, self.rules)
        self.router = IntentRouter(self.catalog)
        self._sessions: dict[str, ConversationState] = {}
        self.clock = None  # optional callable returning the current store time (tests / reproducible examples)
        self._lock = threading.Lock()
        self.llm = llm if llm is not None else self._build_llm()
        if self.policies.missing_rule_anchors:
            logger.warning("Business rules not found in policy text: %s", self.policies.missing_rule_anchors)

    @classmethod
    def from_paths(cls, data_dir: str | Path, policy_path: str | Path | None = None, llm_mode: str = "offline",
                   llm=None) -> "EmporioAgent":
        return cls(Settings.from_env(data_dir=data_dir, policy_path=policy_path, llm_mode=llm_mode), llm=llm)

    def _build_llm(self):
        """Pick the LLM provider. Order in 'auto': Gemini (if a key is set), then a local/OpenAI-compatible
        endpoint (if LOCAL_LLM_BASE_URL is set), otherwise the deterministic engine only."""
        s = self.settings
        if s.llm_mode == "offline":
            return None
        if s.llm_mode in {"auto", "gemini"} and s.gemini_api_key:
            from .llm import GeminiAgent  # optional dependency

            return GeminiAgent(api_key=s.gemini_api_key, model=s.gemini_model, max_tool_rounds=s.max_tool_rounds)
        if s.llm_mode == "gemini":
            raise RuntimeError("EMPORIO_LLM_MODE=gemini but GEMINI_API_KEY is not set")
        if s.local_base_url and s.llm_mode in {"auto", "local"}:
            from .llm_openai import OpenAICompatibleAgent

            return OpenAICompatibleAgent(base_url=s.local_base_url, model=s.local_model, api_key=s.local_api_key,
                                         max_tool_rounds=s.max_tool_rounds, timeout=s.local_timeout)
        if s.llm_mode == "local":
            raise RuntimeError("EMPORIO_LLM_MODE=local but LOCAL_LLM_BASE_URL is not set")
        return None

    @property
    def mode(self) -> str:
        return getattr(self.llm, "name", "gemini") if self.llm is not None else "offline"

    @property
    def model_label(self) -> str:
        if self.llm is None:
            return "Deterministic engine (no LLM)"
        return f"{getattr(self.llm, 'label', 'Gemini')} · {getattr(self.llm, 'model', '')}"

    # ------------------------------------------------------------ sessions
    def session(self, session_id: str = "default") -> ConversationState:
        with self._lock:
            if session_id not in self._sessions:
                if len(self._sessions) >= MAX_SESSIONS:
                    self._sessions.pop(next(iter(self._sessions)))
                self._sessions[session_id] = ConversationState()
            return self._sessions[session_id]

    def reset(self, session_id: str = "default") -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def _toolbox(self) -> ToolBox:
        return ToolBox(self.catalog, self.orders, self.policies, self.rules)

    # ---------------------------------------------------------------- ask
    def ask(self, message: str, session_id: str = "default") -> AgentResponse:
        state = self.session(session_id)
        state.turns += 1
        logger.info("session=%s turn=%d message=%s", session_id, state.turns, redact(message or "")[:200])
        tools = self._toolbox()
        engine = OfflineEngine(tools, self.router, self.rules)
        if self.clock is not None:
            engine.clock = tools.clock = self.clock
        warnings: list[str] = []

        guard = inspect_message(message or "")
        if self.llm is not None and not guard.flagged and not guard.action_request:
            try:
                result = self.llm.respond(message, state, tools)
                if result.grounded:
                    return AgentResponse(result.text, self.mode, result.intents,
                                         [vars(c) for c in tools.trace], result.sources, result.handoff)
                warnings.append("LLM answer failed the grounding check; deterministic answer returned instead")
                logger.warning("grounding check failed: %s", result.grounding_issues)
            except Exception as exc:  # provider/network/quota errors must never break the chat
                warnings.append(f"LLM unavailable ({type(exc).__name__}); deterministic answer returned instead")
                logger.warning("LLM error: %s", exc)
            tools.reset_trace()
        offline = engine.respond(message, state)
        return AgentResponse(offline.text, "offline", offline.intents, [vars(c) for c in tools.trace],
                             offline.sources, offline.handoff, warnings, offline.handoff_summary)
