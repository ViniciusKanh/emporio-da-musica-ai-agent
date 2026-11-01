# Returns, exchanges and warranty (policy RAG with citations)

> **Scenario.** Retrieval of the policy manual with section/page citations, a non-eligible item, and an ambiguous warranty case (45 days: store exchange window expired, legal warranty may still apply) that is escalated instead of denied.
>
> Generated on 2026-11-01 by `scripts/generate_examples.py` · agent mode: **offline** (no LLM configured) · data: original challenge snapshot · store clock fixed at Tue 10:30 for reproducibility. Assistant messages are the verbatim output of `EmporioAgent.ask` (always in English). English notes in *italics* translate the customer's Portuguese messages; they are author translations, not agent output. Customer e-mails are redacted in the transcript.

### Turn 1

**Customer:** Me arrependi da minha compra, posso devolver meu pedido?

*EN: I regret my purchase, can I return my order?*

**Empório AI:**

> If you bought **online**, the right of withdrawal applies: you have up to **7 calendar days after receiving** the product to return it, no justification needed. It must be in the original packaging, unused, with all accessories and manuals. Return shipping is paid by the store and the refund goes back to the original payment method within 10 business days.
>
> _Source: Policy Manual §4.1 (p. 4)._
>
> To start a return, our team confirms the order details. Please contact our team on WhatsApp (67) 3321-4500 (Monday to Friday 09:00 to 18:00; Saturday 09:00 to 13:00).
>
> Is there anything else I can help with?

<sub>mode: `offline` · intents: `returns` · tools: `search_store_policies` · sources: Policy Manual §4.1 (p. 4) · **human handoff**</sub>

### Turn 2

**Customer:** Posso trocar uma boquilha de saxofone?

*EN: Can I exchange a saxophone mouthpiece?*

**Empório AI:**

> Please note: according to the manual some items **are not eligible for exchange**: customised instruments or instruments with made-to-order adjustments (setup), clearance items marked "final sale", and **wind-instrument mouthpieces**, for hygiene reasons.
>
> Policy excerpt (Portuguese) — Policy Manual §4.4 (p. 4):
> > Instrumentos com personalização ou ajustes sob encomenda (setup, regulagem especial).
> > Produtos adquiridos em promoções de liquidação com aviso explícito de "venda final".
> > Boquilhas de instrumentos de sopro, por questões de higiene.
>
> To start a return, our team confirms the order details. Please contact our team on WhatsApp (67) 3321-4500 (Monday to Friday 09:00 to 18:00; Saturday 09:00 to 13:00).
>
> Is there anything else I can help with?

<sub>mode: `offline` · intents: `returns` · tools: `search_store_policies` · sources: Policy Manual §4.4 (p. 4) · **human handoff**</sub>

### Turn 3

**Customer:** Meu violão deu defeito depois de 45 dias, ainda tenho garantia?

*EN: My guitar developed a defect after 45 days, am I still under warranty?*

**Empório AI:**

> Sorry about that! Our policies provide: (1) **exchange for a manufacturing defect** at the store within **30 calendar days of purchase**; (2) a **90-day legal warranty** (Brazilian Consumer Code) against manufacturing defects, counted from **receipt**; (3) after that, the **manufacturer's warranty** (usually 6 months to 2 years, per the product certificate), and the store can mediate the process.
>
> At 45 days the store's direct-exchange window (30 days) has passed, but the product may still be under the 90-day legal warranty counted from receipt. Because the two rules overlap, our team needs to evaluate this case — I won't deny your rights here.
>
> _Source: Policy Manual §4.2 (p. 4)._
>
> Warranty does not cover natural wear (strings, frets, felts), misuse, drops, extreme climate, third-party repairs or purely cosmetic damage.
>
> To have your case evaluated: Please contact our team on WhatsApp (67) 3321-4500 (Monday to Friday 09:00 to 18:00; Saturday 09:00 to 13:00).
>
> Is there anything else I can help with?

<sub>mode: `offline` · intents: `warranty` · tools: `search_store_policies` · sources: Policy Manual §4.2 (p. 4), Policy Manual §8.1 (p. 7), Policy Manual §8.2 (p. 7), Policy Manual §8.3 (p. 7) · **human handoff**</sub>

### Turn 4

**Customer:** Vocês abrem domingo? E aceitam boleto?

*EN: Are you open on Sunday? And do you accept boleto?*

**Empório AI:**

> We accept:
> - **PIX** (instant transfer) with 5% off the list price (not on promotional prices)
> - **Debit card**
> - **Credit card** up to 12 interest-free installments — up to 3x (minimum R$ 50.00 per installment), 4x-6x (minimum R$ 80.00), 7x-12x (minimum R$ 100.00)
> - **Boleto** (bank slip, clears in up to 3 business days)
> - Combining payment methods (e.g. PIX + card) is allowed for purchases above R$ 2.000,00.
>
> Our opening hours:
> - Monday to Friday: 09:00 to 18:00
> - Saturday: 09:00 to 13:00
> - Sundays and holidays: Closed
>
> Right now (Tuesday 10:30 in Campo Grande) the store is **open** until 18:00. Public holidays are not in our system.
>
> WhatsApp support follows the same hours. On special dates (Black Friday, store anniversary, Christmas) hours may be extended.
>
> Is there anything else I can help with?

<sub>mode: `offline` · intents: `payment, hours` · tools: `search_store_policies, get_store_info` · sources: Policy Manual §3 (p. 3), Policy Manual §3.1 (p. 3), Policy Manual §2 (p. 2)</sub>
