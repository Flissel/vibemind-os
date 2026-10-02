"""Chat-Arbeiter am PC (sales-claw Spec 2026-10-02-newsletter-gestaltung-und-agent).
Holt Chat-Auftraege von der Marketing-API der VM (Tailnet, X-Bild-Key), fragt Claude
ueber den lokalen OpenAI-kompatiblen Shim :8117, prueft und wendet die JSON-Aenderungen
an und meldet zurueck. Gestartet von marketing-dienste-starten.ps1; Gesundheits-Port 8134."""
from __future__ import annotations

import contextlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

from spaces.marketing.claw import agent_prompt, agent_werkzeuge
from spaces.marketing.workers.bild_worker import ApiFehler, _grund, tls_kontext, umgebung_laden

PORT = 8134
TAKT_S = 3
SHIM_BIS_S = 180
SHIM_PAUSE_S = 10
LLM_ZEITLIMIT_S = 300
LLM_URL = os.environ.get("MARKETING_CHAT_LLM_URL", "http://127.0.0.1:8117/v1")
MODELL = os.environ.get("MARKETING_CHAT_MODELL", "claude-code-sonnet")
NICHT_ERREICHBAR = "Der Assistent ist gerade nicht erreichbar"
NICHT_UMGESETZT = "Das habe ich nicht umsetzen können: "
STAND = {"letzter_lauf": None, "letztes_ergebnis": None}


class LlmFehler(Exception):
    pass


class ChatApi:
    def __init__(self, basis: str, schluessel: str):
        self.basis, self.schluessel = basis.rstrip("/"), schluessel
        self.tls = tls_kontext()

    def _anfrage(self, methode: str, pfad: str, daten=None, roh: bytes | None = None, typ="application/json") -> bytes:
        koerper = roh if roh is not None else (json.dumps(daten).encode("utf-8") if daten is not None else None)
        if methode == "POST" and koerper is None:
            koerper = b""
        req = urllib.request.Request(self.basis + "/api/chat/arbeiter" + pfad, data=koerper, method=methode,
                                     headers={"Content-Type": typ} if koerper is not None else {})
        req.add_unredirected_header("X-Bild-Key", self.schluessel)
        try:
            with urllib.request.urlopen(req, timeout=60, context=self.tls) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise ApiFehler(e.code, _grund(e)) from None

    def _post(self, pfad: str, daten=None, roh: bytes | None = None, typ="application/json") -> dict:
        return json.loads(self._anfrage("POST", pfad, daten, roh, typ) or b"{}")

    def naechster(self) -> dict | None:
        return self._post("/naechster").get("auftrag")

    def weiter(self, aid) -> bool:
        return bool(self._post(f"/{aid}/weiter").get("ok"))

    def pruefen(self, aid, bloecke) -> str | None:
        return self._post(f"/{aid}/pruefen", {"bloecke": bloecke}).get("fehler") or None

    def fertig(self, aid, daten: dict) -> dict:
        return self._post(f"/{aid}/fertig", daten)

    def zurueck(self, aid, antwort: str) -> str:
        return self._post(f"/{aid}/zurueck", {"antwort": antwort}).get("status", "")

    def medium(self, aid, name) -> bytes | None:
        try:
            return self._anfrage("GET", f"/{aid}/medien/{urllib.parse.quote(name)}")
        except ApiFehler as e:
            if e.code == 404:
                return None
            raise

    def datei(self, aid, name, roh: bytes) -> str:
        return self._post(f"/{aid}/datei?name={urllib.parse.quote(name)}", roh=roh, typ="image/jpeg")["name"]


def frage(system: str, nachrichten: list[dict], url: str = LLM_URL, modell: str = MODELL) -> str:
    """Eine Anfrage an den OpenAI-kompatiblen Shim (kein tool_calls, nur Text)."""
    koerper = {"model": modell, "messages": [{"role": "system", "content": system}, *nachrichten]}
    req = urllib.request.Request(url.rstrip("/") + "/chat/completions",
                                 data=json.dumps(koerper).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=LLM_ZEITLIMIT_S) as r:
            antwort = json.loads(r.read())
        text = antwort["choices"][0]["message"]["content"]
    except (OSError, TimeoutError, ValueError, KeyError, IndexError, TypeError) as e:
        # OSError deckt URLError/HTTPError, ValueError deckt JSONDecodeError.
        raise LlmFehler(f"{type(e).__name__}: {e}") from None
    if not isinstance(text, str) or not text.strip():
        raise LlmFehler("Leere Antwort")
    return text


HALTEN_TAKT_S = 60
FREMD = (404, 409, 422)      # Auftrag gehoert uns nicht mehr


class _Halter:
    def __init__(self):
        self.verloren = threading.Event()


@contextlib.contextmanager
def halten(api, aid, takt_s: float = HALTEN_TAKT_S):
    """Verlaengert die Vergabe alle takt_s Sekunden, solange der Block laeuft (Claude-Antworten
    dauern bis 300 s, die Vergabe nur 5 min). Meldet die Vergabe `verloren`, sobald weiter
    False liefert oder der Auftrag nicht mehr unser ist; Netzstoerungen werden uebersprungen."""
    halter, ende = _Halter(), threading.Event()

    def lauf():
        while not ende.wait(takt_s):
            try:
                if not api.weiter(aid):
                    halter.verloren.set()
                    return
            except ApiFehler as e:
                if e.code in FREMD:
                    halter.verloren.set()
                    return
            except (OSError, ValueError):
                pass
    faden = threading.Thread(target=lauf, daemon=True)
    faden.start()
    try:
        yield halter
    finally:
        ende.set()
        faden.join(timeout=1)


def _kurz(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"[:150]


def _freigeben(api, aid, text: str, e: BaseException) -> None:
    """Auftrag nach einer Stoerung zurueckgeben - einmal, Fehler dabei werden verschluckt.
    Gehoert der Auftrag uns nicht mehr (404/409/422), passiert nichts."""
    if isinstance(e, ApiFehler) and e.code in FREMD:
        return
    try:
        api.zurueck(aid, text)
    except Exception:  # noqa: BLE001 - die VM gibt den Auftrag nach Ablauf selbst frei
        pass


def chat_bearbeiten(api, auftrag, fragen=frage, uhr=time.monotonic, schlafen=time.sleep,
                    halten_takt_s: float = HALTEN_TAKT_S) -> str:
    aid = str(auftrag["id"])
    versucht = []            # zurueck wurde schon aufgerufen

    def zurueckgeben(text: str) -> str:
        versucht.append(1)
        api.zurueck(aid, text)
        return "fehler"
    try:
        return _bearbeiten(api, auftrag, aid, fragen, uhr, schlafen, halten_takt_s, zurueckgeben)
    except (ApiFehler, OSError, ValueError) as e:
        if not versucht:
            _freigeben(api, aid, NICHT_ERREICHBAR, e)
        return "fehler"


def _bearbeiten(api, auftrag, aid, fragen, uhr, schlafen, halten_takt_s, zurueckgeben) -> str:
    medien = list(auftrag.get("medien") or [])
    nachrichten = [{"role": "user", "content": agent_prompt.nutzer_text(auftrag, medien)}]
    fehler = ""
    for versuch in (1, 2):
        beginn = uhr()       # Shim-Fenster je Frage
        with halten(api, aid, halten_takt_s) as halter:
            while True:
                try:
                    text = fragen(agent_prompt.SYSTEM, nachrichten)
                    break
                except LlmFehler:
                    if halter.verloren.is_set():
                        return "fehler"
                    if uhr() - beginn >= SHIM_BIS_S:
                        return zurueckgeben(NICHT_ERREICHBAR)
                    if not api.weiter(aid):
                        return "fehler"      # Vergabe verloren: die VM hat den Auftrag schon verworfen
                    schlafen(SHIM_PAUSE_S)
        if halter.verloren.is_set():
            return "fehler"
        try:
            antwort = agent_prompt.antwort_lesen(text)
            ergebnis = agent_werkzeuge.anwenden(auftrag.get("bloecke") or {}, antwort["aenderungen"], set(medien))
            if ergebnis.geaendert:
                if not api.weiter(aid):
                    return "fehler"
                grund = api.pruefen(aid, ergebnis.bloecke)
                if grund:
                    raise agent_werkzeuge.WerkzeugFehler(grund)
        except (agent_prompt.AntwortFehler, agent_werkzeuge.WerkzeugFehler) as e:
            fehler = str(e)
            if versuch == 1:
                nachrichten += [{"role": "assistant", "content": text},
                                {"role": "user", "content": agent_prompt.korrektur_text(fehler)}]
                continue
            return zurueckgeben(NICHT_UMGESETZT + fehler)
        if not api.weiter(aid):
            return "fehler"
        antwort_vm = api.fertig(aid, {
            "antwort": antwort["antwort"],
            "bloecke": ergebnis.bloecke if ergebnis.geaendert else None,
            "bildauftraege": ergebnis.bildauftraege,
            "export_vorschlag": ergebnis.export_vorschlag,
            "notiz": ergebnis.notiz,
        })
        return "fehler" if antwort_vm.get("status") == "fehler" else "fertig"
    return zurueckgeben(NICHT_UMGESETZT + fehler)


def ein_durchlauf(api, fragen=frage, exportieren=None, uhr=time.monotonic, schlafen=time.sleep,
                  halten_takt_s: float = HALTEN_TAKT_S) -> str:
    auftrag = api.naechster()
    if not auftrag:
        return "leer"
    if auftrag.get("art") == "export":
        try:
            if exportieren is None:
                from spaces.marketing.workers.export_worker import exportieren   # Task 12; braucht Playwright
            return exportieren(api, auftrag)
        except Exception as e:  # noqa: BLE001 - Auftrag gehoert uns, also zurueckgeben
            _freigeben(api, str(auftrag["id"]), "Export nicht möglich: " + _kurz(e), e)
            return "fehler"
    return chat_bearbeiten(api, auftrag, fragen, uhr, schlafen, halten_takt_s)


class _Gesundheit(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        rumpf = json.dumps(STAND, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(rumpf)

    def log_message(self, *_):
        pass


def schleifenschritt(api, ein=ein_durchlauf) -> None:
    try:
        STAND["letztes_ergebnis"] = ein(api)
    except Exception as e:  # noqa: BLE001 - ein Fehler darf die Schleife nicht toeten
        STAND["letztes_ergebnis"] = f"fehler: {type(e).__name__}: {e}"[:200]
    STAND["letzter_lauf"] = time.strftime("%Y-%m-%d %H:%M:%S")
    print(STAND, flush=True)


def main() -> None:
    umgebung_laden()
    basis, schluessel = os.environ.get("MARKETING_BILD_URL", ""), os.environ.get("MARKETING_BILD_KEY", "")
    if not basis or not schluessel:
        raise SystemExit("MARKETING_BILD_URL/MARKETING_BILD_KEY fehlen in Vibemind_V1/.env")
    api = ChatApi(basis, schluessel)
    threading.Thread(target=HTTPServer(("127.0.0.1", PORT), _Gesundheit).serve_forever, daemon=True).start()
    while True:
        schleifenschritt(api)
        time.sleep(TAKT_S)


if __name__ == "__main__":
    main()
