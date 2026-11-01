"""Gradio interface for the Empório AI agent.

    python app.py                 # http://127.0.0.1:7860
    python app.py --share         # temporary public link (e.g. on Colab)
    python app.py --offline       # force deterministic mode

Tabs: customer chat with the virtual attendant "Vinicius" and an evidence panel, catalog explorer, policy
retrieval explorer, "how it works" and a settings tab (LLM provider, API key, local model, fallback and
token budget/usage - all per browser session, kept in memory only).
"""

from __future__ import annotations

import argparse
import base64
import html
import logging
import os
import re
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

try:  # Hugging Face ZeroGPU hardware stops a Space that declares no @spaces.GPU function at start-up.
    import spaces  # noqa: E402 - only present on Hugging Face Spaces

    @spaces.GPU(duration=1)
    def _zerogpu_placeholder() -> None:
        """Never called: the agent needs no GPU (tools are pandas + BM25; LLMs are reached over HTTP)."""
except ImportError:
    spaces = None

import gradio as gr  # noqa: E402
import pandas as pd  # noqa: E402

from emporio_ai.agent import EmporioAgent, ProviderConfig, chain_label, chain_mode  # noqa: E402
from emporio_ai.config import Settings  # noqa: E402
from emporio_ai.store_hours import describe, open_status  # noqa: E402
from emporio_ai.text_utils import brl  # noqa: E402
from emporio_ai.usage import PROVIDER_LABELS, CallUsage  # noqa: E402

BRAND = ROOT / "assets" / "brand"
AVATAR_BOT = BRAND / "assistente_vinicius.png"
AVATAR_USER = BRAND / "cliente.png"
MARK = BRAND / "emporio_mark.png"

QUICK_ACTIONS = [
    ("🎸 Violões até R$ 1000", "Quais opções de violões disponíveis custando até R$1000?"),
    ("🧭 Indicação p/ iniciante", "Qual violão você recomenda para iniciante até 800?"),
    ("🆚 Comparar modelos", "Compare o Yamaha C40 e o Tagima Memphis"),
    ("🏷️ Promoções ativas", "Quais promoções estão ativas?"),
    ("💳 Preço e PIX", "Quanto custa o Takamine GD20? E no pix?"),
    ("📦 Meu pedido", "Onde está meu pedido 8?"),
    ("↩️ Devolução", "Me arrependi da minha compra, posso devolver meu pedido?"),
    ("🕘 Estão abertos?", "Vocês estão abertos agora?"),
    ("🌐 In English", "How much is the Taylor 110e and is it in stock?"),
]
SAMPLE_QUICK_ACTIONS = [
    ("🎸 Violões até R$ 1000", "Quais violões disponíveis até R$ 1000?"),
    ("🧭 Indicação p/ iniciante", "Qual violão você recomenda para iniciante até 1000?"),
    ("🆚 Comparar modelos", "Compare o Aurora C10 e o Vento VD-50"),
    ("🏷️ Promoções ativas", "Quais promoções estão ativas?"),
    ("💳 Preço e PIX", "Quanto custa o Aurora F20? E no pix?"),
    ("📦 Meu pedido", "Onde está meu pedido 1002?"),
    ("↩️ Devolução", "Me arrependi da minha compra, posso devolver meu pedido?"),
    ("🕘 Estão abertos?", "Vocês estão abertos agora?"),
    ("🌐 In English", "How much is the Vento VD-90E and is it in stock?"),
]
GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro"]
LOCAL_PRESETS = {"Ollama": "http://localhost:11434/v1", "LM Studio": "http://localhost:1234/v1",
                 "llama.cpp server": "http://localhost:8080/v1"}
PRIMARY_CHOICES = [("Automático — Gemini, depois modelo local, depois determinístico", "auto"),
                   ("Gemini (online)", "gemini"),
                   ("Modelo local (Ollama · LM Studio · llama.cpp)", "local"),
                   ("Determinístico (sem LLM)", "offline")]


def _data_uri(path: Path) -> str:
    if not path.is_file():
        return ""
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


ICONS = {  # small inline line icons (stroke uses currentColor)
    "mail": '<path d="M4 6h16v12H4z"/><path d="m4 7 8 6 8-6"/>',
    "db": '<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6"/>'
          '<path d="M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"/>',
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    "shield": '<path d="M12 3 5 6v6c0 4.4 3 7.6 7 9 4-1.4 7-4.6 7-9V6z"/><path d="m9 12 2 2 4-4"/>',
    "layers": '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>',
    "gauge": '<path d="M4 18a8 8 0 1 1 16 0"/><path d="m12 18 4-6"/>',
    "key": '<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9M17 6l3 3"/>',
    "chip": '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4'
            'M18 9h4M18 15h4"/>',
}


def icon(name: str, size: int = 20) -> str:
    return (f'<svg class="ic" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONS[name]}</svg>')


CSS = """
:root { --navy:#0B1E3A; --violet:#6C38AA; --magenta:#EC1681; --magenta-soft:#fde7f2; --ink:#18213a;
        --muted:#5f6880; --line:#e6e8f0; --soft:#f6f7fb; --ok:#13804a; }
body, .gradio-container { background:#f7f8fc !important; }
.gradio-container { max-width:1280px !important; margin:auto !important; }
#hero { background:linear-gradient(100deg, var(--navy) 0%, #1d2257 42%, var(--violet) 72%, var(--magenta) 100%);
        border-radius:20px; padding:22px 28px 20px; color:#fff; box-shadow:0 14px 34px rgba(11,30,58,.22); }
#hero .brand { display:flex; align-items:center; gap:18px; }
#hero .logo { width:68px; height:68px; border-radius:16px; background:rgba(255,255,255,.10); padding:6px;
              box-shadow:inset 0 0 0 1px rgba(255,255,255,.16); }
#hero .sep { width:1px; align-self:stretch; background:rgba(255,255,255,.25); }
#hero h1 { margin:0; font-size:1.7rem; color:#fff; letter-spacing:-.01em; font-weight:700; }
#hero .tag { margin:4px 0 0; color:#eef0ff; font-size:.95rem; opacity:.92; }
#hero .pills { margin-top:16px; display:flex; flex-wrap:wrap; gap:10px; }
.pill { display:inline-flex; align-items:center; gap:7px; padding:6px 14px; border-radius:999px; font-size:.84rem;
        font-weight:600; background:#fff; color:var(--navy); box-shadow:0 2px 6px rgba(0,0,0,.08); }
.pill.open { color:var(--ok); } .pill.closed { color:#c0172d; background:#ffecef; }
.pill.dot::before { content:""; width:9px; height:9px; border-radius:50%; background:currentColor; }
.tab-wrapper button, .tabs button[role="tab"] { font-size:1rem !important; font-weight:600 !important; }
button[role="tab"][aria-selected="true"] { color:var(--magenta) !important; border-color:var(--magenta) !important; }
.card { background:#fff; border:1px solid var(--line); border-radius:18px; padding:16px 18px;
        box-shadow:0 6px 18px rgba(16,24,64,.05); }
.card h4 { margin:0 0 10px; color:var(--navy); font-size:1.02rem; display:flex; align-items:center; gap:8px; }
.card h4 .ic { color:var(--magenta); }
#chat-card { background:#fff; border:1px solid var(--line); border-radius:18px; padding:12px 14px 14px;
             box-shadow:0 6px 18px rgba(16,24,64,.05); }
.attendant { display:flex; align-items:center; gap:12px; padding:4px 4px 10px; border-bottom:1px solid var(--line); }
.attendant img { width:46px; height:46px; border-radius:50%; object-fit:cover; box-shadow:0 0 0 3px var(--magenta-soft); }
.attendant .who b { color:var(--navy); font-size:1.02rem; } .attendant .who span { color:var(--muted); font-size:.83rem; }
.attendant .status { margin-left:auto; font-size:.8rem; color:var(--ok); font-weight:600; display:flex; gap:6px;
                     align-items:center; }
.attendant .status::before { content:""; width:8px; height:8px; border-radius:50%; background:var(--ok);
                             animation:pulse 1.8s infinite; }
.attendant .engine { font-size:.75rem; color:var(--muted); background:var(--soft); border:1px solid var(--line);
                     border-radius:999px; padding:3px 10px; }
@keyframes pulse { 0%,100% { opacity:1 } 50% { opacity:.35 } }
#chatbot { border:none !important; box-shadow:none !important; background:transparent !important; }
#chatbot div.user.message { background:var(--magenta-soft) !important; border:1px solid #f7c6de !important;
     color:var(--ink) !important; border-radius:16px 16px 4px 16px !important; }
#chatbot div.bot.message { background:#fff !important; border:1px solid var(--line) !important; color:var(--ink) !important;
     border-radius:16px 16px 16px 4px !important; box-shadow:0 2px 8px rgba(16,24,64,.05) !important; }
#chatbot div.message > div.message, #chatbot [data-testid="user"], #chatbot [data-testid="bot"] {
     background:transparent !important; border:none !important; box-shadow:none !important; }
#chatbot .avatar-container { width:42px !important; height:42px !important; flex:0 0 42px !important; }
#chatbot .avatar-container img { border-radius:50% !important; width:42px !important; height:42px !important;
     object-fit:cover !important; padding:0 !important; }
.typing { display:inline-flex; align-items:center; gap:10px; color:var(--muted); font-size:.9rem; }
.typing .dots { display:inline-flex; gap:4px; }
.typing .dots i { width:8px; height:8px; border-radius:50%; background:var(--magenta); display:inline-block;
                  animation:blink 1.2s infinite ease-in-out; }
.typing .dots i:nth-child(2) { animation-delay:.2s; } .typing .dots i:nth-child(3) { animation-delay:.4s; }
@keyframes blink { 0%,80%,100% { transform:translateY(0); opacity:.35 } 40% { transform:translateY(-5px); opacity:1 } }
#composer { margin-top:6px; align-items:stretch; }
#composer textarea { border-radius:12px !important; font-size:.98rem !important; }
#send { min-width:120px; border-radius:12px !important; font-weight:700 !important;
        background:linear-gradient(90deg, var(--magenta), #c4126d) !important; color:#fff !important; border:none !important; }
#chips { flex-wrap:wrap !important; gap:8px !important; }
#chips > button, #chips > div { flex:0 0 auto !important; min-width:0 !important; width:auto !important; }
#chips button { border-radius:999px !important; font-size:.86rem !important; padding:7px 14px !important;
                border:1px solid var(--line) !important; background:#fff !important; color:var(--ink) !important;
                box-shadow:0 1px 3px rgba(16,24,64,.06) !important; font-weight:500 !important; }
#chips button:hover { border-color:var(--magenta) !important; color:var(--magenta) !important; }
#newchat { background:var(--magenta-soft) !important; color:var(--magenta) !important; border:1px solid #f7c6de !important;
           font-weight:700 !important; border-radius:14px !important; font-size:1rem !important; }
.evidence .row { display:grid; grid-template-columns:96px 1fr; gap:8px; margin:7px 0; color:var(--ink);
                 align-items:start; font-size:.9rem; }
.evidence .row > span:first-child { color:var(--muted); }
.chip { display:inline-block; margin:1px 4px 3px 0; padding:2px 10px; border-radius:999px; font-size:.78rem;
        background:#eef1fb; color:var(--navy); border:1px solid #dfe4f5; }
.chip.tool { background:#f1ecfb; border-color:#e2d6f5; color:#4b2a80; }
.chip.mode-gemini, .chip.mode-local { background:var(--magenta-soft); color:#b3105f; border-color:#f7c6de; }
.evidence ul { margin:2px 0 0 18px; padding:0; } .evidence li { margin:2px 0; color:var(--ink); font-size:.88rem; }
.notice { margin-top:10px; padding:9px 11px; border-radius:12px; font-size:.84rem; line-height:1.45; }
.notice.handoff { background:#fff6e5; color:#7a4b00; } .notice.summary { background:#eef3ff; color:var(--ink); }
.notice.warn { background:#fff0f3; color:#8a1f30; } .notice.fallback { background:#f1ecfb; color:#4b2a80; }
.trust .item { display:grid; grid-template-columns:30px 1fr; gap:10px; padding:10px 0; border-top:1px solid var(--line);
               font-size:.9rem; color:var(--ink); line-height:1.45; }
.trust .item:first-of-type { border-top:none; } .trust .item .ic { color:var(--magenta); margin-top:2px; }
.trust small { color:var(--muted); }
.welcome { text-align:center; color:var(--muted); padding:26px 10px; }
.welcome img { width:92px; height:92px; border-radius:50%; box-shadow:0 0 0 5px var(--magenta-soft); }
.welcome b { color:var(--navy); font-size:1.05rem; }
.note { color:var(--muted); font-size:.86rem; line-height:1.5; }
.policy-hit { border:1px solid var(--line); border-radius:12px; padding:12px 14px; margin:8px 0; background:#fff; }
.policy-hit .cite { color:var(--magenta); font-weight:600; font-size:.85rem; }
.policy-hit .score { float:right; color:var(--muted); font-size:.78rem; }
.policy-hit pre { white-space:pre-wrap; font-family:inherit; margin:6px 0 0; color:var(--ink); font-size:.88rem; }
#catalog-table, #catalog-table *, #promo-table, #promo-table * { font-family:Inter, "Segoe UI", system-ui, sans-serif !important; }
#catalog-table td, #promo-table td { font-size:.86rem !important; }
.kpis { display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:12px; margin:6px 0 4px; }
.kpi { background:#fff; border:1px solid var(--line); border-radius:16px; padding:14px 16px; }
.kpi .v { font-size:1.5rem; font-weight:700; color:var(--navy); } .kpi .l { color:var(--muted); font-size:.82rem; }
.kpi.accent .v { color:var(--magenta); }
.usage { overflow-x:auto; } .usage table { width:100%; border-collapse:collapse; font-size:.82rem; }
.usage th { text-align:left; color:var(--muted); font-weight:600; padding:6px 4px; border-bottom:1px solid var(--line); }
.usage td { padding:7px 4px; border-bottom:1px solid var(--line); color:var(--ink); }
.usage .bar { height:8px; border-radius:99px; background:#eceff7; overflow:hidden; margin-top:4px; }
.usage .bar i { display:block; height:100%; background:linear-gradient(90deg, var(--violet), var(--magenta)); }
.usage .chain { margin:4px 0 10px; font-size:.88rem; color:var(--ink); }
.status-ok { color:var(--ok); font-weight:600; } .status-err { color:#c0172d; font-weight:600; }
.cfg-group { border:1px solid var(--line) !important; border-radius:16px !important; background:#fff !important; }
@media (max-width:760px) { .kpis { grid-template-columns:repeat(2, minmax(0,1fr)); } #hero h1 { font-size:1.25rem; } }
"""

WARNING_PT = [
    (re.compile(r"LLM unavailable: (.+?) \(token budget / quota exhausted\)"),
     r"\1 sem cota ou limite de tokens atingido — usei a próxima opção."),
    (re.compile(r"LLM unavailable: (.+?) \((.+?)\).*"), r"\1 indisponível (\2) — usei a próxima opção."),
    (re.compile(r"LLM answer failed the grounding check \((.+?)\).*"),
     r"A resposta do \1 não passou na checagem de fundamentação — usei a próxima opção."),
    (re.compile(r"deterministic answer returned instead"), "Resposta gerada pelo motor determinístico."),
]


def warning_pt(text: str) -> str:
    for pattern, repl in WARNING_PT:
        if pattern.search(text):
            return pattern.sub(repl, text)
    return text


def mode_text(mode: str, label: str | None = None) -> str:
    if mode == "offline":
        return "Determinístico (sem LLM)"
    return label or PROVIDER_LABELS.get(mode, mode)


def evidence_html(result=None, error: str | None = None) -> str:
    head = f'<div class="card evidence"><h4>{icon("layers")} Evidências da resposta</h4>'
    if error:
        return head + f'<div class="notice warn">{html.escape(error)}</div></div>'
    if result is None:
        return head + ('<div class="note">Envie uma pergunta para ver o modo usado, as intenções detectadas, as '
                       'ferramentas consultadas, as fontes e os tokens de cada resposta.</div></div>')
    intents = "".join(f'<span class="chip">{html.escape(i)}</span>' for i in result.intents) or "—"
    counts: dict[str, int] = {}
    for call in result.tool_calls:
        counts[call["name"]] = counts.get(call["name"], 0) + 1
    tools = "".join(f'<span class="chip tool">{html.escape(name)}{f" ×{n}" if n > 1 else ""}</span>'
                    for name, n in counts.items()) or "—"
    sources = "".join(f"<li>{html.escape(s)}</li>" for s in result.sources) or "<li>—</li>"
    usage = result.usage or {}
    tokens = "—"
    if usage:
        total = sum(u["total_tokens"] for u in usage.values())
        parts = " · ".join(f'{PROVIDER_LABELS.get(k, k)}: {u["prompt_tokens"]:,} entrada / {u["completion_tokens"]:,} '
                           f'saída' for k, u in usage.items()).replace(",", ".")
        tokens = f'<b>{total:,}</b> <span class="note">({parts})</span>'.replace(",", ".")
    extra = ""
    if result.fallback_used:
        extra += ('<div class="notice fallback">↪️ Fallback acionado: ' +
                  "<br>".join(html.escape(warning_pt(w)) for w in result.warnings) + "</div>")
    elif result.warnings:
        extra += "".join(f'<div class="notice warn">⚠️ {html.escape(warning_pt(w))}</div>' for w in result.warnings)
    if result.handoff:
        extra += '<div class="notice handoff">🤝 Caso encaminhado para atendimento humano.</div>'
        if getattr(result, "handoff_summary", None):
            note = html.escape(result.handoff_summary).replace("\n", "<br>")
            extra += (f'<div class="notice summary"><b>📝 Resumo para a equipe</b> (§7.3 — registrar e encaminhar; '
                      f'não é enviado automaticamente)<br>{note}</div>')
    return (head + f'<div class="row"><span>Modo:</span><span><span class="chip mode-{result.mode}">'
            f'{html.escape(mode_text(result.mode, result.provider_label))}</span></span></div>'
            f'<div class="row"><span>Intenções:</span><span>{intents}</span></div>'
            f'<div class="row"><span>Ferramentas:</span><span>{tools}</span></div>'
            f'<div class="row"><span>Tokens:</span><span>{tokens}</span></div>'
            f'<div class="row"><span>Fontes:</span><span><ul>{sources}</ul></span></div>{extra}</div>')


def typing_html(name: str) -> str:
    return (f'<span class="typing"><span class="dots"><i></i><i></i><i></i></span>{html.escape(name)} está '
            f'digitando…</span>')


def stream_chunks(text: str, max_steps: int = 28):
    """Progressive reveal of an answer (word boundaries; never splits a markdown table row)."""
    if "|" in text or len(text) < 60:
        yield text
        return
    words = re.split(r"(\s+)", text)
    step = max(1, len(words) // max_steps)
    for i in range(step, len(words), step):
        yield "".join(words[:i])
    yield text


def usage_html(agent: EmporioAgent, session_id: str) -> str:
    providers = agent.session_providers(session_id)
    chain = " → ".join([html.escape(chain_label([p])) for p in providers] + ["Determinístico"])
    snap = agent.session_usage(session_id).snapshot()
    rows = ""
    for name in ("gemini", "local"):
        u = snap.get(name)
        if not u:
            continue
        budget = u.get("budget")
        bar = ""
        if budget:
            pct = min(100, round(100 * u["total_tokens"] / budget))
            bar = f'<div class="bar"><i style="width:{pct}%"></i></div><small class="note">{pct}% de {budget:,}</small>'
        rows += (f"<tr><td><b>{PROVIDER_LABELS[name]}</b></td><td>{u['requests']}</td><td>{u['answers']}</td>"
                 f"<td>{u['prompt_tokens']:,}</td><td>{u['completion_tokens']:,}</td><td><b>{u['total_tokens']:,}</b>"
                 f"{bar}</td><td>{u['errors']}" + (f" ({u['quota_errors']} cota)" if u["quota_errors"] else "")
                 + "</td></tr>").replace(",", ".")
    table = (f'<table><tr><th>Provedor</th><th>Requisições</th><th>Respostas</th><th>Entrada</th><th>Saída</th>'
             f'<th>Total</th><th>Falhas</th></tr>{rows}</table>' if rows else
             '<div class="note">Nenhum token consumido nesta sessão ainda.</div>')
    return (f'<div class="card usage"><h4>{icon("gauge")} Consumo de tokens desta sessão</h4>'
            f'<div class="chain"><b>Cadeia ativa:</b> {chain}</div>{table}'
            '<div class="note" style="margin-top:8px">Contagem informada pelo próprio provedor (Gemini '
            '<code>usage_metadata</code> · OpenAI-compatível <code>usage</code>). Quando o limite é atingido, o '
            'provedor é pulado e a próxima opção da cadeia responde.</div></div>')


def build_ui(agent: EmporioAgent) -> gr.Blocks:
    settings = agent.settings
    name = settings.assistant_name
    sample = settings.uses_sample_data
    data_label = "Dados sintéticos (públicos)" if sample else "Dados do desafio"
    actions = SAMPLE_QUICK_ACTIONS if sample else QUICK_ACTIONS
    catalog = agent.catalog
    categories = ["Todas"] + list(catalog.categories["category"])
    max_price = float(catalog.products["final_price"].max())
    defaults = agent.config
    mark_uri, avatar_uri = _data_uri(MARK), _data_uri(AVATAR_BOT)

    def hero(session_id: str | None = None) -> str:
        providers = agent.session_providers(session_id) if session_id else agent.providers
        mode = chain_mode(providers)
        mode_label = "Modo determinístico (sem LLM)" if mode == "offline" else chain_label(providers[:1])
        status = open_status(agent.policies.store_info.hours)
        store_pill = ""
        if status:
            cls = "open" if status.is_open else "closed"
            text = "Loja aberta agora" if status.is_open else "Loja fechada agora"
            store_pill = f'<span class="pill dot {cls}" title="{html.escape(describe(status))}">{text}</span>'
        logo = f'<img class="logo" src="{mark_uri}" alt="Empório da Música">' if mark_uri else ""
        return f"""<div id="hero"><div class="brand">{logo}<div class="sep"></div><div>
            <h1>Empório da Música • Assistente Virtual</h1>
            <p class="tag">O {html.escape(name)}, assistente virtual do Empório da Música, responde dúvidas sobre preços,
            estoque, promoções, pedidos e políticas, com respostas fundamentadas em fontes oficiais.</p></div></div>
            <div class="pills">{store_pill}<span class="pill">🤖 {html.escape(mode_label)}</span>
            <span class="pill">📁 {html.escape(data_label)}</span><span class="pill">🔒 Ferramentas somente leitura</span>
            </div></div>"""

    def attendant(session_id: str | None = None) -> str:
        providers = agent.session_providers(session_id) if session_id else agent.providers
        engine = "IA generativa + ferramentas" if providers else "respostas determinísticas"
        img = f'<img src="{avatar_uri}" alt="Avatar do assistente virtual {html.escape(name)}">' if avatar_uri else ""
        return (f'<div class="attendant">{img}<div class="who"><b>{html.escape(name)}</b><br>'
                f'<span>Assistente virtual · Empório da Música</span></div><span class="engine">{engine}</span>'
                f'<span class="status">online</span></div>')

    def respond(message: str, history: list, session_id: str):
        history = list(history or [])
        message = (message or "").strip()
        if not message:
            yield "", history, gr.update(), gr.update()
            return
        history.append({"role": "user", "content": message})
        yield "", history + [{"role": "assistant", "content": typing_html(name)}], gr.update(), gr.update()
        started = time.monotonic()
        try:
            result = agent.ask(message, session_id=session_id)
            answer, panel = result.text, evidence_html(result)
        except Exception as exc:  # never show a stack trace to the customer
            logging.exception("UI error")
            answer = ("Desculpe, tive um problema técnico para responder agora. Tente novamente em instantes ou fale "
                      "com a nossa equipe pelo WhatsApp.")
            panel = evidence_html(error=f"Erro interno: {type(exc).__name__}")
        time.sleep(max(0.0, 0.7 - (time.monotonic() - started)))  # let the typing indicator be seen
        for partial in stream_chunks(answer):
            yield "", history + [{"role": "assistant", "content": partial}], gr.update(), gr.update()
            time.sleep(0.03)
        history.append({"role": "assistant", "content": answer})
        yield "", history, panel, usage_html(agent, session_id)

    def reset(session_id: str):
        agent.reset(session_id, keep_settings=True)
        return [], evidence_html()

    # ------------------------------------------------------------- settings
    def make_config(primary, key, gmodel, gbudget, url, lmodel, lkey, timeout, lbudget, fallback) -> ProviderConfig:
        return ProviderConfig(
            primary=primary or "auto",
            gemini_api_key=(key or "").strip() or defaults.gemini_api_key,
            gemini_model=(gmodel or defaults.gemini_model).strip(),
            local_base_url=(url or "").strip() or None,
            local_model=(lmodel or defaults.local_model).strip(),
            local_api_key=(lkey or "").strip() or defaults.local_api_key,
            local_timeout=float(timeout or defaults.local_timeout),
            fallback_to_local=bool(fallback),
            gemini_token_budget=int(gbudget) if gbudget and gbudget > 0 else None,
            local_token_budget=int(lbudget) if lbudget and lbudget > 0 else None)

    def config_problems(cfg: ProviderConfig) -> list[str]:
        problems = []
        if cfg.primary == "gemini" and not cfg.gemini_api_key:
            problems.append("Gemini escolhido, mas nenhuma chave foi informada (nem no .env).")
        if cfg.primary == "local" and not cfg.local_base_url:
            problems.append("Modelo local escolhido, mas a URL do servidor está vazia.")
        if cfg.primary in {"auto", "gemini"} and cfg.fallback_to_local and not cfg.local_base_url:
            problems.append("Fallback para o modelo local ligado, mas sem URL do servidor local — o fallback "
                            "será direto para o motor determinístico.")
        return problems

    def apply(session_id, *values):
        cfg = make_config(*values)
        try:
            providers = agent.configure_session(session_id, cfg)
        except Exception as exc:  # e.g. google-genai missing; never echo the key
            return (f'<div class="notice warn">Não foi possível aplicar: {html.escape(type(exc).__name__)}.</div>',
                    gr.update(), gr.update(), usage_html(agent, session_id))
        notes = "".join(f'<div class="notice warn">⚠️ {html.escape(p)}</div>' for p in config_problems(cfg))
        chain = " → ".join([chain_label([p]) for p in providers] + ["Determinístico"])
        msg = (f'<div class="notice fallback"><span class="status-ok">✔ Configuração aplicada a esta sessão.</span>'
               f'<br>Cadeia: {html.escape(chain)}<br><small>A chave fica apenas na memória desta sessão do navegador; '
               f'não é gravada em disco nem registrada em log.</small></div>{notes}')
        return msg, hero(session_id), attendant(session_id), usage_html(agent, session_id)

    def restore(session_id):
        agent.configure_session(session_id, None)
        return ('<div class="notice fallback">Configuração padrão (.env / variáveis de ambiente) restaurada.</div>',
                hero(session_id), attendant(session_id), usage_html(agent, session_id))

    def test_provider(which: str, session_id, *values):
        cfg = make_config(*values)
        if which == "gemini" and not cfg.gemini_api_key:
            return '<div class="notice warn">Informe a chave do Gemini (ou configure GEMINI_API_KEY no .env).</div>', \
                usage_html(agent, session_id)
        if which == "local" and not cfg.local_base_url:
            return '<div class="notice warn">Informe a URL do servidor local.</div>', usage_html(agent, session_id)
        call = CallUsage()
        meter = agent.session_usage(session_id)
        try:
            chain = agent.build_providers(ProviderConfig(**{**vars(cfg), "primary": which, "fallback_to_local": False}))
            reply = chain[0].ping(call)
            meter.record(which, call, ok=True)
            status = (f'<span class="status-ok">✔ {PROVIDER_LABELS[which]} respondeu</span> '
                      f'(<code>{html.escape(chain[0].model)}</code>): “{html.escape(reply)}” · {call.total_tokens} tokens')
        except Exception as exc:
            meter.record(which, call, ok=False)
            detail = html.escape(str(exc).replace(cfg.gemini_api_key or "\0", "***")[:220])
            status = f'<span class="status-err">✖ Falhou:</span> {html.escape(type(exc).__name__)} — {detail}'
            if which == "local" and "404" in str(exc):
                status += ("<br><small>404 = o servidor não tem esse modelo. Clique em <b>Detectar modelos</b> e "
                           "escolha um da lista, ou confira o nome com <code>ollama list</code>.</small>")
            elif which == "local" and "cannot reach" in str(exc):
                status += ("<br><small>Servidor local fora do ar: abra o Ollama/LM Studio (ou rode "
                           "<code>ollama serve</code>) e confira a URL.</small>")
        return f'<div class="notice summary">{status}</div>', usage_html(agent, session_id)

    def list_models(which: str, current: str, *values):
        cfg = make_config(*values)
        try:
            chain = agent.build_providers(ProviderConfig(**{**vars(cfg), "primary": which, "fallback_to_local": False}))
            if not chain:
                raise ValueError("configure a chave/URL primeiro")
            models = chain[0].list_models()
        except Exception as exc:
            return gr.update(), (f'<div class="notice warn">Não consegui listar os modelos: '
                                 f'{html.escape(type(exc).__name__)} — {html.escape(str(exc)[:160])}</div>')
        if not models:
            return gr.update(), '<div class="notice warn">O servidor não retornou modelos.</div>'
        value = current if current in models else models[0]
        return (gr.update(choices=models, value=value),
                f'<div class="notice fallback">{len(models)} modelo(s) encontrado(s).</div>')

    def reset_usage(session_id):
        agent.session_usage(session_id).reset()
        return usage_html(agent, session_id)

    # ------------------------------------------------------------- catalog
    def catalog_table(category: str, limit: float, only_available: bool, text: str) -> pd.DataFrame:
        df = catalog.products
        if category and category != "Todas":
            df = df[df["category"] == category]
        df = df[df["final_price"] <= float(limit) + 1e-9]
        if only_available:
            df = df[df["availability"] == "available"]
        if text and text.strip():
            ids = set(catalog.match_products(text).product_ids)
            mask = df["name"].str.lower().str.contains(text.strip().lower(), regex=False) | df["product_id"].isin(ids)
            df = df[mask]
        rows = []
        for pid in df.sort_values(["category", "final_price"]).index:
            v = catalog.product_view(int(pid))
            promo = v.get("promotion")
            rows.append({
                "Produto": v["name"], "Categoria": v["category"],
                "Preço": brl(v["final_price"]),
                "De": brl(v["list_price"]) if promo else "—",
                "Promoção": f"{promo['discount_percent']:g}% · {promo['name']}" if promo else "—",
                "PIX": brl(v["pix_price"]) if v.get("pix_price") else ("não cumulativo" if promo and
                                                                         v["availability"] == "available" else "—"),
                "Cartão": f"{v['card']['installments']}x {brl(v['card']['installment_value'])}" if v.get("card") else "—",
                "Situação": v["availability_label"], "Estoque": v["stock_quantity"],
            })
        return pd.DataFrame(rows, columns=["Produto", "Categoria", "Preço", "De", "Promoção", "PIX", "Cartão",
                                           "Situação", "Estoque"])

    def policy_search(query: str) -> str:
        if not query or not query.strip():
            return "<p class='note'>Digite uma dúvida para ver quais seções do manual o agente recupera.</p>"
        hits = agent.policies.search(query, top_k=3)
        if not hits:
            return ("<p class='note'>Nenhuma seção relevante encontrada — o agente trataria a pergunta como fora do "
                    "escopo das políticas.</p>")
        return "".join(f'<div class="policy-hit"><span class="score">BM25 {h["score"]:.2f}</span>'
                       f'<span class="cite">{html.escape(h["citation"])}</span> — <b>{html.escape(h["title"])}</b>'
                       f'<pre>{html.escape(h["text"])}</pre></div>' for h in hits)

    def promotions_table() -> pd.DataFrame:
        rows = [{"Produto": r["product"], "Campanha": r["campaign"], "Desconto": f"{r['discount_percent']:g}%",
                 "De": brl(r["list_price"]), "Por": brl(r["promotional_price"]),
                 "Situação": "disponível" if r["availability"] == "available" else "sem estoque"}
                for r in agent._toolbox().list_promotions()["active_promotions"]]
        return pd.DataFrame(rows, columns=["Produto", "Campanha", "Desconto", "De", "Por", "Situação"])

    findings = "\n".join(f"- {i}" for i in agent.store.report.issues) or "- nenhuma"
    conflicts = "\n".join(f"- {c}" for c in agent.policies.store_info.conflicts) or "- nenhum"
    welcome = (f"<div class='welcome'>{f'<img src={avatar_uri!r} alt=avatar>' if avatar_uri else ''}<br><br>"
               f"<b>Oi! Eu sou o {html.escape(name)}, assistente virtual do Empório da Música.</b><br>Pergunte sobre "
               "instrumentos, preços, promoções, pedidos ou políticas da loja — ou use um dos atalhos abaixo.</div>")
    trust = (f'<div class="card trust"><h4>{icon("shield")} Informações e confiança</h4>'
             f'<div class="item">{icon("mail")}<div>Pedidos exigem número <b>e</b> e-mail cadastrado<br><small>(verificação '
             f'de protótipo, não autenticação real).</small></div></div>'
             f'<div class="item">{icon("db")}<div>Status de pedido vem do último registro do sistema, não do rastreio '
             f'em tempo real.</div></div>'
             f'<div class="item">{icon("lock")}<div>O assistente não altera pedidos, preços ou descontos.</div></div>'
             f'<div class="item">{icon("chip")}<div>{html.escape(name)} é um assistente virtual (IA), não uma pessoa — '
             f'casos sensíveis vão para a equipe humana.</div></div></div>')
    avatars = (str(AVATAR_USER) if AVATAR_USER.is_file() else None, str(AVATAR_BOT) if AVATAR_BOT.is_file() else None)

    with gr.Blocks(title="Empório da Música — Assistente Virtual") as demo:
        session = gr.State(lambda: uuid.uuid4().hex)
        header = gr.HTML(hero())
        with gr.Tabs():
            with gr.Tab("💬 Atendimento"):
                with gr.Row(equal_height=False):
                    with gr.Column(scale=7, elem_id="chat-card"):
                        attendant_bar = gr.HTML(attendant())
                        chatbot = gr.Chatbot(height=470, show_label=False, placeholder=welcome, buttons=["copy"],
                                             elem_id="chatbot", avatar_images=avatars, layout="bubble",
                                             feedback_options=None)
                        with gr.Row(elem_id="composer"):
                            box = gr.Textbox(placeholder="Digite sua mensagem em português ou inglês…", show_label=False,
                                             scale=6, autofocus=True, max_length=2000, lines=1, container=False)
                            send = gr.Button("➤ Enviar", variant="primary", scale=1, elem_id="send")
                        with gr.Row(elem_id="chips"):
                            chips = [gr.Button(label, size="sm") for label, _ in actions]
                    with gr.Column(scale=4):
                        panel = gr.HTML(evidence_html())
                        clear = gr.Button("＋ Nova conversa", elem_id="newchat")
                        gr.HTML(trust)
            with gr.Tab("🎸 Catálogo"):
                products = catalog.products
                kpis = [(len(products), "produtos no cadastro", ""),
                        (int((products["availability"] == "available").sum()), "disponíveis para venda", ""),
                        (int(products["has_promotion"].sum()), "promoções ativas", "accent"),
                        (int((products["availability"] != "available").sum()), "indisponíveis (sem estoque, "
                                                                               "descontinuados ou em breve)", "")]
                gr.HTML('<div class="kpis">' + "".join(f'<div class="kpi {c}"><div class="v">{v}</div>'
                                                       f'<div class="l">{l}</div></div>' for v, l, c in kpis) + "</div>")
                gr.Markdown("Explore os produtos com os **mesmos dados e regras** que o assistente usa "
                            "(promoções vigentes, PIX não cumulativo, parcelamento sem juros por parcela mínima).")
                with gr.Row():
                    cat = gr.Dropdown(categories, value="Todas", label="Categoria", scale=2)
                    price = gr.Slider(0, max_price, value=max_price, step=50, label="Preço máximo (R$)", scale=3)
                    only = gr.Checkbox(value=True, label="Somente disponíveis", scale=1)
                    query = gr.Textbox(label="Buscar por nome ou modelo", placeholder="ex.: Yamaha, GD20, tenor",
                                       scale=2)
                table = gr.Dataframe(value=catalog_table("Todas", max_price, True, ""), interactive=False, wrap=True,
                                     elem_id="catalog-table",
                                     column_widths=["22%", "11%", "10%", "9%", "13%", "10%", "10%", "9%", "6%"])
                gr.Markdown("#### 🏷️ Promoções ativas")
                gr.Dataframe(value=promotions_table(), interactive=False, wrap=True, elem_id="promo-table")
            with gr.Tab("📄 Políticas"):
                gr.Markdown("Veja **o que o agente recupera do manual de políticas** (RAG) antes de responder: as 3 "
                            "seções mais relevantes, com seção e página para citação.")
                with gr.Row():
                    pquery = gr.Textbox(label="Pergunta sobre políticas", scale=5,
                                        placeholder="ex.: posso devolver um produto comprado online?")
                    pbtn = gr.Button("Buscar no manual", variant="primary", scale=1)
                gr.Examples(["Me arrependi da compra, posso devolver?", "Qual o prazo de garantia?",
                             "O desconto do PIX vale em produto em promoção?", "Frete para outra cidade",
                             "Posso parcelar em 12x?", "Can I return an item?"], inputs=pquery, label="Exemplos")
                presults = gr.HTML(policy_search(""))
                with gr.Accordion(f"Todas as {len(agent.policies.chunks)} seções indexadas", open=False):
                    gr.Markdown("\n".join(f"- **{c.citation()}** — {c.title}" for c in agent.policies.chunks))
            with gr.Tab("ⓘ Como funciona"):
                with gr.Row():
                    with gr.Column(scale=5):
                        gr.Markdown(
                            "### Arquitetura\nCada mensagem passa por **verificação de segurança → cadeia de "
                            "provedores (Gemini → modelo local → motor determinístico) → ferramentas somente leitura "
                            "(CSV + manual) → resposta → checagem de fundamentação** (todo valor em R$ precisa vir de "
                            "uma ferramenta; nenhum dado pessoal é exibido).\n\n**Recursos de conversa:** perguntas "
                            "compostas, continuidade (\"e no pix?\", \"e até 600?\") mesmo quando o provedor muda, "
                            "correção de erros de digitação, comparação lado a lado, recomendações justificadas pela "
                            "descrição do produto, status \"aberto agora\", português e inglês.")
                        diagram = ROOT / "docs" / "assets" / "architecture.png"
                        if diagram.is_file():
                            gr.Image(str(diagram), show_label=False, height=620)
                    with gr.Column(scale=4):
                        gr.Markdown(f"### Achados de qualidade dos dados\n{findings}\n\n"
                                    f"### Conflitos encontrados no manual\n{conflicts}\n\n"
                                    "### Provedores suportados\n"
                                    "| Provedor | Como configurar |\n|---|---|\n"
                                    "| **Gemini** (online, `google-genai`) | aba ⚙️ Configurações ou `GEMINI_API_KEY` |\n"
                                    "| **Local / OpenAI-compatível** (Ollama, LM Studio, llama.cpp — modelos do "
                                    "Hugging Face) | aba ⚙️ Configurações ou `LOCAL_LLM_BASE_URL` |\n"
                                    "| **Determinístico** (sem LLM) | sempre a última camada: sem provedor, falha, cota "
                                    "esgotada ou resposta reprovada na checagem |\n\n"
                                    f"### Fontes\n- {len(agent.policies.chunks)} seções indexadas de "
                                    f"`{settings.policy_path.name}`\n- {agent.store.report.row_counts.get('products', 0)} "
                                    f"produtos, {agent.store.report.row_counts.get('orders', 0)} pedidos")
            with gr.Tab("⚙️ Configurações"):
                gr.Markdown("Escolha qual modelo responde, configure a chave do Gemini e o modelo local, defina limites "
                            "de tokens e o fallback. **Vale só para esta sessão do navegador** — nada é gravado em disco. "
                            "Para tornar permanente, use o arquivo `.env` (veja `.env.example`).")
                with gr.Row(equal_height=False):
                    with gr.Column(scale=6):
                        with gr.Group(elem_classes="cfg-group"):
                            primary = gr.Radio(PRIMARY_CHOICES, value=defaults.primary, label="Provedor principal")
                            fallback = gr.Checkbox(value=defaults.fallback_to_local,
                                                   label="Se o Gemini falhar (sem cota/tokens, limite atingido, erro de "
                                                         "rede ou resposta reprovada), usar o modelo local")
                            gr.Markdown("<span class='note'>O motor determinístico é sempre a última camada — o chat "
                                        "nunca fica sem resposta.</span>")
                        with gr.Accordion("✨ Gemini (online)", open=True):
                            key_info = ("Chave do .env carregada — deixe em branco para usá-la."
                                        if defaults.gemini_api_key else "Crie em aistudio.google.com/apikey.")
                            gkey = gr.Textbox(label="GEMINI_API_KEY", type="password", info=key_info,
                                              placeholder="Cole sua chave (fica só na memória desta sessão)")
                            with gr.Row():
                                gmodel = gr.Dropdown(sorted(set(GEMINI_MODELS + [defaults.gemini_model])),
                                                     value=defaults.gemini_model, label="Modelo", allow_custom_value=True,
                                                     scale=3)
                                gbudget = gr.Number(value=defaults.gemini_token_budget or 0, precision=0, minimum=0,
                                                    label="Limite de tokens na sessão (0 = sem limite)", scale=2)
                            with gr.Row():
                                glist = gr.Button("Listar modelos", size="sm")
                                gtest = gr.Button("Testar conexão", size="sm", variant="secondary")
                            gstatus = gr.HTML()
                        with gr.Accordion("🖥️ Modelo local / OpenAI-compatível", open=True):
                            with gr.Row():
                                preset = gr.Dropdown(list(LOCAL_PRESETS) + ["Personalizado"], value="Personalizado",
                                                     label="Servidor", scale=2)
                                lurl = gr.Textbox(value=defaults.local_base_url or "", label="URL base (/v1)",
                                                  placeholder="http://localhost:11434/v1", scale=4)
                            with gr.Row():
                                lmodel = gr.Dropdown([defaults.local_model], value=defaults.local_model,
                                                     label="Modelo", allow_custom_value=True, scale=3)
                                lbudget = gr.Number(value=defaults.local_token_budget or 0, precision=0, minimum=0,
                                                    label="Limite de tokens na sessão (0 = sem limite)", scale=2)
                            with gr.Row():
                                lkey = gr.Textbox(label="Chave (opcional)", type="password", scale=3,
                                                  placeholder="só para endpoints online compatíveis")
                                ltimeout = gr.Slider(10, 300, value=defaults.local_timeout, step=10,
                                                     label="Timeout (s)", scale=2)
                            with gr.Row():
                                llist = gr.Button("Detectar modelos", size="sm")
                                ltest = gr.Button("Testar conexão", size="sm", variant="secondary")
                            lstatus = gr.HTML()
                        with gr.Row():
                            apply_btn = gr.Button("Aplicar configurações", variant="primary")
                            restore_btn = gr.Button("Restaurar padrão (.env)")
                        cfg_status = gr.HTML()
                    with gr.Column(scale=5):
                        usage_box = gr.HTML(usage_html(agent, "__preview__"))
                        reset_usage_btn = gr.Button("Zerar contadores", size="sm")
                        gr.HTML(f'<div class="card"><h4>{icon("key")} Segurança das chaves</h4><div class="note">'
                                "• A chave digitada aqui fica apenas na memória do servidor, ligada a esta sessão; "
                                "não é salva em disco, não aparece em logs e não é enviada ao modelo.<br>"
                                "• Em uma demonstração pública, cada visitante usa a própria chave sem afetar os "
                                "demais.<br>• Prefira uma chave com cota limitada e use o limite de tokens acima."
                                "<br>• Erros de cota (HTTP 429 / RESOURCE_EXHAUSTED) acionam o fallback automaticamente."
                                "</div></div>")

        form = [primary, gkey, gmodel, gbudget, lurl, lmodel, lkey, ltimeout, lbudget, fallback]
        chat_io = ([box, chatbot, session], [box, chatbot, panel, usage_box])
        send.click(respond, *chat_io)
        box.submit(respond, *chat_io)
        clear.click(reset, [session], [chatbot, panel])
        for chip, (_, question) in zip(chips, actions):
            chip.click(lambda q=question: q, None, box).then(respond, *chat_io)
        pbtn.click(policy_search, pquery, presults)
        pquery.submit(policy_search, pquery, presults)
        filters = [cat, price, only, query]
        for component in filters:
            component.change(catalog_table, filters, table)
        apply_btn.click(apply, [session, *form], [cfg_status, header, attendant_bar, usage_box])
        restore_btn.click(restore, [session], [cfg_status, header, attendant_bar, usage_box])
        gtest.click(lambda s, *v: test_provider("gemini", s, *v), [session, *form], [gstatus, usage_box])
        ltest.click(lambda s, *v: test_provider("local", s, *v), [session, *form], [lstatus, usage_box])
        glist.click(lambda cur, *v: list_models("gemini", cur, *v), [gmodel, *form], [gmodel, gstatus])
        llist.click(lambda cur, *v: list_models("local", cur, *v), [lmodel, *form], [lmodel, lstatus])
        preset.change(lambda p, cur: LOCAL_PRESETS.get(p, cur), [preset, lurl], lurl)
        reset_usage_btn.click(reset_usage, [session], usage_box)
        # refresh "open now" and the session-specific panels when the page is opened
        demo.load(lambda s: (hero(s), attendant(s), usage_html(agent, s)), [session],
                  [header, attendant_bar, usage_box])
    return demo


THEME = gr.themes.Soft(primary_hue="pink", secondary_hue="purple", neutral_hue="slate",
                       font=[gr.themes.Font(f) for f in ("Inter", "Segoe UI", "system-ui", "sans-serif")])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--share", action="store_true", help="create a temporary public Gradio link")
    parser.add_argument("--offline", action="store_true", help="force deterministic mode (no LLM)")
    # Hosted environments such as Hugging Face Spaces set GRADIO_SERVER_NAME=0.0.0.0 (PORT alone also implies it).
    if os.getenv("PORT") and not os.getenv("GRADIO_SERVER_NAME"):
        os.environ["GRADIO_SERVER_NAME"] = "0.0.0.0"
    parser.add_argument("--host", default=os.getenv("GRADIO_SERVER_NAME", "127.0.0.1"))
    # Render, Railway, Cloud Run and similar hosts pass the port in PORT.
    parser.add_argument("--port", type=int, default=int(os.getenv("GRADIO_SERVER_PORT") or os.getenv("PORT") or "7860"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    agent = EmporioAgent(Settings.from_env(llm_mode="offline" if args.offline else None))
    favicon = MARK if MARK.is_file() else None
    build_ui(agent).launch(server_name=args.host, server_port=args.port, share=args.share, theme=THEME, css=CSS,
                           favicon_path=favicon, allowed_paths=[str(BRAND)])


if __name__ == "__main__":
    main()
