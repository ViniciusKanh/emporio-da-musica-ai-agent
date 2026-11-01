# Prices, promotions and non-cumulative discounts

> **Scenario.** Exact model lookup, PIX discount, an active promotion that must not be stacked with PIX, a question about an expired campaign that must not be honoured, the list of active promotions, a misspelled model name and a product whose catalog description names a different brand (flagged instead of hidden).
>
> Generated on 2026-11-01 by `scripts/generate_examples.py` · agent mode: **offline** (no LLM configured) · data: original challenge snapshot · store clock fixed at Tue 10:30 for reproducibility. Assistant messages are the verbatim output of `EmporioAgent.ask` (always in English). English notes in *italics* translate the customer's Portuguese messages; they are author translations, not agent output. Customer e-mails are redacted in the transcript.

### Turn 1

**Customer:** Quanto custa o Takamine GD20?

*EN: How much is the Takamine GD20?*

**Empório AI:**

> **Takamine GD20 Dreadnought Natural** (acoustic guitars)
> - ✅ In stock (5 units)
> - Price: **R$ 2.199,00**
> - With PIX: R$ 2.089,05 (5% off)
> - Credit card: up to 12 interest-free installments of R$ 183,25
> - Specs: top: Solid Cedar, back/sides: Sapele, neck: Mahogany, steel strings, scale 650mm, no pickup, color: Natural
>
> Is there anything else I can help with?

<sub>mode: `offline` · intents: `price, product_info` · tools: `get_product_details` · sources: products.csv, promotions.csv, categories.csv</sub>

### Turn 2

**Customer:** e no pix?

*EN: And paying with PIX?*

**Empório AI:**

> Payment options for **Takamine GD20 Dreadnought Natural** (R$ 2.199,00):
> - PIX: R$ 2.089,05 (5% off)
> - Credit card: up to 12x R$ 183,25 interest-free
> - Debit card or boleto: full price, paid at once
>
> Is there anything else I can help with?

<sub>mode: `offline` · intents: `payment` · tools: `calculate_payment_options` · sources: products.csv, promotions.csv, Policy Manual §3.1 (p. 3)</sub>

### Turn 3

**Customer:** Quanto custa o Taylor 110e?

*EN: How much is the Taylor 110e?*

**Empório AI:**

> **Taylor 110e Dreadnought Natural** (acoustic guitars)
> - ✅ In stock (4 units)
> - Was ~~R$ 5.999,00~~, now **R$ 5.519,08** (8% off — Desconto Exclusivo)
> - The PIX discount does not apply to promotional prices (discounts are not cumulative).
> - Credit card: up to 12 interest-free installments of R$ 459,92
> - Specs: top: Solid Sitka Spruce, back/sides: Walnut, neck: Tropical Mahogany, steel strings, scale 648mm, with pickup (acoustic-electric), color: Natural
>
> Is there anything else I can help with?

<sub>mode: `offline` · intents: `price, product_info` · tools: `get_product_details` · sources: products.csv, promotions.csv, categories.csv</sub>

### Turn 4

**Customer:** A promoção de Black Friday do Martin D-28 ainda vale?

*EN: Is the Black Friday deal on the Martin D-28 still valid?*

**Empório AI:**

> **Martin D-28 Dreadnought Natural** (acoustic guitars)
> - ✅ In stock (2 units)
> - Price: **R$ 11.499,00**
> - With PIX: R$ 10.924,05 (5% off)
> - Credit card: up to 12 interest-free installments of R$ 958,25
> - Specs: top: Solid Sitka Spruce, back/sides: East Indian Rosewood, neck: Select Hardwood, steel strings, scale 645mm, no pickup, color: Natural
>
> There is no **Black Friday** promotion recorded for **Martin D-28 Dreadnought Natural**. The promotions recorded for **Martin D-28 Dreadnought Natural** (Promoção de Páscoa (15%), Promoção de Páscoa (10%)) **are not valid now**. The current price is R$ 11.499,00 — I can't promise an inactive discount.
>
> Is there anything else I can help with?

<sub>mode: `offline` · intents: `promotions, product_info` · tools: `get_product_details, list_promotions` · sources: products.csv, promotions.csv, categories.csv</sub>

### Turn 5

**Customer:** Quais promoções estão ativas?

*EN: Which promotions are active?*

**Empório AI:**

> Active promotions right now:
> - **Ohana CK-20 Concert Natural** — was R$ 549,00 now **R$ 439,20** (20% off, Desconto Exclusivo)
> - **Crafter HT-100 Folk Aço Natural** — was R$ 2.399,00 now **R$ 1.967,18** (18% off, Desconto Exclusivo)
> - **Kalani KAL-700T Tenor Natural** — was R$ 359,00 now **R$ 323,10** (10% off, Oferta Especial)
> - **Taylor 110e Dreadnought Natural** — was R$ 5.999,00 now **R$ 5.519,08** (8% off, Desconto Exclusivo)
>
> Promotions are not cumulative: the 5% PIX discount does not apply on top of these prices.
>
> Is there anything else I can help with?

<sub>mode: `offline` · intents: `promotions` · tools: `list_promotions` · sources: promotions.csv, products.csv, Policy Manual §6.2 (p. 6)</sub>

### Turn 6

**Customer:** quanto custa o takamini gd 20?

*EN: How much is the 'takamini gd 20'? (typo on purpose)*

**Empório AI:**

> _I understood **Takamine** ('takamini')._
>
> **Takamine GD20 Dreadnought Natural** (acoustic guitars)
> - ✅ In stock (5 units)
> - Price: **R$ 2.199,00**
> - With PIX: R$ 2.089,05 (5% off)
> - Credit card: up to 12 interest-free installments of R$ 183,25
> - Specs: top: Solid Cedar, back/sides: Sapele, neck: Mahogany, steel strings, scale 650mm, no pickup, color: Natural
>
> Is there anything else I can help with?

<sub>mode: `offline` · intents: `price, product_info` · tools: `get_product_details` · sources: products.csv, promotions.csv, categories.csv</sub>

### Turn 7

**Customer:** Quanto custa o Korg Synth 1?

*EN: How much is the Korg Synth 1? (its catalog record is inconsistent)*

**Empório AI:**

> **Teclado Sintetizador Korg Synth 1 Pro** (keyboards and pianos)
> - ✅ In stock (2 units)
> - Price: **R$ 1.127,00**
> - With PIX: R$ 1.070,65 (5% off)
> - Credit card: up to 11 interest-free installments of R$ 102,45
> - Specs: 61 keys, polyphony 128, color: Black
> - ⚠️ Note: the catalog description of this item mentions Roland, a different brand from the product name. Please confirm the exact model with our team before buying.
>
> Is there anything else I can help with?

<sub>mode: `offline` · intents: `price, product_info` · tools: `get_product_details` · sources: products.csv, promotions.csv, categories.csv</sub>
