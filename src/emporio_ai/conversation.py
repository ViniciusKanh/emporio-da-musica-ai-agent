"""Per-session conversation state (kept in memory only).

Only what is needed for continuity is stored; e-mails used for order verification
live in this in-memory object for the session and are never written to disk or logs.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ConversationState:
    lang: str = "pt"
    last_product_ids: list[int] = field(default_factory=list)
    last_search: dict = field(default_factory=dict)
    pending_order_id: int | None = None
    pending_email: str | None = field(default=None, repr=False)
    verified_order: tuple[int, str] | None = field(default=None, repr=False)
    verified_order_view: dict | None = field(default=None, repr=False)
    failed_order_attempts: int = 0
    turns: int = 0
    # Provider-neutral transcript of (role, text) pairs, e-mails already tokenised. Every LLM provider builds its
    # own history from it, so the conversation continues when the agent switches provider (e.g. Gemini -> local).
    transcript: list = field(default_factory=list, repr=False)
    pii_vault: object | None = field(default=None, repr=False)  # e-mail <-> token map for the LLM path
    # Optional per-session provider configuration set from the UI (None = use the agent defaults).
    llm_config: object | None = field(default=None, repr=False)
    providers: list | None = field(default=None, repr=False)
    usage: object | None = field(default=None, repr=False)  # UsageMeter for this session
