"""Input/output guardrails: PII handling, prompt-injection and abuse heuristics.

These are defence-in-depth heuristics, not a complete security solution. The
primary control is architectural: tools are read-only, there is no tool that
lists customers, and order data requires order id + matching e-mail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .text_utils import normalize

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?55\s?)?\(?\d{2}\)?\s?9?\d{4}[-\s]?\d{4}")
CPF_RE = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")

INJECTION_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|rules|prompts?)",
    r"ignor[ea] (todas |as |suas )*(instruc|regras|orientac)",
    r"esque[cç]a (todas |as |suas )*(instruc|regras)",
    r"disregard (the |all |your )?(rules|instructions|system)",
    r"(system|developer) prompt", r"prompt (do|de) sistema", r"suas instrucoes (internas|ocultas)",
    r"(reveal|show|print|mostre|revele|exiba) (me )?(your|suas|o seu|seu|as) (instructions|prompt|instrucoes|regras)",
    r"you are now", r"voce agora e", r"a partir de agora voce", r"act as (an? )?(admin|developer|dan)",
    r"aja como (um )?(admin|administrador|desenvolvedor)", r"modo (desenvolvedor|admin|deus)",
    r"developer mode", r"jailbreak", r"\bdan\b mode", r"sem restri[cç][oõ]es", r"without (any )?restrictions",
    r"<\s*/?\s*(system|instructions?)\s*>", r"\[\s*system\s*\]",
]
BULK_DATA_PATTERNS = [
    r"(todos|todas|lista|listar|liste) (os |as )?(clientes|e-?mails|pedidos|telefones|cpfs?)",
    r"(dados|e-?mail|telefone|endereco) d[eoa]s? (outros? )?(clientes?|outra pessoa)",
    r"(all|every|list) (the )?(customers?|emails|orders|phone numbers)",
    r"(customer|client) (list|database|data)", r"base de (dados|clientes)", r"\bdump\b",
    r"(export|exportar|baixar) (a )?(tabela|base|csv)", r"select \* from", r"drop table",
]
ACTION_PATTERNS = [
    r"(cancel[ea]r?|cancele) (o |meu |minha )?(pedido|compra)", r"cancel (my )?order",
    r"(alter[ea]r?|mud[ea]r?|troqu?e) (o |meu )?(endereco|pedido|status)", r"change (my |the )?(address|order|status)",
    r"(me )?(d[eê]|aplique|aplica|quero) (um )?desconto de \d+", r"give me (a )?\d+ ?% (discount|off)",
    r"\b(aplica|aplique|aplicar|da|de|me da|me de|quero|consegue|faz|faca|libera)\b.{0,25}\b\d{1,3} ?% (de )?desconto",
    r"desconto (extra|especial|adicional) (pra|para) mim", r"\b(apply|give me|i want) .{0,20}\d{1,3} ?% (discount|off)",
    r"(reembolse|estorne|devolva meu dinheiro)", r"refund (me|my)",
    r"(mude|altere|atualize) o (preco|estoque)", r"(set|change|update) the (price|stock)",
]


@dataclass
class GuardrailReport:
    injection: list[str] = field(default_factory=list)
    bulk_data_request: bool = False
    action_request: bool = False

    @property
    def flagged(self) -> bool:
        return bool(self.injection) or self.bulk_data_request


def inspect_message(text: str) -> GuardrailReport:
    norm = normalize(text)
    report = GuardrailReport()
    report.injection = [p for p in INJECTION_PATTERNS if re.search(p, norm)]
    report.bulk_data_request = any(re.search(p, norm) for p in BULK_DATA_PATTERNS)
    report.action_request = any(re.search(p, norm) for p in ACTION_PATTERNS)
    return report


def redact(text: str) -> str:
    """Mask personal data before it reaches logs or an external provider."""
    text = EMAIL_RE.sub("[e-mail removido]", text)
    text = CPF_RE.sub("[CPF removido]", text)
    text = CARD_RE.sub(lambda m: "[número removido]" if len(re.sub(r"\D", "", m.group())) >= 13 else m.group(), text)
    text = PHONE_RE.sub(lambda m: "[telefone removido]" if len(re.sub(r"\D", "", m.group())) >= 10 else m.group(), text)
    return text


class PiiVault:
    """Replaces e-mails with opaque tokens (<EMAIL_1>) so the LLM never sees them.

    The order tool receives the token and the vault resolves it locally.
    """

    def __init__(self) -> None:
        self._by_token: dict[str, str] = {}
        self._by_value: dict[str, str] = {}

    def tokenize(self, text: str) -> str:
        def replace(match: re.Match) -> str:
            value = match.group(0).strip().lower()
            if value not in self._by_value:
                token = f"<EMAIL_{len(self._by_value) + 1}>"
                self._by_value[value] = token
                self._by_token[token] = value
            return self._by_value[value]

        text = EMAIL_RE.sub(replace, text)
        return CPF_RE.sub("[CPF removido]", text)

    def resolve(self, token_or_value: str) -> str:
        key = (token_or_value or "").strip()
        return self._by_token.get(key, self._by_token.get(key.upper(), key))

    def latest_email(self) -> str | None:
        return next(reversed(self._by_token.values()), None) if self._by_token else None
