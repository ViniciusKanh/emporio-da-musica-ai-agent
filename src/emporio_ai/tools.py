"""Read-only tools shared by the offline engine and the Gemini function-calling loop.

Every tool:
* validates and normalises its arguments;
* only reads the in-memory snapshot (no tool can write data or trigger actions);
* returns a JSON-serialisable dict that includes the ``sources`` it used;
* is recorded in ``ToolBox.trace`` so the UI/tests can show which tools ran.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .business_rules import BusinessRules, installment_plan, metro_shipping_fee, pix_price, round_money
from .catalog import CatalogService
from .guardrails import redact, sanitize_untrusted
from .orders import OrderService
from .policies import PolicyKnowledgeBase
from .text_utils import normalize

logger = logging.getLogger(__name__)

MAX_RESULTS = 10


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    ok: bool
    duration_ms: float
    sources: list[str] = field(default_factory=list)


class ToolError(ValueError):
    """Invalid tool arguments (reported back to the model, never raised to the user)."""


def _as_float(value: Any, name: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ToolError(f"{name} must be a number") from exc
    if number < 0 or number > 1_000_000:
        raise ToolError(f"{name} out of range")
    return number


class ToolBox:
    def __init__(self, catalog: CatalogService, orders: OrderService, policies: PolicyKnowledgeBase,
                 rules: BusinessRules | None = None):
        self.catalog, self.orders, self.policies = catalog, orders, policies
        self.rules = rules or catalog.rules
        self.trace: list[ToolCall] = []
        self.email_resolver: Callable[[str], str] = lambda value: value
        self.clock: Callable[[], Any] | None = None
        self.lang = "pt"  # language of the labels in tool results ("en" when the agent always answers in English)
        self.registry: dict[str, Callable[..., dict]] = {
            "search_products": self.search_products,
            "get_product_details": self.get_product_details,
            "list_promotions": self.list_promotions,
            "calculate_payment_options": self.calculate_payment_options,
            "estimate_shipping": self.estimate_shipping,
            "search_store_policies": self.search_store_policies,
            "get_store_info": self.get_store_info,
            "get_order_status": self.get_order_status,
        }

    # ---------------------------------------------------------- dispatch
    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict:
        arguments = dict(arguments or {})
        start = time.perf_counter()
        func = self.registry.get(name)
        if func is None:
            result = {"error": f"unknown tool '{name}'", "sources": []}
        else:
            try:
                result = func(**arguments)
            except ToolError as exc:
                result = {"error": str(exc), "sources": []}
            except TypeError as exc:  # unexpected/missing argument names from the model
                result = {"error": f"invalid arguments: {exc}", "sources": []}
        # Data fields (descriptions, order notes...) are untrusted: strip instruction-like text (indirect injection).
        result = sanitize_untrusted(result)
        safe_args = {k: (redact(str(v)) if isinstance(v, str) else v) for k, v in arguments.items()}
        self.trace.append(ToolCall(name, safe_args, "error" not in result,
                                   round((time.perf_counter() - start) * 1000, 2), result.get("sources", [])))
        logger.info("tool=%s ok=%s args=%s", name, "error" not in result, safe_args)
        return result

    def reset_trace(self) -> None:
        self.trace = []

    # ------------------------------------------------------------- tools
    def _resolve_categories(self, category: str | None) -> list[int]:
        if not category:
            return []
        ids = self.catalog.detect_categories(category)
        if not ids:
            norm = normalize(category)
            ids = [int(cid) for cid, name in self.catalog.categories["category"].items() if norm in normalize(name)]
        if not ids:
            raise ToolError(f"unknown category '{category}'. Valid: " + ", ".join(self.catalog.categories["category"]))
        return ids

    def search_products(self, category: str | None = None, max_price: float | None = None,
                        min_price: float | None = None, keywords: str | None = None, brand: str | None = None,
                        include_unavailable: bool = False, sort: str = "price_asc", limit: int = 8) -> dict:
        """Search the catalog. Prices are compared with the current final price (after any active promotion)."""
        cat_ids = self._resolve_categories(category)
        hi, lo = _as_float(max_price, "max_price"), _as_float(min_price, "min_price")
        if hi is not None and lo is not None and lo > hi:
            lo, hi = hi, lo
        kws = [k for k in normalize(keywords or "").replace(",", " ").split() if k] if keywords else []
        limit = max(1, min(int(limit or 8), MAX_RESULTS))
        frame = self.catalog.search(cat_ids, hi, lo, kws, brand, bool(include_unavailable), limit,
                                    "price_desc" if sort == "price_desc" else "price_asc")
        total = len(self.catalog.search(cat_ids, hi, lo, kws, brand, bool(include_unavailable), 10_000))
        unavailable_matches = 0
        if not include_unavailable:
            unavailable_matches = len(self.catalog.search(cat_ids, hi, lo, kws, brand, True, 10_000)) - total
        return {
            "filters": {"categories": [self.catalog.category_name(c) for c in cat_ids], "max_price": hi,
                        "min_price": lo, "keywords": kws, "brand": brand},
            "total_matches": total,
            "unavailable_matches_hidden": unavailable_matches,
            "products": [self.catalog.product_view(int(pid), self.lang) for pid in frame["product_id"]],
            "sources": ["products.csv", "categories.csv", "promotions.csv"],
        }

    def get_product_details(self, product: str) -> dict:
        """Resolve a product by name/model (e.g. 'Takamine GD20') and return price, stock and promotion."""
        if not product or not str(product).strip():
            raise ToolError("product is required")
        if str(product).strip().isdigit() and int(product) in self.catalog.products.index:
            ids, exact = [int(product)], True
        else:
            match = self.catalog.match_products(str(product))
            ids, exact = list(match.product_ids), match.exact
        if not ids:
            return {"found": False, "query": product,
                    "message": "No product with that name/model in the catalog snapshot.",
                    "sources": ["products.csv"]}
        products = []
        for pid in ids[:MAX_RESULTS]:
            view = self.catalog.product_view(pid, self.lang)
            if view["availability"] != "available":
                view["alternatives"] = [self.catalog.product_view(a, self.lang) for a in self.catalog.alternatives(pid)]
            products.append(view)
        return {"found": True, "exact_match": exact, "products": products,
                "sources": ["products.csv", "promotions.csv", "categories.csv"]}

    def list_promotions(self, product: str | None = None, promotion_name: str | None = None) -> dict:
        """Active promotions, or the promotion history of a product / campaign name."""
        rows = []
        if product:
            match = self.catalog.match_products(product)
            for pid in match.product_ids[:MAX_RESULTS]:
                view = self.catalog.product_view(pid, self.lang)
                history = self.catalog.promotions_for_product(pid)
                rows.append({"product": view["name"], "availability": view["availability"],
                             "list_price": view["list_price"], "final_price": view["final_price"],
                             "active_promotion": view.get("promotion"),
                             "inactive_promotions": [
                                 {"name": r.description, "discount_percent": float(r.discount_percent)}
                                 for r in history.itertuples() if not r.is_active]})
            if not rows:
                return {"found": False, "message": "Product not found", "sources": ["products.csv"]}
            return {"found": True, "products": rows, "sources": ["promotions.csv", "products.csv"],
                    "note": "Inactive promotions are not valid now. The dataset has no start/end dates, "
                            "so expired and future campaigns cannot be distinguished."}
        if promotion_name:
            history = self.catalog.promotions_by_name(promotion_name)
            if history.empty:
                return {"found": False, "message": f"No campaign named '{promotion_name}' in promotions.csv",
                        "sources": ["promotions.csv"]}
            for r in history.itertuples():
                view = self.catalog.product_view(int(r.product_id), self.lang)
                rows.append({"campaign": r.description, "product": view["name"], "active": bool(r.is_active),
                             "discount_percent": float(r.discount_percent), "availability": view["availability"],
                             "current_final_price": view["final_price"], "list_price": view["list_price"]})
            return {"found": True, "promotions": rows, "sources": ["promotions.csv", "products.csv"]}
        for _, row in self.catalog.active_promotions().iterrows():
            view = self.catalog.product_view(int(row["product_id"]), self.lang)
            rows.append({"product": view["name"], "campaign": view["promotion"]["name"],
                         "discount_percent": view["promotion"]["discount_percent"], "list_price": view["list_price"],
                         "promotional_price": view["final_price"], "availability": view["availability"],
                         "stock_quantity": view["stock_quantity"]})
        return {"active_promotions": rows, "count": len(rows), "sources": ["promotions.csv", "products.csv"],
                "rules": "Promotions are not cumulative; the 5% PIX discount does not apply to promotional prices."}

    def calculate_payment_options(self, product: str | None = None, amount: float | None = None) -> dict:
        """PIX price and interest-free installments for a product or a purchase amount."""
        base_amount = _as_float(amount, "amount")
        sources = [self.policies.get_section("3.1").citation() if self.policies.get_section("3.1") else "policy §3.1"]
        if product:
            match = self.catalog.match_products(product)
            if not match.product_ids:
                return {"found": False, "message": "Product not found", "sources": ["products.csv"]}
            if not match.exact:
                return {"found": True, "exact_match": False,
                        "candidates": [self.catalog.products.at[p, "name"] for p in match.product_ids[:MAX_RESULTS]],
                        "message": "Ambiguous product, ask the customer which one", "sources": ["products.csv"]}
            view = self.catalog.product_view(match.product_ids[0], self.lang)
            promo = "promotion" in view
            total = view["final_price"]
            result = {"product": view["name"], "availability": view["availability"], "list_price": view["list_price"],
                      "final_price": total, "has_promotion": promo}
            sources = ["products.csv", "promotions.csv"] + sources
        elif base_amount is not None:
            promo, total = False, round_money(base_amount)
            result = {"amount": total}
        else:
            raise ToolError("provide a product or an amount")
        plan = installment_plan(total, self.rules)
        result.update({
            "pix": {"price": pix_price(total, promo, self.rules),
                    "discount_percent": 0.0 if promo else self.rules.pix_discount_percent,
                    "note": "PIX discount not applied: promotions are not cumulative" if promo else
                            f"{self.rules.pix_discount_percent:g}% off the list price"},
            "credit_card": plan,
            "debit_card": {"price": total}, "boleto": {"price": total, "clearing": "até 3 dias úteis"},
            "combined_payment_allowed": total > self.rules.combined_payment_min_total,
        })
        result["sources"] = sources + ([self.policies.get_section("6.2").citation()] if promo and
                                       self.policies.get_section("6.2") else [])
        return result

    def estimate_shipping(self, order_total: float, city: str | None = None) -> dict:
        """Shipping rule for the Campo Grande metro area; other cities depend on CEP/weight (quoted at checkout)."""
        total = _as_float(order_total, "order_total")
        if total is None:
            raise ToolError("order_total is required")
        metro = bool(city) and "campo grande" in normalize(city)
        section_51 = self.policies.find_section("Região Metropolitana")
        section_52 = self.policies.find_section("Outras Cidades")
        if metro:
            fee = metro_shipping_fee(total, self.rules)
            return {"city": city, "region": "metropolitan", "fee": fee, "free_shipping": fee == 0,
                    "delivery_time": self.rules.metro_delivery_days,
                    "sources": [section_51.citation() if section_51 else "policy §5.1"]}
        return {"city": city, "region": "other" if city else "unknown", "fee": None,
                "message": "Freight outside the Campo Grande metro area is calculated from destination CEP, weight "
                           "and dimensions at checkout; it cannot be quoted from the available data.",
                "carriers": "PAC (5-12 dias úteis), SEDEX (2-5 dias úteis), Jadlog (3-8 dias úteis)",
                "sources": [section_52.citation() if section_52 else "policy §5.2"]}

    def search_store_policies(self, query: str, top_k: int = 3) -> dict:
        """Retrieve the most relevant sections of the store policy manual (with page numbers)."""
        if not query or not str(query).strip():
            raise ToolError("query is required")
        hits = self.policies.search(str(query), top_k=max(1, min(int(top_k or 3), 5)))
        return {"query": query, "results": [
            {"citation": h["citation"], "title": h["title"], "text": h["text"], "score": h["score"]} for h in hits],
            "found": bool(hits), "sources": [h["citation"] for h in hits]}

    def get_store_info(self) -> dict:
        """Address, phone/WhatsApp, e-mail and opening hours extracted from the policy manual."""
        from .store_hours import describe, open_status

        info = self.policies.store_info
        status = open_status(info.hours, self.clock() if self.clock else None)
        return {"address": info.address, "phones": [{"number": n, "type": t} for n, t in info.phones],
                "open_now": None if status is None else {"is_open": status.is_open, "summary_pt": describe(status, "pt"),
                                                         "summary_en": describe(status, "en"),
                                                         "note": "public holidays are not in the data"},
                "emails": info.emails, "opening_hours": [{"days": d, "hours": h} for d, h in info.hours],
                "conflicts_in_manual": info.conflicts,
                "sources": [c.citation() for c in (self.policies.find_section("Dados da Empresa"),
                                                   self.policies.find_section("Horário de Funcionamento"),
                                                   self.policies.find_section("Atendimento via WhatsApp")) if c]}

    def get_order_status(self, order_id: int, email: str) -> dict:
        """Order status for an order number + the e-mail registered on that order (both required)."""
        resolved = self.email_resolver(str(email or ""))
        lookup = self.orders.lookup(order_id, resolved, self.lang)
        if not lookup.verified:
            return {"verified": False, "reason": lookup.reason,
                    "message": "Order number and e-mail do not match our records, or input is invalid. "
                               "Do not reveal whether the order exists.", "sources": []}
        return {"verified": True, "order": lookup.order, "sources": ["orders.csv", "order_items.csv"]}
