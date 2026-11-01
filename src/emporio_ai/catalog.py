"""Catalog, inventory and promotion queries (read-only, deterministic)."""

from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

from .business_rules import BusinessRules, installment_plan, pix_price, round_money
from .data_store import SELLABLE_STATUS, DataStore
from .text_utils import compact, normalize, tokenize

# Words that describe many products and therefore cannot identify a single one.
GENERIC_TOKENS = {
    "natural", "nylon", "aco", "folk", "eletrico", "eletrica", "eletroacustico", "violao", "violoes",
    "guitarra", "baixo", "bass", "electric", "mogno", "cedar", "cedro", "sunburst", "preto",
    "soprano", "concert", "tenor", "baritono", "dreadnought", "cordas", "7", "studio", "pro",
    "kit", "bateria", "acustica", "teclado", "sintetizador", "synth", "solido", "estudante",
    "classical", "series", "collection", "standard", "player", "american", "custom", "se",
    "de", "do", "da", "o", "a", "e", "the", "genesis", "ii", "50s", "1", "2", "3", "4",
    "1x", "2x", "3x", "4x", "tuner", "cedr", "acustico", "electrico",
}

CATEGORY_KEYWORDS: dict[int, tuple[str, ...]] = {
    1: ("guitarra", "guitarras", "electric guitar", "electric guitars"),
    2: ("baixo", "baixos", "contrabaixo eletrico", "bass", "basses", "bass guitar"),
    3: ("bateria", "baterias", "percussao", "drum", "drums", "drum kit", "percussion"),
    4: ("teclado", "teclados", "piano", "pianos", "sintetizador", "sintetizadores", "synth",
        "synths", "synthesizer", "keyboard", "keyboards", "controlador midi"),
    5: ("violao", "violoes", "acoustic guitar", "acoustic guitars", "classical guitar",
        "classical guitars", "folk guitar"),
    6: ("flauta", "flautas", "clarinete", "clarinetes", "saxofone", "saxofones", "sax", "oboe",
        "flute", "clarinet", "saxophone", "woodwind", "woodwinds", "sopro"),
    7: ("trompete", "trompetes", "trombone", "trombones", "tuba", "tubas", "trompa", "trumpet",
        "brass", "metais"),
    8: ("violino", "violinos", "violoncelo", "violoncelos", "cello", "violin", "violins",
        "contrabaixo acustico", "viola de arco", "orquestra", "orchestral"),
    9: ("ukulele", "ukuleles", "uke", "ukes", "ukelele"),
}
# Plain "guitar" in English is ambiguous between acoustic (5) and electric (1).
AMBIGUOUS_CATEGORY_KEYWORDS: dict[str, tuple[int, ...]] = {"guitar": (1, 5), "guitars": (1, 5)}

STATUS_LABELS = {
    "pt": {"available": "disponível", "out_of_stock": "temporariamente sem estoque",
           "discontinued": "descontinuado (fora do catálogo)", "coming_soon": "em breve (ainda não disponível para venda)",
           "unavailable": "indisponível"},
    "en": {"available": "available", "out_of_stock": "temporarily out of stock",
           "discontinued": "discontinued (no longer in the catalog)", "coming_soon": "coming soon (not yet on sale)",
           "unavailable": "unavailable"},
}


@dataclass(frozen=True)
class ProductMatch:
    product_ids: tuple[int, ...]
    exact: bool  # a single product was identified unambiguously
    by_brand: bool = False  # matched only by brand name (model not recognised)


_SPEC_WORDS_EN = {"cedro": "cedar", "mogno": "mahogany", "sólido": "solid", "solido": "solid",
                  "jacarandá": "rosewood", "acácia": "acacia", "preto": "black", "verde": "green"}


def _spec_en(value: str) -> str:
    """Translate the Portuguese words used in the specs column; other values (brand woods, colours) are kept."""
    words = value.split()
    out = [(_SPEC_WORDS_EN.get(w.lower(), w)).capitalize() if w.lower() in _SPEC_WORDS_EN else w for w in words]
    # "Cedro Sólido" -> "Solid Cedar"
    if len(out) >= 2 and out[-1] == "Solid":
        out = ["Solid"] + out[:-1]
    return " ".join(out)


def availability_of(status: str, stock: int) -> str:
    if status == SELLABLE_STATUS:
        return "available" if stock > 0 else "out_of_stock"
    if status in {"discontinued", "coming_soon"}:
        return status
    return "unavailable"


class CatalogService:
    def __init__(self, store: DataStore, rules: BusinessRules | None = None):
        self.rules = rules or BusinessRules()
        products = store.table("products")
        categories = store.table("categories").rename(columns={"name": "category"})
        promos = store.table("promotions")
        self.promotions = promos.merge(products[["product_id", "name"]], on="product_id", how="left")

        active = promos[promos["is_active"]].sort_values("discount_percent", ascending=False)
        best_active = active.drop_duplicates("product_id", keep="first")  # non-cumulative
        frame = products.merge(categories[["category_id", "category"]], on="category_id", how="left")
        frame = frame.merge(best_active[["product_id", "promotion_id", "discount_percent", "description"]]
                            .rename(columns={"description": "promotion_name"}), on="product_id", how="left")
        frame["availability"] = [availability_of(s, q) for s, q in zip(frame["status"], frame["stock_quantity"])]
        frame["has_promotion"] = frame["discount_percent"].notna()
        frame["final_price"] = [
            round_money(p * (1 - d / 100)) if pd.notna(d) else round_money(p)
            for p, d in zip(frame["price_brl"], frame["discount_percent"])
        ]
        frame["brand"] = frame["name"].map(self._brand_of)
        self.products = frame.set_index("product_id", drop=False).sort_index()
        self.products.index.name = None
        self.categories = categories.set_index("category_id", drop=False)
        self.categories.index.name = None
        self._index = {pid: self._product_tokens(name) for pid, name in self.products["name"].items()}
        self.brands = {normalize(b) for b in self.products["brand"]}

    @staticmethod
    def _brand_of(name: str) -> str:
        words = str(name).split()
        for word in words:
            if normalize(word) not in {"bateria", "acustica", "teclado", "sintetizador"}:
                return word
        return words[0] if words else ""

    # ------------------------------------------------------------ matching
    @staticmethod
    def _product_tokens(name: str) -> set[str]:
        raw = tokenize(name)
        tokens = {compact(t) for t in raw}
        for tok in raw:  # 'ka-15s' -> also 'ka15s'; keep alphanumeric parts only
            tokens.update(compact(p) for p in re.split(r"[-/]", tok)
                          if re.search(r"[a-z]", p) and re.search(r"\d", p))
        # bigrams such as 'kit 1' -> 'kit1', 'bass 1x' -> 'bass1x', 'synth 2' -> 'synth2'
        tokens.update(compact(a + b) for a, b in zip(raw, raw[1:]) if re.search(r"\d", b))
        return {t for t in tokens if t and not t.isdigit()}

    @staticmethod
    def _query_tokens(text: str) -> set[str]:
        raw = tokenize(text)
        query = {compact(t) for t in raw}
        query |= {compact(p) for t in raw for p in re.split(r"[-/]", t)
                  if re.search(r"[a-z]", p) and re.search(r"\d", p)}
        query |= {compact(a + b) for a, b in zip(raw, raw[1:]) if re.search(r"\d", b)}
        return {t for t in query if t}

    def match_products(self, text: str) -> ProductMatch:
        """Identify products mentioned in free text by model code and/or brand.

        A product is matched when the query contains at least one *distinctive*
        token of its name (e.g. 'gd20', '110e', 'c40'). Brand-only mentions
        ('Taylor') return all products of that brand as a non-exact match.
        """
        query = self._query_tokens(text)
        scores: dict[int, float] = {}
        for pid, tokens in self._index.items():
            distinctive = tokens - GENERIC_TOKENS
            hits = distinctive & query
            brand = compact(self.products.at[pid, "brand"])
            model_hits = {h for h in hits if h != brand}
            if not model_hits:
                continue
            # Prefer model codes containing digits ("gd20") over plain words.
            score = sum(2.0 if re.search(r"\d", h) else 1.0 for h in model_hits)
            score += 0.5 if brand in query else 0.0
            score += 0.1 * len((tokens & query) - distinctive)  # descriptive words break ties
            scores[pid] = score
        if scores:
            top = max(scores.values())
            best = tuple(sorted(pid for pid, s in scores.items() if s == top))
            return ProductMatch(best, exact=len(best) == 1)
        brand_ids = tuple(int(pid) for pid, b in self.products["brand"].items() if compact(b) in query)
        return ProductMatch(brand_ids, exact=len(brand_ids) == 1, by_brand=bool(brand_ids))

    def match_many(self, text: str) -> list[int]:
        """Products mentioned in a list/comparison ('C40 e o Memphis', 'GD20 vs FG800'), in order."""
        segments = re.split(r"\s*(?:,|;|\+|\be\b|\bou\b|\bvs\.?|\bversus\b|\bx\b|\band\b|\bor\b|"
                            r"\bcom o\b|\bcom a\b|\bcontra\b)\s*", normalize(text))
        found: list[int] = []
        for segment in segments:
            if not segment.strip():
                continue
            match = self.match_products(segment)
            if match.exact and match.product_ids[0] not in found:
                found.append(match.product_ids[0])
        return found

    OTHER_BRANDS = ("fender", "ibanez", "pearl", "tama", "roland", "yamaha", "korg", "nord", "gibson", "music man",
                    "prs", "taylor", "martin", "takamine", "tagima", "giannini", "kala", "ohana", "crafter", "casio")

    def description_conflict(self, pid: int) -> str | None:
        """Brand named in the description when it differs from the brand in the product name (data-entry issue)."""
        row = self.products.loc[pid]
        brand = normalize(row["brand"])
        desc = normalize(row["description"])
        mentioned = [b for b in self.OTHER_BRANDS if re.search(rf"\b{b}\b", desc)]
        if mentioned and not any(b.startswith(brand) or brand.startswith(b.split()[0]) for b in mentioned):
            return mentioned[0].title()
        return None

    def data_findings(self) -> list[str]:
        conflicts = [f"{self.products.at[p, 'name']} (description mentions {self.description_conflict(p)})"
                     for p in self.products.index if self.description_conflict(p)]
        return ["products: name and description refer to different brands for " + "; ".join(conflicts)] \
            if conflicts else []

    def spelling_vocabulary(self) -> set[str]:
        """Brand names and distinctive alphabetic model words, used for typo correction."""
        words = {normalize(b) for b in self.products["brand"]}
        for tokens in self._index.values():
            words |= {t for t in tokens - GENERIC_TOKENS if t.isalpha() and len(t) >= 4}
        for kws in CATEGORY_KEYWORDS.values():
            words |= {w for w in kws if " " not in w and len(w) >= 5}
        return words

    @staticmethod
    def detect_categories(text: str) -> list[int]:
        norm = " " + normalize(text) + " "
        found: list[int] = []
        for cat_id, words in CATEGORY_KEYWORDS.items():
            if any(re.search(rf"\b{re.escape(w)}\b", norm) for w in words):
                found.append(cat_id)
        # "violino" contains no "violao"; "baixo" inside "abaixo" is guarded by \b.
        if 1 not in found and 5 not in found:
            for word, cats in AMBIGUOUS_CATEGORY_KEYWORDS.items():
                if re.search(rf"\b{word}\b", norm):
                    found.extend(c for c in cats if c not in found)
        if 8 in found and 2 in found and "contrabaixo acustico" in norm:
            found.remove(2)
        return sorted(set(found))

    # ------------------------------------------------------------- views
    def product_view(self, pid: int, lang: str = "pt") -> dict:
        row = self.products.loc[pid]
        promo = bool(row["has_promotion"])
        view = {
            "product_id": int(pid),
            "name": row["name"],
            "brand": row["brand"],
            "category": row["category"],
            "status": row["status"],
            "availability": row["availability"],
            "availability_label": STATUS_LABELS.get(lang, STATUS_LABELS["pt"])[row["availability"]],
            "stock_quantity": int(row["stock_quantity"]) if row["availability"] == "available" else 0,
            "list_price": round_money(row["price_brl"]),
            "final_price": round_money(row["final_price"]),
            "description": row["description"],
            "specs": DataStore.parse_specs(row.get("specs", "")),
        }
        if promo:
            view["promotion"] = {"promotion_id": int(row["promotion_id"]), "name": row["promotion_name"],
                                 "discount_percent": float(row["discount_percent"])}
        if row["availability"] == "available":
            view["pix_price"] = pix_price(row["price_brl"], promo, self.rules)
            view["card"] = installment_plan(row["final_price"], self.rules)
        return view

    @staticmethod
    def spec_summary(specs: dict[str, str], lang: str = "pt") -> str:
        """Human-readable summary of the JSON specs column (only fields present in the data)."""
        pt = lang != "en"
        if not pt:  # data values are partly Portuguese (e.g. "Cedro Sólido"): translate the known words
            specs = {k: _spec_en(v) if isinstance(v, str) else v for k, v in specs.items()}
        parts = []
        if specs.get("top"):
            parts.append(("tampo " if pt else "top: ") + specs["top"])
        if specs.get("back_sides"):
            parts.append(("fundo/laterais " if pt else "back/sides: ") + specs["back_sides"])
        if specs.get("body"):
            parts.append(("corpo " if pt else "body: ") + specs["body"])
        if specs.get("neck"):
            parts.append(("braço " if pt else "neck: ") + specs["neck"])
        strings = specs.get("strings")
        if strings in {"nylon", "steel"}:
            parts.append(("cordas de " + ("nylon" if strings == "nylon" else "aço")) if pt
                         else (strings + " strings"))
        elif strings and strings.isdigit():
            parts.append(f"{strings} cordas" if pt else f"{strings} strings")
        if specs.get("scale"):
            parts.append(("escala " if pt else "scale ") + specs["scale"])
        if specs.get("electronics") in {"yes", "no"}:
            yes = specs["electronics"] == "yes"
            parts.append(("com captação (eletroacústico)" if yes else "sem captação") if pt
                         else ("with pickup (acoustic-electric)" if yes else "no pickup"))
        if specs.get("keys"):
            parts.append(f"{specs['keys']} teclas" if pt else f"{specs['keys']} keys")
        if specs.get("pieces"):
            parts.append(f"{specs['pieces']} peças" if pt else f"{specs['pieces']} pieces")
        if specs.get("polyphony"):
            parts.append(("polifonia " if pt else "polyphony ") + specs["polyphony"])
        if specs.get("color"):
            parts.append(("cor " if pt else "color: ") + specs["color"])
        return ", ".join(parts)

    def search(self, category_ids: list[int] | None = None, max_price: float | None = None,
               min_price: float | None = None, keywords: list[str] | None = None,
               brand: str | None = None, include_unavailable: bool = False, limit: int = 8,
               sort: str = "price_asc") -> pd.DataFrame:
        df = self.products
        if category_ids:
            df = df[df["category_id"].isin(category_ids)]
        if brand:
            df = df[df["brand"].map(normalize) == normalize(brand)]
        if not include_unavailable:
            df = df[df["availability"] == "available"]
        if max_price is not None:
            df = df[df["final_price"] <= max_price + 1e-9]
        if min_price is not None:
            df = df[df["final_price"] >= min_price - 1e-9]
        for kw in keywords or []:
            pattern = normalize(kw)
            haystack = (df["name"] + " " + df["specs"].fillna("")).map(normalize)
            df = df[haystack.str.contains(pattern, regex=False)]
        ascending = sort != "price_desc"
        return df.sort_values(["final_price", "product_id"], ascending=[ascending, True]).head(limit)

    def alternatives(self, pid: int, limit: int = 3) -> list[int]:
        """Available products of the same category with the closest price."""
        row = self.products.loc[pid]
        same = self.products[(self.products["category_id"] == row["category_id"])
                             & (self.products["availability"] == "available")
                             & (self.products["product_id"] != pid)].copy()
        same["distance"] = (same["final_price"] - row["price_brl"]).abs()
        return [int(x) for x in same.sort_values(["distance", "product_id"]).head(limit)["product_id"]]

    def active_promotions(self) -> pd.DataFrame:
        return self.products[self.products["has_promotion"]].sort_values("discount_percent", ascending=False)

    def promotions_for_product(self, pid: int) -> pd.DataFrame:
        return self.promotions[self.promotions["product_id"] == pid]

    def promotions_by_name(self, text: str) -> pd.DataFrame:
        norm = normalize(text)
        names = {n for n in self.promotions["description"].unique() if normalize(n) in norm}
        return self.promotions[self.promotions["description"].isin(names)]

    def category_name(self, cat_id: int) -> str:
        return str(self.categories.loc[cat_id, "category"]) if cat_id in self.categories.index else str(cat_id)
