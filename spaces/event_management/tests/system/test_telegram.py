"""Telegram-Anbindung (system/telegram.py): Speicher-Attrappe und HTTP ohne echtes Netz.

`TelegramHttp` wird nur gegen ein ersetztes `urlopen` getestet. Der Token ist erfunden.
"""
from __future__ import annotations

import io
import json
import logging
import traceback
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

import pytest

from spaces.event_management.system import telegram
from spaces.event_management.system.telegram import (
    SpeicherTelegram, TelegramFehler, TelegramHttp, telegram_aus_umgebung,
)

TOKEN = "123456:FAKE-test_token-nur-fuer-tests"
CHAT = -1001234567890


class _Antwort:
    def __init__(self, daten: dict[str, Any]) -> None:
        self._roh = json.dumps(daten).encode("utf-8")

    def read(self) -> bytes:
        return self._roh

    def __enter__(self) -> _Antwort:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def _http_fehler(url: str, beschreibung: str) -> HTTPError:
    koerper = json.dumps({"ok": False, "description": beschreibung}).encode("utf-8")
    return HTTPError(url, 400, "Bad Request", Message(), io.BytesIO(koerper))


def _alles_text(e: BaseException) -> str:
    """Alles, was von einer Ausnahme sichtbar werden kann: str, repr, Traceback-Kette."""
    return "\n".join([str(e), repr(e), *traceback.format_exception(e)])


def test_speicher_telegram_merkt_und_scheitert_auf_wunsch() -> None:
    t = SpeicherTelegram()
    t.senden(CHAT, "Hallo")
    assert t.gesendet == [(CHAT, "Hallo")]
    t.chats[CHAT] = {"titel": "Beispielgruppe", "art": "supergroup", "bot_ist_mitglied": True}
    assert t.chat_info(CHAT)["titel"] == "Beispielgruppe"
    with pytest.raises(TelegramFehler):
        t.chat_info(-1009999999999)
    t.fehler = "Bad Request: chat not found"
    with pytest.raises(TelegramFehler):
        t.senden(CHAT, "Nochmal")
    assert t.gesendet == [(CHAT, "Hallo")]


def test_senden_ruft_send_message(monkeypatch: pytest.MonkeyPatch) -> None:
    aufrufe: list[tuple[str, dict[str, Any], float]] = []

    def oeffnen(anfrage: Any, timeout: float) -> _Antwort:
        aufrufe.append((anfrage.full_url, json.loads(anfrage.data), timeout))
        return _Antwort({"ok": True, "result": {"message_id": 1}})

    monkeypatch.setattr(telegram, "urlopen", oeffnen)
    TelegramHttp(TOKEN).senden(CHAT, "Hallo zusammen")
    assert len(aufrufe) == 1
    url, daten, timeout = aufrufe[0]
    assert url == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert daten["chat_id"] == CHAT and daten["text"] == "Hallo zusammen"
    assert timeout == 10


def test_chat_info(monkeypatch: pytest.MonkeyPatch) -> None:
    def oeffnen(anfrage: Any, timeout: float) -> _Antwort:
        if anfrage.full_url.endswith("/getChat"):
            return _Antwort({"ok": True, "result": {"id": CHAT, "type": "supergroup",
                                                    "title": "Beispielgruppe"}})
        assert anfrage.full_url.endswith("/getChatMember")
        assert json.loads(anfrage.data) == {"chat_id": CHAT, "user_id": 123456}
        return _Antwort({"ok": True, "result": {"status": "administrator"}})

    monkeypatch.setattr(telegram, "urlopen", oeffnen)
    assert TelegramHttp(TOKEN).chat_info(CHAT) == {
        "titel": "Beispielgruppe", "art": "supergroup", "bot_ist_mitglied": True}


@pytest.mark.parametrize(("chat", "titel"), [
    ({"first_name": "Erika", "last_name": "Muster", "username": "erika_beispiel"},
     "Erika Muster (@erika_beispiel)"),
    ({"first_name": "Erika", "username": "erika_beispiel"}, "Erika (@erika_beispiel)"),
    ({"first_name": "Erika"}, "Erika"),
    ({"username": "erika_beispiel"}, "@erika_beispiel"),
    ({}, None),
])
def test_chat_info_privat_mit_anzeigename(monkeypatch: pytest.MonkeyPatch,
                                          chat: dict[str, str], titel: str | None) -> None:
    """Ruling T7-b: private Chats haben keinen `title`; der Anzeigename kommt aus
    first_name/last_name/@username. getChatMember nur fuer Gruppen."""
    aufrufe: list[str] = []

    def oeffnen(anfrage: Any, timeout: float) -> _Antwort:
        aufrufe.append(anfrage.full_url.rsplit("/", 1)[-1])
        assert aufrufe[-1] == "getChat"
        return _Antwort({"ok": True, "result": {"id": 123456789, "type": "private", **chat}})

    monkeypatch.setattr(telegram, "urlopen", oeffnen)
    assert TelegramHttp(TOKEN).chat_info(123456789) == {
        "titel": titel, "art": "private", "bot_ist_mitglied": True}
    assert aufrufe == ["getChat"]


@pytest.mark.parametrize("fall", ["http", "netz", "antwort"])
def test_telegram_fehlertext_maskiert_token(monkeypatch: pytest.MonkeyPatch,
                                            caplog: pytest.LogCaptureFixture,
                                            fall: str) -> None:
    def oeffnen(anfrage: Any, timeout: float) -> _Antwort:
        url = anfrage.full_url
        assert TOKEN in url  # der Token steckt in der URL ...
        if fall == "http":
            raise _http_fehler(url, f"Bad Request: bei {url} ging es schief")
        if fall == "netz":
            raise URLError(f"keine Verbindung zu {url}")
        return _Antwort({"ok": False, "description": f"Unauthorized bot{TOKEN}"})

    monkeypatch.setattr(telegram, "urlopen", oeffnen)
    t = TelegramHttp(TOKEN)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(TelegramFehler) as e:
            t.senden(CHAT, "Geheimer Kundentext")
    sichtbar = _alles_text(e.value) + caplog.text
    # ... aber nie im Fehlertext, in der Kette oder im Log.
    assert TOKEN not in sichtbar
    assert "FAKE-test_token" not in sichtbar
    # Der Traceback zitiert Quelltextzeilen dieses Tests; Text und Chat-ID nur ohne ihn.
    meldung = str(e.value) + repr(e.value) + caplog.text
    assert e.value.__context__ is None and e.value.__cause__ is None
    assert "Geheimer Kundentext" not in meldung and str(CHAT) not in meldung
    assert "Telegram" in str(e.value)
    if fall in ("http", "antwort"):
        assert "bot***" in str(e.value)  # Beschreibung bleibt lesbar, Token maskiert


def test_maskieren() -> None:
    assert telegram.maskieren(f"x https://api.telegram.org/bot{TOKEN}/sendMessage y") == (
        "x https://api.telegram.org/bot***/sendMessage y")


def test_repr_ohne_token() -> None:
    t = TelegramHttp(TOKEN)
    assert TOKEN not in repr(t) and "FAKE" not in repr(t) and "FAKE" not in str(t)


def test_ungueltiger_token_abgelehnt() -> None:
    for schlecht in ("", "kein-token", "abc:def", "123456:"):
        with pytest.raises(ValueError) as e:
            TelegramHttp(schlecht)
        assert schlecht == "" or schlecht not in str(e.value)


def test_aus_umgebung(tmp_path: Path) -> None:
    assert telegram_aus_umgebung({}) is None
    datei = tmp_path / "token"
    datei.write_text(TOKEN + "\r\n", encoding="utf-8")  # CRLF von Windows
    t = telegram_aus_umgebung({"TELEGRAM_BOT_TOKEN_FILE": str(datei)})
    assert isinstance(t, TelegramHttp)
    for inhalt in ("", "kein-token"):
        datei.write_text(inhalt, encoding="utf-8")
        with pytest.raises(ValueError) as e:
            telegram_aus_umgebung({"TELEGRAM_BOT_TOKEN_FILE": str(datei)})
        assert "TELEGRAM_BOT_TOKEN_FILE" in str(e.value) and str(datei) not in str(e.value)
    with pytest.raises(ValueError):
        telegram_aus_umgebung({"TELEGRAM_BOT_TOKEN_FILE": str(tmp_path / "fehlt")})


def test_basis_url_ohne_schema_abgelehnt_ohne_token() -> None:
    for schlecht in ("ftp://example.org", "api.telegram.org", ""):
        with pytest.raises(ValueError) as e:
            TelegramHttp(TOKEN, basis_url=schlecht)
        assert TOKEN not in _alles_text(e.value)


def test_fehler_beim_bauen_der_anfrage_maskiert(monkeypatch: pytest.MonkeyPatch) -> None:
    def kaputt(url: str, **_: Any) -> Any:
        raise ValueError(f"unknown url type: {url!r}")

    monkeypatch.setattr(telegram, "Request", kaputt)
    with pytest.raises(TelegramFehler) as e:
        TelegramHttp(TOKEN).senden(CHAT, "Hallo")
    assert TOKEN not in _alles_text(e.value) and e.value.__context__ is None


def test_http_client_fehler_maskiert(monkeypatch: pytest.MonkeyPatch) -> None:
    import http.client

    def oeffnen(anfrage: Any, timeout: float) -> _Antwort:
        raise http.client.BadStatusLine(f"Unsinn von {anfrage.full_url}")

    monkeypatch.setattr(telegram, "urlopen", oeffnen)
    with pytest.raises(TelegramFehler) as e:
        TelegramHttp(TOKEN).senden(CHAT, "Hallo")
    assert "nicht erreichbar (BadStatusLine)" in str(e.value)
    assert TOKEN not in _alles_text(e.value) and e.value.__context__ is None
