"""Gradio chat interface for the Empório AI agent.

    python app.py                 # http://127.0.0.1:7860
    python app.py --share         # temporary public link (e.g. on Colab)
    python app.py --offline       # force deterministic mode
"""

from __future__ import annotations

import argparse
import html
import logging
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import gradio as gr  # noqa: E402

from emporio_ai.agent import EmporioAgent  # noqa: E402
from emporio_ai.config import Settings  # noqa: E402

EXAMPLES = [
    "Quais opções de violões disponíveis custando até R$1000?",
    "Quanto custa o Takamine GD20?",
    "Quais promoções estão ativas?",
    "Me arrependi da minha compra, posso devolver meu pedido?",
    "Quanto custa o Taylor 110e e qual o horário de sábado?",
    "Vocês vendem palhetas?",
    "How much is the Yamaha C40 and is it in stock?",
    "Onde está meu pedido 8?",
]

CSS = """
:root { --emporio-navy: #0b2545; --emporio-magenta: #c2185b; }
.gradio-container { max-width: 1180px !important; margin: auto; }
#header { background: linear-gradient(120deg, var(--emporio-navy), #13315c 60%, var(--emporio-magenta));
          color: #fff; border-radius: 14px; padding: 18px 22px; }
#header h1 { color: #fff; margin: 0 0 4px 0; font-size: 1.55rem; }
#header p { color: #f3d6e4; margin: 0; }
.badge { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: .8rem; font-weight: 600;
         background: #fff; color: var(--emporio-navy); margin-left: 6px; }
#trace { font-size: .86rem; }
"""


def build_ui(agent: EmporioAgent) -> gr.Blocks:
    settings = agent.settings
    data_label = "synthetic sample fixtures (public)" if settings.uses_sample_data else "original challenge snapshot"
    mode_label = f"Gemini · {settings.gemini_model}" if agent.mode == "gemini" else "Offline deterministic (no LLM)"

    def respond(message: str, history: list, session_id: str):
        history = list(history or [])
        message = (message or "").strip()
        if not message:
            return "", history, session_id, gr.update()
        try:
            result = agent.ask(message, session_id=session_id)
            answer = result.text
            trace = (f"**Mode:** {result.mode}  \n**Intents:** {', '.join(result.intents)}  \n"
                     f"**Tools:** {', '.join(c['name'] for c in result.tool_calls) or '—'}  \n"
                     f"**Sources:** {', '.join(result.sources) or '—'}"
                     + ("  \n**Human handoff suggested**" if result.handoff else "")
                     + "".join(f"  \n⚠️ {html.escape(w)}" for w in result.warnings))
        except Exception as exc:  # never show a stack trace to the customer
            logging.exception("UI error")
            answer = ("Desculpe, tive um problema técnico para responder agora. Tente novamente em instantes ou fale "
                      "com a nossa equipe pelo WhatsApp.")
            trace = f"⚠️ internal error: {type(exc).__name__}"
        history += [{"role": "user", "content": message}, {"role": "assistant", "content": answer}]
        return "", history, session_id, trace

    def reset(session_id: str):
        agent.reset(session_id)
        new_id = uuid.uuid4().hex
        return [], new_id, "_New conversation started._"

    with gr.Blocks(title="Empório AI — Customer Service Agent") as demo:
        session = gr.State(lambda: uuid.uuid4().hex)
        gr.HTML(f"""<div id="header"><h1>🎸 Empório da Música — Assistente Virtual</h1>
            <p>Catálogo, preços, promoções, pedidos e políticas da loja
            <span class="badge">{html.escape(mode_label)}</span>
            <span class="badge">Data: {html.escape(data_label)}</span></p></div>""")
        with gr.Row():
            with gr.Column(scale=3):
                chatbot = gr.Chatbot(height=520, label="Conversa", buttons=["copy"])
                with gr.Row():
                    box = gr.Textbox(placeholder="Digite sua mensagem (português ou inglês)…", show_label=False,
                                     scale=5, autofocus=True, max_length=2000)
                    send = gr.Button("Enviar", variant="primary", scale=1)
                gr.Examples(EXAMPLES, inputs=box, label="Exemplos")
            with gr.Column(scale=2):
                gr.Markdown("### Trace (para avaliação)")
                trace = gr.Markdown("_Ask something to see the selected tools and sources._", elem_id="trace")
                clear = gr.Button("Nova conversa")
                gr.Markdown(
                    "**Notes**\n\n- Answers come from the CSV snapshot and the policy manual; the assistant never "
                    "changes orders or prices.\n- Order status requires order number **and** the e-mail registered on "
                    "the order (prototype check, not production authentication).\n- Order status is a data snapshot, "
                    "not real-time carrier tracking.")
        send.click(respond, [box, chatbot, session], [box, chatbot, session, trace])
        box.submit(respond, [box, chatbot, session], [box, chatbot, session, trace])
        clear.click(reset, [session], [chatbot, session, trace])
    return demo


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--share", action="store_true", help="create a temporary public Gradio link")
    parser.add_argument("--offline", action="store_true", help="force deterministic mode (no LLM)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    agent = EmporioAgent(Settings.from_env(llm_mode="offline" if args.offline else None))
    build_ui(agent).launch(server_name=args.host, server_port=args.port, share=args.share,
                           theme=gr.themes.Soft(primary_hue="pink", secondary_hue="blue"), css=CSS)


if __name__ == "__main__":
    main()
