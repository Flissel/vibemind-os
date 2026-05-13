"""Clarify-Question store used by ``handoff_clarify`` /
``handoff_clarify_check``.

Two-step pattern:
  1. Skill calls :func:`create_clarify` with a question (and optionally a
     ``form_schema`` describing input fields). This returns a ``clarify_id``
     and a ``form_url`` the user can open.
  2. Skill polls :func:`get_clarify_answer` (or the MCP tool
     ``handoff_clarify_check``) until the user submits via the FastAPI form.

When the form contains a ``credential_id`` field, the submitted value is
written to the :class:`SecretVault` (DPAPI-encrypted) and only the opaque
``vault://...`` token is returned in the answer payload.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

# secret_vault lives under app.services; expose path so we can import it.
_BACKEND = Path(__file__).resolve().parents[3]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

try:
    from app.services.secret_vault import SecretVault  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - backend may not be importable in some contexts
    SecretVault = None  # type: ignore[assignment,misc]

logger = logging.getLogger("clarify_notify")

DEFAULT_DB = _BACKEND / "moire_agents" / "data" / "clarify.db"

# Form host config — the standalone approval server (scripts/approval_server.py)
# on port 8008. Was 8007 (Automation_ui FastAPI) but that one isn't always
# running, so we use a dedicated lightweight stdlib HTTP server now.
FORM_HOST = os.environ.get("VIBEMIND_APPROVAL_HOST", "http://127.0.0.1:8008")


@contextmanager
def _connect(db_path: Path = DEFAULT_DB) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def _init_schema(db_path: Path = DEFAULT_DB) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clarifies (
                clarify_id TEXT PRIMARY KEY,
                question TEXT NOT NULL,
                options TEXT,         -- JSON list[str], optional
                form_schema TEXT,     -- JSON list[dict], optional
                answer TEXT,          -- JSON dict | NULL while pending
                status TEXT NOT NULL, -- pending | answered | cancelled
                created_at TEXT NOT NULL,
                answered_at TEXT
            )
            """
        )
        conn.commit()


_init_schema()


# --------------------------------------------------------------------- public API


def create_clarify(
    question: str,
    options: list[str] | None = None,
    form_schema: list[dict[str, Any]] | None = None,
    db_path: Path = DEFAULT_DB,
) -> dict[str, str]:
    """Insert a new clarify request.

    Returns ``{clarify_id, form_url, status}``. The skill is expected to
    surface ``form_url`` to the user (e.g. via Telegram message) so they can
    fill in the form.
    """
    clarify_id = uuid.uuid4().hex
    created = datetime.now(timezone.utc).isoformat()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO clarifies (clarify_id, question, options, form_schema,
                                    answer, status, created_at, answered_at)
            VALUES (?, ?, ?, ?, NULL, 'pending', ?, NULL)
            """,
            (
                clarify_id,
                question,
                json.dumps(options) if options else None,
                json.dumps(form_schema) if form_schema else None,
                created,
            ),
        )
        conn.commit()
    form_url = f"{FORM_HOST}/api/clarify/{clarify_id}/form"
    logger.info("clarify created %s (form? %s)", clarify_id, bool(form_schema))
    return {"clarify_id": clarify_id, "form_url": form_url, "status": "pending"}


def get_clarify(clarify_id: str, db_path: Path = DEFAULT_DB) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT clarify_id, question, options, form_schema, answer, status,
                   created_at, answered_at
            FROM clarifies WHERE clarify_id = ?
            """,
            (clarify_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "clarify_id": row[0],
        "question": row[1],
        "options": json.loads(row[2]) if row[2] else None,
        "form_schema": json.loads(row[3]) if row[3] else None,
        "answer": json.loads(row[4]) if row[4] else None,
        "status": row[5],
        "created_at": row[6],
        "answered_at": row[7],
    }


def record_clarify_answer(
    clarify_id: str,
    answer_fields: dict[str, Any],
    db_path: Path = DEFAULT_DB,
) -> dict[str, Any]:
    """Persist user answer.

    For any field that is named ``credential_id`` we treat its value as a
    name and store *the rest of the form fields* into the SecretVault under
    that name; the answer payload then contains the vault token instead of
    the plaintext (so logs/telemetry never see the raw value).
    """
    record = get_clarify(clarify_id, db_path=db_path)
    if record is None:
        raise KeyError(f"unknown clarify_id: {clarify_id}")
    if record["status"] == "answered":
        return record  # idempotent

    schema = record.get("form_schema") or []
    answer_payload: dict[str, Any] = dict(answer_fields)
    cred_field = next(
        (f for f in schema if f.get("type") == "vault" or f.get("vault")),
        None,
    )
    if cred_field is None:
        # Heuristic: if any field is `password`/`secret` and a `credential_id`
        # was provided in answer_fields, encrypt under that name.
        if "credential_id" in answer_fields and SecretVault is not None:
            cred_id = str(answer_fields["credential_id"]).strip()
            secret_payload = {
                k: v
                for k, v in answer_fields.items()
                if k != "credential_id"
            }
            if secret_payload and cred_id:
                vault = SecretVault()
                token = vault.store(cred_id, json.dumps(secret_payload), label=cred_id)
                answer_payload = {"vault_token": token, "credential_id": cred_id}
    elif SecretVault is not None:
        cred_id = str(answer_fields.get("credential_id") or cred_field.get("name") or clarify_id)
        secret_payload = {
            k: v for k, v in answer_fields.items() if k != "credential_id"
        }
        vault = SecretVault()
        token = vault.store(cred_id, json.dumps(secret_payload), label=cred_id)
        answer_payload = {"vault_token": token, "credential_id": cred_id}

    answered_at = datetime.now(timezone.utc).isoformat()
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE clarifies SET answer=?, status='answered', answered_at=? WHERE clarify_id=?",
            (json.dumps(answer_payload), answered_at, clarify_id),
        )
        conn.commit()
    logger.info("clarify answered %s", clarify_id)
    return get_clarify(clarify_id, db_path=db_path)  # type: ignore[return-value]


# --------------------------------------------------------- handoff_* facades


def handoff_clarify(
    question: str,
    options: list[str] | None = None,
    form_schema: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Tool body for ``handoff_clarify`` MCP tool."""
    return {"success": True, **create_clarify(question, options=options, form_schema=form_schema)}


def handoff_clarify_check(clarify_id: str) -> dict[str, Any]:
    """Tool body for ``handoff_clarify_check`` MCP tool."""
    record = get_clarify(clarify_id)
    if record is None:
        return {"success": False, "error": f"unknown clarify_id: {clarify_id}"}
    return {"success": True, **record}


# ---------------------------------------------------------------- approval flow


_REPO_ROOT = Path(__file__).resolve().parents[8]
_DEFAULT_TG_CHAT_ID = "1092040975"


def _load_repo_dotenv() -> dict[str, str]:
    env: dict[str, str] = {}
    p = _REPO_ROOT / ".env"
    if not p.is_file():
        return env
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _telegram_notify(message: str, inline_buttons: list[list[dict]] | None = None) -> bool:
    """Best-effort Telegram push direct via Bot API. Silent on failure.

    ``inline_buttons`` is a 2D list of button dicts following Telegram's
    InlineKeyboardMarkup spec, e.g.::

        [[
          {"text": "✅ Approve", "url": "http://localhost:8007/.../approve"},
          {"text": "❌ Decline", "url": "http://localhost:8007/.../decline"},
        ]]

    Each button can have ``url`` (opens link) or ``callback_data`` (triggers
    a callback we'd handle separately). For our approval flow we use ``url``
    so the user can tap the button and the bot doesn't need callback wiring.
    """
    import json as _json
    import os
    import urllib.parse
    import urllib.request

    token = os.environ.get("TELEGRAM_BOT_TOKEN") or _load_repo_dotenv().get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN not in env or .env — approval will not be pushed to Telegram")
        return False
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", _DEFAULT_TG_CHAT_ID)
    payload: dict[str, str] = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": "true",
    }
    if inline_buttons:
        payload["reply_markup"] = _json.dumps({"inline_keyboard": inline_buttons})
    try:
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data, method="POST"
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            ok = resp.status == 200
        logger.info("telegram approval notify sent ok=%s buttons=%s", ok, bool(inline_buttons))
        return ok
    except Exception as exc:
        logger.warning("telegram approval notify failed: %s", exc)
        return False


def handoff_approval_request(
    action: str,
    reason: str | None = None,
    timeout_seconds: int = 60,
    default_on_timeout: str = "approved",
    poll_interval_seconds: float = 1.5,
) -> dict[str, Any]:
    """Block until the user approves/declines the action, or timeout elapses.

    Pre-authorisation pattern: a skill calls this once before its first
    destructive tool call. The user gets a Telegram push with a one-click
    HTML form (Approve / Decline). If they don't answer within
    ``timeout_seconds``, ``default_on_timeout`` decides — for VibeMind we
    default to ``approved`` (optimistic, the user explicitly opted into
    autonomy) but the skill can override per-call.

    Returns ``{decision, decision_source, clarify_id}`` where decision is
    one of ``approved``/``declined`` and decision_source is
    ``user_explicit`` / ``timeout_default``.
    """
    import time

    record = create_clarify(
        question=f"[Approval] {action}" + (f"\n\n{reason}" if reason else ""),
        options=["Approve", "Decline"],
        form_schema=[],
    )
    clarify_id = record["clarify_id"]
    form_url = record["form_url"]
    approve_url = f"{FORM_HOST}/clarify/{clarify_id}/approve"
    decline_url = f"{FORM_HOST}/clarify/{clarify_id}/decline"

    msg = (
        f"⚠️ Approval-Anfrage\n\n"
        f"{action}"
        + (f"\n\nGrund: {reason}" if reason else "")
        + f"\n\nDesktop-Link: {form_url}\n"
        f"In {timeout_seconds}s wird automatisch '{default_on_timeout}' angenommen."
    )
    inline_buttons = [
        [
            {"text": "✅ Approve", "url": approve_url},
            {"text": "❌ Decline", "url": decline_url},
        ]
    ]
    _telegram_notify(msg, inline_buttons=inline_buttons)

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        cur = get_clarify(clarify_id)
        if cur and cur["status"] == "answered":
            ans = cur.get("answer") or {}
            choice = (ans.get("_choice") or "").strip().lower()
            decision = "approved" if choice.startswith("appro") else "declined"
            return {
                "success": True,
                "decision": decision,
                "decision_source": "user_explicit",
                "clarify_id": clarify_id,
                "answered_at": cur.get("answered_at"),
            }
        time.sleep(poll_interval_seconds)

    # Timeout — apply default
    return {
        "success": True,
        "decision": default_on_timeout,
        "decision_source": "timeout_default",
        "clarify_id": clarify_id,
        "timeout_seconds": timeout_seconds,
    }
