"""Smarter conversation features: typo tolerance, specs, comparison, recommendation, opening status."""

from datetime import datetime

import pytest

from emporio_ai.store_hours import STORE_TZ, open_status


def test_typo_in_brand_is_corrected_and_disclosed(agent):
    response = agent.ask("Quanto custa o Aurorra C10?")
    assert "R$ 480,00" in response.text
    assert "I understood" in response.text and "Aurora" in response.text


def test_words_of_the_router_are_never_corrected(agent):
    route = agent.router.route("violões abaixo de 500")
    assert route.corrections == [] and route.max_price == 500 and route.category_ids == [5]


def test_product_card_shows_specs(agent):
    text = agent.ask("Quanto custa o Vento VD-90E?").text
    assert "Specs" in text and "steel strings" in text and "with pickup" in text


def test_side_by_side_comparison(agent):
    response = agent.ask("Compare o Aurora C10 e o Vento VD-50")
    assert "compare" in response.intents
    assert "| **Aurora C10 Nylon Natural** | **Vento VD-50 Dreadnought Natural** |" in response.text
    assert "R$ 480,00" in response.text and "R$ 1.035,00" in response.text
    assert "Most affordable in stock: **Aurora C10" in response.text


def test_comparison_flags_unavailable_items(agent):
    text = agent.ask("qual a diferença entre o Sabiá S7 e o Aurora F20?").text
    assert "Not available right now: Sabiá S7" in text


def test_two_products_without_compare_word_get_two_cards(agent):
    response = agent.ask("quanto custa o Aurora C10 e o Ipê UK-10?")
    assert "R$ 480,00" in response.text and "R$ 199,90" in response.text
    assert "compare" not in response.intents


def test_compare_follow_up_after_listing(agent):
    agent.ask("quanto custa o Aurora C10 e o Aurora F20?", session_id="c")
    assert agent.ask("e no pix?", session_id="c").text


def test_recommendation_for_beginner_respects_budget_and_stock(agent):
    response = agent.ask("Qual violão você recomenda para iniciante até 1000?")
    assert "recommend" in response.intents
    assert "Aurora C10" in response.text  # description says "para iniciantes"
    assert "Sabiá S7" not in response.text and "Vento VD-90E" not in response.text  # out of stock / over budget
    assert "Why:" in response.text


def test_recommendation_without_category_asks_which_instrument(agent):
    response = agent.ask("O que você recomenda para um iniciante?")
    assert "Which instrument" in response.text


def test_stage_recommendation_prefers_pickup(agent):
    text = agent.ask("Qual violão você indica para tocar ao vivo?").text
    first = text.split("1.")[1].split("\n")[0]
    assert "Vento VD-90E" in first


@pytest.mark.parametrize("when,expected", [
    (datetime(2026, 11, 2, 11, 0, tzinfo=STORE_TZ), True),   # Monday 11:00
    (datetime(2026, 11, 7, 15, 0, tzinfo=STORE_TZ), False),  # Saturday 15:00 (sample closes 14:00)
    (datetime(2026, 11, 8, 10, 0, tzinfo=STORE_TZ), False),  # Sunday
])
def test_open_status(agent, when, expected):
    status = open_status(agent.policies.store_info.hours, when)
    assert status.is_open is expected


def test_hours_answer_includes_open_now(agent):
    agent_engine_clock = datetime(2026, 11, 7, 15, 0, tzinfo=STORE_TZ)
    from emporio_ai.offline_engine import OfflineEngine

    engine = OfflineEngine(agent._toolbox(), agent.router)
    engine.clock = lambda: agent_engine_clock
    text = engine.respond("Vocês estão abertos agora?", agent.session("h")).text
    assert "closed" in text and "opens again Monday at 10:00" in text


def test_description_brand_conflict_is_flagged(original_agent):
    text = original_agent.ask("Quanto custa o Korg Synth 1?").text
    assert "mentions Roland" in text
    assert any("different brands" in issue for issue in original_agent.store.report.issues)


def test_original_beginner_recommendation(original_agent):
    text = original_agent.ask("Qual violão você recomenda para iniciante até 800?").text
    assert "Yamaha C40" in text or "Tagima Memphis" in text or "Rozini RC-104" in text
