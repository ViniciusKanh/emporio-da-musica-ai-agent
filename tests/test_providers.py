"""Provider chain, per-session configuration, token accounting and quota fallback.

Uses the real google-genai response types with a scripted fake client (Gemini) and an injected ``post``
function (OpenAI-compatible endpoint). No network access; this validates the orchestration, not any real model.
"""

from __future__ import annotations

import json

import pytest

genai_types = pytest.importorskip("google.genai.types")

from emporio_ai.agent import EmporioAgent, ProviderConfig  # noqa: E402
from emporio_ai.config import SAMPLE_DATA_DIR  # noqa: E402
from emporio_ai.llm import GeminiAgent  # noqa: E402
from emporio_ai.llm_openai import LLMProviderError, OpenAICompatibleAgent  # noqa: E402
from emporio_ai.usage import CallUsage, TokenBudgetExceeded, UsageMeter, is_quota_error  # noqa: E402


class QuotaError(Exception):
    code = 429


def g_call(name, tokens=0, **args):
    return genai_types.GenerateContentResponse(
        candidates=[genai_types.Candidate(content=genai_types.Content(role="model", parts=[
            genai_types.Part(function_call=genai_types.FunctionCall(name=name, args=args))]))],
        usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
            prompt_token_count=tokens, candidates_token_count=5, total_token_count=tokens + 5))


def g_text(text, tokens=0):
    return genai_types.GenerateContentResponse(
        candidates=[genai_types.Candidate(content=genai_types.Content(role="model", parts=[genai_types.Part(text=text)]))],
        usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
            prompt_token_count=tokens, candidates_token_count=10, total_token_count=tokens + 10))


class FakeModels:
    def __init__(self, script):
        self.script, self.requests = list(script), []

    def generate_content(self, model, contents, config):
        self.requests.append(contents)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    def __init__(self, script):
        self.models = FakeModels(script)


def o_final(text, tokens=30):
    return {"choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": tokens, "completion_tokens": 7, "total_tokens": tokens + 7}}


def o_call(name, **args):
    return {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 3, "total_tokens": 23}}


class FakePost:
    def __init__(self, script):
        self.script, self.requests = list(script), []

    def __call__(self, url, payload, headers, timeout):
        self.requests.append(payload)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def build(gemini_script=None, local_script=None):
    providers, gemini, post = [], None, None
    if gemini_script is not None:
        gemini = GeminiAgent(api_key="test", model="fake-gemini", client=FakeClient(gemini_script))
        providers.append(gemini)
    if local_script is not None:
        post = FakePost(local_script)
        providers.append(OpenAICompatibleAgent(base_url="http://local/v1", model="fake-local", post=post))
    agent = EmporioAgent.from_paths(SAMPLE_DATA_DIR, SAMPLE_DATA_DIR / "policies.md", llm_mode="offline")
    agent.providers = providers
    return agent, gemini, post


def test_quota_error_on_gemini_falls_back_to_local_model():
    agent, _, post = build([QuotaError("429 RESOURCE_EXHAUSTED: quota exceeded")],
                           [o_call("get_product_details", product="Aurora C10"),
                            o_final("O Aurora C10 custa R$ 480,00.")])
    response = agent.ask("Quanto custa o Aurora C10?")
    assert response.mode == "local" and response.fallback_used
    assert any("quota" in w for w in response.warnings)
    assert response.usage["local"]["total_tokens"] == 23 + 37
    snap = agent.session_usage("default").snapshot()
    assert snap["gemini"]["quota_errors"] == 1 and snap["local"]["answers"] == 1


def test_all_providers_fail_returns_deterministic_answer():
    agent, _, _ = build([QuotaError("quota")], [LLMProviderError("cannot reach endpoint")])
    response = agent.ask("Quanto custa o Aurora C10?")
    assert response.mode == "offline" and "R$ 480,00" in response.text
    assert response.warnings[-1] == "deterministic answer returned instead"


def test_gemini_tokens_are_counted_and_budget_switches_provider():
    agent, gemini, post = build(
        [g_call("get_product_details", tokens=100, product="Aurora C10"), g_text("Custa R$ 480,00.", tokens=150)],
        [o_call("get_product_details", product="Aurora F20"), o_final("O Aurora F20 custa R$ 690,00.")])
    meter = agent.session_usage("default")
    meter.budgets = {"gemini": 200}
    first = agent.ask("Quanto custa o Aurora C10?")
    assert first.mode == "gemini" and first.usage["gemini"] == {
        "requests": 2, "prompt_tokens": 250, "completion_tokens": 15, "total_tokens": 265}
    second = agent.ask("Quanto custa o Aurora F20?")
    assert second.mode == "local" and any("budget" in w for w in second.warnings)
    assert gemini.client.models.script == []  # Gemini was not called again
    # the local model received the earlier turn answered by Gemini (provider-neutral transcript)
    history = [m["content"] for m in post.requests[0]["messages"] if m["role"] == "assistant"]
    assert "Custa R$ 480,00." in history


def test_ungrounded_gemini_answer_tries_local_before_deterministic():
    agent, _, _ = build([g_text("O Aurora C10 custa R$ 99,00.")],
                        [o_call("get_product_details", product="Aurora C10"), o_final("Custa R$ 480,00.")])
    response = agent.ask("Quanto custa o Aurora C10?")
    assert response.mode == "local" and any("grounding" in w for w in response.warnings)


def test_session_configuration_is_isolated_and_keys_are_not_exposed():
    agent = EmporioAgent.from_paths(SAMPLE_DATA_DIR, SAMPLE_DATA_DIR / "policies.md", llm_mode="offline")
    config = ProviderConfig(primary="local", local_base_url="http://127.0.0.1:9/v1", local_model="m",
                            local_api_key="secret-local", gemini_api_key="secret-gemini", gemini_token_budget=500)
    chain = agent.configure_session("visitor-a", config)
    assert [p.name for p in chain] == ["local"]
    assert agent.session_providers("visitor-b") == [] and agent.mode == "offline"
    assert agent.session_usage("visitor-a").budgets["gemini"] == 500
    assert "secret" not in repr(config)
    agent.configure_session("visitor-a", None)
    assert agent.session_providers("visitor-a") == []


@pytest.mark.parametrize("primary,fallback,expected", [
    ("auto", True, ["gemini", "local"]), ("auto", False, ["gemini"]), ("gemini", True, ["gemini", "local"]),
    ("local", True, ["local"]), ("offline", True, []),
])
def test_provider_chain(primary, fallback, expected):
    agent = EmporioAgent.from_paths(SAMPLE_DATA_DIR, SAMPLE_DATA_DIR / "policies.md", llm_mode="offline")
    config = ProviderConfig(primary=primary, gemini_api_key="k", local_base_url="http://x/v1",
                            fallback_to_local=fallback)
    assert [p.name for p in agent.build_providers(config)] == expected


def test_usage_meter_and_quota_detection():
    meter = UsageMeter({"gemini": 10})
    call = CallUsage()
    call.add(8, 4)
    meter.record("gemini", call, ok=True)
    assert meter.remaining("gemini") == 0
    with pytest.raises(TokenBudgetExceeded):
        meter.check("gemini")
    assert meter.remaining("local") is None
    assert is_quota_error(QuotaError("x")) and is_quota_error(RuntimeError("RESOURCE_EXHAUSTED"))
    assert not is_quota_error(ValueError("bad request"))


def test_local_ping_and_model_listing():
    post = FakePost([o_final("OK", tokens=3), {"data": [{"id": "qwen2.5:7b-instruct"}, {"id": "llama3.1:8b"}]}])
    llm = OpenAICompatibleAgent(base_url="http://local/v1", model="m", post=post)
    usage = CallUsage()
    assert llm.ping(usage) == "OK" and usage.total_tokens == 10
    assert llm.list_models() == ["llama3.1:8b", "qwen2.5:7b-instruct"]


def test_greeting_introduces_the_assistant_by_name(agent):
    assert "Vinicius" in agent.ask("Oi, tudo bem?").text
