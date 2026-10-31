"""Command-line chat with the Empório AI agent.

Usage:
    python run_cli.py                      # interactive chat
    python run_cli.py --offline            # force deterministic mode (no LLM)
    python run_cli.py -m "Quanto custa o Takamine GD20?"   # single question
    python run_cli.py --trace              # also print tools and sources
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from emporio_ai.agent import EmporioAgent  # noqa: E402
from emporio_ai.config import Settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Empório AI — customer service agent (CLI)")
    parser.add_argument("-m", "--message", help="ask a single question and exit")
    parser.add_argument("--offline", action="store_true", help="force deterministic mode (no LLM)")
    parser.add_argument("--data-dir", help="folder with the CSV files (default: data/ or data/sample/)")
    parser.add_argument("--trace", action="store_true", help="print intents, tools and sources")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging (PII is redacted)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = Settings.from_env(data_dir=args.data_dir, llm_mode="offline" if args.offline else None)
    agent = EmporioAgent(settings)
    data_label = "synthetic sample fixtures" if settings.uses_sample_data else str(settings.data_dir)
    print(f"Empório AI | mode: {agent.mode} | data: {data_label} | type 'sair' or 'exit' to quit\n")

    def answer(question: str) -> None:
        response = agent.ask(question, session_id="cli")
        print(f"\n🎸 Empório AI [{response.mode}]:\n{response.text}\n")
        if args.trace:
            print(f"   intents: {', '.join(response.intents)}")
            print(f"   tools:   {', '.join(c['name'] for c in response.tool_calls) or '-'}")
            print(f"   sources: {', '.join(response.sources) or '-'}")
            for warning in response.warnings:
                print(f"   warning: {warning}")
            print()

    if args.message:
        answer(args.message)
        return 0
    while True:
        try:
            question = input("Você: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if question.lower() in {"sair", "exit", "quit"}:
            return 0
        if question:
            answer(question)


if __name__ == "__main__":
    raise SystemExit(main())
