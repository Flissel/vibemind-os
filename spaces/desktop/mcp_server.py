"""Werkzeug-Dienst des Desktop-Space (`spaces-desktop`).

OpenFang startet diesen Dienst über stdio. Er hat kein Modell und keinen Schlüssel.
Er spricht nur mit dem Desktop-Dienst am eigenen Rechner (Vorgabe 127.0.0.1:8007) und
führt das Aufgabenbuch (`aufgaben.py`), aus dem die App liest, was gerade geschieht.

Schritte, die Maus oder Tastatur bewegen, gehen nur
- innerhalb einer laufenden Aufgabe (`desktop_task_start`),
- solange keine Datei STOP im Aufgaben-Ordner liegt,
- und nur, wenn der Dienst mit VIBEMIND_DESKTOP_AUSFUEHREN=1 gestartet wurde.
Ohne diese Angabe läuft alles "trocken": Der Schritt wird gebucht, aber nicht ausgeführt.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aufgaben  # noqa: E402  (Geschwisterdatei; der Dienst wird als Skript gestartet)

SERVER_NAME = "spaces-desktop"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"
DEFAULT_URL = "http://127.0.0.1:8007"
LOKALE_HOSTS = ("127.0.0.1", "localhost", "::1")
LESE_TIMEOUT = 20
SCHRITT_TIMEOUT = 30
MAX_TEXT_ZEICHEN = 2000
MAX_BILDSCHIRMTEXT = 6000
MAX_WARTEN_SEKUNDEN = 5
MAX_ELEMENTE = 60
ARTEN = ("click", "type", "key", "hotkey", "scroll", "wait", "note")
# Bewegen weder Maus noch Tastatur und brauchen deshalb keinen Blick auf den Bildschirm.
OHNE_HANDLUNG = ("wait", "note")
MAX_NOTIZ_ZEICHEN = 120
# Nur was der Agent wirklich zu lesen bekommt, zählt als Blick; ein gespeichertes Bild nicht.
BLICK_WERKZEUGE = ("desktop_screen_read", "desktop_elements", "desktop_find")

_AUFGABE_ID = {"type": "string", "pattern": "^" + aufgaben.ID_MUSTER.pattern + "$"}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "desktop_status",
        "description": "Read-only: is the desktop service reachable, which monitors exist, which task is running.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "desktop_screen_read",
        "description": "Read-only: text currently visible on the screen (OCR) and the monitor layout.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "desktop_elements",
        "description": (
            "Read-only: the active window (title) and its clickable controls, each with name, type "
            "and the point to click (x, y). Prefer this over desktop_screen_read to choose a click target."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "desktop_find",
        "description": "Read-only: find a visible element by its text; returns click coordinates when found.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 200}},
            "required": ["text"],
        },
    },
    {
        "name": "desktop_screenshot",
        "description": "Read-only: save a picture of one monitor (1 = primary) and return its path, size and checksum.",
        "inputSchema": {
            "type": "object",
            "properties": {"monitor": {"type": "integer", "minimum": 1, "maximum": 8, "default": 1}},
        },
    },
    {
        "name": "desktop_task_start",
        "description": "Open a desktop task. Returns its id at once; the app shows the task as running. Only one task runs at a time.",
        "inputSchema": {
            "type": "object",
            "properties": {"task": {"type": "string", "minLength": 1, "maxLength": aufgaben.MAX_AUFGABE_ZEICHEN}},
            "required": ["task"],
        },
    },
    {
        "name": "desktop_task_step",
        "description": (
            "Execute ONE step of a running task on the real screen and record it. "
            "Refused with nicht_hingesehen unless desktop_elements, desktop_screen_read or desktop_find "
            "was called since the previous step. "
            "action: click (x, y), type (text), key (key), hotkey (keys), scroll (direction, amount), wait (seconds), "
            "note (text: a line for the journal, nothing is done on the screen)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": _AUFGABE_ID,
                "action": {"type": "string", "enum": list(ARTEN)},
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "text": {"type": "string", "maxLength": MAX_TEXT_ZEICHEN},
                "key": {"type": "string", "maxLength": 20},
                "keys": {"type": "array", "items": {"type": "string", "maxLength": 20}, "minItems": 2, "maxItems": 4},
                "direction": {"type": "string", "enum": ["up", "down"]},
                "amount": {"type": "integer", "minimum": 1, "maximum": 20},
                "seconds": {"type": "number", "minimum": 0, "maximum": MAX_WARTEN_SEKUNDEN},
                "why": {"type": "string", "maxLength": 120, "description": "One short line for the person watching."},
            },
            "required": ["task_id", "action"],
        },
    },
    {
        "name": "desktop_task_finish",
        "description": "Close a running task with its outcome: fertig, fehlgeschlagen or abgebrochen.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": _AUFGABE_ID,
                "state": {"type": "string", "enum": ["fertig", "fehlgeschlagen", "abgebrochen"]},
                "result": {"type": "string", "maxLength": aufgaben.MAX_ERGEBNIS_ZEICHEN},
            },
            "required": ["task_id", "state"],
        },
    },
    {
        "name": "desktop_task_status",
        "description": "Read-only: a task with all recorded steps. Without task_id: the most recent task.",
        "inputSchema": {"type": "object", "properties": {"task_id": _AUFGABE_ID}},
    },
]


class ToolError(Exception):
    """Fehler, dessen Text an den Aufrufer zurück darf."""


def _basis(env: Mapping[str, str]) -> str:
    url = env.get("DESKTOP_AUTOMATION_URL", "").strip() or DEFAULT_URL
    teile = urllib.parse.urlsplit(url)
    # Dieser Dienst bewegt Maus und Tastatur. Er spricht nie mit einem anderen Rechner.
    if teile.scheme != "http" or teile.hostname not in LOKALE_HOSTS:
        raise ToolError("konfiguration: DESKTOP_AUTOMATION_URL muss auf den eigenen Rechner zeigen")
    return url.rstrip("/")


def _ausfuehren_erlaubt(env: Mapping[str, str]) -> bool:
    return env.get("VIBEMIND_DESKTOP_AUSFUEHREN", "").strip() == "1"


def _http(method: str, pfad: str, body: Mapping[str, Any] | None = None, *, timeout: int) -> Any:
    daten = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        _basis(os.environ) + pfad,
        data=daten,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ToolError(f"dienst_veraltet: der Desktop-Dienst kennt {pfad} nicht") from None
        raise ToolError(f"dienst_fehler: HTTP {exc.code} bei {pfad}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ToolError("dienst_nicht_erreichbar: der Desktop-Dienst antwortet nicht") from None
    except (ValueError, UnicodeDecodeError):
        raise ToolError(f"dienst_fehler: unlesbare Antwort bei {pfad}") from None


def _nur(arguments: Mapping[str, Any], erlaubt: set[str]) -> None:
    fremd = sorted(set(arguments) - erlaubt)
    if fremd:
        raise ToolError(f"eingabe_ungueltig: unbekannte Angabe {fremd[0]}")


def _ganzzahl(arguments: Mapping[str, Any], name: str, *, lo: int, hi: int) -> int:
    wert = arguments.get(name)
    if isinstance(wert, bool) or not isinstance(wert, int) or not lo <= wert <= hi:
        raise ToolError(f"eingabe_ungueltig: {name} muss eine ganze Zahl von {lo} bis {hi} sein")
    return wert


def _text(arguments: Mapping[str, Any], name: str, *, hoechstens: int) -> str:
    wert = arguments.get(name)
    if not isinstance(wert, str) or not wert or len(wert) > hoechstens:
        raise ToolError(f"eingabe_ungueltig: {name} muss Text mit 1 bis {hoechstens} Zeichen sein")
    return wert


def _monitore() -> list[dict[str, Any]]:
    try:
        return list(_http("GET", "/api/desktop/monitors", timeout=LESE_TIMEOUT).get("monitors", []))
    except ToolError as exc:
        if not str(exc).startswith("dienst_veraltet"):
            raise
        return []  # älterer Dienst ohne Monitorliste


def _status() -> dict[str, Any]:
    erreichbar = True
    monitore: list[dict[str, Any]] = []
    try:
        _http("GET", "/api/health/health", timeout=5)
        monitore = _monitore()
    except ToolError:
        erreichbar = False
    letzte = aufgaben.letzte()
    return {
        "dienst_erreichbar": erreichbar,
        "monitore": monitore,
        "ausfuehren": _ausfuehren_erlaubt(os.environ),
        "not_aus": aufgaben.not_aus(),
        "aufgabe": _kurz(letzte) if letzte else None,
    }


def _kurz(aufgabe: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": aufgabe["id"],
        "zustand": aufgabe["zustand"],
        "trocken": aufgabe["trocken"],
        "schritte": len(aufgabe["schritte"]),
    }


def _screen_read() -> dict[str, Any]:
    antwort = _http("GET", "/api/mcp/read-screen", timeout=LESE_TIMEOUT)
    text = str(antwort.get("text") or "")
    return {
        "text": text[:MAX_BILDSCHIRMTEXT],
        "gekuerzt": len(text) > MAX_BILDSCHIRMTEXT,
        "monitore": antwort.get("monitors", []),
    }


def _elements() -> dict[str, Any]:
    antwort = _http("GET", f"/api/mcp/elements?max_elements={MAX_ELEMENTE}", timeout=LESE_TIMEOUT)
    if not isinstance(antwort, Mapping) or antwort.get("success") is not True:
        raise ToolError("dienst_fehler: die Bedienelemente des aktiven Fensters sind nicht lesbar")
    elemente = []
    for roh in antwort.get("elements") or []:
        rand = roh.get("bounds") if isinstance(roh, Mapping) else None
        if not isinstance(rand, Mapping) or rand.get("width", 0) <= 0 or rand.get("height", 0) <= 0:
            continue  # ohne sichtbare Fläche gibt es nichts zu klicken
        elemente.append(
            {
                "name": str(roh.get("name") or "")[:80],
                "typ": str(roh.get("control_type") or "").removesuffix("Control"),
                "x": int(rand["left"] + rand["width"] // 2),
                "y": int(rand["top"] + rand["height"] // 2),
                "aktiv": roh.get("is_enabled") is not False,
            }
        )
    fenster = antwort.get("window") if isinstance(antwort.get("window"), Mapping) else {}
    return {"fenster": str(fenster.get("title") or "")[:120], "elemente": elemente[:MAX_ELEMENTE]}


def _find(arguments: Mapping[str, Any]) -> dict[str, Any]:
    _nur(arguments, {"text"})
    antwort = _http(
        "POST", "/api/mcp/find-element", {"text": _text(arguments, "text", hoechstens=200)}, timeout=SCHRITT_TIMEOUT
    )
    if not isinstance(antwort, Mapping):
        raise ToolError("dienst_fehler: unlesbare Antwort bei /api/mcp/find-element")
    felder = ("success", "source", "element_location", "element", "matched_text", "confidence", "note", "error")
    return {k: antwort[k] for k in felder if k in antwort}


def _screenshot(arguments: Mapping[str, Any]) -> dict[str, Any]:
    _nur(arguments, {"monitor"})
    monitor = _ganzzahl({"monitor": arguments.get("monitor", 1)}, "monitor", lo=1, hi=8)
    antwort = _http("GET", f"/api/desktop/screenshot?monitor={monitor}", timeout=LESE_TIMEOUT)
    bild = (antwort.get("screenshot") or {}) if isinstance(antwort, Mapping) else {}
    try:
        roh = base64.b64decode(bild.get("data") or "", validate=True)
    except ValueError:
        roh = b""
    if not roh:
        raise ToolError("dienst_fehler: der Desktop-Dienst hat kein Bild geliefert")
    ziel = aufgaben.ordner() / "bilder"
    ziel.mkdir(parents=True, exist_ok=True)
    pfad = ziel / (time.strftime("%Y%m%d-%H%M%S") + f"-m{monitor}.jpg")
    pfad.write_bytes(roh)
    return {
        "pfad": str(pfad),
        "bytes": len(roh),
        "sha256": hashlib.sha256(roh).hexdigest(),
        "breite": bild.get("width", 0),
        "hoehe": bild.get("height", 0),
        "monitor": monitor,
    }


def _schritt_auftrag(arguments: Mapping[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any], str]:
    """Aus den Angaben: Art, was ins Buch kommt, was an den Dienst geht, Pfad am Dienst."""
    art = arguments.get("action")
    if art == "click":
        _nur(arguments, {"task_id", "action", "x", "y", "why"})
        punkt = {"x": _ganzzahl(arguments, "x", lo=-20000, hi=20000), "y": _ganzzahl(arguments, "y", lo=-20000, hi=20000)}
        return art, punkt, punkt, "/api/mcp/click"
    if art == "type":
        _nur(arguments, {"task_id", "action", "text", "why"})
        text = _text(arguments, "text", hoechstens=MAX_TEXT_ZEICHEN)
        # Getippter Text kann vertraulich sein. Ins Buch kommt nur die Länge.
        return art, {"zeichen": len(text)}, {"text": text}, "/api/mcp/type"
    if art == "key":
        _nur(arguments, {"task_id", "action", "key", "why"})
        taste = _text(arguments, "key", hoechstens=20)
        return art, {"key": taste}, {"key": taste}, "/api/mcp/key"
    if art == "hotkey":
        _nur(arguments, {"task_id", "action", "keys", "why"})
        tasten = arguments.get("keys")
        if not isinstance(tasten, list) or not 2 <= len(tasten) <= 4 or not all(
            isinstance(t, str) and 0 < len(t) <= 20 for t in tasten
        ):
            raise ToolError("eingabe_ungueltig: keys muss 2 bis 4 Tastennamen enthalten")
        return art, {"keys": tasten}, {"keys": tasten}, "/api/mcp/hotkey"
    if art == "scroll":
        _nur(arguments, {"task_id", "action", "direction", "amount", "why"})
        richtung = arguments.get("direction")
        if richtung not in ("up", "down"):
            raise ToolError("eingabe_ungueltig: direction muss up oder down sein")
        menge = _ganzzahl({"amount": arguments.get("amount", 3)}, "amount", lo=1, hi=20)
        angaben = {"direction": richtung, "amount": menge}
        return art, angaben, angaben, "/api/mcp/scroll"
    if art == "wait":
        _nur(arguments, {"task_id", "action", "seconds", "why"})
        sekunden = arguments.get("seconds", 1)
        if isinstance(sekunden, bool) or not isinstance(sekunden, (int, float)) or not 0 <= sekunden <= MAX_WARTEN_SEKUNDEN:
            raise ToolError(f"eingabe_ungueltig: seconds muss von 0 bis {MAX_WARTEN_SEKUNDEN} sein")
        return art, {"seconds": sekunden}, {}, ""
    if art == "note":
        _nur(arguments, {"task_id", "action", "text"})
        return art, {"notiz": _text(arguments, "text", hoechstens=MAX_NOTIZ_ZEICHEN)}, {}, ""
    raise ToolError("eingabe_ungueltig: action muss eine von " + ", ".join(ARTEN) + " sein")


def _task_step(arguments: Mapping[str, Any]) -> dict[str, Any]:
    art, ins_buch, an_dienst, pfad = _schritt_auftrag(arguments)
    kennung = arguments.get("task_id")
    warum = arguments.get("why")
    if isinstance(warum, str) and warum.strip():
        ins_buch = {**ins_buch, "warum": warum.strip()[:120]}
    # Not-Aus, Laufzeit und Obergrenze werden hier vor jedem einzelnen Schritt geprüft.
    aufgabe, begonnen = aufgaben.schritt_beginnen(kennung, art=art, angaben=ins_buch, braucht_blick=art not in OHNE_HANDLUNG)

    start = time.monotonic()
    ok, echt, meldung = True, False, "ausgeführt"
    try:
        if art == "wait":
            time.sleep(float(ins_buch["seconds"]))
            meldung = "gewartet"
        elif art == "note":
            meldung = "vermerkt"
        elif aufgabe["trocken"] or not _ausfuehren_erlaubt(os.environ):
            meldung = "trocken: gebucht, nicht ausgeführt"
        else:
            echt = True  # ab hier kann der Dienst gehandelt haben, auch wenn die Antwort ausbleibt
            antwort = _http("POST", pfad, an_dienst, timeout=SCHRITT_TIMEOUT)
            ok = isinstance(antwort, Mapping) and antwort.get("success") is True
            if not ok:
                meldung = "der Desktop-Dienst meldet keinen Erfolg"
    except ToolError as exc:
        ok, meldung = False, str(exc)
    schritt = aufgaben.schritt_abschliessen(
        kennung, begonnen["nr"], ok=ok, echt=echt, meldung=meldung, dauer_ms=int((time.monotonic() - start) * 1000)
    )
    return {"task_id": kennung, "schritt": schritt}


def call_tool(name: str, arguments: Mapping[str, Any]) -> Any:
    try:
        if name == "desktop_status":
            _nur(arguments, set())
            return _status()
        if name in BLICK_WERKZEUGE:
            if name == "desktop_find":
                ergebnis = _find(arguments)
            else:
                _nur(arguments, set())
                ergebnis = _screen_read() if name == "desktop_screen_read" else _elements()
            aufgaben.hingesehen()  # nur ein gelungener Blick zählt
            return ergebnis
        if name == "desktop_screenshot":
            return _screenshot(arguments)
        if name == "desktop_task_start":
            _nur(arguments, {"task"})
            trocken = not _ausfuehren_erlaubt(os.environ)
            return aufgaben.beginnen(arguments.get("task"), trocken=trocken)
        if name == "desktop_task_step":
            return _task_step(arguments)
        if name == "desktop_task_finish":
            _nur(arguments, {"task_id", "state", "result"})
            return aufgaben.beenden(
                arguments.get("task_id"), zustand=arguments.get("state"), ergebnis=str(arguments.get("result") or "")
            )
        if name == "desktop_task_status":
            _nur(arguments, {"task_id"})
            if arguments.get("task_id"):
                return aufgaben.lesen(arguments["task_id"])
            return aufgaben.letzte() or {"aufgabe": None}
    except aufgaben.AufgabenFehler as exc:
        raise ToolError(str(exc)) from None
    except OSError:
        raise ToolError("aufgabenbuch_unlesbar: der Aufgaben-Ordner ist nicht les- oder schreibbar") from None
    raise ToolError(f"werkzeug_unbekannt: {name}")


def _tool_result(payload: Any, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=True, sort_keys=True)}],
        "isError": is_error,
    }


def handle_message(message: Mapping[str, Any]) -> dict[str, Any] | None:
    """Die unterstützten JSON-RPC-Methoden; schreibt nichts nach stdout."""
    request_id = message.get("id")
    method = message.get("method")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "capabilities": {"tools": {}},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params")
        name = params.get("name") if isinstance(params, Mapping) else None
        arguments = params.get("arguments", {}) if isinstance(params, Mapping) else None
        if not isinstance(name, str) or not isinstance(arguments, Mapping):
            fehler = {"error": "eingabe_ungueltig: name und arguments sind nötig"}
            return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result(fehler, is_error=True)}
        try:
            return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result(call_tool(name, arguments))}
        except ToolError as exc:
            return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result({"error": str(exc)}, is_error=True)}
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "method not found"}}


def main() -> int:
    """JSON-RPC-Zeilen über stdio, bis die Eingabe endet.

    Bytes rein, Bytes raus: Die Anfrage ist UTF-8, egal was die Codepage sagt, und die
    Antwort ist reines ASCII. Unter Windows ist eine umgeleitete Ausgabe ohne
    PYTHONIOENCODING cp1252; ein Pfeil oder Emoji ließ `print` scheitern und beendete
    den Ideen-Dienst (23.11.2026). Eine fehlerhafte Anfrage beendet die Schleife nie.
    """
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    for raw_line in stdin:
        if not raw_line.strip():
            continue
        request_id = None
        try:
            message = json.loads(raw_line.decode("utf-8"))
            if not isinstance(message, Mapping):
                raise ValueError("request must be an object")
            request_id = message.get("id")
            response = handle_message(message)
        except (ValueError, UnicodeDecodeError):
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        except Exception as exc:  # noqa: BLE001 - eine Anfrage darf den Dienst nie beenden
            fehler = {"error": f"internal_error: {type(exc).__name__}"}
            response = {"jsonrpc": "2.0", "id": request_id, "result": _tool_result(fehler, is_error=True)}
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=True).encode("ascii") + b"\n")
            stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
