"""Acceptance scenarios from the challenge statement, run against the ORIGINAL files.

Skipped automatically when data/ does not contain the (unpublished) challenge files.
"""

import re


def test_acoustic_guitars_up_to_1000(original_agent):
    response = original_agent.ask("Quais opções de violões disponíveis custando até R$1000?")
    assert "Tagima Memphis AC-39" in response.text
    assert "Giannini GF-3D" not in response.text  # zero stock
    assert "Shelby SN-7C" not in response.text  # discontinued
    prices = [float(p.replace(".", "").replace(",", ".")) for p in re.findall(r"— R\$ ([\d.]+,\d{2})", response.text)]
    assert prices and max(prices) <= 1000


def test_store_address(original_agent):
    assert "Rua 14 de Maio, 3200" in original_agent.ask("Qual o endereço da loja?").text


def test_takamine_price(original_agent):
    text = original_agent.ask("Quanto custa o Takamine GD20?").text
    assert "R$ 2.199,00" in text and "R$ 2.089,05" in text and "12x" in text


def test_return_policy(original_agent):
    response = original_agent.ask("Me arrependi da minha compra, posso devolver meu pedido?")
    assert "7 dias corridos" in response.text and "§4.1" in " ".join(response.sources)


def test_taylor_promotion_not_stacked_with_pix(original_agent):
    text = original_agent.ask("Quanto custa o Taylor 110e?").text
    assert "R$ 5.519,08" in text and "8% off" in text and "não se aplica" in text


def test_shelby_discontinued_and_kala_coming_soon(original_agent):
    assert "Descontinuado" in original_agent.ask("Quanto custa o Shelby SN-7C?").text
    assert "Em breve" in original_agent.ask("Tem o Kala KA-ABP-CTG?").text


def test_discontinued_bass_with_stock_is_not_sold(original_agent):
    text = original_agent.ask("Quanto custa o Ibanez Bass 2X?").text
    assert "Descontinuado" in text and "No PIX" not in text


def test_order_8_verification(original_agent):
    customers = original_agent.store.table("customers")
    email = customers.loc[customers.customer_id == 2, "email"].iloc[0]
    response = original_agent.ask(f"pedido 8 {email}")
    assert "BRJL5544332BR" in response.text and email not in response.text
