"""Telegram-Anbindung: Protokoll, HTTP-Client (Bot-API per urllib) und Speicher-Attrappe.

Der Bot-Token steht nur in ``TelegramHttp._token`` (gelesen aus der Datei in
``TELEGRAM_BOT_TOKEN_FILE``). Er erscheint nie in Fehlertexten, ``repr``, Logs oder einer
Ausnahme-Kette: jeder Fehlertext laeuft durch `maskieren` (``bot<zahl>:<geheim>`` wird
``bot***``), und `TelegramFehler` wird ausserhalb des ``except``-Blocks geworfen, damit
keine urllib-Ausnahme (deren URL den Token traegt) als Kontext daranhaengt. Geloggt werden
weder Texte noch Chat-IDs.
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)

BASIS_URL = "https://api.telegram.org"
TIMEOUT_SEKUNDEN = 10.0
_TOKEN = re.compile(r"[0-9]+:[A-Za-z0-9_-]+")
_TOKEN_IN_TEXT = re.compile(r"bot\d+:[\w-]+")
_MITGLIED = frozenset({"creator", "administrator", "member", "restricted"})


class TelegramFehler(RuntimeError):
    """Telegram nicht erreichbar oder Anfrage abgelehnt. Der Text ist maskiert."""


class Telegram(Protocol):
    def senden(self, chat_id: int, text: str) -> None: ...

    def chat_info(self, chat_id: int) -> dict[str, Any]:
        """``{"titel": str | None, "art": str, "bot_ist_mitglied": bool}``"""
        ...


def maskieren(text: str) -> str:
    """Ersetzt jeden Bot-Token (``bot<zahl>:<geheim>``) durch ``bot***``."""
    return _TOKEN_IN_TEXT.sub("bot***", text)


class SpeicherTelegram:
    """Testattrappe: merkt sich gesendete Texte; `fehler` gesetzt = jedes Senden scheitert."""

    def __init__(self) -> None:
        self.gesendet: list[tuple[int, str]] = []
        self.chats: dict[int, dict[str, Any]] = {}
        self.fehler: str | None = None

    def senden(self, chat_id: int, text: str) -> None:
        if self.fehler is not None:
            raise TelegramFehler(self.fehler)
        self.gesendet.append((chat_id, text))

    def chat_info(self, chat_id: int) -> dict[str, Any]:
        if chat_id not in self.chats:
            raise TelegramFehler("Telegram-Fehler: Bad Request: chat not found")
        return dict(self.chats[chat_id])


class TelegramHttp:
    """Bot-API ueber HTTPS (urllib, Timeout 10 s)."""

    def __init__(self, token: str, *, timeout: float = TIMEOUT_SEKUNDEN,
                 basis_url: str = BASIS_URL) -> None:
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise ValueError("Bot-Token ungueltig")
        # Sonst meldete urllib "unknown url type: <URL mit Token>".
        if not isinstance(basis_url, str) or not basis_url.startswith(("https://", "http://")):
            raise ValueError("basis_url muss mit http:// oder https:// beginnen")
        self._token = token
        self._bot_id = int(token.split(":", 1)[0])
        self.timeout = timeout
        self.basis_url = basis_url.rstrip("/")

    def __repr__(self) -> str:  # Token nie ausgeben
        return f"TelegramHttp(basis_url={self.basis_url!r}, timeout={self.timeout})"

    def _aufruf(self, methode: str, daten: Mapping[str, Any]) -> Any:
        fehler: str | None = None
        antwort: Any = None
        try:
            # Auch das Bauen der Anfrage im try: jede Meldung daraus kann die URL zitieren.
            anfrage = Request(
                f"{self.basis_url}/bot{self._token}/{methode}",
                data=json.dumps(dict(daten)).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urlopen(anfrage, timeout=self.timeout) as roh:
                antwort = json.loads(roh.read().decode("utf-8"))
        except HTTPError as e:
            fehler = f"HTTP {e.code}: {_beschreibung(e)}"
        except (URLError, TimeoutError, OSError, http.client.HTTPException) as e:
            fehler = f"nicht erreichbar ({type(e).__name__})"
        except (ValueError, UnicodeDecodeError):
            fehler = "Anfrage oder Antwort ungueltig"
        if fehler is None and not (isinstance(antwort, dict) and antwort.get("ok") is True):
            beschreibung = antwort.get("description") if isinstance(antwort, dict) else None
            fehler = str(beschreibung) if beschreibung else "Anfrage abgelehnt"
        if fehler is not None:
            # Ausserhalb des except-Blocks: keine Kette zur urllib-Ausnahme (URL mit Token).
            log.warning("Telegram-Aufruf %s fehlgeschlagen", methode)
            raise TelegramFehler(self._maskieren(f"Telegram-Fehler: {fehler}"))
        return antwort.get("result")

    def _maskieren(self, text: str) -> str:
        return maskieren(text).replace(self._token, "***")

    def senden(self, chat_id: int, text: str) -> None:
        self._aufruf("sendMessage", {"chat_id": chat_id, "text": text,
                                     "disable_web_page_preview": True})
        log.info("Telegram: Nachricht gesendet")

    def chat_info(self, chat_id: int) -> dict[str, Any]:
        chat = self._aufruf("getChat", {"chat_id": chat_id})
        mitglied = self._aufruf("getChatMember", {"chat_id": chat_id, "user_id": self._bot_id})
        chat = chat if isinstance(chat, dict) else {}
        status = mitglied.get("status") if isinstance(mitglied, dict) else None
        return {"titel": chat.get("title"), "art": str(chat.get("type") or ""),
                "bot_ist_mitglied": status in _MITGLIED}


def _beschreibung(e: HTTPError) -> str:
    """`description` aus der JSON-Antwort der Bot-API, sonst der Statustext."""
    try:
        daten = json.loads(e.read().decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        daten = None
    if isinstance(daten, dict) and daten.get("description"):
        return str(daten["description"])
    return str(e.reason)


def telegram_aus_umgebung(umgebung: Mapping[str, str] | None = None) -> TelegramHttp | None:
    """`TelegramHttp` mit dem Token aus ``TELEGRAM_BOT_TOKEN_FILE``; None, wenn nicht gesetzt.

    Fehlermeldungen nennen nur den Variablennamen (nie Pfad oder Inhalt).
    """
    env = os.environ if umgebung is None else umgebung
    datei = env.get("TELEGRAM_BOT_TOKEN_FILE", "").strip()
    if not datei:
        return None
    try:
        # strip(): Dateien von Windows enden oft mit CRLF.
        token = Path(datei).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        raise ValueError("TELEGRAM_BOT_TOKEN_FILE nicht lesbar") from None
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN_FILE ist leer")
    try:
        return TelegramHttp(token)
    except ValueError:
        raise ValueError("TELEGRAM_BOT_TOKEN_FILE enthaelt keinen gueltigen Bot-Token") from None
