import pytest

from emporio_ai.business_rules import BusinessRules
from emporio_ai.policies import PolicyKnowledgeBase


@pytest.mark.parametrize("query,section", [
    ("Me arrependi da compra, posso devolver?", "4.1"),
    ("Can I return my order?", "4.1"),
    ("posso parcelar em 12x?", "3.1"),
    ("o desconto do pix vale na promoção?", "6.2"),
    ("qual o horário de sábado?", "2"),
])
def test_sample_retrieval_ranks_expected_section_first(agent, query, section):
    hits = agent.policies.search(query)
    assert hits and hits[0]["section"] == section
    assert hits[0]["citation"].startswith("Manual de Políticas §")


def test_unrelated_query_returns_no_policy(agent):
    assert agent.policies.search("qual a capital da França?") == []


def test_store_info_detects_conflicting_whatsapp_numbers(agent):
    info = agent.policies.store_info
    assert info.address.startswith("Rua Fictícia")
    assert any("whatsapp" in c for c in info.conflicts)


def test_rule_drift_is_detected(tmp_path):
    doc = tmp_path / "p.md"
    doc.write_text("1. Formas de Pagamento\nPIX com 10% de desconto sobre o preço.\n", encoding="utf-8")
    kb = PolicyKnowledgeBase(doc, BusinessRules())
    assert "pix_discount_percent" in kb.missing_rule_anchors


def test_offline_answers_fall_back_to_excerpts_when_manual_differs(tmp_path, agent):
    """If the manual does not contain the curated facts, the engine quotes the retrieved text instead."""
    from emporio_ai.agent import EmporioAgent

    doc = tmp_path / "p.md"
    doc.write_text("1. Formas de Pagamento\nAceitamos somente PIX com 3% de desconto.\n", encoding="utf-8")
    other = EmporioAgent.from_paths(agent.settings.data_dir, doc)
    text = other.ask("Quais as formas de pagamento?").text
    assert "somente PIX com 3% de desconto" in text
    assert "Boleto" not in text


# --- original manual (skipped when not available) ---------------------------------------------
@pytest.mark.parametrize("query,section,page", [
    ("Me arrependi da minha compra, posso devolver meu pedido?", "4.1", 4),
    ("Qual é o horário de funcionamento aos sábados?", "2", 2),
    ("frete para outra cidade", "5.2", 4),
    ("boquilha de saxofone pode trocar?", "4.4", 4),
    ("meu violão veio com defeito", "4.2", 4),
])
def test_original_manual_retrieval(original_agent, query, section, page):
    hits = original_agent.policies.search(query)
    assert hits[0]["section"] == section
    assert page in hits[0]["pages"]


def test_original_manual_conflicts_are_reported(original_agent):
    conflicts = " ".join(original_agent.policies.store_info.conflicts)
    assert "(67) 3321-4500" in conflicts and "(67) 3341-4444" in conflicts
    assert original_agent.policies.store_info.emails == ["contato@emporiodamusica.com.br"]
    assert original_agent.policies.missing_rule_anchors == []
