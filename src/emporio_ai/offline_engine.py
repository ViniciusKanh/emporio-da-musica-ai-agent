"""Deterministic response engine (no LLM).

It is both the fallback when Gemini is unavailable and the reference behaviour
used by the regression tests. Every factual sentence is built from a tool result
(CSV snapshot or retrieved policy section) and the sources are returned with it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from .business_rules import BusinessRules
from .conversation import ConversationState
from .guardrails import inspect_message
from .router import SPEC_KEYWORD, IntentRouter, Route
from .text_utils import brl, normalize
from .tools import ToolBox

MAX_MESSAGE_CHARS = 2000
MAX_ORDER_ATTEMPTS = 3
CATEGORY_EN = {"Guitarras": "electric guitars", "Baixos": "basses", "Baterias e Percussão": "drums and percussion",
               "Teclados e Pianos": "keyboards and pianos", "Violões": "acoustic guitars",
               "Instrumentos de Sopro (Madeiras)": "woodwind instruments",
               "Instrumentos de Sopro (Metais)": "brass instruments", "Cordas Orquestrais": "orchestral strings",
               "Ukuleles": "ukuleles"}
PROFILES = {
    "beginner": (r"(iniciante|comecando|comecar|aprender|primeiro|beginner|first|learn)",
                 r"(inician|estudante|comec|estudo|aprend|acessivel)"),
    "gift": (r"(presente|gift|filho|filha|crianca|sobrinh)", r"(inician|estudante|comec|leve|confort|viajant)"),
    "stage": (r"(palco|ao vivo|show|apresenta|tocar na igreja|banda|live|stage|gig)",
              r"(palco|ao vivo|apresenta|amplific|performance|show)"),
    "professional": (r"(profissional|avancad|exigente|professional|advanced|melhor qualidade|top)",
                     r"(profission|premium|alta qualidade|lendari|referencia|exigente|impecavel|excepcional|solido)"),
    "intermediate": (r"(intermediari|intermediate|evoluir|upgrade)", r"(intermedi|avancad|custo-benef|solido)"),
}
PROFILE_LABEL = {
    "pt": {"beginner": "para quem está começando", "gift": "para presentear", "stage": "para tocar ao vivo",
           "professional": "para uso profissional", "intermediate": "para quem quer evoluir", None: "com bom equilíbrio"},
    "en": {"beginner": "for beginners", "gift": "as a gift", "stage": "for playing live",
           "professional": "for professional use", "intermediate": "for players who want to step up",
           None: "with a good balance"},
}
KNOWN_CITIES = ("Campo Grande", "Dourados", "Três Lagoas", "Corumbá", "Ponta Porã", "São Paulo",
                "Rio de Janeiro", "Cuiabá", "Brasília", "Curitiba", "Goiânia")


@dataclass
class EngineResult:
    text: str
    intents: list[str]
    sources: list[str] = field(default_factory=list)
    handoff: bool = False
    handoff_summary: str | None = None


def _t(lang: str, pt: str, en: str) -> str:
    return en if lang == "en" else pt


class OfflineEngine:
    def __init__(self, tools: ToolBox, router: IntentRouter, rules: BusinessRules | None = None,
                 assistant_name: str = "Vinicius"):
        self.tools, self.router = tools, router
        self.assistant_name = assistant_name
        self.rules = rules or tools.rules
        self.kb = tools.policies
        from .store_hours import STORE_TZ

        self.clock: Callable[[], datetime] = lambda: datetime.now(STORE_TZ)

    # ================================================================ entry
    def respond(self, message: str, state: ConversationState) -> EngineResult:
        text = (message or "").strip()
        if not text:
            return EngineResult(_t(state.lang, "Pode me contar como posso ajudar? 🙂",
                                   "How can I help you today?"), ["empty"])
        if len(text) > MAX_MESSAGE_CHARS:
            text = text[:MAX_MESSAGE_CHARS]
        guard = inspect_message(text)
        route = self.router.route(text, default_lang=state.lang)
        state.lang = route.lang
        lang = route.lang
        self._resolve_context(route, state)

        sections: list[str] = []
        sources: list[str] = []
        handoff = False
        intents = list(route.intents)

        if guard.injection:
            intents.insert(0, "prompt_injection")
            sections.append(_t(lang,
                "Não posso ignorar ou alterar minhas regras de atendimento, nem revelar instruções internas. "
                "Sigo respondendo só com base nos dados e nas políticas da loja.",
                "I can't ignore or change my service rules or reveal internal instructions. "
                "I only answer based on the store's data and policies."))
        if guard.bulk_data_request:
            intents.insert(0, "data_exfiltration")
            sections.append(_t(lang,
                "Por privacidade (LGPD), não compartilho dados de clientes nem listas de pedidos. "
                "Consigo consultar um pedido específico quando você informa o número do pedido e o e-mail cadastrado nele.",
                "For privacy reasons (Brazilian LGPD), I can't share customer data or lists of orders. "
                "I can check one specific order if you give me the order number and the e-mail registered on it."))
            route.intents = [i for i in route.intents if i != "order_status"]
        if guard.action_request:
            intents.append("unauthorized_action")
            handoff = True
            sections.append(_t(lang,
                "Eu não consigo cancelar ou alterar pedidos, aplicar descontos extras ou fazer reembolsos — "
                "essas ações precisam da nossa equipe. " + self._human_contact_line(lang),
                "I can't cancel or change orders, apply extra discounts or issue refunds — "
                "our team needs to handle that. " + self._human_contact_line(lang)))

        handled = False
        builders = [
            ("order_status", self._order_section), ("compare", self._compare_section),
            ("recommend", self._recommend_section), ("product_info", self._product_section),
            ("product_search", self._search_section), ("promotions", self._promotion_section),
            ("payment", self._payment_section), ("returns", self._returns_section),
            ("warranty", self._warranty_section), ("shipping", self._shipping_section),
            ("hours", self._hours_section), ("contact", self._contact_section),
            ("catalog_overview", self._catalog_overview_section), ("about", self._about_section),
            ("privacy", self._privacy_section), ("accessory", self._accessory_section),
            ("human", self._human_section),
        ]
        skip = self._skip_rules(route)
        if guard.action_request:
            skip.add("promotions")
        for intent, builder in builders:
            if intent in route.intents and intent not in skip:
                out = builder(route, state)
                if out:
                    body, srcs, needs_human = out
                    sections.append(body)
                    sources.extend(srcs)
                    handoff = handoff or needs_human
                    handled = True

        if not handled and not guard.flagged and not guard.action_request:
            if route.has("price", "availability"):
                sections.append(_t(lang,
                    "Não encontrei esse produto no nosso catálogo. Pode me dizer a marca e o modelo? "
                    "Também posso buscar por categoria (ex.: violões, guitarras, ukuleles) e faixa de preço.",
                    "I couldn't find that product in our catalog. Could you tell me the brand and model? "
                    "I can also search by category (e.g. acoustic guitars, ukuleles) and price range."))
                intents.append("product_not_found")
                handled = True
            elif route.has("greeting"):
                sections.append(self._greeting(lang))
                handled = True
            elif route.has("thanks"):
                sections.append(_t(lang, "Imagina! Foi um prazer ajudar. Boa música! 🎶",
                                   "You're welcome! Happy playing! 🎶"))
                return EngineResult("\n\n".join(sections), intents or ["thanks"], [])
            else:
                fallback = self._policy_fallback(route)
                if fallback:
                    body, srcs = fallback
                    sections.append(body)
                    sources.extend(srcs)
                    intents.append("policy_lookup")
                else:
                    sections.append(self._out_of_scope(lang))
                    intents.append("out_of_scope")
        elif route.has("greeting") and handled:
            sections.insert(0, _t(lang, "Oi! Tudo bem? 🎸", "Hi there! 🎸"))
        if route.corrections and handled:
            fixed = ", ".join(f"**{good.title()}** ({typed!r})" for typed, good in route.corrections)
            sections.insert(0, _t(lang, f"_Entendi {fixed}._", f"_I understood {fixed}._"))

        self._dedupe_contact(sections, lang)
        if handled or guard.flagged or guard.action_request:
            sections.append(_t(lang, "Posso ajudar em mais alguma coisa?", "Is there anything else I can help with?"))
        self._remember(route, state)
        final_intents = list(dict.fromkeys(intents)) or ["unknown"]
        summary = self._handoff_summary(route, state, final_intents) if handoff else None
        return EngineResult("\n\n".join(s for s in sections if s), final_intents,
                            list(dict.fromkeys(sources)), handoff, summary)

    def _handoff_summary(self, route: Route, state: ConversationState, intents: list[str]) -> str:
        """Short case note for the human team (manual §7.3: register and forward). Shown to staff, not sent anywhere."""
        from .guardrails import redact

        products = ", ".join(self.tools.catalog.products.at[p, "name"] for p in (route.multi_ids or route.product_ids)[:3])
        order = state.verified_order_view or {}
        parts = [f"Motivo: {', '.join(i for i in intents if i not in {'price', 'availability'})}",
                 f"Idioma: {'inglês' if route.lang == 'en' else 'português'}"]
        if products:
            parts.append(f"Produto(s): {products}")
        if order:
            parts.append(f"Pedido verificado: #{order.get('order_id')} ({order.get('status_label')})")
        elif route.order_id:
            parts.append(f"Pedido citado (não verificado): #{route.order_id}")
        parts.append(f"Mensagem do cliente: \"{redact(route.text)[:240]}\"")
        parts.append(f"Prazo de retorno para reclamações: {self.rules.complaint_reply_hours} horas úteis (§7.3)")
        return "\n".join(parts)

    # ========================================================== context
    def _resolve_context(self, route: Route, state: ConversationState) -> None:
        product_dependent = {"payment", "promotions", "price", "availability", "warranty", "shipping"}
        if not route.product_ids and state.last_product_ids and not route.category_ids:
            short = len(route.norm.split()) <= 8
            general = re.search(r"\b(quais|quaisquer|todas|todos|lista|which|what are|all|list)\b", route.norm)
            if route.is_followup or (short and not general and route.has("price", "availability")):
                if route.has("price", "availability") or (route.is_followup and not route.has(
                        "payment", "promotions", "warranty", "shipping", "returns")):
                    route.product_ids = list(state.last_product_ids)
                    route.product_exact = len(route.product_ids) == 1
                    route.intents.append("product_info")
                elif route.has("payment", "promotions"):
                    route.product_ids = list(state.last_product_ids)
                    route.product_exact = len(route.product_ids) == 1
        # "e até 2000?" -> re-run the previous search with a new budget
        if (not route.intents or route.intents == ["product_search"]) and not route.category_ids \
                and (route.max_price is not None or route.min_price is not None) and state.last_search:
            route.category_ids = list(state.last_search.get("category_ids", []))
            route.spec_keywords = route.spec_keywords or list(state.last_search.get("spec_keywords", []))
            if "product_search" not in route.intents:
                route.intents.append("product_search")
        # Order verification may span several messages.
        if "order_status" in route.intents or (state.pending_order_id and route.email) \
                or (state.pending_email and route.order_id):
            route.order_id = route.order_id or state.pending_order_id
            route.email = route.email or state.pending_email
            if "order_status" not in route.intents:
                route.intents.append("order_status")
        elif state.verified_order and re.search(r"\b(pedido|order|chega|entrega|rastreio|tracking)\b", route.norm) \
                and not route.has("returns", "warranty"):
            route.intents.append("order_status")
        route.intents = list(dict.fromkeys(route.intents))

    def _remember(self, route: Route, state: ConversationState) -> None:
        if route.multi_ids:
            state.last_product_ids = list(route.multi_ids[:6])
        elif route.product_ids and "product_info" in route.intents:
            state.last_product_ids = list(route.product_ids[:6])
        if "product_search" in route.intents:
            state.last_search = {"category_ids": route.category_ids, "spec_keywords": route.spec_keywords}

    @staticmethod
    def _skip_rules(route: Route) -> set[str]:
        skip: set[str] = set()
        if route.product_ids and route.product_by_brand and route.category_ids:
            skip.add("product_info")  # "violão Yamaha" -> filtered search instead of all Yamaha products
        elif route.product_ids and "product_info" in route.intents:
            skip.add("product_search")
        return skip

    # ========================================================= products
    def _price_lines(self, view: dict, lang: str) -> list[str]:
        lines = []
        promo = view.get("promotion")
        if promo:
            lines.append(_t(lang,
                f"De ~~{brl(view['list_price'])}~~ por **{brl(view['final_price'])}** "
                f"({promo['discount_percent']:g}% off — {promo['name']})",
                f"Was ~~{brl(view['list_price'])}~~, now **{brl(view['final_price'])}** "
                f"({promo['discount_percent']:g}% off — {promo['name']})"))
        else:
            lines.append(_t(lang, f"Preço: **{brl(view['list_price'])}**", f"Price: **{brl(view['list_price'])}**"))
        if view["availability"] == "available":
            if view.get("pix_price") is not None:
                lines.append(_t(lang, f"No PIX: {brl(view['pix_price'])} ({self.rules.pix_discount_percent:g}% de desconto)",
                                f"With PIX: {brl(view['pix_price'])} ({self.rules.pix_discount_percent:g}% off)"))
            elif promo:
                lines.append(_t(lang, "O desconto do PIX não se aplica sobre preço promocional (promoções não são cumulativas).",
                                "The PIX discount does not apply to promotional prices (discounts are not cumulative)."))
            card = view["card"]
            if card["installments"] > 1:
                lines.append(_t(lang, f"No cartão: até {card['installments']}x de {brl(card['installment_value'])} sem juros",
                                f"Credit card: up to {card['installments']} interest-free installments of "
                                f"{brl(card['installment_value'])}"))
        return lines

    def _product_card(self, view: dict, lang: str) -> str:
        avail = view["availability"]
        status = {
            "available": _t(lang, f"✅ Disponível ({view['stock_quantity']} em estoque)",
                            f"✅ In stock ({view['stock_quantity']} units)"),
            "out_of_stock": _t(lang, "⚠️ Temporariamente sem estoque", "⚠️ Temporarily out of stock"),
            "discontinued": _t(lang, "⛔ Descontinuado — não faz mais parte do catálogo",
                               "⛔ Discontinued — no longer part of our catalog"),
            "coming_soon": _t(lang, "🕒 Em breve — ainda não está disponível para venda",
                              "🕒 Coming soon — not yet available for purchase"),
        }.get(avail, _t(lang, "Indisponível", "Unavailable"))
        category = CATEGORY_EN.get(view["category"], view["category"]) if lang == "en" else view["category"]
        lines = [f"**{view['name']}** ({category})", status]
        if avail in {"available", "out_of_stock"}:
            lines += self._price_lines(view, lang)
        specs = self.tools.catalog.spec_summary(view.get("specs") or {}, lang)
        if specs:
            lines.append(_t(lang, f"Especificações: {specs}", f"Specs: {specs}"))
        other = self.tools.catalog.description_conflict(view["product_id"])
        if other:
            lines.append(_t(lang, f"⚠️ Atenção: no cadastro, a descrição deste item cita a marca {other}, diferente do nome "
                                  "do produto. Vale confirmar o modelo com a equipe antes de fechar a compra.",
                            f"⚠️ Note: the catalog description of this item mentions {other}, a different brand from the "
                            "product name. Please confirm the exact model with our team before buying."))
        return "\n".join(f"- {line}" if i else line for i, line in enumerate(lines))

    def _short_line(self, view: dict, lang: str) -> str:
        price = brl(view["final_price"])
        if view.get("promotion"):
            price = _t(lang, f"{price} (de {brl(view['list_price'])}, {view['promotion']['discount_percent']:g}% off)",
                       f"{price} (was {brl(view['list_price'])}, {view['promotion']['discount_percent']:g}% off)")
        tag = "" if view["availability"] == "available" else f" — {view['availability_label']}"
        return f"- **{view['name']}** — {price}{tag}"

    def _product_section(self, route: Route, state: ConversationState):
        lang = route.lang
        if not route.product_ids:
            return None
        if route.multi_ids:
            cards, srcs = [], []
            for pid in route.multi_ids[:4]:
                res = self.tools.call("get_product_details", {"product": str(pid)})
                cards.append(self._product_card(self.tools.catalog.product_view(pid, lang), lang))
                srcs = res.get("sources", srcs)
            tip = _t(lang, "Quer que eu compare esses modelos lado a lado?", "Want me to compare them side by side?")
            return "\n\n".join(cards) + "\n\n" + tip, srcs, False
        result = self.tools.call("get_product_details", {"product": str(route.product_ids[0])}) \
            if route.product_exact else None
        if route.product_exact and result and result.get("found"):
            view = result["products"][0]
            if lang == "en":
                view = self.tools.catalog.product_view(view["product_id"], "en")
                view["alternatives"] = result["products"][0].get("alternatives", [])
            parts = [self._product_card(view, lang)]
            if view["availability"] != "available":
                alts = view.get("alternatives") or []
                if alts:
                    parts.append(_t(lang, "Opções parecidas disponíveis agora:", "Similar options in stock now:") + "\n"
                                 + "\n".join(self._short_line(a, lang) for a in alts))
                elif view["availability"] != "coming_soon":
                    parts.append(_t(lang, "No momento não encontrei alternativas disponíveis na mesma categoria.",
                                    "I couldn't find in-stock alternatives in the same category right now."))
            return "\n\n".join(parts), result["sources"], False
        views = [self.tools.catalog.product_view(pid, lang) for pid in route.product_ids[:8]]
        self.tools.call("get_product_details", {"product": route.text})
        header = _t(lang, "Não encontrei exatamente esse modelo, mas temos estes da marca:" if route.product_by_brand
                    else "Encontrei mais de um produto com esse nome. Qual deles você procura?",
                    "I couldn't find that exact model, but we have these from the brand:" if route.product_by_brand
                    else "I found more than one product with that name. Which one do you mean?")
        return header + "\n" + "\n".join(self._short_line(v, lang) for v in views), \
            ["products.csv", "promotions.csv"], False

    def _compare_section(self, route: Route, state: ConversationState):
        lang = route.lang
        ids = route.multi_ids[:4]
        if len(ids) < 2:
            return None
        views = []
        for pid in ids:
            self.tools.call("get_product_details", {"product": str(pid)})
            views.append(self.tools.catalog.product_view(pid, lang))
        yes, no = _t(lang, "sim", "yes"), _t(lang, "não", "no")

        def avail(v):
            return _t(lang, f"✅ {v['stock_quantity']} em estoque", f"✅ {v['stock_quantity']} in stock") \
                if v["availability"] == "available" else v["availability_label"]

        def price(v):
            return (f"**{brl(v['final_price'])}**" + (f" ({v['promotion']['discount_percent']:g}% off)"
                                                      if v.get("promotion") else ""))

        def pix(v):
            if v["availability"] != "available":
                return "—"
            return brl(v["pix_price"]) if v.get("pix_price") is not None else _t(lang, "sem desconto extra", "no extra discount")

        def card(v):
            c = v.get("card")
            return f"{c['installments']}x {brl(c['installment_value'])}" if c else "—"

        def spec(v, key, mapping=None):
            value = (v.get("specs") or {}).get(key, "")
            return (mapping or {}).get(value, value) or "—"

        rows = [
            (_t(lang, "Categoria", "Category"), lambda v: CATEGORY_EN.get(v["category"], v["category"]) if lang == "en"
             else v["category"]),
            (_t(lang, "Preço", "Price"), price), ("PIX", pix), (_t(lang, "Cartão (sem juros)", "Card (interest-free)"), card),
            (_t(lang, "Disponibilidade", "Availability"), avail),
            (_t(lang, "Tampo", "Top"), lambda v: spec(v, "top")),
            (_t(lang, "Fundo/laterais", "Back/sides"), lambda v: spec(v, "back_sides")),
            (_t(lang, "Cordas", "Strings"), lambda v: spec(v, "strings", {"nylon": "nylon", "steel": _t(lang, "aço", "steel")})),
            (_t(lang, "Captação", "Pickup"), lambda v: spec(v, "electronics", {"yes": yes, "no": no})),
            (_t(lang, "Escala", "Scale"), lambda v: spec(v, "scale")),
        ]
        header = "| | " + " | ".join(f"**{v['name']}**" for v in views) + " |"
        lines = [header, "|---|" + "---|" * len(views)]
        for label, fn in rows:
            cells = [str(fn(v)) for v in views]
            if all(c == "—" for c in cells):
                continue
            lines.append(f"| {label} | " + " | ".join(cells) + " |")
        available = [v for v in views if v["availability"] == "available"]
        notes = []
        if available:
            cheapest = min(available, key=lambda v: v["final_price"])
            notes.append(_t(lang, f"Mais em conta entre os disponíveis: **{cheapest['name']}** ({brl(cheapest['final_price'])}).",
                            f"Most affordable in stock: **{cheapest['name']}** ({brl(cheapest['final_price'])})."))
        electric = [v["name"] for v in views if (v.get("specs") or {}).get("electronics") == "yes"]
        if electric and len(electric) < len(views):
            notes.append(_t(lang, f"Com captação para ligar no amplificador: {', '.join(electric)}.",
                            f"With a pickup for amplification: {', '.join(electric)}."))
        unavailable = [v["name"] for v in views if v["availability"] != "available"]
        if unavailable:
            notes.append(_t(lang, f"Indisponível no momento: {', '.join(unavailable)}.",
                            f"Not available right now: {', '.join(unavailable)}."))
        notes.append(_t(lang, "A escolha depende do seu uso — se me contar o estilo que você toca e o orçamento, eu "
                              "indico a opção mais adequada.",
                        "The right pick depends on how you play — tell me your style and budget and I'll point you to the "
                        "best fit."))
        intro = _t(lang, "Comparando lado a lado (dados do nosso sistema):", "Side-by-side comparison (from our system):")
        return intro + "\n\n" + "\n".join(lines) + "\n\n" + " ".join(notes), \
            ["products.csv", "promotions.csv", "categories.csv"], False

    def _recommend_section(self, route: Route, state: ConversationState):
        lang = route.lang
        categories = route.category_ids or list(state.last_search.get("category_ids", []))
        if not categories:
            return _t(lang, "Claro, te ajudo a escolher! Qual instrumento você procura — violão, guitarra, baixo, teclado, "
                            "bateria ou ukulele? Se puder, me diga também o orçamento e para quem é (iniciante, presente, "
                            "palco, uso profissional).",
                      "Happy to help you choose! Which instrument are you looking for — acoustic guitar, electric guitar, "
                      "bass, keyboard, drums or ukulele? Your budget and who it is for (beginner, gift, stage, "
                      "professional) also help."), [], False
        profile = next((name for name, (ask, _) in PROFILES.items() if re.search(ask, route.norm)), None)
        keywords = " ".join(SPEC_KEYWORD[k] for k in route.spec_keywords if k in SPEC_KEYWORD) or None
        candidates: list[dict] = []
        for cid in categories:
            res = self.tools.call("search_products", {"category": self.tools.catalog.category_name(cid),
                                                      "max_price": route.max_price, "min_price": route.min_price,
                                                      "keywords": keywords, "limit": 10})
            candidates += res.get("products", [])
        if not candidates:
            return _t(lang, "Não encontrei opções disponíveis com esses critérios. Quer que eu amplie o orçamento ou "
                            "mostre outra categoria?",
                      "I couldn't find available options with those criteria. Shall I widen the budget or show another "
                      "category?"), ["products.csv"], False
        prices = sorted(v["final_price"] for v in candidates)

        def percentile(v):
            return prices.index(v["final_price"]) / max(1, len(prices) - 1)

        def score(v):
            desc = normalize(v["description"])
            if self.tools.catalog.description_conflict(v["product_id"]):
                desc = ""
            electric = (v.get("specs") or {}).get("electronics") == "yes"
            pct = percentile(v)
            s = 0.0
            if profile:
                s += 3.0 * bool(re.search(PROFILES[profile][1], desc))
            if profile in {"beginner", "gift"}:
                s += 2.0 * (1 - pct)
            elif profile == "professional":
                s += 2.0 * pct
            elif profile == "stage":
                s += 2.5 * electric
            else:  # intermediate or unspecified: favour the middle of the range and value-for-money mentions
                s += 1.5 * (1 - abs(pct - 0.5) * 2) + 1.0 * bool(re.search(r"custo-benef", desc))
            return s

        ranked = sorted(candidates, key=lambda v: (-score(v), v["final_price"]))[:3]
        state.last_product_ids = [v["product_id"] for v in ranked]
        cat_names = [self.tools.catalog.category_name(c) for c in categories]
        if lang == "en":
            cat_names = [CATEGORY_EN.get(n, n) for n in cat_names]
        intro = _t(lang, f"Separei {len(ranked)} opções de **{', '.join(cat_names).lower()}** "
                         f"{PROFILE_LABEL['pt'][profile]}{self._budget_label(route, lang)}, todas disponíveis agora:",
                   f"Here are {len(ranked)} **{', '.join(cat_names).lower()}** {PROFILE_LABEL['en'][profile]}"
                   f"{self._budget_label(route, lang)}, all in stock now:")
        items = []
        for i, v in enumerate(ranked, start=1):
            view = self.tools.catalog.product_view(v["product_id"], lang)
            reason = "" if self.tools.catalog.description_conflict(v["product_id"]) else \
                re.split(r"(?<=[.!?])\s", view["description"].strip())[-1]
            line = f"{i}. {self._short_line(view, lang)[2:]}"
            if reason:
                line += "\n   " + _t(lang, f"Por quê: _{reason}_", f"Why: _{reason}_ (catalog description)")
            items.append(line)
        outro = _t(lang, "A indicação usa a descrição cadastrada de cada produto, o preço e o estoque atuais. Quer que eu "
                         "compare duas delas lado a lado?",
                   "This suggestion uses each product's catalog description, current price and stock. Want me to "
                   "compare two of them side by side?")
        return intro + "\n\n" + "\n".join(items) + "\n\n" + outro, ["products.csv", "promotions.csv"], False

    def _search_section(self, route: Route, state: ConversationState):
        lang = route.lang
        categories = route.category_ids
        brand = None
        if route.product_by_brand and route.product_ids:
            brand = self.tools.catalog.products.at[route.product_ids[0], "brand"]
        # Subtype filters (nylon/steel/electro-acoustic/7-string...) only make sense for string instruments
        keywords = " ".join(SPEC_KEYWORD[k] for k in route.spec_keywords if k in SPEC_KEYWORD) or None
        if keywords and categories and not set(categories) & {2, 5, 9}:
            keywords = None
        results: list[dict] = []
        hidden = 0
        total = 0
        srcs: list[str] = []
        for cid in (categories or [None]):
            res = self.tools.call("search_products", {
                "category": self.tools.catalog.category_name(cid) if cid else None,
                "max_price": route.max_price, "min_price": route.min_price, "keywords": keywords,
                "brand": brand, "sort": route.sort, "limit": 10})
            if "error" in res:
                continue
            results += res["products"]
            hidden += res["unavailable_matches_hidden"]
            total += res["total_matches"]
            srcs = res["sources"]
        names = [self.tools.catalog.category_name(c) for c in categories]
        if lang == "en":
            names = [CATEGORY_EN.get(n, n) for n in names]
        cat_label = ", ".join(names) or _t(lang, "produtos", "products")
        budget = self._budget_label(route, lang)
        if not results:
            # Distinguish "category empty in the snapshot" from "nothing within budget".
            any_in_cat = any(not self.tools.catalog.search([c], include_unavailable=True).empty for c in categories) \
                if categories else True
            if categories and not any_in_cat:
                return _t(lang,
                          f"No momento não temos itens de **{cat_label}** cadastrados no nosso sistema. "
                          "Posso te mostrar outra categoria?",
                          f"We currently have no **{cat_label}** listed in our system. "
                          "Would you like to see another category?"), ["products.csv", "categories.csv"], False
            msg = _t(lang, f"Não encontrei {cat_label.lower()} disponíveis{budget}.",
                     f"I couldn't find available {cat_label.lower()}{budget}.")
            if route.max_price is not None and categories:
                cheapest = self.tools.catalog.search(categories, limit=1)
                if not cheapest.empty:
                    view = self.tools.catalog.product_view(int(cheapest.iloc[0]["product_id"]), lang)
                    msg += _t(lang, " A opção disponível mais em conta é:", " The most affordable option in stock is:")
                    msg += "\n" + self._short_line(view, lang)
            return msg, srcs or ["products.csv"], False
        results = sorted(results, key=lambda v: (v["final_price"], v["product_id"]),
                         reverse=route.sort == "price_desc")[:8]
        state.last_product_ids = [v["product_id"] for v in results]
        intro = _t(lang, f"Estas são as opções de **{cat_label.lower()}** disponíveis{budget}:",
                   f"Here are the **{cat_label.lower()}** in stock{budget}:")
        body = [intro, "\n".join(self._short_line(self.tools.catalog.product_view(v["product_id"], lang), lang)
                                 for v in results)]
        if total > len(results):
            body.append(_t(lang, f"(Mostrando {len(results)} de {total} opções. Quer filtrar por faixa de preço ou tipo?)",
                           f"(Showing {len(results)} of {total} options. Want to narrow it down by price or type?)"))
        if hidden:
            body.append(_t(lang, f"Obs.: {hidden} item(ns) que atenderiam à busca estão sem estoque ou fora de linha, "
                                 "por isso não aparecem na lista.",
                           f"Note: {hidden} matching item(s) are out of stock or discontinued, so they are not listed."))
        body.append(_t(lang, "Preços já consideram promoções vigentes. No PIX há 5% de desconto em itens fora de promoção.",
                       "Prices already include active promotions. PIX gives 5% off items that are not on promotion.")
                    if self.rules.pix_discount_percent == 5 else "")
        head, notes = "\n".join(body[:2]), [b for b in body[2:] if b]
        return head + ("\n\n" + " ".join(notes) if notes else ""), srcs, False

    @staticmethod
    def _budget_label(route: Route, lang: str) -> str:
        lo, hi = route.min_price, route.max_price
        if lo is not None and hi is not None:
            return _t(lang, f" entre {brl(lo)} e {brl(hi)}", f" between {brl(lo)} and {brl(hi)}")
        if hi is not None:
            return _t(lang, f" até {brl(hi)}", f" up to {brl(hi)}")
        if lo is not None:
            return _t(lang, f" a partir de {brl(lo)}", f" from {brl(lo)}")
        return ""

    def _catalog_overview_section(self, route: Route, state: ConversationState):
        lang = route.lang
        catalog = self.tools.catalog
        lines = []
        for cid, name in catalog.categories["category"].items():
            n = len(catalog.search([int(cid)]))
            lines.append(f"- {name}: " + (_t(lang, f"{n} produto(s) disponível(is)", f"{n} product(s) in stock") if n
                                         else _t(lang, "sem itens no momento", "none at the moment")))
        head = _t(lang, "Trabalhamos exclusivamente com instrumentos musicais. Hoje o sistema mostra:",
                  "We sell musical instruments only. Our system currently shows:")
        return head + "\n" + "\n".join(lines), ["products.csv", "categories.csv"], False

    # ======================================================= promotions
    def _campaign_calendar(self, name: str, body: str, lang: str, sources: list[str]) -> tuple[str, list[str]]:
        """Add what the manual (§6.1) says about when a campaign usually happens - without promising a discount."""
        section = self.kb.find_section("Tipos de Promoção")
        key = normalize(name)
        line = next((l.lstrip("- ").strip() for l in (section.text.splitlines() if section else [])
                     if key and key in normalize(l)), None)
        if not line:
            return body, sources
        body += "\n\n" + _t(lang, f"Pelo nosso manual ({section.citation()}): _{line}_ Quando a campanha estiver ativa "
                                   "no sistema, os preços promocionais aparecem aqui — antes disso não consigo garantir "
                                   "desconto.",
                             f"According to our manual ({section.citation()}, in Portuguese): _{line}_ Once the "
                             "campaign is active in our system, the promotional prices will show up here — until then "
                             "I can't promise a discount.")
        return body, sources + [section.citation()]

    def _promotion_section(self, route: Route, state: ConversationState):
        lang = route.lang
        if route.product_ids and route.product_exact:
            res = self.tools.call("list_promotions", {"product": str(self.tools.catalog.products.at[route.product_ids[0], "name"])})
            if not res.get("found"):
                return None
            row = res["products"][0]
            asked = self.tools.catalog.promotions_by_name(route.text)
            asked_names = sorted(set(asked["description"])) if not asked.empty else []
            if asked_names and not any(n in [p["name"] for p in row["inactive_promotions"]] +
                                       ([row["active_promotion"]["name"]] if row["active_promotion"] else [])
                                       for n in asked_names):
                prefix = _t(lang, f"Não há registro de promoção **{asked_names[0]}** para o **{row['product']}**. ",
                            f"There is no **{asked_names[0]}** promotion recorded for **{row['product']}**. ")
            else:
                prefix = ""
            if row["active_promotion"]:
                if "product_info" in route.intents:
                    return None  # already shown in the product card
                p = row["active_promotion"]
                return _t(lang, f"Sim! O **{row['product']}** está na promoção **{p['name']}**: de {brl(row['list_price'])} "
                                f"por **{brl(row['final_price'])}** ({p['discount_percent']:g}% off).",
                          f"Yes! **{row['product']}** is in the **{p['name']}** promotion: was {brl(row['list_price'])}, "
                          f"now **{brl(row['final_price'])}** ({p['discount_percent']:g}% off)."), res["sources"], False
            if row["inactive_promotions"]:
                old = ", ".join(f"{p['name']} ({p['discount_percent']:g}%)" for p in row["inactive_promotions"])
                return prefix + _t(lang, f"As promoções registradas para o **{row['product']}** ({old}) **não estão vigentes**. "
                                f"O preço atual é {brl(row['final_price'])} — não consigo garantir desconto que não esteja ativo.",
                          f"The promotions recorded for **{row['product']}** ({old}) **are not valid now**. "
                          f"The current price is {brl(row['final_price'])} — I can't promise an inactive discount."), \
                    res["sources"], False
            return prefix + _t(lang, f"O **{row['product']}** não tem promoção ativa no momento; o preço vigente é {brl(row['final_price'])}.",
                      f"**{row['product']}** has no active promotion right now; the current price is "
                      f"{brl(row['final_price'])}."), res["sources"], False
        campaign = self.tools.catalog.promotions_by_name(route.text)
        if not campaign.empty:
            res = self.tools.call("list_promotions", {"promotion_name": route.text})
            active = [r for r in res["promotions"] if r["active"]]
            name = res["promotions"][0]["campaign"]
            if not active:
                body = _t(lang, f"A campanha **{name}** não está ativa no momento, então os descontos dela não valem agora.",
                          f"The **{name}** campaign is not active right now, so its discounts don't apply.")
                body, srcs = self._campaign_calendar(name, body, lang, res["sources"])
                return body + " " + _t(lang, "Posso te mostrar as promoções que estão vigentes?",
                                       "Want to see the promotions that are currently valid?"), srcs, False
        res = self.tools.call("list_promotions", {})
        rows = res["active_promotions"]
        if not rows:
            return _t(lang, "No momento não há promoções ativas.", "There are no active promotions right now."), \
                res["sources"], False
        lines = []
        for r in rows:
            tag = "" if r["availability"] == "available" else _t(lang, " — sem estoque", " — out of stock")
            lines.append(f"- **{r['product']}** — {_t(lang, 'de', 'was')} {brl(r['list_price'])} "
                         f"{_t(lang, 'por', 'now')} **{brl(r['promotional_price'])}** "
                         f"({r['discount_percent']:g}% off, {r['campaign']}){tag}")
        note = _t(lang, "Promoções não são cumulativas: o desconto de 5% no PIX não vale sobre esses preços.",
                  "Promotions are not cumulative: the 5% PIX discount does not apply on top of these prices.")
        sec = self.kb.find_section("Regras de Promoções")
        srcs = res["sources"] + ([sec.citation()] if sec else [])
        return _t(lang, "Promoções ativas agora:", "Active promotions right now:") + "\n" + "\n".join(lines) + "\n\n" + note, \
            srcs, False

    # ========================================================== payment
    def _payment_section(self, route: Route, state: ConversationState):
        lang = route.lang
        requested = re.search(r"\b(\d{1,2})\s?x\b", route.norm)
        if route.product_ids and route.product_exact:
            name = self.tools.catalog.products.at[route.product_ids[0], "name"]
            res = self.tools.call("calculate_payment_options", {"product": name})
            if "product_info" in route.intents and not requested:
                return None  # payment lines already included in the product card
            if res.get("availability") != "available":
                return None
            card = res["credit_card"]
            lines = [_t(lang, f"Formas de pagamento para o **{res['product']}** ({brl(res['final_price'])}):",
                        f"Payment options for **{res['product']}** ({brl(res['final_price'])}):")]
            if res["pix"]["price"] is not None:
                lines.append(f"- PIX: {brl(res['pix']['price'])} ({res['pix']['discount_percent']:g}% off)")
            else:
                lines.append(_t(lang, "- PIX: sem desconto adicional (o item já está em promoção e os descontos não são cumulativos)",
                                "- PIX: no extra discount (the item is already on promotion and discounts are not cumulative)"))
            lines.append(_t(lang, f"- Cartão de crédito: até {card['installments']}x de {brl(card['installment_value'])} sem juros",
                            f"- Credit card: up to {card['installments']}x {brl(card['installment_value'])} interest-free"))
            if requested:
                n = int(requested.group(1))
                if n > card["installments"]:
                    lines.append(_t(lang, f"- Em {n}x não é possível: pelas regras de parcela mínima, o máximo para esse valor é "
                                          f"{card['installments']}x.",
                                    f"- {n} installments is not possible: given the minimum installment rules, the maximum "
                                    f"for this amount is {card['installments']}."))
            lines.append(_t(lang, "- Débito ou boleto: à vista", "- Debit card or boleto: full price, paid at once"))
            return "\n".join(lines), res["sources"], False
        amount = re.search(r"r\$\s*([\d.]+(?:,\d{1,2})?)", route.norm)
        if amount and requested:
            from .text_utils import parse_amount
            value = parse_amount(amount.group(1))
            if value:
                res = self.tools.call("calculate_payment_options", {"amount": value})
                card = res["credit_card"]
                return _t(lang, f"Para uma compra de {brl(value)}: até {card['installments']}x de "
                                f"{brl(card['installment_value'])} sem juros no cartão; no PIX sai {brl(res['pix']['price'])} "
                                "(5% sobre o preço de tabela, se o item não estiver em promoção).",
                          f"For a {brl(value)} purchase: up to {card['installments']}x {brl(card['installment_value'])} "
                          f"interest-free; with PIX it is {brl(res['pix']['price'])} (5% off list price, if the item is "
                          "not on promotion)."), res["sources"], False
        sec3, sec31 = self.kb.find_section("Formas de Pagamento"), self.kb.find_section("Parcelamento")
        self.tools.call("search_store_policies", {"query": route.text})
        r = self.rules
        body = _t(lang,
                  "Aceitamos:\n"
                  f"- **PIX** à vista, com {r.pix_discount_percent:g}% de desconto sobre o preço de tabela "
                  "(não vale sobre preço promocional)\n"
                  "- **Cartão de débito** à vista\n"
                  f"- **Cartão de crédito** em até {r.max_installments}x sem juros — até 3x (parcela mínima R$ 50,00), "
                  "4x a 6x (parcela mínima R$ 80,00), 7x a 12x (parcela mínima R$ 100,00)\n"
                  "- **Boleto** à vista (compensação em até 3 dias úteis)\n"
                  f"- Combinar formas de pagamento (ex.: PIX + cartão) é permitido em compras acima de "
                  f"{brl(r.combined_payment_min_total)}.",
                  "We accept:\n"
                  f"- **PIX** (instant transfer) with {r.pix_discount_percent:g}% off the list price "
                  "(not on promotional prices)\n"
                  "- **Debit card**\n"
                  f"- **Credit card** up to {r.max_installments} interest-free installments — up to 3x (minimum R$ 50.00 "
                  "per installment), 4x-6x (minimum R$ 80.00), 7x-12x (minimum R$ 100.00)\n"
                  "- **Boleto** (bank slip, clears in up to 3 business days)\n"
                  f"- Combining payment methods (e.g. PIX + card) is allowed for purchases above "
                  f"{brl(r.combined_payment_min_total)}.")
        if self.kb.missing_rule_anchors or not self._doc_says("boleto", "debito", "3 dias uteis"):
            body = self._excerpt(sec3, lang) + ("\n\n" + self._excerpt(sec31, lang) if sec31 else "")
        srcs = [s.citation() for s in (sec3, sec31) if s]
        if re.search(r"(promo|desconto|discount)", route.norm):
            sec62 = self.kb.find_section("Regras de Promoções")
            body += "\n\n" + _t(lang, "Importante: promoções não são cumulativas — o desconto do PIX não se soma ao preço promocional.",
                                "Important: discounts are not cumulative — the PIX discount is not added to a promotional price.")
            if sec62:
                srcs.append(sec62.citation())
        return body, srcs, False

    # ========================================================== policies
    def _excerpt(self, chunk, lang: str, max_lines: int = 8) -> str:
        if chunk is None:
            return ""
        lines = [l for l in chunk.text.splitlines()[1:] if l.strip()][:max_lines]
        head = _t(lang, f"Trecho do manual — {chunk.citation()}:", f"Policy excerpt (Portuguese) — {chunk.citation()}:")
        return head + "\n" + "\n".join(f"> {l.lstrip('- ').strip()}" for l in lines)

    def _doc_says(self, *phrases: str) -> bool:
        """Curated wording is only used when the retrieved manual actually contains these facts."""
        if not hasattr(self, "_corpus"):
            self._corpus = normalize(" ".join(c.text for c in self.kb.chunks))
        return all(normalize(p) in self._corpus for p in phrases)

    def _rule_ok(self, *fields: str) -> bool:
        return not set(fields) & set(self.kb.missing_rule_anchors)

    def _returns_section(self, route: Route, state: ConversationState):
        lang, r = route.lang, self.rules
        self.tools.call("search_store_policies", {"query": route.text})
        regret = self.kb.find_section("Arrependimento")
        preference = self.kb.find_section("Preferência")
        not_eligible = self.kb.find_section("Não Elegíveis")
        parts = []
        ineligible = re.search(r"(boquilha|personaliz|setup|regulagem|venda final|mouthpiece|custom)", route.norm)
        if ineligible and not_eligible:
            parts.append(_t(lang,
                "Atenção: pelo manual, alguns itens **não são elegíveis para troca**: instrumentos personalizados ou com "
                "ajustes sob encomenda (setup, regulagem especial), produtos de liquidação com aviso de \"venda final\" e "
                "**boquilhas de instrumentos de sopro**, por questões de higiene.",
                "Please note: according to the manual some items **are not eligible for exchange**: customised instruments "
                "or instruments with made-to-order adjustments (setup), clearance items marked \"final sale\", and "
                "**wind-instrument mouthpieces**, for hygiene reasons."))
        elif self._rule_ok("regret_days"):
            parts.append(_t(lang,
                f"Se a compra foi **online**, vale o direito de arrependimento: você tem até **{r.regret_days} dias corridos "
                f"após o recebimento** para pedir a devolução, sem precisar justificar. O produto deve estar na embalagem "
                f"original, sem sinais de uso e com todos os acessórios e manuais. O frete de devolução fica por conta da loja e "
                f"o reembolso é feito na mesma forma de pagamento em até {r.refund_business_days} dias úteis.",
                f"If you bought **online**, the right of withdrawal applies: you have up to **{r.regret_days} calendar days "
                f"after receiving** the product to return it, no justification needed. It must be in the original packaging, "
                f"unused, with all accessories and manuals. Return shipping is paid by the store and the refund goes back to "
                f"the original payment method within {r.refund_business_days} business days."))
        if re.search(r"\b(troca|trocar|exchange|swap|cor|modelo|tamanho|color|size)\b", route.norm) and preference \
                and not ineligible and self._rule_ok("regret_days"):
            parts.append(_t(lang,
                f"Para **troca por preferência** (cor, modelo, tamanho) o prazo é de {r.preference_exchange_days} dias, "
                "com o produto em perfeito estado, na embalagem original e sujeito à disponibilidade.",
                f"For an **exchange by preference** (color, model, size) the window is {r.preference_exchange_days} days, "
                "product in perfect condition and original packaging, subject to availability."))
        if ineligible and not_eligible:
            parts.append(self._excerpt(not_eligible, lang))
        else:
            parts.append(self._excerpt(regret, lang, max_lines=4))
        srcs = [c.citation() for c in ((not_eligible,) if ineligible else (regret,)) if c]
        if preference and not ineligible and re.search(r"\b(troca|trocar|exchange|swap|cor|modelo|tamanho|color|size)\b", route.norm):
            srcs.append(preference.citation())
        needs_human = False
        refers_to_order = route.order_id is not None or re.search(r"\b(pedido|order|compra|purchase)\b", route.norm)
        if state.verified_order and refers_to_order and not ineligible:
            order = state.verified_order_view or {}
            status = order.get("status")
            if status in {"delivered", "shipped"}:
                parts.append(_t(lang,
                    f"Sobre o seu pedido #{order.get('order_id')}: o status registrado é **{order.get('status_label')}**. "
                    "O prazo conta a partir da data em que você **recebeu** o produto, e essa data não consta no sistema "
                    "(só a previsão de entrega). Por isso vou te direcionar para a equipe confirmar a elegibilidade e abrir a "
                    "devolução. " + self._human_contact_line(lang),
                    f"About your order #{order.get('order_id')}: its recorded status is **{order.get('status_label')}**. "
                    "The window counts from the day you **received** it, and that date is not in our system (only the "
                    "estimated delivery). Our team needs to confirm eligibility and open the return. "
                    + self._human_contact_line(lang)))
                needs_human = True
            elif status in {"pending", "confirmed"}:
                parts.append(_t(lang,
                    f"Seu pedido #{order.get('order_id')} ainda não foi enviado (status: {order.get('status_label')}). "
                    "Cancelamentos antes do envio precisam ser feitos pela equipe. " + self._human_contact_line(lang),
                    f"Your order #{order.get('order_id')} has not shipped yet (status: {order.get('status_label')}). "
                    "Cancellations before shipping are handled by our team. " + self._human_contact_line(lang)))
                needs_human = True
        else:
            parts.append(_t(lang, "Para iniciar uma devolução, nossa equipe confirma os dados do pedido. "
                                  + self._human_contact_line(lang),
                            "To start a return, our team confirms the order details. " + self._human_contact_line(lang)))
            needs_human = True
        return "\n\n".join(p for p in parts if p), srcs, needs_human

    def _warranty_section(self, route: Route, state: ConversationState):
        lang, r = route.lang, self.rules
        self.tools.call("search_store_policies", {"query": route.text})
        defect, legal, maker, excluded = (self.kb.find_section(k) for k in
                                          ("Trocas por Defeito", "Garantia Legal", "Garantia do Fabricante", "Não Cobre"))
        days = None
        m = re.search(r"(\d{1,3})\s*(dias|days|d\b)", route.norm)
        mm = re.search(r"(\d{1,2})\s*(mes|meses|months?)", route.norm)
        if m:
            days = int(m.group(1))
        elif mm:
            days = int(mm.group(1)) * 30
        parts = [_t(lang,
            f"Sinto muito pelo problema! Pelas nossas políticas: (1) **troca por defeito de fabricação** direto na loja em até "
            f"**{r.defect_exchange_days} dias corridos após a compra**; (2) **garantia legal de {r.legal_warranty_days} dias** "
            f"(CDC) contra defeitos de fabricação, contados do **recebimento**; (3) depois disso, **garantia do fabricante** "
            f"(normalmente de 6 meses a 2 anos, conforme o certificado do produto), e a loja pode intermediar o processo.",
            f"Sorry about that! Our policies provide: (1) **exchange for a manufacturing defect** at the store within "
            f"**{r.defect_exchange_days} calendar days of purchase**; (2) a **{r.legal_warranty_days}-day legal warranty** "
            f"(Brazilian Consumer Code) against manufacturing defects, counted from **receipt**; (3) after that, the "
            f"**manufacturer's warranty** (usually 6 months to 2 years, per the product certificate), and the store can "
            f"mediate the process.")] if self._rule_ok("defect_exchange_days", "legal_warranty_days") \
            and self._doc_says("6 meses a 2 anos") else []
        if days is not None:
            if days <= r.defect_exchange_days:
                parts.append(_t(lang, f"Com {days} dias, você ainda está dentro do prazo de troca por defeito na loja "
                                      "(sujeito à avaliação técnica — mau uso, quedas e umidade não são cobertos).",
                                f"At {days} days you are still within the store's defect-exchange window (subject to "
                                "technical evaluation — misuse, drops and humidity are not covered)."))
            elif days <= r.legal_warranty_days:
                parts.append(_t(lang, f"Com {days} dias, o prazo de troca direta na loja ({r.defect_exchange_days} dias) já passou, "
                                      f"mas o produto ainda pode estar na garantia legal de {r.legal_warranty_days} dias, que conta "
                                      "a partir do recebimento. Como as duas regras se sobrepõem, esse caso precisa de avaliação "
                                      "da nossa equipe — não vou negar seu direito por aqui.",
                                f"At {days} days the store's direct-exchange window ({r.defect_exchange_days} days) has passed, "
                                f"but the product may still be under the {r.legal_warranty_days}-day legal warranty counted "
                                "from receipt. Because the two rules overlap, our team needs to evaluate this case — I won't "
                                "deny your rights here."))
            else:
                parts.append(_t(lang, f"Com {days} dias, o caminho costuma ser a garantia do fabricante; a loja pode intermediar.",
                                f"At {days} days the usual path is the manufacturer's warranty; the store can mediate."))
        parts.append(self._excerpt(defect, lang, max_lines=3))
        if not parts:
            parts += [self._excerpt(legal, lang, max_lines=3)]
        if excluded and self._doc_says("desgaste natural"):
            parts.append(_t(lang, "A garantia não cobre desgaste natural (cordas, trastes, feltros), mau uso, quedas, "
                                  "condições climáticas extremas, reparos por terceiros e danos apenas estéticos.",
                            "Warranty does not cover natural wear (strings, frets, felts), misuse, drops, extreme climate, "
                            "third-party repairs or purely cosmetic damage."))
        parts.append(_t(lang, "Para avaliarmos o seu caso: ", "To have your case evaluated: ") + self._human_contact_line(lang))
        srcs = [c.citation() for c in (defect, legal, maker, excluded) if c]
        return "\n\n".join(p for p in parts if p), srcs, True

    def _extract_city(self, text: str) -> str | None:
        norm = normalize(text)
        for city in KNOWN_CITIES:
            if normalize(city) in norm:
                return city
        m = re.search(r"\b(?:para|pra|to|em|in)\s+([A-ZÀ-Ú][a-zà-ú]+(?:\s+(?:d[aeo]s?\s+)?[A-ZÀ-Ú][a-zà-ú]+)*)", text)
        return m.group(1) if m else None

    def _shipping_section(self, route: Route, state: ConversationState):
        lang, r = route.lang, self.rules
        city = self._extract_city(route.text)
        sec51, sec52, sec53 = (self.kb.find_section(k) for k in ("Região Metropolitana", "Outras Cidades", "Rastreamento"))
        srcs = [c.citation() for c in (sec51, sec52) if c]
        total = None
        if route.product_ids and route.product_exact:
            total = self.tools.catalog.product_view(route.product_ids[0])["final_price"]
        large = route.product_ids and route.product_exact and \
            self.tools.catalog.products.at[route.product_ids[0], "category_id"] in {3, 4} and \
            self._doc_says("frete especial")
        if large and city and normalize(city) != "campo grande":
            name = self.tools.catalog.products.at[route.product_ids[0], "name"]
            return _t(lang, f"Para {city}, o **{name}** é um instrumento de grande porte: pelo manual, baterias e pianos "
                            "digitais podem exigir **frete especial com cotação individual**. Nossa equipe faz essa "
                            "cotação. " + self._human_contact_line(lang),
                      f"To {city}, **{name}** is a large instrument: according to the manual, drum kits and digital "
                      "pianos may require **special freight with an individual quote**, which our team provides. "
                      + self._human_contact_line(lang)), [sec52.citation()] if sec52 else [], True
        if city and total is not None:
            res = self.tools.call("estimate_shipping", {"order_total": total, "city": city})
            if res.get("fee") is not None:
                fee = _t(lang, "grátis", "free") if res["free_shipping"] else brl(res["fee"])
                return _t(lang, f"Para {city}, o frete desse item ({brl(total)}) é **{fee}**, com entrega por motoboy em "
                                f"{r.metro_delivery_days}.",
                          f"To {city}, shipping for this item ({brl(total)}) is **{fee}**, delivered by our courier in "
                          f"1-3 business days."), res["sources"], False
        self.tools.call("search_store_policies", {"query": route.text})
        body = _t(lang,
                  f"- **Campo Grande e região metropolitana:** frete grátis acima de {brl(r.metro_free_shipping_above)}; "
                  f"abaixo disso, taxa fixa de {brl(r.metro_flat_shipping_fee)}. Entrega por motoboy em {r.metro_delivery_days}.\n"
                  "- **Outras cidades:** Correios (PAC 5–12 dias úteis, SEDEX 2–5 dias úteis) ou Jadlog (3–8 dias úteis), "
                  "com rastreamento e seguro inclusos. O valor é calculado pelo CEP, peso e dimensões — não consigo cotar "
                  "por aqui. Instrumentos grandes (baterias, pianos digitais, contrabaixos) podem exigir frete especial.",
                  f"- **Campo Grande metro area:** free shipping above {brl(r.metro_free_shipping_above)}; below that, a flat "
                  f"fee of {brl(r.metro_flat_shipping_fee)}. Delivered by our courier in 1-3 business days.\n"
                  "- **Other cities:** Correios (PAC 5-12 business days, SEDEX 2-5) or Jadlog (3-8), tracking and insurance "
                  "included. The price depends on destination CEP, weight and size, so I can't quote it here. Large "
                  "instruments (drum kits, digital pianos, double basses) may need a special freight quote.")
        curated = not self.kb.missing_rule_anchors and self._doc_says("PAC", "SEDEX", "Jadlog", "motoboy")
        if not curated:
            body = self._excerpt(sec51, lang) + "\n\n" + self._excerpt(sec52, lang)
        if city and normalize(city) != "campo grande":
            carriers = _t(lang, "pelos Correios ou Jadlog", "via Correios or Jadlog") if curated else \
                _t(lang, "por transportadora", "by carrier")
            body = _t(lang, f"Para {city}, o envio é feito {carriers} e o frete é calculado pelo CEP no fechamento do pedido "
                            "— não consigo cotar o valor por aqui.",
                      f"For {city}, we ship {carriers} and the freight is calculated from the CEP at checkout — "
                      "I can't quote it here.") + "\n" + body
        if re.search(r"rastre|tracking|codigo", route.norm) and sec53:
            body += "\n" + _t(lang, "- O código de rastreamento é enviado por e-mail e WhatsApp assim que o pedido é despachado.",
                              "- The tracking code is sent by e-mail and WhatsApp as soon as the order ships.")
            srcs.append(sec53.citation())
        return body, srcs, False

    def _hours_section(self, route: Route, state: ConversationState):
        lang = route.lang
        info = self.tools.call("get_store_info", {})
        if not info["opening_hours"]:
            return None
        lines = []
        for item in info["opening_hours"]:
            days, hours = item["days"], item["hours"]
            if lang == "en":
                days = {"Segunda a Sexta-feira": "Monday to Friday", "Sábado": "Saturday",
                        "Domingo e Feriados": "Sundays and holidays"}.get(days, days)
                hours = hours.replace("às", "to").replace("Fechado", "Closed")
            lines.append(f"- {days}: {hours}")
        sec = self.kb.find_section("Horário de Funcionamento")
        note = _t(lang, "O WhatsApp segue o mesmo horário. Em datas especiais (Black Friday, Aniversário da Loja, Natal) "
                        "o horário pode ser estendido.",
                  "WhatsApp support follows the same hours. On special dates (Black Friday, store anniversary, "
                  "Christmas) hours may be extended.") if self._doc_says("datas especiais", "mesmo horario") else ""
        from .store_hours import describe, open_status

        status = open_status(self.kb.store_info.hours, self.clock())
        now_line = ""
        if status:
            now_line = "\n\n" + describe(status, lang) + _t(lang, " Feriados não constam no sistema.",
                                                              " Public holidays are not in our system.")
        return _t(lang, "Nosso horário de funcionamento:", "Our opening hours:") + "\n" + "\n".join(lines) + \
            now_line + ("\n\n" + note if note else ""), \
            [sec.citation()] if sec else info["sources"], False

    def _contact_section(self, route: Route, state: ConversationState):
        lang = route.lang
        info = self.tools.call("get_store_info", {})
        lines = []
        if info["address"]:
            lines.append(_t(lang, f"📍 Endereço: {info['address']}", f"📍 Address: {info['address']}"))
        for p in info["phones"]:
            lines.append(f"📞 {p['type']}: {p['number']}")
        if info["emails"]:
            lines.append(f"✉️ E-mail: {info['emails'][0]}")
        if any(c.startswith("whatsapp") for c in info["conflicts_in_manual"]):
            lines.append(_t(lang, "Obs.: o manual da loja traz dois números de WhatsApp diferentes. Se um deles não responder, "
                                  "tente o outro ou o telefone fixo.",
                            "Note: the store manual lists two different WhatsApp numbers. If one does not answer, "
                            "please try the other one or the landline."))
        return "\n".join(lines), info["sources"], False

    def _privacy_section(self, route: Route, state: ConversationState):
        sec = self.kb.find_section("Privacidade")
        if not self._doc_says("LGPD", "exclusao de seus dados"):
            return self._excerpt(sec, route.lang), [sec.citation()] if sec else [], False
        lead = _t(route.lang,
                  "Seguimos a LGPD: seus dados (nome, telefone, e-mail, endereço) são usados só para processar e entregar "
                  "pedidos, informar status/rastreamento, enviar promoções com seu consentimento e cumprir obrigações legais. "
                  "Não compartilhamos dados com terceiros para marketing, e você pode pedir a exclusão a qualquer momento pelo "
                  "WhatsApp ou e-mail.",
                  "We follow Brazil's LGPD: your data (name, phone, e-mail, address) is used only to process and deliver "
                  "orders, send status/tracking updates, send promotions with your consent and meet legal obligations. "
                  "We don't share it with third parties for marketing, and you can ask for deletion at any time via "
                  "WhatsApp or e-mail.")
        return lead, [sec.citation()] if sec else [], False

    def _about_section(self, route: Route, state: ConversationState):
        """Store profile from the manual (§1) + what the catalog snapshot actually contains.

        The manual's marketing figures (e.g. "more than 300 instruments") are not repeated to the customer: the
        numbers given come from the data, as required by §7.1 ("never give information without checking the system").
        """
        sec = self.kb.find_section("Sobre a")
        mission = self.kb.find_section("Missão")
        if sec is None:
            return None
        text = " ".join(sec.text.split())
        founded = re.search(r"fundada em (\d{4})(?: em ([^,.]+(?:, [A-Z][^,.]+)?))?", text)
        products = self.tools.catalog.products
        available = products[products["availability"] == "available"]
        cats = sorted(available["category"].dropna().unique())
        cats_en = [CATEGORY_EN.get(c, c) for c in cats]
        lang = route.lang
        intro_pt = "A **Empório da Música** é uma loja especializada em instrumentos musicais"
        intro_en = "**Empório da Música** is a store specialised in musical instruments"
        if founded:
            where = f" em {founded.group(2).strip()}" if founded.group(2) else ""
            intro_pt += f", fundada em {founded.group(1)}{where}"
            intro_en += f", founded in {founded.group(1)}" + (f" in {founded.group(2).strip()}" if founded.group(2) else "")
        parts = [_t(lang, intro_pt + ".", intro_en + ".")]
        if self._doc_says("exclusivamente com instrumentos"):
            parts.append(_t(lang, "Trabalhamos **exclusivamente com instrumentos** — não vendemos acessórios.",
                            "We sell **instruments only** — no accessories."))
        if mission is not None:
            body = " ".join(l.strip() for l in mission.text.splitlines()[1:] if l.strip())
            if body:
                parts.append(_t(lang, f"Nossa missão: _{body}_", f"Our mission (in Portuguese): _{body}_"))
        parts.append(_t(lang,
                        f"No sistema, hoje temos **{len(available)} instrumentos disponíveis** para venda, em "
                        f"{len(cats)} categorias: {', '.join(cats)}.",
                        f"Our system currently lists **{len(available)} instruments available** for sale in "
                        f"{len(cats)} categories: {', '.join(cats_en)}."))
        sources = [sec.citation()] + ([mission.citation()] if mission else []) + ["products.csv", "categories.csv"]
        return "\n\n".join(parts), sources, False

    def _accessory_section(self, route: Route, state: ConversationState):
        sec = self.kb.find_section("Sobre a")
        return _t(route.lang,
                  "Nós trabalhamos **exclusivamente com instrumentos musicais** — não vendemos acessórios como cordas avulsas, "
                  "palhetas, cabos, cases, pedais ou amplificadores. Para esses itens, recomendo procurar uma loja "
                  "especializada em acessórios. Se quiser, te ajudo a escolher um instrumento!",
                  "We sell **musical instruments only** — we don't carry accessories such as strings, picks, cables, cases, "
                  "pedals or amplifiers. For those, I'd recommend a store specialised in accessories. Happy to help you "
                  "choose an instrument, though!"), [sec.citation()] if sec else [], False

    def _human_contact_line(self, lang: str) -> str:
        info = self.tools.policies.store_info
        whats = [n for n, t in info.phones if "WhatsApp" in t]
        number = whats[-1] if whats else (info.phones[0][0] if info.phones else "")
        hours = "; ".join(f"{d} {h}" for d, h in info.hours[:2])
        line = _t(lang, f"Fale com nossa equipe pelo WhatsApp {number}" + (f" ({hours})." if hours else "."),
                  f"Please contact our team on WhatsApp {number}" + (f" ({hours})." if hours else "."))
        # Manual §7.1: outside business hours the virtual assistant must say when the team will be back.
        from .store_hours import DAY_NAMES_EN, DAY_NAMES_PT, open_status

        status = open_status(info.hours, self.clock())
        if status and not status.is_open and status.next_open_day is not None:
            today = status.next_open_day == status.now.weekday()
            tomorrow = status.next_open_day == (status.now.weekday() + 1) % 7
            when_pt = "hoje" if today else ("amanhã" if tomorrow else DAY_NAMES_PT[status.next_open_day])
            when_en = "today" if today else ("tomorrow" if tomorrow else DAY_NAMES_EN[status.next_open_day])
            line += _t(lang, f" Agora estamos fora do expediente: a equipe retorna {when_pt} às "
                             f"{status.next_open_at:%H:%M}.",
                       f" We are outside business hours right now: the team is back {when_en} at "
                       f"{status.next_open_at:%H:%M}.")
        return line

    def _dedupe_contact(self, sections: list[str], lang: str) -> None:
        """When several sections refer the customer to the team, give the full contact line only once (last)."""
        line = self._human_contact_line(lang)
        holders = [i for i, body in enumerate(sections) if line in body]
        short = _t(lang, "fale com nossa equipe pelo WhatsApp (contato logo abaixo).",
                   "contact our team on WhatsApp (details below).")
        for i in holders[:-1]:
            sections[i] = sections[i].replace(line, short)

    def _human_section(self, route: Route, state: ConversationState):
        sec = self.kb.find_section("Situações Especiais")
        r = self.rules
        return _t(route.lang,
                  "Entendo, e sinto muito pelo transtorno. Vou te direcionar para um atendente humano. "
                  + self._human_contact_line(route.lang) +
                  f" Reclamações são encaminhadas ao responsável, com retorno em até {r.complaint_reply_hours} horas úteis.",
                  "I understand, and I'm sorry for the trouble. Let me point you to a human agent. "
                  + self._human_contact_line(route.lang) +
                  f" Complaints are forwarded to the person in charge, with a reply within {r.complaint_reply_hours} "
                  "business hours."), [sec.citation()] if sec else [], True

    # ============================================================ orders
    def _order_section(self, route: Route, state: ConversationState):
        lang = route.lang
        if state.failed_order_attempts >= MAX_ORDER_ATTEMPTS:
            return _t(lang, "Por segurança, não consigo fazer novas tentativas de consulta de pedido nesta conversa. "
                            + self._human_contact_line(lang),
                      "For security reasons I can't make more order lookups in this conversation. "
                      + self._human_contact_line(lang)), [], True
        if route.order_id is None and state.verified_order and not route.email:
            order = state.verified_order_view or {}
            if order and re.search(r"\b(quando|chega|chegar|previsao|prazo|when|arrive|eta)\b", route.norm):
                self.tools.call("get_order_status", {"order_id": state.verified_order[0], "email": state.verified_order[1]})
                if order.get("status") == "shipped" and order.get("estimated_delivery"):
                    msg = _t(lang, f"O pedido #{order['order_id']} consta como **enviado**, com previsão de entrega registrada para "
                                   f"**{order['estimated_delivery']}**. Para a posição atualizada, acompanhe o código "
                                   f"{order['tracking_code']} no site dos Correios ou da Jadlog — eu não tenho acesso ao "
                                   "rastreamento em tempo real. Se a previsão já passou e o pedido não chegou: "
                                   + self._human_contact_line(lang),
                             f"Order #{order['order_id']} is recorded as **shipped**, with an estimated delivery of "
                             f"**{order['estimated_delivery']}**. For the latest position, track code {order['tracking_code']} "
                             "on the Correios or Jadlog website — I don't have real-time tracking. If the estimate has passed "
                             "and it hasn't arrived: " + self._human_contact_line(lang))
                    return msg, ["orders.csv"], False
            route.order_id = state.verified_order[0]
            route.email = state.verified_order[1]
        if route.order_id is None or not route.email:
            state.pending_order_id = route.order_id or state.pending_order_id
            state.pending_email = route.email or state.pending_email
            if route.order_id is None and not route.email:
                msg = _t(lang, "Consigo verificar o status do seu pedido! Por segurança, me informe o **número do pedido** e o "
                               "**e-mail cadastrado** na compra.",
                         "I can check your order status! For security, please send me the **order number** and the "
                         "**e-mail registered** on the purchase.")
            elif route.order_id is None:
                msg = _t(lang, "Obrigado! Agora me informe o **número do pedido**, por favor.",
                         "Thanks! Now please send me the **order number**.")
            else:
                msg = _t(lang, f"Para consultar o pedido #{route.order_id}, preciso também do **e-mail cadastrado** na compra "
                               "(é uma verificação de segurança).",
                         f"To look up order #{route.order_id}, I also need the **e-mail registered** on the purchase "
                         "(security check).")
            return msg, [], False
        res = self.tools.call("get_order_status", {"order_id": route.order_id, "email": route.email})
        state.pending_order_id = state.pending_email = None
        if not res.get("verified"):
            state.failed_order_attempts += 1
            left = MAX_ORDER_ATTEMPTS - state.failed_order_attempts
            msg = _t(lang, "Não consegui localizar um pedido com esse número e e-mail. Confira os dados (o e-mail precisa ser "
                           "o mesmo usado na compra) e tente novamente.",
                     "I couldn't find an order with that number and e-mail. Please double-check (it must be the e-mail "
                     "used for the purchase) and try again.")
            if left <= 0:
                msg += " " + self._human_contact_line(lang)
            return msg, [], left <= 0
        order = res["order"]
        if lang == "en":
            from .orders import STATUS_LABELS
            order["status_label"] = STATUS_LABELS["en"].get(order["status"], order["status"])
        state.verified_order = (route.order_id, route.email)
        state.verified_order_view = order
        items = ", ".join(f"{i['quantity']}x {i['product']}" for i in order["items"])
        # Manual §7.2: greet the customer by name when available - only the verified customer's own first name.
        first = self.tools.orders.first_name(route.order_id, route.email)
        hello = _t(lang, f"Obrigado, {first}! ", f"Thanks, {first}! ") if first else ""
        lines = [hello + _t(lang, f"Encontrei seu pedido **#{order['order_id']}** (feito em {order['order_date']}):",
                            f"I found your order **#{order['order_id']}** (placed on {order['order_date']}):"),
                 _t(lang, f"- Status registrado: **{order['status_label']}**", f"- Recorded status: **{order['status_label']}**"),
                 _t(lang, f"- Itens: {items}", f"- Items: {items}"),
                 f"- Total: {brl(order['total_brl'])} ({order['payment_method']})"]
        if order["tracking_code"]:
            lines.append(_t(lang, f"- Código de rastreamento: {order['tracking_code']} (consulte no site dos Correios ou da Jadlog)",
                            f"- Tracking code: {order['tracking_code']} (check it on the Correios or Jadlog website)"))
        if order["estimated_delivery"] and order["status"] in {"shipped", "confirmed", "pending"}:
            lines.append(_t(lang, f"- Previsão de entrega registrada: {order['estimated_delivery']}",
                            f"- Recorded delivery estimate: {order['estimated_delivery']}"))
        if order["notes"]:
            lines.append(_t(lang, f"- Observação: {order['notes']}", f"- Note: {order['notes']}"))
        disclaimer = _t(lang, "Essas informações vêm do último registro do nosso sistema; eu não tenho acesso ao "
                              "rastreamento em tempo real da transportadora.",
                        "This comes from the latest record in our system; I don't have access to the carrier's real-time "
                        "tracking.")
        overdue = ""
        late = False
        if order["status"] == "shipped" and order["estimated_delivery"]:
            try:
                late = datetime.fromisoformat(order["estimated_delivery"]).date() < self.clock().date()
            except ValueError:
                late = False
        if late:
            overdue = "\n\n" + _t(lang,
                f"⚠️ A previsão de entrega registrada ({order['estimated_delivery']}) já passou e o pedido ainda consta "
                "como enviado. Vale conferir o rastreio; se não chegou, nossa equipe verifica com a transportadora. "
                + self._human_contact_line(lang),
                f"⚠️ The recorded delivery estimate ({order['estimated_delivery']}) has passed and the order is still "
                "recorded as shipped. Please check the tracking; if it has not arrived, our team will check with the "
                "carrier. " + self._human_contact_line(lang))
        return "\n".join(lines) + "\n\n" + disclaimer + overdue, res["sources"], late

    # ========================================================= fallbacks
    def _greeting(self, lang: str) -> str:
        return _t(lang,
                  f"Oi! Eu sou o {self.assistant_name}, assistente virtual da **Empório da Música** 🎸. Posso te ajudar com preços, estoque e "
                  "promoções de instrumentos, status de pedidos e nossas políticas (pagamento, trocas, entrega, garantia "
                  "e horários). Como posso ajudar?",
                  f"Hi! I'm {self.assistant_name}, the virtual assistant of **Empório da Música** 🎸. I can help with instrument prices, stock and "
                  "promotions, order status and store policies (payment, returns, shipping, warranty and hours). "
                  "How can I help?")

    def _policy_fallback(self, route: Route):
        if route.out_of_scope_hint:
            return None
        res = self.tools.call("search_store_policies", {"query": route.text, "top_k": 1})
        hits = res.get("results", [])
        if not hits or hits[0]["score"] < 4.0:
            return None
        chunk = next((c for c in self.kb.chunks if c.citation() == hits[0]["citation"]), None)
        lead = _t(route.lang, "Encontrei isto nas nossas políticas:", "Here is what our policies say:")
        return lead + "\n" + self._excerpt(chunk, route.lang), [hits[0]["citation"]]

    def _out_of_scope(self, lang: str) -> str:
        return _t(lang,
                  "Essa pergunta foge do que eu consigo responder por aqui 🙂. Sou o assistente da Empório da Música e posso "
                  "ajudar com: instrumentos (preço, estoque e promoções), status de pedidos e políticas da loja (pagamento, "
                  "trocas e devoluções, frete, garantia, horários e contato).",
                  "That's outside what I can help with here 🙂. I'm Empório da Música's assistant and I can help with: "
                  "instruments (price, stock and promotions), order status and store policies (payment, returns, shipping, "
                  "warranty, opening hours and contact).")
