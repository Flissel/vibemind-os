"""Uebergabe Brain -> Event-Bot ueber das OpenClaw-Gateway (HTTP, Standardbibliothek).

Einziger Code, der mit dem Gateway spricht. Vor jedem POST prueft ``GET /v1/models``,
dass der Agent ``openclaw/<id>`` eingetragen ist (fail-closed): OpenClaw legt einen
unbekannten Agentennamen im Sitzungsschluessel still mit ALLEN Werkzeugen an.

Ausnahmen gehen nie nach aussen; der Token steht in keiner Rueckgabe und keinem Log.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import time
import urllib.error
import urllib.request
import uuid

log = logging.getLogger(__name__)

AGENT_LESEN = "brain-lesen"
AGENT_AUFTRAG = "brain-auftrag"

MODELS_TIMEOUT_S = 10.0
CHAT_TIMEOUT_S = 90.0
FEHLER_MAXLAENGE = 300
KOPF = "[VibeMind] "

# Als Konstante, damit Tests den Pfad umbiegen koennen (unter Windows gibt es ihn nicht).
SECRETS_DATEI = "/run/secrets/event_gateway_token"


class _Fehler(Exception):
    """Interner Abbruch mit lesbarer Meldung (ohne Token)."""

    def __init__(self, meldung: str, status: int | None = None) -> None:
        super().__init__(meldung)
        self.status = status


def _lesen(pfad: str) -> str:
    try:
        with open(pfad, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _token() -> str:
    datei = os.environ.get("EVENT_GATEWAY_TOKEN_FILE", "").strip()
    if datei:
        wert = _lesen(datei)
        if wert:
            return wert
    wert = _lesen(SECRETS_DATEI)
    if wert:
        return wert
    return os.environ.get("EVENT_GATEWAY_TOKEN", "").strip()


def _schwaerzen(text: str, token: str) -> str:
    return text.replace(token, "<token>") if token else text


def _http(methode: str, url: str, token: str, timeout: float,
          nutzlast: dict | None = None, kopf: dict | None = None) -> tuple[int, dict]:
    """Eine Anfrage; wirft nur _Fehler. Kein Wiederholen (502 ist endgueltig)."""
    try:
        anfrage = urllib.request.Request(
            url, method=methode,
            data=json.dumps(nutzlast).encode("utf-8") if nutzlast is not None else None,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {token}", **(kopf or {})})
        with urllib.request.urlopen(anfrage, timeout=timeout) as antwort:
            code = int(antwort.status)
            daten = json.loads(antwort.read().decode("utf-8"))
        if not isinstance(daten, dict):
            raise _Fehler("Gateway-Antwort ist kein JSON-Objekt", code)
        return code, daten
    except _Fehler:
        raise
    except urllib.error.HTTPError as e:  # vor URLError
        try:
            detail = e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - Detail ist Beiwerk
            detail = ""
        detail = " ".join(_schwaerzen(detail, token).split())[:FEHLER_MAXLAENGE]
        raise _Fehler(f"Gateway HTTP {e.code}: {detail}", e.code) from None
    except socket.timeout:
        raise _Fehler(f"Gateway Zeitueberschreitung nach {timeout:g} s") from None
    except urllib.error.URLError as e:
        if isinstance(e.reason, socket.timeout):
            raise _Fehler(f"Gateway Zeitueberschreitung nach {timeout:g} s") from None
        raise _Fehler(
            f"Gateway nicht erreichbar: {_schwaerzen(str(e.reason), token)[:FEHLER_MAXLAENGE]}"
        ) from None
    except Exception as e:  # noqa: BLE001 - nie als Traceback sterben
        # Nie str(e): die Meldung kann den Header (und damit den Token) enthalten.
        raise _Fehler(f"Anfragefehler {type(e).__name__}") from None


def _ergebnis(agent: str, ok: bool, antwort: str = "", result_id: str = "",
              fehler: str | None = None, token: str = "") -> dict:
    return {
        "ok": ok,
        "antwort": _schwaerzen(antwort, token) if ok else "",
        "agent": agent,
        "result_id": result_id,
        "fehler": None if ok else _schwaerzen(fehler or "unbekannter Fehler", token),
    }


def _text(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        roh = value.get("text")
        return roh.strip() if isinstance(roh, str) else None
    return None


def _uebergeben(agent: str, value: object) -> dict:
    start = time.monotonic()
    token = ""
    result_id = ""
    status = "-"
    try:
        text = _text(value)
        if not text:
            return _ergebnis(agent, False, fehler="kein Text uebergeben")

        url = os.environ.get("EVENT_GATEWAY_URL", "").strip().rstrip("/")
        token = _token()
        if not url:
            return _ergebnis(agent, False, fehler="EVENT_GATEWAY_URL fehlt")
        if not token:
            return _ergebnis(
                agent, False,
                fehler="EVENT_GATEWAY_TOKEN_FILE oder EVENT_GATEWAY_TOKEN fehlt")
        if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in token):
            token = ""  # ungueltiger Wert wird nirgends weiterverwendet
            return _ergebnis(
                agent, False,
                fehler="EVENT_GATEWAY_TOKEN ungueltig (Leer- oder Steuerzeichen)")

        modell = f"openclaw/{agent}"
        _, modelle = _http("GET", f"{url}/v1/models", token, MODELS_TIMEOUT_S)
        ids = [m.get("id") for m in modelle.get("data") or [] if isinstance(m, dict)]
        if modell not in ids:
            return _ergebnis(
                agent, False, token=token,
                fehler=f"Agent '{agent}' fehlt im Gateway - nichts gesendet")

        result_id = f"agent:{agent}:vibemind-{uuid.uuid4()}"
        status, daten = _http(
            "POST", f"{url}/v1/chat/completions", token, CHAT_TIMEOUT_S,
            {"model": modell,
             "messages": [{"role": "user", "content": KOPF + text}],
             "stream": False},
            {"x-openclaw-session-key": result_id})
        antwort = ""
        wahlen = daten.get("choices")
        if isinstance(wahlen, list) and wahlen and isinstance(wahlen[0], dict):
            nachricht = wahlen[0].get("message")
            if isinstance(nachricht, dict) and isinstance(nachricht.get("content"), str):
                antwort = nachricht["content"].strip()
        if not antwort:
            return _ergebnis(agent, False, result_id=result_id,
                             fehler="leere Antwort", token=token)
        return _ergebnis(agent, True, antwort, result_id, token=token)
    except _Fehler as e:
        if e.status is not None:
            status = str(e.status)
        return _ergebnis(agent, False, result_id=result_id, fehler=str(e), token=token)
    except Exception as e:  # noqa: BLE001 - Ausnahmen gehen nie nach aussen
        return _ergebnis(agent, False, result_id=result_id,
                         fehler=f"interner Fehler {type(e).__name__}", token=token)
    finally:
        log.info("uebergabe agent=%s status=%s dauer_ms=%d",
                 agent, status, int((time.monotonic() - start) * 1000))


def frage(value: dict) -> dict:
    """Lesende Frage an den Event-Bot (Agent brain-lesen)."""
    return _uebergeben(AGENT_LESEN, value)


def auftrag(value: dict) -> dict:
    """Schreibender Auftrag an den Event-Bot (Agent brain-auftrag)."""
    return _uebergeben(AGENT_AUFTRAG, value)
