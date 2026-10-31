"""Deterministic, multi-label intent detection and entity extraction (PT + EN).

Used directly by the offline engine and, in Gemini mode, only for pre-LLM safety
decisions (the LLM selects tools itself). Keeping routing in plain Python makes
every decision testable and explainable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .catalog import CatalogService
from .guardrails import EMAIL_RE
from .text_utils import detect_language, normalize, parse_amount

INTENT_PATTERNS: dict[str, list[str]] = {
    "greeting": [r"^(oi|ola|ola+|bom dia|boa tarde|boa noite|hello|hi|hey|e ai)\b"],
    "thanks": [r"\b(obrigad[oa]|valeu|thanks|thank you|brigad)"],
    "promotions": [r"\bpromo", r"\boferta", r"\bliquida", r"black friday", r"queima de estoque",
                   r"\bsale\b", r"\bdeals?\b", r"\bon sale\b", r"\bdiscounts?\b(?! (for|with|on|no|in) pix)",
                   r"\bdescontos?\b(?! (no|do|com|via|pagando|pelo|de) pix)", r"\bsemana do musico"],
    "payment": [r"\bpix\b", r"\bcart(ao|oes)\b", r"\bparcel", r"\bboleto", r"\bdebito\b", r"\bcredito\b",
                r"forma(s)? de pagamento", r"\bpagamento", r"\bpagar\b", r"\bpay(ment)?\b", r"\binstallments?\b",
                r"\bcredit card\b", r"\bdebit\b", r"\b\d{1,2}x\b(?! ?(baixo|bass|electric))", r"\bem \d{1,2} ?(x|vezes)\b",
                r"sem juros", r"interest[- ]free"],
    "returns": [r"\bdevol", r"\barrepend", r"\btroca", r"\btrocar\b", r"\breembols", r"\breturn", r"\brefund",
                r"\bexchange", r"\bdesist"],
    "warranty": [r"\bgarantia", r"\bdefeito", r"\bdefeituos", r"\bquebr", r"\bwarrant", r"\bdefect",
                 r"\bbroken\b", r"\bnao funciona", r"\bstopped working"],
    "shipping": [r"\bfrete", r"\bentrega(m|s)?\b", r"\bentregar\b", r"\benvi(o|am)\b", r"\bshipping\b", r"\bdeliver",
                 r"\bship\b", r"\bcorreios\b", r"\bsedex\b", r"\bjadlog\b", r"\bmotoboy\b", r"\brastre",
                 r"\btracking\b"],
    "hours": [r"\bhorario", r"\babre(m)?\b", r"\bfecha(m)?\b", r"\bfunciona(m|mento)?\b(?! (o|a) (violao|produto))",
              r"\bsabado", r"\bdomingo", r"\bferiado", r"\bopen\b", r"\bopening\b", r"\bhours\b", r"\bclose[sd]?\b",
              r"\bsaturday", r"\bsunday", r"\bholiday"],
    "contact": [r"\bendereco", r"\bonde fica", r"\blocaliza", r"\btelefone", r"\bwhats(app|zap)?\b",
                r"\bcontato\b", r"\baddress\b", r"\bphone\b", r"\bwhere is (the|your) store", r"\blocated\b",
                r"\bcontact\b", r"\bqual (o|e o) e-?mail", r"\bemail (da|de) loja", r"\bcnpj\b"],
    "privacy": [r"\blgpd\b", r"\bprivacidade", r"\bmeus dados", r"\bdados pessoais", r"\bprivacy\b",
                r"\bpersonal data\b", r"\bexcluir (meus )?dados", r"\bdelete my data"],
    "human": [r"\breclama", r"\batendente", r"\bhumano\b", r"\bfalar com (alguem|uma pessoa|o gerente|gerente)",
              r"\bgerente\b", r"\bprocon\b", r"\bpessim", r"\babsurd", r"\bhuman\b", r"\breal person",
              r"\bmanager\b", r"\bcomplain", r"\bsupervisor"],
    "about": [r"sobre a loja", r"quem (sao|e) voces", r"\bhistoria da loja", r"\bmissao\b", r"\babout (the|your) store"],
    "catalog_overview": [r"o que voces vendem", r"\bquais (produtos|instrumentos|categorias)", r"\bcatalogo\b",
                         r"what do you sell", r"\bcategories\b", r"\bwhat (products|instruments)"],
    "availability": [r"\btem\b", r"\btêm\b", r"\btem em estoque", r"\bestoque\b", r"\bdisponive", r"\bdisponibilidade",
                     r"\bin stock\b", r"\bavailable\b", r"\bavailability\b", r"\bdo you have\b", r"\besgotad"],
    "price": [r"\bquanto (custa|e|sai|ta|esta)\b", r"\bpreco", r"\bvalor\b", r"\bcusta\b", r"\bprice\b",
              r"\bhow much\b", r"\bcost(s)?\b"],
}

ORDER_WORDS = [r"\bpedido", r"\border\b", r"\bminha compra\b", r"\bmy purchase\b", r"\bencomenda"]
ORDER_STATUS_WORDS = [r"\bstatus\b", r"\bonde (esta|ta)\b", r"\bchegou\b", r"\bchega\b", r"\brastre", r"\btrack",
                      r"\bwhere is\b", r"\bsituacao\b", r"\bandamento\b", r"\bjá (foi|saiu)\b", r"\bja (foi|saiu)\b",
                      r"\bconsultar\b", r"\bcheck\b", r"\bquando (vai )?cheg", r"\bwhen will\b"]
FOLLOWUP_REFERENCES = [r"\b(esse|essa|este|esta|desse|dessa|deste|nesse|nessa|dele|dela|ele|ela|o mesmo|a mesma)\b",
                       r"\b(it|this one|that one|this|that|its)\b", r"^e (no|na|o|a|em|pelo|pela|com|se|quanto|qual)\b",
                       r"^(and|what about)\b"]

# Accessories are explicitly outside the catalog (policy §1 and §7.1).
ACCESSORY_PATTERNS = [
    r"\bpalhetas?\b", r"\bcabos?\b(?! de (guerra|madeira))", r"(?<!in )\bcases?\b(?! (of|de|i|you|it))", r"\bbags?\b", r"\bcapas?\b",
    r"\bpedais\b", r"\bpedal\b(?! de bumbo)", r"\bpedaleira", r"\bamplificador(es)?\b", r"\bamp(s|lifiers?)?\b",
    r"\bcorreias?\b", r"\bafinador(es)?\b", r"\bestante(s)? de partitura", r"\bsuportes?\b", r"\bencordoamentos?\b",
    r"\bbaquetas?\b", r"\bmetronomo", r"\bpicks?\b", r"\bguitar picks?\b", r"\bcables?\b", r"\bstraps?\b",
    r"\btuners?\b(?! ac)", r"\bacess[oó]rios?\b", r"\baccessor(y|ies)\b", r"\bstrings? (for|para)\b",
    r"\b(jogo|kit|pacote|set) de cordas\b", r"\bcordas? (para|pra|de reposicao|novas?)\b",
    r"\b(vende\w*|tem|tem|preciso de|quero|comprar|compro|vcs tem)\s+(umas? |de )?cordas\b",
    r"\b(sell|buy|need|have)\s+(any\s+)?(guitar\s+)?strings\b", r"\bset of strings\b", r"\bheadphones?\b", r"\bfones?\b",
]
OUT_OF_SCOPE_HINTS = [
    r"\b(clima|tempo amanha|previsao do tempo|weather)\b", r"\b(receita|recipe|cozinhar)\b", r"\b(politica|eleic|election)\b",
    r"\b(futebol|football|soccer|jogo do)\b", r"\b(capital (da|do|of))\b", r"\b(piada|joke)\b",
    r"\b(codigo|code|python|javascript|programa(r|cao))\b", r"\b(investir|bitcoin|acoes|stock market)\b",
    r"\b(aula de|curso de|como (tocar|afinar)|how to (play|tune)|ensina)\b", r"\b(marte|mars)\b",
    r"\b(medico|doctor|remedio)\b",
]

SUBTYPE_KEYWORDS = {
    "nylon": ("nylon",), "aco": ("steel", "aco", "aço"), "eletrico": ("eletrico", "eletrica", "eletroacustico", "eletroacustica",
                                                                       "electro-acoustic", "electroacoustic", "acoustic-electric",
                                                                       "acoustic electric", "electric acoustic", "with pickup",
                                                                       "com captador"),
    "7 cordas": ("7 cordas", "sete cordas", "7-string", "seven string", "7 string"),
    "soprano": ("soprano",), "concert": ("concert", "concerto"), "tenor": ("tenor",),
    "baritono": ("baritono", "baritone"),
}
SPEC_KEYWORD = {"nylon": "nylon", "aco": "steel", "eletrico": '"electronics":"yes"', "7 cordas": "7 cordas",
                "soprano": "soprano", "concert": "concert", "tenor": "tenor", "baritono": "baritono"}

_NUM = r"(?:r\$\s*)?(\d{1,3}(?:[.\s]\d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(mil|k)?"


@dataclass
class Route:
    text: str
    norm: str
    lang: str
    intents: list[str] = field(default_factory=list)
    product_ids: list[int] = field(default_factory=list)
    product_exact: bool = False
    product_by_brand: bool = False
    category_ids: list[int] = field(default_factory=list)
    min_price: float | None = None
    max_price: float | None = None
    spec_keywords: list[str] = field(default_factory=list)
    sort: str = "price_asc"
    order_id: int | None = None
    email: str | None = None
    accessory: bool = False
    out_of_scope_hint: bool = False
    is_followup: bool = False

    def has(self, *intents: str) -> bool:
        return any(i in self.intents for i in intents)


def _amount(number: str, suffix: str | None) -> float | None:
    return parse_amount(number + (" " + suffix if suffix else ""))


def extract_price_range(norm: str) -> tuple[float | None, float | None]:
    lo = hi = None
    between = (re.search(rf"\b(?:entre|between)\s+{_NUM}\s+(?:e|and|a|to)\s+{_NUM}", norm)
               or re.search(rf"\b(?:de|from)\s+{_NUM}\s+(?:a|ate|to)\s+{_NUM}", norm))
    if between:
        return _amount(between.group(1), between.group(2)), _amount(between.group(3), between.group(4))
    upper = re.search(rf"\b(ate|abaixo de|menos de|no maximo|maximo|max|under|below|less than|up to|at most|"
                      rf"cheaper than|mais barato que|nao passe de|orcamento de|budget of|budget)\s*(de\s+)?{_NUM}", norm)
    if upper:
        hi = _amount(upper.group(3), upper.group(4))
    lower = re.search(rf"\b(acima de|a partir de|mais de|mais caro que|above|over|more than|at least|from)\s*{_NUM}", norm)
    if lower:
        lo = _amount(lower.group(2), lower.group(3))
    return lo, hi


def extract_order_id(norm: str) -> int | None:
    match = re.search(r"\b(?:pedido|order|compra|encomenda)\s*(?:n[o°º.]?\s*|numero\s*|number\s*|nr\.?\s*|#\s*)?(\d{1,7})\b", norm)
    if not match:
        match = re.search(r"#\s*(\d{1,7})\b", norm)
    return int(match.group(1)) if match else None


class IntentRouter:
    def __init__(self, catalog: CatalogService):
        self.catalog = catalog

    def route(self, text: str, default_lang: str = "pt") -> Route:
        norm = normalize(text)
        route = Route(text=text, norm=norm, lang=detect_language(EMAIL_RE.sub(" ", text), default=default_lang))
        intents: list[str] = []
        intent_text = EMAIL_RE.sub(" ", norm)
        for intent, patterns in INTENT_PATTERNS.items():
            if any(re.search(p, intent_text) for p in patterns):
                intents.append(intent)

        email_match = EMAIL_RE.search(text)
        route.email = email_match.group(0).strip() if email_match else None
        norm_wo_email = EMAIL_RE.sub(" ", norm)
        route.order_id = extract_order_id(norm_wo_email)
        has_order_word = any(re.search(p, norm_wo_email) for p in ORDER_WORDS)
        policy_question = any(i in intents for i in ("returns", "warranty", "shipping", "payment"))
        if route.order_id is not None or route.email or (not policy_question and
                has_order_word and (any(re.search(p, norm_wo_email) for p in ORDER_STATUS_WORDS)
                                    or re.search(r"\b(meu|minha|my)\s+(pedido|order|compra)", norm_wo_email))):
            intents.append("order_status")
        if route.email and "contact" in intents and not re.search(r"qual (o|e o) e-?mail", norm):
            intents.remove("contact")

        route.out_of_scope_hint = any(re.search(p, norm) for p in OUT_OF_SCOPE_HINTS)

        search_text = EMAIL_RE.sub(" ", text)
        if route.order_id is not None:
            search_text = re.sub(r"(?i)(pedido|order|compra|encomenda)\s*(n[º°o.]?|#)?\s*\d+", " ", search_text)
        match = self.catalog.match_products(search_text)
        route.product_ids, route.product_exact = list(match.product_ids), match.exact
        route.product_by_brand = match.by_brand
        # Words that belong to a matched product name (e.g. "Tuner" in "Tagima Dallas Tuner")
        # must not trigger the accessory rule.
        accessory_text = norm
        for pid in route.product_ids:
            for word in normalize(self.catalog.products.at[pid, "name"]).split():
                accessory_text = re.sub(rf"\b{re.escape(word)}\b", " ", accessory_text)
        route.accessory = any(re.search(p, accessory_text) for p in ACCESSORY_PATTERNS)
        route.category_ids = self.catalog.detect_categories(norm)
        if route.accessory and route.category_ids:
            # "cabo para guitarra" / "guitar strings": the category only qualifies the accessory.
            from .catalog import CATEGORY_KEYWORDS, AMBIGUOUS_CATEGORY_KEYWORDS
            words = {w for ws in CATEGORY_KEYWORDS.values() for w in ws} | set(AMBIGUOUS_CATEGORY_KEYWORDS)
            alt = "|".join(sorted((re.escape(w) for w in words), key=len, reverse=True))
            modifier = (rf"\b(para|pra|p/|for|de)\s+(o |a |um |uma |meu |minha |my |the |a )?({alt})\b|"
                        rf"\b({alt})\s+(strings?|picks?|cables?|cases?|straps?|amps?|amplifiers?|pedals?|tuners?)\b")
            stripped = re.sub(modifier, " ", norm)
            route.category_ids = self.catalog.detect_categories(stripped)
        route.min_price, route.max_price = extract_price_range(norm_wo_email)
        for key, words in SUBTYPE_KEYWORDS.items():
            if any(re.search(rf"\b{re.escape(normalize(w))}\b", norm) for w in words):
                route.spec_keywords.append(key)
        if re.search(r"\b(mais caro|mais caros|most expensive|top de linha|premium)\b", norm):
            route.sort = "price_desc"
        route.is_followup = any(re.search(p, norm) for p in FOLLOWUP_REFERENCES)

        has_budget = route.max_price is not None or route.min_price is not None
        search_words = re.search(r"\b(instrumentos?|produtos?|instruments?|products?|opcoes|options|modelos|models|"
                                 r"quero|procuro|procurando|comprar|recomenda\w*|sugere|sugest\w*|indica\w*|tem|tem algum|"
                                 r"looking for|recommend|suggest|buy|do you have|any|disponive\w*|available)\b", norm)
        policy_context = bool(set(intents) & {"returns", "warranty", "shipping", "payment", "order_status", "promotions"})
        if route.product_ids and not (route.product_by_brand and route.category_ids):
            intents.append("product_info")
        elif route.category_ids or has_budget or (search_words and not policy_context and
                                                  re.search(r"\b(instrumentos?|produtos?|instruments?|products?|"
                                                            r"opcoes|options|modelos|models)\b", norm)):
            if not policy_context or has_budget or (route.category_ids and search_words):
                intents.append("product_search")
        if route.accessory and not route.category_ids and not route.product_ids:
            intents = [i for i in intents if i not in {"availability", "price", "product_search"}]
        if route.accessory:
            intents.append("accessory")
        # "availability"/"price" alone are modifiers of product intents, not standalone intents.
        route.intents = list(dict.fromkeys(intents))
        return route
