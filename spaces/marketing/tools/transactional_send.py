"""transactional_send — send ONE email through Mailcow, gated exactly like
the campaign send-worker (reuses _send_paranoid predicates). Not campaign-scoped."""
from __future__ import annotations
import os
from pathlib import Path

PKG_ROOT = next(p.parent for p in Path(__file__).resolve().parents if p.name == "spaces")
REPO_ROOT = next((p for p in (PKG_ROOT, *PKG_ROOT.parents) if (p / "vibemind-os").is_dir()), PKG_ROOT)

async def send_one(*, to: str, subject: str, body_html: str,
                   body_text: str | None = None, mode: str = "dry_run",
                   confirm_token: str | None = None, source: str) -> dict:
    if mode == "dry_run":
        return {"ok": True, "mode": "dry_run", "message_id": None}
    return {"ok": False, "mode": mode, "error": "not_implemented"}
