"""OpenAI-compatible provider (local models via Ollama/LM Studio, or online endpoints).

A real HTTP server in a background thread plays the role of the model endpoint, so the test exercises the
actual wire format (JSON over HTTP, tool_calls, tool messages) and the standard-library client. It does NOT
evaluate the quality of any real model.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from emporio_ai.agent import EmporioAgent
from emporio_ai.config import SAMPLE_DATA_DIR
from emporio_ai.llm_openai import OpenAICompatibleAgent


def tool_call(name, **args):
    return {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}}]}


def final(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


@pytest.fixture()
def fake_endpoint():
    script: list[dict] = []
    received: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})
            payload = json.dumps(script.pop(0)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1", script, received
    server.shutdown()


def make_agent(base_url):
    llm = OpenAICompatibleAgent(base_url=base_url, model="qwen2.5:7b-instruct", api_key="test-key", timeout=10)
    return EmporioAgent.from_paths(SAMPLE_DATA_DIR, SAMPLE_DATA_DIR / "policies.md", llm=llm)


def test_tool_round_trip_over_http(fake_endpoint):
    url, script, received = fake_endpoint
    script += [tool_call("get_product_details", product="Aurora C10"),
               final("O Aurora C10 custa R$ 480,00 e sai por R$ 456,00 no PIX.")]
    response = make_agent(url).ask("Quanto custa o Aurora C10?")
    assert response.mode == "local" and "R$ 480,00" in response.text
    assert received[0]["path"] == "/v1/chat/completions" and received[0]["auth"] == "Bearer test-key"
    assert {t["function"]["name"] for t in received[0]["body"]["tools"]} >= {"get_product_details", "get_order_status"}
    tool_message = received[1]["body"]["messages"][-1]
    assert tool_message["role"] == "tool" and json.loads(tool_message["content"])["products"][0]["list_price"] == 480


def test_e_mail_never_sent_to_the_endpoint(fake_endpoint):
    url, script, received = fake_endpoint
    script += [tool_call("get_order_status", order_id=1002, email="<EMAIL_1>"), final("Pedido #1002 enviado.")]
    response = make_agent(url).ask("pedido 1002 cliente.dois@example.com")
    assert "cliente.dois@example.com" not in json.dumps(received)
    assert json.loads(received[1]["body"]["messages"][-1]["content"])["verified"] is True
    assert response.mode == "local"


def test_ungrounded_local_answer_falls_back(fake_endpoint):
    url, script, _ = fake_endpoint
    script += [final("Custa R$ 99,00!")]
    response = make_agent(url).ask("Quanto custa o Aurora C10?")
    assert response.mode == "offline" and "R$ 480,00" in response.text


def test_unreachable_endpoint_falls_back():
    response = make_agent("http://127.0.0.1:9/v1").ask("Qual o horário de sábado?")
    assert response.mode == "offline" and any("LLM unavailable" in w for w in response.warnings)


def test_local_provider_selected_from_settings(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    agent = EmporioAgent.from_paths(SAMPLE_DATA_DIR, SAMPLE_DATA_DIR / "policies.md", llm_mode="auto")
    assert agent.mode == "local" and "qwen" in agent.model_label
