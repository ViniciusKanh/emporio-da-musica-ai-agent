"""Policy retrieval (the "R" in RAG) over the store policy manual.

Design (deliberately small and auditable):
* text extraction with ``pypdf`` in layout mode, page by page (page numbers kept);
* structure-aware chunking: one chunk per numbered (sub)section of the manual;
* lexical BM25 ranking with accent folding, light prefix stemming and a PT/EN
  synonym table - an 8-page manual does not justify a vector database;
* structured extraction of store facts (address, phones, e-mail, hours) with
  explicit detection of contradictory values inside the manual.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .business_rules import BusinessRules
from .text_utils import normalize, tokenize

STOPWORDS = {
    "a", "o", "as", "os", "de", "do", "da", "dos", "das", "e", "em", "no", "na", "nos", "nas", "um",
    "uma", "para", "pra", "por", "com", "que", "qual", "quais", "se", "ao", "aos", "ou", "eu", "meu",
    "minha", "voce", "voces", "vcs", "ser", "sao", "sobre", "como", "mais", "the", "is", "are", "of",
    "to", "in", "on", "and", "or", "my", "your", "you", "i", "it", "do", "does", "can", "what", "how",
    "which", "for", "posso", "pode", "tem", "ter", "loja", "store", "emporio", "musica",
}

# Query expansion: customer wording (PT and EN) -> vocabulary used in the manual.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "devolver": ("devolucao", "arrependimento", "reembolso"), "devolvo": ("devolucao", "arrependimento"),
    "arrependi": ("arrependimento", "devolucao"), "desistir": ("arrependimento", "devolucao"),
    "return": ("devolucao", "arrependimento"), "returns": ("devolucao", "arrependimento"),
    "refund": ("reembolso", "devolucao"), "regret": ("arrependimento",),
    "exchange": ("troca",), "trocar": ("troca",), "swap": ("troca",),
    "defeito": ("defeito", "garantia"), "defect": ("defeito", "garantia"), "defective": ("defeito", "garantia"),
    "broken": ("defeito", "garantia"), "quebrado": ("defeito", "garantia"), "estragado": ("defeito",),
    "warranty": ("garantia",), "guarantee": ("garantia",),
    "horario": ("horario", "funcionamento"), "abre": ("horario", "funcionamento"), "abrem": ("horario",),
    "fecha": ("horario", "funcionamento"), "funciona": ("funcionamento", "horario"),
    "hours": ("horario", "funcionamento"), "open": ("horario", "funcionamento"), "opening": ("horario",),
    "sabado": ("sabado", "horario"), "saturday": ("sabado", "horario"), "sunday": ("domingo", "horario"),
    "domingo": ("domingo", "horario"), "feriado": ("feriados", "horario"), "holiday": ("feriados", "horario"),
    "pagar": ("pagamento",), "pay": ("pagamento",), "payment": ("pagamento", "formas"),
    "parcelar": ("parcelamento", "parcela"), "parcelas": ("parcelamento",), "installments": ("parcelamento",),
    "installment": ("parcelamento",), "card": ("cartao",), "credit": ("credito", "cartao"),
    "cartao": ("cartao", "credito"), "boleto": ("boleto",), "pix": ("pix",),
    "frete": ("frete", "entrega"), "shipping": ("frete", "entrega"), "delivery": ("entrega", "prazo"),
    "entregam": ("entrega", "frete"), "entrega": ("entrega", "frete"), "ship": ("frete", "entrega"),
    "rastrear": ("rastreamento",), "rastreio": ("rastreamento",), "tracking": ("rastreamento",),
    "promocao": ("promocoes", "descontos"), "desconto": ("descontos", "promocoes", "cumulatividade"),
    "promotion": ("promocoes",), "discount": ("descontos", "cumulatividade"), "cumulativo": ("cumulatividade",),
    "endereco": ("endereco",), "address": ("endereco",), "telefone": ("telefone", "whatsapp"),
    "phone": ("telefone", "whatsapp"), "whatsapp": ("whatsapp",), "email": ("e-mail",),
    "privacidade": ("privacidade", "lgpd", "dados"), "privacy": ("privacidade", "lgpd", "dados"),
    "data": ("dados",), "acessorio": ("acessorios",), "accessories": ("acessorios",),
}


def _stem(token: str) -> str:
    return token[:5] if len(token) > 5 else token


def analyze(text: str, expand: bool = False) -> list[str]:
    terms: list[str] = []
    for tok in tokenize(text):
        if tok in STOPWORDS:
            continue
        terms.append(_stem(tok))
        if expand:
            terms.extend(_stem(t) for s in SYNONYMS.get(tok, ()) for t in tokenize(s))
    return terms


@dataclass
class PolicyChunk:
    chunk_id: str
    section: str
    title: str
    pages: tuple[int, ...]
    text: str

    @property
    def page_label(self) -> str:
        return "p. " + "-".join(str(p) for p in (self.pages[0], self.pages[-1]) if p) if len(set(self.pages)) > 1 \
            else f"p. {self.pages[0]}"

    def citation(self) -> str:
        return f"Manual de Políticas §{self.section} ({self.page_label})"


@dataclass
class StoreInfo:
    address: str | None = None
    phones: list[tuple[str, str]] = field(default_factory=list)  # (number, context)
    emails: list[str] = field(default_factory=list)
    hours: list[tuple[str, str]] = field(default_factory=list)  # (days, hours)
    conflicts: list[str] = field(default_factory=list)
    pages: dict[str, int] = field(default_factory=dict)


_HEADING_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2})?)\.?\s+([A-ZÀ-Ú][^:]{2,70})$")


def _read_pages(path: Path) -> list[str]:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader  # imported lazily: optional for markdown fixtures

        reader = PdfReader(str(path))
        return [page.extract_text(extraction_mode="layout") or "" for page in reader.pages]
    text = path.read_text(encoding="utf-8")
    pages = re.split(r"<!--\s*page\s*\d+\s*-->", text)
    return [p for p in pages if p.strip()] or [text]


def _clean_lines(page_text: str) -> list[str]:
    lines: list[str] = []
    for raw in page_text.splitlines():
        line = raw.strip()
        if not line or re.fullmatch(r"P[áa]gina\s+\d+", line):
            continue
        if line.startswith("Empório da Música") and "Manual de Políticas" in line:
            continue  # running header
        line = re.sub(r"^#+\s*", "", line)  # markdown headings in fixtures
        line = re.sub(r"^[•\-*]\s+", "- ", line)  # bullets
        line = re.sub(r"\s{2,}", " | ", line)  # layout tables -> pipe separated
        line = line.replace("**", "")
        lines.append(line)
    return lines


class PolicyKnowledgeBase:
    def __init__(self, path: str | Path, rules: BusinessRules | None = None):
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(f"Policy document not found: {self.path}")
        self.rules = rules or BusinessRules()
        self.pages = _read_pages(self.path)
        self.chunks = self._chunk()
        if not self.chunks:
            raise ValueError("No text could be extracted from the policy document")
        self._build_index()
        self.store_info = self._extract_store_info()
        self.missing_rule_anchors = self.verify_rules()

    # ----------------------------------------------------------- chunking
    def _chunk(self) -> list[PolicyChunk]:
        chunks: list[PolicyChunk] = []
        current: dict | None = None
        top_titles: dict[str, str] = {}

        def flush() -> None:
            if current and len(" ".join(current["lines"])) > 20:
                section = current["section"]
                parent = section.split(".")[0]
                title = current["title"]
                if "." in section and parent in top_titles:
                    title = f"{top_titles[parent]} > {title}"
                body = "\n".join(current["lines"])
                chunks.append(PolicyChunk(f"sec-{section}", section, title,
                                          tuple(sorted(set(current["pages"]))), body))

        for page_no, page in enumerate(self.pages, start=1):
            for line in _clean_lines(page):
                heading = _HEADING_RE.match(line)
                if heading and not line.endswith("."):
                    flush()
                    section, title = heading.group(1), heading.group(2).strip()
                    if "." not in section:
                        top_titles[section] = title
                    current = {"section": section, "title": title, "pages": [page_no], "lines": [line]}
                    continue
                if current is None:
                    current = {"section": "0", "title": "Capa / identificação", "pages": [page_no], "lines": []}
                if page_no not in current["pages"]:
                    current["pages"].append(page_no)
                if current["lines"] and not re.match(r"^(-|\d+\.|\|)", line) and current["lines"][-1].startswith("-"):
                    current["lines"][-1] += " " + line  # wrapped bullet
                else:
                    current["lines"].append(line)
        flush()
        return chunks

    # -------------------------------------------------------------- BM25
    def _build_index(self, k1: float = 1.4, b: float = 0.75) -> None:
        self._k1, self._b = k1, b
        self._docs = [analyze(c.title + " " + c.title + " " + c.text) for c in self.chunks]
        self._tf = [Counter(d) for d in self._docs]
        self._avgdl = sum(len(d) for d in self._docs) / len(self._docs)
        df = Counter(term for doc in self._docs for term in set(doc))
        n = len(self._docs)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, top_k: int = 3, min_score: float = 0.5) -> list[dict]:
        terms = analyze(query, expand=True)
        scored = []
        for chunk, tf, doc in zip(self.chunks, self._tf, self._docs):
            score = 0.0
            for term in set(terms):
                if term not in tf:
                    continue
                freq = tf[term]
                denom = freq + self._k1 * (1 - self._b + self._b * len(doc) / self._avgdl)
                score += self._idf[term] * freq * (self._k1 + 1) / denom
            if score >= min_score:
                scored.append((score, chunk))
        scored.sort(key=lambda x: (-x[0], x[1].chunk_id))
        return [{"chunk_id": c.chunk_id, "section": c.section, "title": c.title, "pages": list(c.pages),
                 "citation": c.citation(), "score": round(s, 3), "text": c.text} for s, c in scored[:top_k]]

    def get_section(self, section: str) -> PolicyChunk | None:
        return next((c for c in self.chunks if c.section == section), None)

    def find_section(self, keyword: str) -> PolicyChunk | None:
        key = normalize(keyword)
        return next((c for c in self.chunks if key in normalize(c.title)), None)

    # -------------------------------------------------- structured facts
    def _page_of(self, needle: str) -> int:
        for i, page in enumerate(self.pages, start=1):
            if needle in normalize(page):
                return i
        return 0

    def _extract_store_info(self) -> StoreInfo:
        info = StoreInfo()
        full = "\n".join(" ".join(_clean_lines(p)) for p in self.pages)
        flat = re.sub(r"\s*\|\s*", " ", full)
        addr = re.search(r"Endere[çc]o\s+(.+?\d{5}-?\d{3})", flat)
        if addr:
            info.address = re.sub(r"\s+", " ", addr.group(1)).strip()
            info.pages["address"] = self._page_of("endereco")
        labels: dict[str, set[str]] = {}
        for match in re.finditer(r"\(\d{2}\)\s?\d{4,5}\s?-\s?\d{4}", flat):
            number = re.sub(r"\s?-\s?", "-", match.group(0))
            context = normalize(flat[max(0, match.start() - 40):match.start()])
            found = labels.setdefault(number, set())
            if "whatsapp" in context:
                found.add("WhatsApp")
            if "telefone" in context or not found:
                found.add("Telefone")
        for number, kinds in labels.items():
            if len(labels) > 1 and "WhatsApp" in kinds and "Telefone" in kinds:
                kinds = {"Telefone/WhatsApp"}
            info.phones.append((number, "/".join(sorted(kinds))))
        whatsapp_numbers = [n for n, k in info.phones if "WhatsApp" in k]
        if len(whatsapp_numbers) > 1:
            info.conflicts.append("whatsapp: the manual lists more than one WhatsApp number: "
                                  + ", ".join(whatsapp_numbers))
        emails = list(dict.fromkeys(re.findall(r"[\w.\-À-ÿ]+@[\w.\-À-ÿ]+\.[a-z]{2,}", flat)))
        ascii_emails = [e for e in emails if e.isascii()]
        if len(emails) > 1:
            info.conflicts.append("email: the manual spells the contact e-mail in more than one way: "
                                  + ", ".join(emails))
        info.emails = ascii_emails or emails
        for days, value in re.findall(
                r"(Segunda a Sexta-feira|Segunda a Sexta|S[áa]bado|Domingo e Feriados|Domingo)\s+"
                r"(\d{2}:\d{2}\s+[àa]s\s+\d{2}:\d{2}|Fechado)", flat):
            if (days, value) not in info.hours:
                info.hours.append((days, value))
        if info.hours:
            info.pages["hours"] = self._page_of("horario de funcionamento")
        info.pages["contacts"] = self._page_of("whatsapp")
        return info

    def verify_rules(self) -> list[str]:
        """Return BusinessRules fields whose anchor text is NOT found in the document."""
        corpus = normalize(" ".join(" ".join(_clean_lines(p)) for p in self.pages))
        corpus = re.sub(r"\s*\|\s*", " ", corpus)
        return [name for name, anchor in self.rules.anchors().items() if normalize(anchor) not in corpus]
