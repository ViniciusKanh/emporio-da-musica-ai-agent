"""One-command launcher for Empório AI.

    python run.py              # setup (if needed) -> tests -> Gradio UI
    python run.py --cli        # setup (if needed) -> tests -> terminal chat
    python run.py --skip-tests # do not run the test suite before starting
    python run.py --test-only  # setup (if needed) -> tests, then exit
    python run.py --offline    # force the deterministic mode (no LLM) even if GEMINI_API_KEY is set
    python run.py --share      # Gradio temporary public link (useful on Colab/remote machines)
    python run.py --eval       # setup (if needed) -> LLM evaluation report (docs/LLM_EVALUATION.md)

What it does, in a single terminal:
1. creates the virtual environment ``.venv`` only if it does not exist;
2. installs requirements only when requirements.txt changed since the last install;
3. runs the automated tests (stops if any test fails);
4. starts the Gradio interface (or the CLI with ``--cli``).
Only the Python standard library is used here, so it runs before anything is installed.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
STAMP = VENV / ".requirements.sha256"


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def step(message: str) -> None:
    print(f"\n==> {message}", flush=True)


def run(cmd: list[str], **kwargs) -> int:
    return subprocess.call(cmd, cwd=ROOT, **kwargs)


def ensure_environment() -> Path:
    python = venv_python()
    if python.exists():
        step(f"Virtual environment found: {VENV.name} (skipping creation)")
    else:
        step(f"Creating virtual environment in {VENV.name} ...")
        venv.EnvBuilder(with_pip=True, clear=False).create(VENV)
    digest = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()
    if STAMP.exists() and STAMP.read_text().strip() == digest:
        step("Dependencies already installed (requirements.txt unchanged)")
    else:
        step("Installing dependencies from requirements.txt (first run may take a few minutes) ...")
        code = run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-q", "--upgrade", "pip"])
        code = code or run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-q",
                            "-r", str(REQUIREMENTS)])
        if code:
            sys.exit("Dependency installation failed. Check your internet connection and try again.")
        STAMP.write_text(digest)
    return python


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up, test and run Empório AI with one command.")
    parser.add_argument("--cli", action="store_true", help="start the terminal chat instead of the web UI")
    parser.add_argument("--skip-tests", action="store_true", help="do not run the tests before starting")
    parser.add_argument("--test-only", action="store_true", help="run the tests and exit")
    parser.add_argument("--offline", action="store_true", help="force deterministic mode (no LLM)")
    parser.add_argument("--share", action="store_true", help="create a temporary public Gradio link")
    parser.add_argument("--eval", action="store_true", help="run the LLM evaluation (needs the original files)")
    args = parser.parse_args()

    if sys.version_info < (3, 10):
        sys.exit(f"Python 3.10+ is required (found {sys.version.split()[0]}).")
    python = ensure_environment()

    data_dir = ROOT / "data"
    has_original = any(data_dir.glob("*.pdf"))
    step("Data: " + ("original challenge files found in data/" if has_original
                     else "using the synthetic fixtures in data/sample/ (copy the original files to data/ to use them)"))
    if os.getenv("GEMINI_API_KEY") or (ROOT / ".env").is_file():
        step("LLM: a .env file or GEMINI_API_KEY was found - Gemini mode will be used if the key is valid")
    else:
        step("LLM: no GEMINI_API_KEY configured - running in deterministic offline mode")

    if not args.skip_tests:
        step("Running automated tests ...")
        if run([str(python), "-m", "pytest", "-q", "-rf", "-p", "no:cacheprovider"]):
            print("\nSome tests failed - fix them before starting the agent (or use --skip-tests).")
            return 1
    if args.test_only:
        return 0
    if args.eval:
        step("Running the evaluation set (writes docs/LLM_EVALUATION.md) ...")
        return run([str(python), "scripts/evaluate_llm.py"])

    extra = ["--offline"] if args.offline else []
    try:
        if args.cli:
            step("Starting the terminal chat (type 'exit' to quit) ...")
            return run([str(python), "run_cli.py", *extra])
        step("Starting the web interface at http://127.0.0.1:7860  (press Ctrl+C to stop) ...")
        return run([str(python), "app.py", *extra, *(["--share"] if args.share else [])])
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
