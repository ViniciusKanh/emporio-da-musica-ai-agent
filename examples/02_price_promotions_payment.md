# Prices, promotions and non-cumulative discounts

> **Scenario.** Exact model lookup, PIX discount, an active promotion that must not be stacked with PIX, a question about an expired campaign that must not be honoured, the list of active promotions, a misspelled model name and a product whose catalog description names a different brand (flagged instead of hidden).
>
> Generated on 2026-10-31 by `scripts/generate_examples.py` · agent mode: **offline** (no Gemini key was configured) · data: original challenge snapshot. Assistant messages are the verbatim output of `EmporioAgent.ask`. English notes in *italics* are author translations, not agent output. Customer e-mails are redacted in the transcript.

### Turn 1

**Customer:** Quanto custa o Takamine GD20?

*EN: How much is the Takamine GD20?*

**Empório AI:**

> **Takamine GD20 Dreadnought Natural** (Violões)
> - ✅ Disponível (5 em estoque)
> - Preço: **R$ 2.199,00**
> - No PIX: R$ 2.089,05 (5% de desconto)
> - No cartão: até 12x de R$ 183,25 sem juros
> - Especificações: tampo Cedro Sólido, fundo/laterais Sapele, braço Mogno, cordas de aço, escala 650mm, sem captação, cor Natural
>
> Posso ajudar em mais alguma coisa?

<sub>mode: `offline` · intents: `price, product_info` · tools: `get_product_details` · sources: products.csv, promotions.csv, categories.csv</sub>

### Turn 2

**Customer:** e no pix?

*EN: And paying with PIX?*

**Empório AI:**

> Formas de pagamento para o **Takamine GD20 Dreadnought Natural** (R$ 2.199,00):
> - PIX: R$ 2.089,05 (5% off)
> - Cartão de crédito: até 12x de R$ 183,25 sem juros
> - Débito ou boleto: à vista
>
> Posso ajudar em mais alguma coisa?

<sub>mode: `offline` · intents: `payment` · tools: `calculate_payment_options` · sources: products.csv, promotions.csv, Manual de Políticas §3.1 (p. 3)</sub>

### Turn 3

**Customer:** Quanto custa o Taylor 110e?

*EN: How much is the Taylor 110e?*

**Empório AI:**

> **Taylor 110e Dreadnought Natural** (Violões)
> - ✅ Disponível (4 em estoque)
> - De ~~R$ 5.999,00~~ por **R$ 5.519,08** (8% off — Desconto Exclusivo)
> - O desconto do PIX não se aplica sobre preço promocional (promoções não são cumulativas).
> - No cartão: até 12x de R$ 459,92 sem juros
> - Especificações: tampo Sitka Spruce Sólido, fundo/laterais Walnut, braço Tropical Mahogany, cordas de aço, escala 648mm, com captação (eletroacústico), cor Natural
>
> Posso ajudar em mais alguma coisa?

<sub>mode: `offline` · intents: `price, product_info` · tools: `get_product_details` · sources: products.csv, promotions.csv, categories.csv</sub>

### Turn 4

**Customer:** A promoção de Black Friday do Martin D-28 ainda vale?

*EN: Is the Black Friday deal on the Martin D-28 still valid?*

**Empório AI:**

> **Martin D-28 Dreadnought Natural** (Violões)
> - ✅ Disponível (2 em estoque)
> - Preço: **R$ 11.499,00**
> - No PIX: R$ 10.924,05 (5% de desconto)
> - No cartão: até 12x de R$ 958,25 sem juros
> - Especificações: tampo Sitka Spruce Sólido, fundo/laterais East Indian Rosewood, braço Select Hardwood, cordas de aço, escala 645mm, sem captação, cor Natural
>
> Não há registro de promoção **Black Friday** para o **Martin D-28 Dreadnought Natural**. As promoções registradas para o **Martin D-28 Dreadnought Natural** (Promoção de Páscoa (15%), Promoção de Páscoa (10%)) **não estão vigentes**. O preço atual é R$ 11.499,00 — não consigo garantir desconto que não esteja ativo.
>
> Posso ajudar em mais alguma coisa?

<sub>mode: `offline` · intents: `promotions, product_info` · tools: `get_product_details, list_promotions` · sources: products.csv, promotions.csv, categories.csv</sub>

### Turn 5

**Customer:** Quais promoções estão ativas?

*EN: Which promotions are active?*

**Empório AI:**

> Promoções ativas agora:
> - **Ohana CK-20 Concert Natural** — de R$ 549,00 por **R$ 439,20** (20% off, Desconto Exclusivo)
> - **Crafter HT-100 Folk Aço Natural** — de R$ 2.399,00 por **R$ 1.967,18** (18% off, Desconto Exclusivo)
> - **Kalani KAL-700T Tenor Natural** — de R$ 359,00 por **R$ 323,10** (10% off, Oferta Especial)
> - **Taylor 110e Dreadnought Natural** — de R$ 5.999,00 por **R$ 5.519,08** (8% off, Desconto Exclusivo)
>
> Promoções não são cumulativas: o desconto de 5% no PIX não vale sobre esses preços.
>
> Posso ajudar em mais alguma coisa?

<sub>mode: `offline` · intents: `promotions` · tools: `list_promotions` · sources: promotions.csv, products.csv, Manual de Políticas §6.2 (p. 6)</sub>

### Turn 6

**Customer:** quanto custa o takamini gd 20?

*EN: How much is the 'takamini gd 20'? (typo on purpose)*

**Empório AI:**

> _Entendi **Takamine** ('takamini')._
>
> **Takamine GD20 Dreadnought Natural** (Violões)
> - ✅ Disponível (5 em estoque)
> - Preço: **R$ 2.199,00**
> - No PIX: R$ 2.089,05 (5% de desconto)
> - No cartão: até 12x de R$ 183,25 sem juros
> - Especificações: tampo Cedro Sólido, fundo/laterais Sapele, braço Mogno, cordas de aço, escala 650mm, sem captação, cor Natural
>
> Posso ajudar em mais alguma coisa?

<sub>mode: `offline` · intents: `price, product_info` · tools: `get_product_details` · sources: products.csv, promotions.csv, categories.csv</sub>

### Turn 7

**Customer:** Quanto custa o Korg Synth 1?

*EN: How much is the Korg Synth 1? (its catalog record is inconsistent)*

**Empório AI:**

> **Teclado Sintetizador Korg Synth 1 Pro** (Teclados e Pianos)
> - ✅ Disponível (2 em estoque)
> - Preço: **R$ 1.127,00**
> - No PIX: R$ 1.070,65 (5% de desconto)
> - No cartão: até 11x de R$ 102,45 sem juros
> - Especificações: 61 teclas, polifonia 128, cor Black
> - ⚠️ Atenção: no cadastro, a descrição deste item cita a marca Roland, diferente do nome do produto. Vale confirmar o modelo com a equipe antes de fechar a compra.
>
> Posso ajudar em mais alguma coisa?

<sub>mode: `offline` · intents: `price, product_info` · tools: `get_product_details` · sources: products.csv, promotions.csv, categories.csv</sub>
