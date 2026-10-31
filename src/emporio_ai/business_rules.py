"""Commercial rules from the store policy manual, expressed as explicit, testable code.

Values are configured here (not left to an LLM) and are cross-checked against the
policy document at start-up by ``PolicyKnowledgeBase.verify_rules`` so that a
policy change that is not reflected in code is reported instead of silently ignored.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class InstallmentTier:
    min_installments: int
    max_installments: int
    min_installment_value: float


@dataclass(frozen=True)
class BusinessRules:
    pix_discount_percent: float = 5.0
    max_installments: int = 12
    # Section 3.1 ("Regras de Parcelamento") is more specific than the summary table
    # in section 3 (which only says "parcela minima R$ 100,00"); the specific rule wins
    # and the discrepancy is documented as a known policy conflict.
    installment_tiers: tuple[InstallmentTier, ...] = field(default_factory=lambda: (
        InstallmentTier(1, 3, 50.0),
        InstallmentTier(4, 6, 80.0),
        InstallmentTier(7, 12, 100.0),
    ))
    combined_payment_min_total: float = 2000.0
    metro_free_shipping_above: float = 500.0
    metro_flat_shipping_fee: float = 35.0
    metro_delivery_days: str = "1 a 3 dias úteis"
    regret_days: int = 7
    defect_exchange_days: int = 30
    preference_exchange_days: int = 7
    legal_warranty_days: int = 90
    refund_business_days: int = 10
    complaint_reply_hours: int = 24

    # Text anchors that must appear in the policy document for the rules above.
    def anchors(self) -> dict[str, str]:
        return {
            "pix_discount_percent": f"{self.pix_discount_percent:g}% de desconto",
            "max_installments": f"{self.max_installments}x",
            "metro_free_shipping_above": "500,00",
            "metro_flat_shipping_fee": "35,00",
            "regret_days": f"{self.regret_days} (sete) dias",
            "defect_exchange_days": f"{self.defect_exchange_days} (trinta) dias",
            "legal_warranty_days": f"{self.legal_warranty_days} (noventa) dias",
            "combined_payment_min_total": "2.000,00",
        }


def round_money(value: float) -> float:
    return float(round(value + 1e-9, 2))


def pix_price(list_price: float, has_promotion: bool, rules: BusinessRules) -> float | None:
    """PIX discount applies to the list price only; it is never stacked on a promotion."""
    if has_promotion:
        return None
    return round_money(list_price * (1 - rules.pix_discount_percent / 100))


def max_interest_free_installments(total: float, rules: BusinessRules) -> int:
    """Largest number of interest-free installments allowed for a purchase total."""
    best = 1
    for tier in rules.installment_tiers:
        for n in range(tier.min_installments, tier.max_installments + 1):
            if n <= rules.max_installments and total / n >= tier.min_installment_value - 1e-9:
                best = max(best, n)
    return best


def installment_plan(total: float, rules: BusinessRules) -> dict[str, float | int]:
    n = max_interest_free_installments(total, rules)
    # Floor to cents so the customer is never quoted more than the total.
    value = math.floor(total / n * 100) / 100
    return {"installments": n, "installment_value": round_money(value), "total": round_money(total)}


def metro_shipping_fee(order_total: float, rules: BusinessRules) -> float:
    """Shipping inside the Campo Grande metropolitan area ('acima de R$ 500' = strictly above)."""
    return 0.0 if order_total > rules.metro_free_shipping_above else rules.metro_flat_shipping_fee
