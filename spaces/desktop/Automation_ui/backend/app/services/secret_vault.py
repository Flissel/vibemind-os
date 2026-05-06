"""Local secret vault using Windows DPAPI.

Used by adaptive skills that need credentials. The skill never sees the raw
value; it gets back an opaque token (``vault://<credential_id>``) that the
skill runner resolves at run-time by asking the vault to decrypt.

Storage: a SQLite file alongside ``data/clarify.db`` so it lives in the same
backend persistence dir.

Encryption: ``win32crypt.CryptProtectData`` with ``CRYPTPROTECT_LOCAL_MACHINE``
unset, i.e. the cipher text is bound to the *current Windows user account*.
On non-Windows hosts we fall back to a plain-text store with a warning, so
development / Linux CI doesn't crash — but secrets are not encrypted there.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

logger = logging.getLogger("secret_vault")

DEFAULT_DB = (
    Path(__file__).resolve().parents[2]
    / "moire_agents"
    / "data"
    / "secret_vault.db"
)

_HAS_DPAPI = False
try:
    if sys.platform == "win32":
        import win32crypt  # type: ignore[import-not-found]
        _HAS_DPAPI = True
except Exception:  # pragma: no cover - import-time
    _HAS_DPAPI = False


class SecretVault:
    """Tiny wrapper around DPAPI + SQLite for skill credentials."""

    TOKEN_PREFIX = "vault://"

    def __init__(self, db_path: Path = DEFAULT_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        if not _HAS_DPAPI:
            logger.warning(
                "win32crypt not available; secrets will be stored in PLAINTEXT. "
                "Only acceptable in dev/CI environments."
            )

    # ------------------------------------------------------------------ schema
    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS secrets (
                    credential_id TEXT PRIMARY KEY,
                    blob BLOB NOT NULL,
                    encrypted INTEGER NOT NULL,
                    label TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    # --------------------------------------------------------------- encryption
    @staticmethod
    def _encrypt(plaintext: str) -> tuple[bytes, bool]:
        if _HAS_DPAPI:
            blob = win32crypt.CryptProtectData(
                plaintext.encode("utf-8"), "vibemind-skill-secret", None, None, None, 0
            )
            return blob, True
        return plaintext.encode("utf-8"), False

    @staticmethod
    def _decrypt(blob: bytes, encrypted: bool) -> str:
        if encrypted and _HAS_DPAPI:
            _, plaintext = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
            return plaintext.decode("utf-8")
        return bytes(blob).decode("utf-8")

    # ----------------------------------------------------------------- public api
    def store(self, credential_id: str, plaintext: str, label: str | None = None) -> str:
        """Persist a secret and return the opaque token.

        Re-storing the same credential_id replaces the previous value.
        """
        blob, encrypted = self._encrypt(plaintext)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO secrets (credential_id, blob, encrypted, label, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(credential_id) DO UPDATE SET
                    blob=excluded.blob,
                    encrypted=excluded.encrypted,
                    label=excluded.label,
                    updated_at=excluded.updated_at
                """,
                (credential_id, blob, int(encrypted), label, now, now),
            )
            conn.commit()
        return f"{self.TOKEN_PREFIX}{credential_id}"

    def resolve(self, token_or_id: str) -> str | None:
        """Return plaintext for a vault token / credential_id, or None if unknown."""
        cred_id = token_or_id
        if cred_id.startswith(self.TOKEN_PREFIX):
            cred_id = cred_id[len(self.TOKEN_PREFIX):]
        with self._connect() as conn:
            row = conn.execute(
                "SELECT blob, encrypted FROM secrets WHERE credential_id = ?",
                (cred_id,),
            ).fetchone()
        if not row:
            return None
        return self._decrypt(row[0], bool(row[1]))

    def has(self, credential_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM secrets WHERE credential_id = ?", (credential_id,)
            ).fetchone()
        return row is not None

    def delete(self, credential_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM secrets WHERE credential_id = ?", (credential_id,)
            )
            conn.commit()
        return cur.rowcount > 0

    def list_ids(self) -> list[dict[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT credential_id, label, encrypted, created_at, updated_at FROM secrets ORDER BY updated_at DESC"
            ).fetchall()
        return [
            {
                "credential_id": r[0],
                "label": r[1],
                "encrypted": bool(r[2]),
                "created_at": r[3],
                "updated_at": r[4],
            }
            for r in rows
        ]
