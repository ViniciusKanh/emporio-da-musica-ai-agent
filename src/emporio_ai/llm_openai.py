"""LLM provider for any OpenAI-compatible chat-completions endpoint with tool calling.

Covers, with no extra Python dependency (standard-library HTTP only):
* local models — e.g. Ollama (``http://localhost:11434/v1``), LM Studio (``http://localhost:1234/v1``) or a
  llama.cpp server, serving open-weight models such as Qwen 2.5 or Llama 3.1 downloaded from Hugging Face;
* online providers that expose the same API.

The loop, the read-only tools, the e-mail tokenisation and the grounding check are the same as in the Gemini
provider; only the wire format changes. Small local models call tools less reliably than hosted models — when
they fail or produce an ungrounded answer, the agent falls back to the deterministic engine.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Callable

from .conversation import ConversationState
from .guardrails import PiiVault
from .llm import MAX_HISTORY_CONTENTS, SYSTEM_PROMPT, TOOL_DECLARATIONS, LLMResult, assess_answer, build_result
from .tools import ToolBox

logger = logging.getLogger(__name__)

OPENAI_TOOLS = [{"type": "function", "function": {"name": d["name"], "description": d["description"],
                                                  "parameters": d["parameters"]}} for d in TOOL_DECLARATIONS]


class LLMProviderError(RuntimeError):
    """Endpoint unreachable, HTTP error or malformed response."""


def http_post_json(url: str, payload: dict, headers: dict[str, str], timeout: float) -> dict:
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL is user-configured
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise LLMProviderError(f"HTTP {exc.code} from {url}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMProviderError(f"cannot reach {url}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LLMProviderError("invalid JSON from the model endpoint") from exc


class OpenAICompatibleAgent:
    name = "local"
    label = "Local/OpenAI-compatible"

    def __init__(self, base_url: str, model: str, api_key: str | None = None, max_tool_rounds: int = 5,
                 timeout: float = 120.0, temperature: float = 0.2,
                 post: Callable[[str, dict, dict[str, str], float], dict] | None = None):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.max_tool_rounds = max_tool_rounds
        self.timeout = timeout
        self.temperature = temperature
        self.post = post or http_post_json  # injectable for tests

    def respond(self, message: str, state: ConversationState, tools: ToolBox) -> LLMResult:
        if state.pii_vault is None:
            state.pii_vault = PiiVault()
        vault: PiiVault = state.pii_vault  # type: ignore[assignment]
        tools.email_resolver = vault.resolve
        user_turn = {"role": "user", "content": vault.tokenize(message or "")}
        history = list(state.llm_history[-MAX_HISTORY_CONTENTS:])
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}, *history, user_turn]
        results: list[dict] = []
        text = ""
        for _ in range(self.max_tool_rounds + 1):
            data = self.post(self.url, {"model": self.model, "messages": messages, "tools": OPENAI_TOOLS,
                                        "tool_choice": "auto", "temperature": self.temperature},
                             self.headers, self.timeout)
            try:
                reply = data["choices"][0]["message"]
            except (KeyError, IndexError, TypeError) as exc:
                raise LLMProviderError("unexpected response format") from exc
            calls = reply.get("tool_calls") or []
            if not calls:
                text = (reply.get("content") or "").strip()
                break
            messages.append({"role": "assistant", "content": reply.get("content") or "", "tool_calls": calls})
            for call in calls:
                function = call.get("function", {})
                raw_args = function.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except json.JSONDecodeError:
                    args = {}
                result = tools.call(function.get("name", ""), args if isinstance(args, dict) else {})
                results.append(result)
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "name": function.get("name", ""),
                                 "content": json.dumps(result, ensure_ascii=False)})
        else:
            raise LLMProviderError("tool-call limit reached without a final answer")
        if not text:
            raise LLMProviderError("empty answer from model")
        issues = assess_answer(text, results, tools)
        state.llm_history = (history + [user_turn, {"role": "assistant", "content": text}])[-MAX_HISTORY_CONTENTS:]
        logger.info("local-llm tools=%s grounded=%s", [c.name for c in tools.trace], not issues)
        return build_result(text, issues, tools)
