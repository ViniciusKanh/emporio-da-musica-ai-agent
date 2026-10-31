"""Text helpers: accent-insensitive normalisation, money parsing/formatting and
very small language detection. Pure functions, no third-party dependencies."""

from __future__ import annotations

import re
import unicodedata

_WORD_RE = re.compile(r"[a-z0-9]+(?:[-/][a-z0-9]+)*")


def strip_accents(text: str) -> str:
    """'Violão Elétrico' -> 'Violao Eletrico'."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize(text: str) -> str:
    """Lower-case, accent-free, single-spaced text used for matching."""
    text = strip_accents(str(text)).lower()
    text = text.replace("’", "'").replace("`", "'")
    return re.sub(r"\s+", " ", text).strip()


def tokenize(text: str) -> list[str]:
    """Tokens keep hyphenated model codes together ('ka-15s', 'd-28')."""
    return _WORD_RE.findall(normalize(text))


def compact(token: str) -> str:
    """'KA-15S' -> 'ka15s'; used to compare model codes typed with/without dashes."""
    return re.sub(r"[^a-z0-9]", "", normalize(token))


def brl(value: float) -> str:
    """Format a number as Brazilian currency: 1234.5 -> 'R$ 1.234,50'."""
    formatted = f"{float(value):,.2f}"
    return "R$ " + formatted.replace(",", "_").replace(".", ",").replace("_", ".")


_MONEY_RE = re.compile(
    r"(?:r\$|brl|reais)?\s*(\d{1,3}(?:[.\s]\d{3})+|\d+)(?:,(\d{1,2}))?(?:\.(\d{1,2}))?\s*(mil|k)?\b"
)


def parse_amount(raw: str) -> float | None:
    """Parse '1.000', '1000', '1.299,90', '1,5 mil', '2k' into a float."""
    text = normalize(raw).replace("r$", "").strip()
    match = re.fullmatch(r"(\d+(?:[.\s]\d{3})*|\d+)(?:[,.](\d{1,2}))?\s*(mil|k)?", text)
    if not match:
        return None
    integer = re.sub(r"[.\s]", "", match.group(1))
    value = float(integer)
    if match.group(2):
        value += float("0." + match.group(2))
    if match.group(3):
        value *= 1000
    return value


def extract_money_values(text: str) -> list[float]:
    """Return every currency amount written as 'R$ x' in a text (for grounding checks)."""
    values = []
    for match in re.finditer(r"R\$\s?([\d.]+(?:,\d{1,2})?)", text):
        raw = match.group(1).rstrip(".")
        value = parse_amount(raw)
        if value is not None:
            values.append(round(value, 2))
    return values


_EN_MARKERS = {
    "the", "is", "are", "do", "does", "you", "your", "what", "which", "how", "much",
    "price", "cost", "costs", "available", "store", "open", "can", "i", "my", "order",
    "return", "refund", "warranty", "shipping", "hours", "where", "have", "sell", "any",
    "under", "below", "between", "guitar", "guitars", "promotion", "promotions", "deals",
    "payment", "pay", "installments", "hello", "hi", "thanks", "please", "want", "need",
    "with", "for", "and", "it", "this", "that", "there", "address", "when", "status",
}
_PT_MARKERS = {
    "o", "a", "os", "as", "de", "do", "da", "dos", "das", "que", "qual", "quais", "quanto",
    "custa", "voces", "vcs", "tem", "loja", "meu", "minha", "pedido", "para", "pra", "com",
    "um", "uma", "e", "nao", "sim", "ola", "oi", "obrigado", "obrigada", "posso", "como",
    "onde", "esta", "estao", "ate", "violao", "violoes", "preco", "horario", "endereco",
    "promocao", "promocoes", "devolver", "troca", "garantia", "frete", "quero", "gostaria",
}


def detect_language(text: str, default: str = "pt") -> str:
    """Tiny stop-word vote between Portuguese and English. Accents imply Portuguese."""
    if re.search(r"[ãõçáéíóúâêô]", text.lower()):
        return "pt"
    tokens = tokenize(text)
    en = sum(t in _EN_MARKERS for t in tokens)
    pt = sum(t in _PT_MARKERS for t in tokens)
    if en > pt:
        return "en"
    if pt > en:
        return "pt"
    return default
