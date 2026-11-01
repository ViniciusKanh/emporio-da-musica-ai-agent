"""Behaviours required by the store manual (§5.2, §6.1, §7.1, §7.2, §7.3)."""

from datetime import datetime

from emporio_ai.store_hours import STORE_TZ

SUNDAY = datetime(2026, 11, 8, 10, 0, tzinfo=STORE_TZ)
MONDAY = datetime(2026, 11, 2, 11, 0, tzinfo=STORE_TZ)


def test_out_of_hours_handoff_says_when_the_team_is_back(agent):
    agent.clock = lambda: SUNDAY
    text = agent.ask("Quero falar com um atendente").text
    assert "fora do expediente" in text and "amanhã às 10:00" in text


def test_business_hours_handoff_has_no_out_of_hours_note(agent):
    agent.clock = lambda: MONDAY
    assert "fora do expediente" not in agent.ask("Quero falar com um atendente").text


def test_verified_customer_is_greeted_by_first_name_only(agent):
    text = agent.ask("pedido 1002 cliente.dois@example.com").text
    assert text.startswith("Obrigado, Cliente!")
    assert "Cliente Sintético Dois" not in text


def test_handoff_summary_for_the_team(agent):
    response = agent.ask("Quero reclamar, meu pedido 1003 está atrasado, email cliente.tres@example.org")
    assert response.handoff and response.handoff_summary
    assert "Pedido verificado: #1003" in response.handoff_summary
    assert "@" not in response.handoff_summary  # e-mail redacted in the case note
    assert "handoff_summary" not in response.text


def test_inactive_campaign_quotes_the_manual_calendar(original_agent):
    text = original_agent.ask("Vai ter desconto de Black Friday?").text
    assert "não está ativa" in text and "§6.1" in text and "Novembro" in text
    assert "não consigo garantir" in text


def test_large_instrument_freight_needs_individual_quote(original_agent):
    response = original_agent.ask("Quanto fica o frete da Bateria Acústica Pearl Kit 2 para Dourados?")
    assert "frete especial" in response.text and response.handoff


def test_overdue_shipment_is_flagged_and_escalated(agent):
    agent.clock = lambda: MONDAY  # order 1002 was expected on 2026-01-25
    response = agent.ask("pedido 1002 cliente.dois@example.com")
    assert "já passou" in response.text and response.handoff


def test_shipment_not_yet_due_is_not_flagged(agent):
    agent.clock = lambda: datetime(2026, 1, 20, 11, 0, tzinfo=STORE_TZ)
    response = agent.ask("pedido 1002 cliente.dois@example.com")
    assert "já passou" not in response.text and not response.handoff
