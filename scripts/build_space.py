"""Package the app as a Hugging Face Space (Gradio SDK) in dist/huggingface-space/.

    python scripts/build_space.py

Then create a Space at https://huggingface.co/new-space (SDK: Gradio), and upload the *contents* of
dist/huggingface-space/ (web "Files > Add file > Upload files", or git push to the Space repository).
Add GEMINI_API_KEY under Settings > Variables and secrets to enable Gemini; without it the Space runs in
deterministic mode. Only the synthetic sample data is included - the original challenge files are not published.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dist" / "huggingface-space"


def gradio_version() -> str:
    try:
        from importlib.metadata import version

        return version("gradio")
    except Exception:
        return "6.31.0"


CARD = """---
title: Empório AI
emoji: 🎸
colorFrom: blue
colorTo: pink
sdk: gradio
sdk_version: {gradio}
app_file: app.py
pinned: false
short_description: Grounded customer-service agent for a music store
---

# Empório AI — live demo

Customer-service agent for *Empório da Música*, a fictional musical-instrument store (Artefact Brasil AI Engineer
technical challenge). Source code, documentation and tests:
https://github.com/ViniciusKanh/emporio-da-musica-ai-agent

This demo runs on the **synthetic sample data** of the repository (the original challenge files are not public).
With the `GEMINI_API_KEY` secret configured the agent uses Gemini with function calling; otherwise it answers with
the deterministic engine. The evidence panel shows which one answered, the sources used and the tokens spent.
Visitors can also paste their own Gemini key in the "Configurações" tab: it applies only to their browser session
and is kept in memory (never written to disk or logs). Set `GEMINI_TOKEN_BUDGET` to cap tokens per visitor session.

**Local model:** not available in this online demo — a model served by Ollama runs on the user's own computer
(`localhost`), which a cloud container cannot reach. Run the project locally to use the chain
Gemini → local model → deterministic engine (see the README in the GitHub repository).
"""


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    shutil.copy(ROOT / "app.py", OUT / "app.py")
    shutil.copytree(ROOT / "src", OUT / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "data" / "sample", OUT / "data" / "sample")
    shutil.copytree(ROOT / "assets", OUT / "assets")
    (OUT / "docs" / "assets").mkdir(parents=True)
    shutil.copy(ROOT / "docs" / "assets" / "architecture.png", OUT / "docs" / "assets" / "architecture.png")
    requirements = [line for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.startswith("#") and not line.startswith("pytest")
                    and not line.startswith("gradio")]  # the Space image provides Gradio (sdk_version)
    requirements.append("spaces")  # ZeroGPU helper; harmless on CPU hardware
    (OUT / "requirements.txt").write_text("\n".join(requirements) + "\n", encoding="utf-8")
    (OUT / "README.md").write_text(CARD.format(gradio=gradio_version()), encoding="utf-8")
    files = sorted(p.relative_to(OUT).as_posix() for p in OUT.rglob("*") if p.is_file())
    print(f"Space ready in {OUT} ({len(files)} files). Upload its contents to a new Gradio Space.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
