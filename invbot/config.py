"""Loads config.yaml (settings) and environment variables (secrets)."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

DEFAULT_PATH = Path("config.yaml")
EXAMPLE_PATH = Path(__file__).resolve().parent.parent / "config.example.yaml"


def load_config(path: str | Path | None = None) -> dict:
    path = Path(path) if path else DEFAULT_PATH
    if not path.exists():
        path = EXAMPLE_PATH
    with open(path) as f:
        cfg = yaml.safe_load(f)
    _load_dotenv(Path(".env"))
    return cfg


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader so we don't need python-dotenv. Real env vars win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def secret(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None
