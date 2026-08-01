"""LLM-Client für Bürgergeld-Skills.

Wraps vibemind_shared.get_client_sync() mit fester Config-Auflösung auf
unsere _lib/llm_config.yml. Lädt .env aus dem üblichen Vibemind_V1-Root.
"""

from __future__ import annotations

import os
from pathlib import Path

_LIB_DIR = Path(__file__).resolve().parent


def _bootstrap_env() -> None:
    """Setze VIBEMIND_CONFIG_DIR auf _lib/ und lade .env aus Vibemind_V1-Root."""
    os.environ.setdefault("VIBEMIND_CONFIG_DIR", str(_LIB_DIR))

    if os.environ.get("OPENROUTER_API_KEY"):
        return

    candidates = [
        Path("C:/Users/User/Desktop/Vibemind_V1/.env"),
        Path("C:/Users/User/Desktop/Vibemind_V1/vibemind-os/.env"),
    ]
    for env_path in candidates:
        if not env_path.exists():
            continue
        try:
            for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip("'").strip('"')
                if key and key not in os.environ:
                    os.environ[key] = val
            break
        except Exception:  # noqa: BLE001
            continue


_bootstrap_env()

# Import nachladen NACHDEM env gesetzt ist
from vibemind_shared import (  # noqa: E402
    get_client_sync,
    get_model,
    get_provider_info,
)


def llm_client(role: str = "default"):
    """Get sync LLM client for a role."""
    return get_client_sync(role)


def llm_model(role: str = "default") -> str:
    return get_model(role)


def llm_info(role: str = "default") -> dict:
    return get_provider_info(role)
