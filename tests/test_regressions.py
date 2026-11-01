"""Regression tests for the eight failures reported in the previous implementation."""

import re

import pytest


# 1. Accessory questions must not return instruments as if they were the requested product.
@pytest.mark.parametrize("question", [
    "Vocês vendem palhetas?", "Tem cabo para guitarra?", "Do you sell guitar strings?",
    "Quero cordas para violão", "Vocês têm amplificador?", "Tem case para ukulele?",
])
def test_accessories_are_redirected_not_matched_to_instruments(agent, question):
    response = agent.ask(question)
    assert "accessory" in response.intents
    assert "product_search" not in response.intents and "product_info" not in response.intents
    assert not re.search(r"R\$ ?\d", response.text)  # no instrument prices offered as the accessory


def test_instrument_name_containing_accessory_word_is_not_an_accessory(original_agent):
    response = original_agent.ask("Quanto custa o Tagima Dallas Tuner?")  # "tuner" is part of the model name
    assert "accessory" not in response.intents
    assert "R$ 749,00" in response.text


def test_instrument_and_accessory_in_same_message(agent):
    response = agent.ask("Quero um violão até 700 e palhetas")
    assert {"product_search", "accessory"} <= set(response.intents)
    assert "Aurora C10" in response.text and "accessories" in response.text


# 2. Questions with two intents must get complete answers.
def test_two_intents_price_and_hours(agent):
    response = agent.ask("Quanto custa o Aurora C10 e qual o horário de sábado?")
    assert "R$ 480,00" in response.text
    assert "Saturday: 10:00 to 14:00" in response.text


def test_two_intents_returns_and_payment(agent):
    response = agent.ask("Posso devolver um produto comprado online? E vocês parcelam no cartão?")
    assert "7 calendar days" in response.text and "12x" in response.text
    assert {"returns", "payment"} <= set(response.intents)


# 3. English questions must identify products and operations.
def test_english_product_price_and_stock(agent):
    response = agent.ask("How much is the Vento VD-50 and is it in stock?")
    assert "product_info" in response.intents
    assert "In stock" in response.text and "R$ 1.035,00" in response.text


def test_english_category_budget_search(agent):
    response = agent.ask("Do you have acoustic guitars under 1000?")
    assert "product_search" in response.intents
    assert "Aurora C10" in response.text and "Vento VD-50" not in response.text  # 1035 > 1000


def test_english_order_and_policy(agent):
    response = agent.ask("Can I return an item I bought online?")
    assert "returns" in response.intents and "7 calendar days" in response.text


# 4. Out-of-stock products must never be announced as available.
def test_out_of_stock_not_listed_and_flagged(agent):
    listing = agent.ask("Quais violões vocês têm?")
    assert "Sabiá S7" not in listing.text.split("Note:")[0]
    single = agent.ask("Tem o Sabiá S7?")
    assert "out of stock" in single.text and "✅" not in single.text.split("Similar options")[0]


@pytest.mark.parametrize("model,label", [("Sabiá SX-1", "Discontinued"), ("Aurora C30", "Coming soon")])
def test_discontinued_and_coming_soon_are_not_sellable(agent, model, label):
    response = agent.ask(f"Quanto custa o {model}?")
    assert label in response.text
    assert "No PIX" not in response.text and "No cartão" not in response.text


# 5. Promotions must not be applied outside their validity.
def test_inactive_promotion_is_reported_as_not_valid(agent):
    response = agent.ask("A promoção de Black Friday do Ipê UK-30 ainda vale?")
    assert "are not valid now" in response.text
    assert "R$ 336,00" not in response.text  # 420 - 20% would be the invalid price
    assert "R$ 420,00" in response.text


def test_active_promotion_shows_original_price_and_percentage(agent):
    response = agent.ask("Quanto custa o Vento VD-50?")
    assert "~~R$ 1.150,00~~" in response.text and "R$ 1.035,00" in response.text and "10% off" in response.text


# 6. Non-cumulative discounts must respect the policy.
def test_pix_not_stacked_on_promotional_price(agent):
    response = agent.ask("Quanto fica o Vento VD-90E no pix?")
    assert "R$ 2.010,68" not in response.text  # 2116.50 * 0.95 would be stacking
    assert "not" in response.text.lower() and "cumulative" in response.text


def test_pix_discount_on_regular_price(agent):
    response = agent.ask("Quanto fica o Aurora C10 no pix?")
    assert "R$ 456,00" in response.text  # 480 * 0.95


# 7. Order lookups must not reveal data without the expected verification.
def test_order_requires_email(agent):
    response = agent.ask("Qual o status do pedido 1002?")
    assert "e-mail" in response.text and "BRSY0000002BR" not in response.text


def test_order_with_wrong_email_reveals_nothing(agent):
    wrong = agent.ask("pedido 1002 cliente.um@example.com", session_id="a")
    unknown = agent.ask("pedido 999999 cliente.um@example.com", session_id="b")
    assert "BRSY" not in wrong.text and "Ipê" not in wrong.text
    assert wrong.text == unknown.text  # same message: existence of an order is not disclosed


def test_order_with_correct_email(agent):
    response = agent.ask("pedido 1002, meu e-mail é cliente.dois@example.com")
    assert "BRSY0000002BR" in response.text and "shipped" in response.text
    assert "cliente.dois@example.com" not in response.text
    assert "Cliente Sintético" not in response.text  # no personal data echoed


# 8. Unverified information must not be invented.
def test_unknown_product_is_not_invented(agent):
    response = agent.ask("Quanto custa o Stradivarius Messiah?")
    assert "I couldn't find" in response.text and not re.search(r"R\$ ?\d", response.text)


def test_empty_category_is_reported_honestly(agent):
    response = agent.ask("Vocês têm flauta?")
    assert "currently have no" in response.text.lower()


def test_freight_outside_metro_is_not_invented(agent):
    response = agent.ask("Qual o frete para Dourados?")
    assert "CEP" in response.text and "Dourados" in response.text


def test_live_tracking_is_not_claimed(agent):
    response = agent.ask("pedido 1002 cliente.dois@example.com")
    assert "real-time tracking" in response.text
