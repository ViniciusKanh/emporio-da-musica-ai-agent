"""Reply language: English by default (the agent understands Portuguese); 'auto' keeps the customer's language."""

from __future__ import annotations

from emporio_ai.agent import EmporioAgent
from emporio_ai.config import SAMPLE_DATA_DIR


def test_portuguese_question_gets_an_english_answer(agent):
    text = agent.ask("Qual o horário de funcionamento no sábado?").text
    assert "Saturday" in text and "Sábado" not in text and "Is there anything else" in text


def test_handoff_case_note_is_in_english(agent):
    response = agent.ask("Quero reclamar do atendimento, preciso falar com alguém")
    assert response.handoff and response.handoff_summary.startswith("Reason:")
    assert "Customer wrote in: Portuguese" in response.handoff_summary


def test_auto_mode_keeps_the_customer_language(monkeypatch):
    monkeypatch.setenv("EMPORIO_REPLY_LANGUAGE", "auto")
    agent = EmporioAgent.from_paths(SAMPLE_DATA_DIR, SAMPLE_DATA_DIR / "policies.md", llm_mode="offline")
    assert agent.settings.reply_language == "auto"
    assert "Sábado" in agent.ask("Qual o horário de sábado?").text
    assert "Saturday" in agent.ask("What are your Saturday hours?", session_id="en").text
