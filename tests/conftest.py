"""Shared fixtures.

* ``agent`` runs on the synthetic fixtures in data/sample (always available, public).
* ``original_agent`` runs on the original challenge files when they were copied to
  data/ (they are not published); tests using it are skipped otherwise.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from emporio_ai.agent import EmporioAgent  # noqa: E402
from emporio_ai.config import DEFAULT_DATA_DIR, SAMPLE_DATA_DIR, _has_dataset  # noqa: E402

ORIGINAL_POLICY = DEFAULT_DATA_DIR / "políticas_da_loja.pdf"
HAS_ORIGINAL = _has_dataset(DEFAULT_DATA_DIR) and ORIGINAL_POLICY.is_file()


@pytest.fixture()
def agent() -> EmporioAgent:
    return EmporioAgent.from_paths(SAMPLE_DATA_DIR, SAMPLE_DATA_DIR / "policies.md", llm_mode="offline")


@pytest.fixture(scope="session")
def _original() -> EmporioAgent:
    if not HAS_ORIGINAL:
        pytest.skip("original challenge files not present in data/ (they are not published)")
    return EmporioAgent.from_paths(DEFAULT_DATA_DIR, ORIGINAL_POLICY, llm_mode="offline")


@pytest.fixture()
def original_agent(_original: EmporioAgent) -> EmporioAgent:
    _original._sessions.clear()
    return _original
