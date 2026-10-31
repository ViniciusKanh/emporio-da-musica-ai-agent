"""Gradio interface for the Empório AI agent.

    python app.py                 # http://127.0.0.1:7860
    python app.py --share         # temporary public link (e.g. on Colab)
    python app.py --offline       # force deterministic mode

Tabs: customer chat with an evidence panel, catalog explorer, and "how it works".
"""

from __future__ import annotations

import argparse
import html
import logging
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import gradio as gr  # noqa: E402
import pandas as pd  # noqa: E402

from emporio_ai.agent import EmporioAgent  # noqa: E402
from emporio_ai.config import Settings  # noqa: E402
from emporio_ai.store_hours import describe, open_status  # noqa: E402
from emporio_ai.text_utils import brl  # noqa: E402

QUICK_ACTIONS = [
    ("🎸 Violões até R$ 1000", "Quais opções de violões disponíveis custando até R$1000?"),
    ("🧭 Indicação p/ iniciante", "Qual violão você recomenda para iniciante até 800?"),
    ("🆚 Comparar modelos", "Compare o Yamaha C40 e o Tagima Memphis"),
    ("💸 Promoções ativas", "Quais promoções estão ativas?"),
    ("💳 Preço e PIX", "Quanto custa o Takamine GD20? E no pix?"),
    ("↩️ Devolução", "Me arrependi da minha compra, posso devolver meu pedido?"),
    ("📦 Meu pedido", "Onde está meu pedido 8?"),
    ("🕘 Estão abertos?", "Vocês estão abertos agora?"),
    ("🌎 In English", "How much is the Taylor 110e and is it in stock?"),
]
SAMPLE_QUICK_ACTIONS = [
    ("🎸 Violões até R$ 1000", "Quais violões disponíveis até R$ 1000?"),
    ("🧭 Indicação p/ iniciante", "Qual violão você recomenda para iniciante até 1000?"),
    ("🆚 Comparar modelos", "Compare o Aurora C10 e o Vento VD-50"),
    ("💸 Promoções ativas", "Quais promoções estão ativas?"),
    ("💳 Preço e PIX", "Quanto custa o Aurora F20? E no pix?"),
    ("↩️ Devolução", "Me arrependi da minha compra, posso devolver meu pedido?"),
    ("📦 Meu pedido", "Onde está meu pedido 1002?"),
    ("🕘 Estão abertos?", "Vocês estão abertos agora?"),
    ("🌎 In English", "How much is the Vento VD-90E and is it in stock?"),
]

CSS = """
:root { --navy:#0b2545; --navy-2:#13315c; --magenta:#c2185b; --magenta-soft:#fce4ef; --ink:#1d2433;
        --muted:#5b6475; --line:#e3e7f0; --soft:#f5f7fb; }
.gradio-container { max-width: 1240px !important; margin: auto !important; }
#hero { background: linear-gradient(118deg, var(--navy) 0%, var(--navy-2) 58%, var(--magenta) 100%);
        border-radius: 18px; padding: 22px 26px; color: #fff; box-shadow: 0 10px 30px rgba(11,37,69,.18); }
#hero .brand { display:flex; align-items:center; gap:14px; }
#hero .logo { width:52px; height:52px; border-radius:14px; background:rgba(255,255,255,.14); display:grid;
              place-items:center; font-size:28px; }
#hero h1 { margin:0; font-size:1.55rem; color:#fff; letter-spacing:-.01em; }
#hero .tag { margin:2px 0 0; color:#f6cfe0; font-size:.95rem; }
#hero .pills { margin-top:14px; display:flex; flex-wrap:wrap; gap:8px; }
.pill { display:inline-flex; align-items:center; gap:6px; padding:4px 12px; border-radius:999px; font-size:.8rem;
        font-weight:600; background:rgba(255,255,255,.95); color:var(--navy); }
.pill.open { background:#e7f7ee; color:#13653a; } .pill.closed { background:#fdeceb; color:#a0281c; }
.pill.dot::before { content:""; width:8px; height:8px; border-radius:50%; background:currentColor; }
#chips { flex-wrap:wrap !important; gap:8px !important; }
#chips > button, #chips > div { flex:0 0 auto !important; min-width:0 !important; width:auto !important; }
#chips button { border-radius:999px !important; font-size:.85rem !important; padding:6px 12px !important;
                border:1px solid var(--line) !important; background:#fff !important; color:var(--ink) !important; }
#chips button:hover { border-color:var(--magenta) !important; color:var(--magenta) !important; }
#send { min-width: 110px; }
.evidence { border:1px solid var(--line); border-radius:14px; padding:14px 16px; background:#fff; font-size:.9rem; }
.evidence h4 { margin:0 0 8px; color:var(--navy); font-size:.95rem; }
.evidence .row { margin:6px 0; color:var(--muted); }
.evidence .chip { display:inline-block; margin:2px 4px 2px 0; padding:2px 9px; border-radius:999px; font-size:.78rem;
                  background:var(--soft); color:var(--navy); border:1px solid var(--line); }
.evidence .chip.tool { background:#eef3ff; } .evidence .chip.mode-gemini { background:var(--magenta-soft); color:var(--magenta); }
.evidence .chip.mode-offline { background:#e9eef7; }
.evidence ul { margin:4px 0 0 18px; padding:0; } .evidence li { margin:2px 0; color:var(--ink); }
.evidence .handoff { margin-top:10px; padding:8px 10px; border-radius:10px; background:#fff6e5; color:#7a4b00; }
.evidence .warn { margin-top:8px; padding:8px 10px; border-radius:10px; background:#fdeceb; color:#8a1f12; }
.welcome { text-align:center; color:var(--muted); padding:30px 10px; }
.welcome b { color:var(--navy); }
.note { color:var(--muted); font-size:.85rem; line-height:1.5; }
#catalog-table, #catalog-table *, #promo-table, #promo-table * { font-family:Inter, "Segoe UI", system-ui, sans-serif !important; }
#catalog-table td, #promo-table td { font-size:.86rem !important; }
.kpis { display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:12px; margin:6px 0 4px; }
.kpi { background:#fff; border:1px solid var(--line); border-radius:14px; padding:12px 14px; }
.kpi .v { font-size:1.45rem; font-weight:700; color:var(--navy); } .kpi .l { color:var(--muted); font-size:.82rem; }
.kpi.accent .v { color:var(--magenta); }
@media (max-width: 760px) { .kpis { grid-template-columns:repeat(2, minmax(0,1fr)); } }
"""


def evidence_html(result=None, error: str | None = None) -> str:
    if error:
        return f'<div class="evidence"><h4>Evidências da resposta</h4><div class="warn">{html.escape(error)}</div></div>'
    if result is None:
        return ('<div class="evidence"><h4>Evidências da resposta</h4><div class="row">Envie uma pergunta para ver '
                'o modo usado, as intenções detectadas, as ferramentas consultadas e as fontes de cada resposta.</div></div>')
    mode = "Gemini (function calling)" if result.mode == "gemini" else "Determinístico (sem LLM)"
    intents = "".join(f'<span class="chip">{html.escape(i)}</span>' for i in result.intents) or "—"
    counts: dict[str, int] = {}
    for call in result.tool_calls:
        counts[call["name"]] = counts.get(call["name"], 0) + 1
    tools = "".join(f'<span class="chip tool">{html.escape(name)}{f" ×{n}" if n > 1 else ""}</span>'
                    for name, n in counts.items()) or "—"
    sources = "".join(f"<li>{html.escape(s)}</li>" for s in result.sources) or "<li>—</li>"
    extra = ""
    if result.handoff:
        extra += '<div class="handoff">🤝 Caso encaminhado para atendimento humano.</div>'
    for warning in result.warnings:
        extra += f'<div class="warn">⚠️ {html.escape(warning)}</div>'
    return (f'<div class="evidence"><h4>Evidências da resposta</h4>'
            f'<div class="row">Modo: <span class="chip mode-{result.mode}">{mode}</span></div>'
            f'<div class="row">Intenções: {intents}</div><div class="row">Ferramentas: {tools}</div>'
            f'<div class="row">Fontes:<ul>{sources}</ul></div>{extra}</div>')


def build_ui(agent: EmporioAgent) -> gr.Blocks:
    settings = agent.settings
    sample = settings.uses_sample_data
    data_label = "Dados sintéticos (públicos)" if sample else "Dados originais do desafio"
    mode_label = f"Gemini · {settings.gemini_model}" if agent.mode == "gemini" else "Modo determinístico (sem LLM)"
    actions = SAMPLE_QUICK_ACTIONS if sample else QUICK_ACTIONS
    catalog = agent.catalog
    categories = ["Todas"] + list(catalog.categories["category"])
    max_price = float(catalog.products["final_price"].max())

    def hero() -> str:
        status = open_status(agent.policies.store_info.hours)
        store_pill = ""
        if status:
            cls = "open" if status.is_open else "closed"
            text = "Loja aberta agora" if status.is_open else "Loja fechada agora"
            store_pill = f'<span class="pill dot {cls}" title="{html.escape(describe(status))}">{text}</span>'
        return f"""<div id="hero"><div class="brand"><div class="logo">🎸</div><div>
            <h1>Empório da Música · Assistente Virtual</h1>
            <p class="tag">Sua música começa aqui. Preços, estoque, promoções, pedidos e políticas — sempre com a fonte.</p>
            </div></div><div class="pills">{store_pill}<span class="pill">🤖 {html.escape(mode_label)}</span>
            <span class="pill">🗂️ {html.escape(data_label)}</span><span class="pill">🔒 Ferramentas somente leitura</span>
            </div></div>"""

    def respond(message: str, history: list, session_id: str):
        history = list(history or [])
        message = (message or "").strip()
        if not message:
            return "", history, session_id, gr.update()
        try:
            result = agent.ask(message, session_id=session_id)
            answer, panel = result.text, evidence_html(result)
        except Exception as exc:  # never show a stack trace to the customer
            logging.exception("UI error")
            answer = ("Desculpe, tive um problema técnico para responder agora. Tente novamente em instantes ou fale "
                      "com a nossa equipe pelo WhatsApp.")
            panel = evidence_html(error=f"Erro interno: {type(exc).__name__}")
        history += [{"role": "user", "content": message}, {"role": "assistant", "content": answer}]
        return "", history, session_id, panel

    def reset(session_id: str):
        agent.reset(session_id)
        return [], uuid.uuid4().hex, evidence_html()

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

    def promotions_table() -> pd.DataFrame:
        rows = [{"Produto": r["product"], "Campanha": r["campaign"], "Desconto": f"{r['discount_percent']:g}%",
                 "De": brl(r["list_price"]), "Por": brl(r["promotional_price"]),
                 "Situação": "disponível" if r["availability"] == "available" else "sem estoque"}
                for r in agent._toolbox().list_promotions()["active_promotions"]]
        return pd.DataFrame(rows, columns=["Produto", "Campanha", "Desconto", "De", "Por", "Situação"])

    findings = "\n".join(f"- {i}" for i in agent.store.report.issues) or "- nenhuma"
    conflicts = "\n".join(f"- {c}" for c in agent.policies.store_info.conflicts) or "- nenhum"
    welcome = ("<div class='welcome'><div style='font-size:34px'>🎶</div><b>Olá! Sou o assistente virtual da Empório "
               "da Música.</b><br>Pergunte sobre instrumentos, preços, promoções, pedidos ou políticas da loja — "
               "ou use um dos atalhos abaixo.</div>")

    with gr.Blocks(title="Empório AI — Assistente Virtual") as demo:
        session = gr.State(lambda: uuid.uuid4().hex)
        header = gr.HTML(hero())
        with gr.Tabs():
            with gr.Tab("💬 Atendimento"):
                with gr.Row(equal_height=False):
                    with gr.Column(scale=7):
                        chatbot = gr.Chatbot(height=540, show_label=False, placeholder=welcome, buttons=["copy"])
                        with gr.Row():
                            box = gr.Textbox(placeholder="Digite sua mensagem em português ou inglês…", show_label=False,
                                             scale=6, autofocus=True, max_length=2000, lines=1)
                            send = gr.Button("Enviar", variant="primary", scale=1, elem_id="send")
                        with gr.Row(elem_id="chips"):
                            chips = [gr.Button(label, size="sm") for label, _ in actions]
                    with gr.Column(scale=4):
                        panel = gr.HTML(evidence_html())
                        clear = gr.Button("🧹 Nova conversa", variant="secondary")
                        gr.HTML(
                            "<div class='note'>🔐 Pedidos exigem número <b>e</b> e-mail cadastrado (verificação de "
                            "protótipo, não autenticação real).<br>📦 Status de pedido vem do último registro do "
                            "sistema, não do rastreio em tempo real.<br>🛡️ O assistente não altera pedidos, preços ou "
                            "descontos.</div>")
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
                gr.Markdown("#### 💸 Promoções ativas")
                gr.Dataframe(value=promotions_table(), interactive=False, wrap=True, elem_id="promo-table")
            with gr.Tab("🧠 Como funciona"):
                with gr.Row():
                    with gr.Column(scale=5):
                        gr.Markdown(
                            "### Arquitetura\nCada mensagem passa por **verificação de segurança → escolha do modo "
                            "(Gemini ou determinístico) → ferramentas somente leitura (CSV + manual) → resposta → "
                            "checagem de fundamentação** (todo valor em R$ precisa vir de uma ferramenta; nenhum dado "
                            "pessoal é exibido).\n\n**Recursos de conversa:** perguntas compostas, continuidade "
                            "(\"e no pix?\", \"e até 600?\"), correção de erros de digitação, comparação lado a lado, "
                            "recomendações justificadas pela descrição do produto, status \"aberto agora\", português "
                            "e inglês.")
                        diagram = ROOT / "docs" / "assets" / "architecture.png"
                        if diagram.is_file():
                            gr.Image(str(diagram), show_label=False, height=620)
                    with gr.Column(scale=4):
                        gr.Markdown(f"### Achados de qualidade dos dados\n{findings}\n\n"
                                    f"### Conflitos encontrados no manual\n{conflicts}\n\n"
                                    f"### Fontes\n- {len(agent.policies.chunks)} seções indexadas de "
                                    f"`{settings.policy_path.name}`\n- {agent.store.report.row_counts.get('products', 0)} "
                                    f"produtos, {agent.store.report.row_counts.get('orders', 0)} pedidos")

        send.click(respond, [box, chatbot, session], [box, chatbot, session, panel])
        box.submit(respond, [box, chatbot, session], [box, chatbot, session, panel])
        clear.click(reset, [session], [chatbot, session, panel])
        for chip, (_, question) in zip(chips, actions):
            chip.click(lambda q=question: q, None, box).then(respond, [box, chatbot, session],
                                                             [box, chatbot, session, panel])
        filters = [cat, price, only, query]
        for component in filters:
            component.change(catalog_table, filters, table)
        demo.load(hero, None, header)  # refresh "open now" when the page is opened
    return demo


THEME = gr.themes.Soft(primary_hue="pink", secondary_hue="blue", neutral_hue="slate",
                       font=[gr.themes.Font(f) for f in ("Inter", "Segoe UI", "system-ui", "sans-serif")])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--share", action="store_true", help="create a temporary public Gradio link")
    parser.add_argument("--offline", action="store_true", help="force deterministic mode (no LLM)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    agent = EmporioAgent(Settings.from_env(llm_mode="offline" if args.offline else None))
    build_ui(agent).launch(server_name=args.host, server_port=args.port, share=args.share, theme=THEME, css=CSS)


if __name__ == "__main__":
    main()
