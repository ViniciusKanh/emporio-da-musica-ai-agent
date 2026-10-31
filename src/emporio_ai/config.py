"""Runtime configuration, resolved from environment variables (optionally a .env file).

Data resolution order:
1. ``EMPORIO_DATA_DIR`` if set;
2. ``data/`` when the original challenge files were copied there;
3. ``data/sample/`` - synthetic fixtures committed to the public repository.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
SAMPLE_DATA_DIR = DEFAULT_DATA_DIR / "sample"
DEFAULT_MODEL = "gemini-2.5-flash"

# Dataset name -> accepted file names (original challenge naming first).
DATASET_FILES: dict[str, tuple[str, ...]] = {
    name: (f"desafio_tecnico_ai_eng - {name}.csv", f"{name}.csv")
    for name in ("products", "categories", "promotions", "orders", "order_items", "customers")
}
POLICY_FILES: tuple[str, ...] = ("políticas_da_loja.pdf", "politicas_da_loja.pdf", "policies.pdf", "policies.md")


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env reader (KEY=VALUE lines). Existing environment variables win."""
    path = path or PROJECT_ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _has_dataset(directory: Path) -> bool:
    return all(any((directory / f).is_file() for f in files) for files in DATASET_FILES.values())


def resolve_data_dir(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit)
    env_dir = os.getenv("EMPORIO_DATA_DIR")
    if env_dir:
        return Path(env_dir)
    if _has_dataset(DEFAULT_DATA_DIR):
        return DEFAULT_DATA_DIR
    return SAMPLE_DATA_DIR


def resolve_policy_path(data_dir: Path, explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit)
    env_path = os.getenv("EMPORIO_POLICY_PATH")
    if env_path:
        return Path(env_path)
    for name in POLICY_FILES:
        candidate = data_dir / name
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"No policy document found in {data_dir} (expected one of {POLICY_FILES})")


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    policy_path: Path
    gemini_api_key: str | None = field(default=None, repr=False)  # never printed
    gemini_model: str = DEFAULT_MODEL
    llm_mode: str = "auto"  # auto | gemini | offline
    max_tool_rounds: int = 5

    @property
    def uses_sample_data(self) -> bool:
        return self.data_dir.resolve() == SAMPLE_DATA_DIR.resolve()

    @classmethod
    def from_env(cls, data_dir: str | Path | None = None, policy_path: str | Path | None = None,
                 llm_mode: str | None = None) -> "Settings":
        load_dotenv()
        resolved_dir = resolve_data_dir(data_dir)
        mode = (llm_mode or os.getenv("EMPORIO_LLM_MODE", "auto")).strip().lower()
        if mode not in {"auto", "gemini", "offline"}:
            raise ValueError("EMPORIO_LLM_MODE must be one of: auto, gemini, offline")
        key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or None
        return cls(
            data_dir=resolved_dir,
            policy_path=resolve_policy_path(resolved_dir, policy_path),
            gemini_api_key=key,
            gemini_model=os.getenv("GEMINI_MODEL", DEFAULT_MODEL),
            llm_mode=mode,
            max_tool_rounds=int(os.getenv("EMPORIO_MAX_TOOL_ROUNDS", "5")),
        )
