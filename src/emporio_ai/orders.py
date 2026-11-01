"""Order-status lookup with a minimal ownership check (order number + e-mail).

IMPORTANT: matching an order id with the e-mail on file is a *prototype* safeguard
against casual enumeration. It is NOT production-grade authentication (no OTP,
no session binding to an authenticated account).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .data_store import DataStore

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")

STATUS_LABELS = {
    "pt": {"pending": "pendente (aguardando processamento/pagamento)", "confirmed": "confirmado (em preparação)",
           "shipped": "enviado", "delivered": "entregue", "cancelled": "cancelado"},
    "en": {"pending": "pending (awaiting processing/payment)", "confirmed": "confirmed (being prepared)",
           "shipped": "shipped", "delivered": "delivered", "cancelled": "cancelled"},
}
PAYMENT_LABELS = {
    "pix": "PIX", "debit": "cartão de débito", "boleto": "boleto bancário",
}


PAYMENT_LABELS_EN = {"pix": "PIX", "debit": "debit card", "boleto": "boleto (bank slip)", "credit": "credit card"}


def payment_label(code: str, lang: str = "pt") -> str:
    code = str(code).strip().lower()
    labels = PAYMENT_LABELS_EN if lang == "en" else PAYMENT_LABELS
    if code in labels:
        return labels[code]
    match = re.fullmatch(r"credit_(\d+)x", code)
    if match:
        return f"credit card in {match.group(1)} installments" if lang == "en" else f"cartão de crédito em {match.group(1)}x"
    return code or ("not informed" if lang == "en" else "não informado")


@dataclass(frozen=True)
class OrderLookup:
    verified: bool
    reason: str  # ok | invalid_input | not_verified
    order: dict | None = None


class OrderService:
    def __init__(self, store: DataStore):
        self._orders = store.table("orders").set_index("order_id", drop=False)
        self._items = store.table("order_items")
        products = store.table("products")
        self._names = dict(zip(products["product_id"], products["name"]))
        customers = store.table("customers")
        self._email_by_customer = dict(zip(customers["customer_id"], customers["email_norm"]))
        names = customers["name"] if "name" in customers.columns else [""] * len(customers)
        self._first_name = {cid: (str(n).split() or [""])[0] for cid, n in zip(customers["customer_id"], names)}

    @staticmethod
    def valid_email(email: str) -> bool:
        return bool(email) and EMAIL_RE.fullmatch(email.strip()) is not None

    def lookup(self, order_id: int | str, email: str, lang: str = "pt") -> OrderLookup:
        try:
            oid = int(str(order_id).strip().lstrip("#"))
        except (TypeError, ValueError):
            return OrderLookup(False, "invalid_input")
        if oid <= 0 or not self.valid_email(email or ""):
            return OrderLookup(False, "invalid_input")
        # Same answer whether the order does not exist or the e-mail does not match,
        # so the tool cannot be used to discover which order numbers exist.
        if oid not in self._orders.index:
            return OrderLookup(False, "not_verified")
        row = self._orders.loc[oid]
        expected = self._email_by_customer.get(int(row["customer_id"]))
        if not expected or expected != email.strip().lower():
            return OrderLookup(False, "not_verified")
        return OrderLookup(True, "ok", self._view(row, lang))

    def first_name(self, order_id: int | str | None, email: str | None) -> str | None:
        """First name of the verified customer (used only to greet them, never sent to an LLM)."""
        if order_id is None or not email or not self.lookup(order_id, email).verified:
            return None
        customer = int(self._orders.loc[int(str(order_id).lstrip("#")), "customer_id"])
        return self._first_name.get(customer) or None

    def _view(self, row, lang: str) -> dict:
        items = self._items[self._items["order_id"] == row["order_id"]]
        status = str(row["status"])
        view = {
            "order_id": int(row["order_id"]),
            "order_date": str(row["order_date"]),
            "status": status,
            "status_label": STATUS_LABELS.get(lang, STATUS_LABELS["pt"]).get(status, status),
            "total_brl": float(row["total_brl"]),
            "payment_method": payment_label(row["payment_method"], lang),
            "items": [{"product": self._names.get(int(r.product_id), (f"product #{int(r.product_id)}" if lang == "en" else f"produto #{int(r.product_id)}")),
                       "quantity": int(r.quantity)} for r in items.itertuples()],
            "tracking_code": str(row.get("tracking_code", "") or "") or None,
            "estimated_delivery": str(row.get("estimated_delivery", "") or "") or None,
            "notes": str(row.get("notes", "") or "") or None,
            "data_source": "orders.csv snapshot - not a live carrier/ERP feed",
        }
        return view
