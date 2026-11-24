"""Telegram-Bot der Musik-Claw: Felix schreibt, ein Claude-Code-Assistent mit den Musik-Werkzeugen antwortet,
und fertige Bestellungen kommen mit Datei zurueck (die MP3, bzw. das fertige Laura-Video).

Aufbau wie `spaces/marketing/workers/telegram_bot.py` (Long-Polling, nur der eigene Privatchat), aber ohne
Whisper und Fotos. Die Chat-Antworten holt der Bot von einer eigenen Shim-Instanz; fertige Auftraege und ihre
Dateien holt er vom Musik-Dienst (nur 127.0.0.1, nur JSON-Routen, nie ein Dateipfad).

Umgebung (fehlende Werte aus der .env des aeusseren Repos):
  MUSIK_TELEGRAM_TOKEN      Bot-Token (nie loggen)
  TELEGRAM_CHAT_ID          einziger erlaubter Chat
  MUSIK_TELEGRAM_LLM_URL    Vorgabe http://127.0.0.1:8152/v1 (die Musik-Shim)
  MUSIK_STUDIO_URL          Vorgabe http://127.0.0.1:8148 (der Musik-Dienst)
  MUSIK_TELEGRAM_PORT       Gesundheitsport, Vorgabe 8142
  MUSIK_TELEGRAM_ZUSTAND    Zustandsordner, Vorgabe ~/.vibemind/musik-telegram
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Literal

from djset.dienst.auftraege import _ID as _AUFTRAG_ID  # eine Quelle fuer die ID-Form

LOG = logging.getLogger("djset.claw.telegram")

STANDARD_PORT = 8142
STANDARD_LLM_URL = "http://127.0.0.1:8152/v1"
STANDARD_DIENST_URL = "http://127.0.0.1:8148"
MODELL = "claude-code-sonnet"
LLM_ZEITLIMIT_S = 900          # Werkzeug-Laeufe (Bestellen, Laura) brauchen Minuten
TELEGRAM_MAX = 4000            # Grenze 4096; etwas Luft
CAPTION_MAX = 1000             # Grenze 1024
VERLAUF_WECHSEL = 10           # Frage+Antwort-Paare je Chat
POLL_S = 50
MELDE_TAKT_S = 30              # spaetestens alle 30 s nach fertigen Auftraegen sehen
MELDE_VERSUCHE = 5             # so oft darf Telegram bei einem Auftrag scheitern, dann gibt der Bot auf
DATEI_MAX = 50 * 1024 * 1024   # Telegram: Bots senden bis 50 MB
DIENST_ZEITLIMIT_S = 120
ART_NAME = {"video": "Videomusik", "set": "DJ-Set", "aufnehmen": "Aufnahme", "laura_render": "Laura-Video"}
ZUSTAND_TEXT = {"fertig": "ist fertig", "fertig_mit_befund": "ist fertig, mit Befunden",
                "gescheitert": "ist gescheitert", "abgebrochen": "wurde abgebrochen"}
FERTIG = ("fertig", "fertig_mit_befund")
# Welche Datei zu welcher Art gehoert: (Dateiname, Telegram-Art)
DATEI_ZUR_ART: dict[str, tuple[str, Literal["audio", "video", "document"]]] = {
    "video": ("videomusik.mp3", "audio"),
    "set": ("set.mp3", "audio"),
    "laura_render": ("laura-export.mp4", "video"),
}
STAND: dict[str, Any] = {"letzte_abfrage": None, "letzte_nachricht": None, "letzte_meldung": None}
HILFE = ("Ich bin die Musik-Assistentin. Schreib mir z. B. \"Was liegt im Bestand?\", "
         "\"Bestell mir 30 Sekunden ruhige Musik\" oder \"Leg Musik unter das Laura-Video <Name>\".\n"
         "Fertige Bestellungen schicke ich dir von selbst, mit Datei.\n"
         "/neu - Gespräch vergessen und neu anfangen\n/hilfe - diese Hilfe")

Dienstanfrage = Callable[..., tuple[int, bytes, "int | None"]]


class AntwortFehler(Exception):
    """Die Antwort des Assistenten ist ausgefallen; der Text ist fuer den Chat gedacht."""


# ---------------------------------------------------------------- Umgebung, Pfade, Token


def umgebung_laden() -> None:
    """Fehlende Werte aus der .env des aeusseren Repos (dieselbe Suche wie der Musik-Dienst)."""
    from djset.dienst import server

    env = server._aeusseres_env()
    if env is not None:
        server.env_nachladen(env, ("MUSIK_TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "MUSIK_TELEGRAM_LLM_URL",
                                   "MUSIK_STUDIO_URL", "MUSIK_TELEGRAM_PORT"))


def ohne_token(text: str, token: str) -> str:
    return text.replace(token, "***") if token else text


class TokenFilter(logging.Filter):
    """Ersetzt den Bot-Token in jedem Log-Eintrag, auch wenn ihn ein Fremder hineingeschrieben hat."""

    def __init__(self, token: str) -> None:
        super().__init__()
        self.token = token

    def filter(self, record: logging.LogRecord) -> bool:
        if self.token:
            record.msg = ohne_token(record.getMessage(), self.token)
            record.args = None
            if record.exc_info:  # ein Traceback koennte ihn tragen: nur den Typ behalten
                record.exc_text = None
                record.exc_info = None
        return True


def prompt_pfad() -> Path:
    """`spaces/musik/claw/AGENTS.md`, vom Paket aus gefunden (nie vom Arbeitsordner).
    djset/claw/telegram.py -> studio/djset/claw -> parents[3] ist `spaces/musik`; so im Haupt-Checkout wie im Worktree."""
    return Path(__file__).resolve().parents[3] / "claw" / "AGENTS.md"


def systemprompt() -> str:
    return prompt_pfad().read_text(encoding="utf-8")


def zustand_ordner() -> Path:
    vorgabe = os.environ.get("MUSIK_TELEGRAM_ZUSTAND", "").strip()
    return Path(vorgabe) if vorgabe else Path.home() / ".vibemind" / "musik-telegram"


def _llm_url() -> str:
    return os.environ.get("MUSIK_TELEGRAM_LLM_URL", "").strip() or STANDARD_LLM_URL


def _dienst_url() -> str:
    return (os.environ.get("MUSIK_STUDIO_URL", "").strip() or STANDARD_DIENST_URL).rstrip("/")


def _port() -> int:
    try:
        return int(os.environ.get("MUSIK_TELEGRAM_PORT", "").strip() or STANDARD_PORT)
    except ValueError:
        return STANDARD_PORT


# ---------------------------------------------------------------- Erlaubnis, Text


def erlaubt(update: dict, chat_id: int) -> bool:
    """Nur neue Nachrichten aus dem Privatchat des Betreibers - keine Gruppen, keine bearbeiteten Nachrichten."""
    m = update.get("message")
    if not chat_id or not isinstance(m, dict):
        return False
    chat, von = m.get("chat") or {}, m.get("from") or {}
    return chat.get("type") == "private" and chat.get("id") == chat_id and von.get("id") == chat_id


def zerlegen(text: str) -> list[str]:
    """Antwort in Telegram-taugliche Stuecke, bevorzugt an Absatz-, sonst an Zeilengrenzen."""
    text = (text or "").strip()
    if not text:
        return ["(leere Antwort)"]
    teile = []
    while len(text) > TELEGRAM_MAX:
        schnitt = text.rfind("\n\n", 0, TELEGRAM_MAX)
        if schnitt < TELEGRAM_MAX // 2:
            schnitt = text.rfind("\n", 0, TELEGRAM_MAX)
        if schnitt < TELEGRAM_MAX // 2:
            schnitt = TELEGRAM_MAX
        teile.append(text[:schnitt].rstrip())
        text = text[schnitt:].lstrip("\n")
    teile.append(text)
    return teile


def _atomar_schreiben(ziel: Path, text: str) -> None:
    ziel.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=ziel.parent, prefix=ziel.name + ".", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as strom:
            strom.write(text)
        os.replace(tmp, ziel)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class Verlauf:
    """Letzte VERLAUF_WECHSEL Frage/Antwort-Paare je Chat, als JSON-Datei (atomar geschrieben)."""

    def __init__(self, datei: Path):
        self.datei = Path(datei)
        try:
            self.daten = json.loads(self.datei.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.daten = {}

    def _speichern(self) -> None:
        _atomar_schreiben(self.datei, json.dumps(self.daten, ensure_ascii=False))

    def nachrichten(self, chat: int) -> list[dict]:
        return list(self.daten.get(str(chat), []))

    def anhaengen(self, chat: int, frage: str, antwort: str) -> None:
        liste = self.daten.setdefault(str(chat), [])
        liste += [{"role": "user", "content": frage}, {"role": "assistant", "content": antwort}]
        self.daten[str(chat)] = liste[-2 * VERLAUF_WECHSEL:]
        self._speichern()

    def leeren(self, chat: int) -> None:
        self.daten.pop(str(chat), None)
        self._speichern()


def versatz_lesen(ordner: Path) -> int:
    try:
        return int((Path(ordner) / "versatz.txt").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def versatz_schreiben(ordner: Path, wert: int) -> None:
    _atomar_schreiben(Path(ordner) / "versatz.txt", str(wert))


# ---------------------------------------------------------------- HTTP

def _post_json(url: str, koerper: dict, zeitlimit: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(koerper).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=zeitlimit) as r:
        return json.loads(r.read() or b"{}")


def _fehlertext(e: urllib.error.HTTPError) -> str:
    try:
        return str((json.loads(e.read() or b"{}").get("error") or {}).get("message", ""))
    except Exception:  # noqa: BLE001 - nur fuer die Meldung
        return ""


def antwort_holen(text: str, verlauf: list[dict], system: str) -> str:
    """Fragt Claude ueber die Musik-Shim. Bei einem Ausfall `AntwortFehler` mit einem Text fuer den Chat."""
    koerper = {"model": MODELL, "messages": [{"role": "system", "content": system}, *verlauf,
                                             {"role": "user", "content": text}]}
    try:
        antwort = _post_json(_llm_url().rstrip("/") + "/chat/completions", koerper, LLM_ZEITLIMIT_S)
    except urllib.error.HTTPError as e:
        meldung = _fehlertext(e)
        if e.code == 429 or "budget" in meldung:
            raise AntwortFehler("Die Tagesgrenze für die Musik-Assistentin ist erreicht. Morgen geht es weiter.") from None
        raise AntwortFehler(f"Das hat nicht geklappt (HTTP {e.code}). Versuch es gleich noch einmal.") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise AntwortFehler("Der Assistent ist gerade nicht erreichbar (läuft der PC?). "
                            "Versuch es später noch einmal.") from None
    return ((antwort.get("choices") or [{}])[0].get("message") or {}).get("content") or ""


def dienst_anfrage(methode: str, pfad: str, koerper: dict | None = None,
                   max_bytes: int = DATEI_MAX) -> tuple[int, bytes, int | None]:
    """Eine Anfrage an den Musik-Dienst -> (Status, Koerper, Gesamtgroesse laut Content-Length).
    Liest hoechstens `max_bytes` + 1 Byte (eine grosse Datei kommt nie ganz in den Speicher). Ein
    HTTP-Fehler ist ein Status; Verbindungsfehler wirft. POST sendet immer JSON (sonst 415), nie einen Origin."""
    daten = json.dumps(koerper if koerper is not None else {}).encode("utf-8") if methode == "POST" else None
    kopf = {"Content-Type": "application/json"} if daten is not None else {}
    req = urllib.request.Request(_dienst_url() + pfad, data=daten, headers=kopf, method=methode)
    try:
        with urllib.request.urlopen(req, timeout=DIENST_ZEITLIMIT_S) as r:
            body = r.read(max_bytes + 1)
            laenge = r.headers.get("Content-Length")
            return r.status, body, int(laenge) if laenge and laenge.isdigit() else len(body)
    except urllib.error.HTTPError as e:
        return e.code, b"", None


# ---------------------------------------------------------------- Telegram


_MIME = {".mp3": "audio/mpeg", ".mp4": "video/mp4"}
_FELD = {"audio": ("sendAudio", "audio"), "video": ("sendVideo", "video"),
         "document": ("sendDocument", "document")}


def _multipart(felder: dict[str, str], feld: str, name: str, daten: bytes, mime: str) -> tuple[bytes, str]:
    grenze = "musik" + secrets.token_hex(12)
    teile: list[bytes] = []
    for schluessel, wert in felder.items():
        teile.append(f'--{grenze}\r\nContent-Disposition: form-data; name="{schluessel}"\r\n\r\n'
                     f"{wert}\r\n".encode("utf-8"))
    sicher = name.replace('"', "").replace("\r", "").replace("\n", "")
    teile.append(f'--{grenze}\r\nContent-Disposition: form-data; name="{feld}"; filename="{sicher}"\r\n'
                 f"Content-Type: {mime}\r\n\r\n".encode("utf-8") + daten + b"\r\n")
    teile.append(f"--{grenze}--\r\n".encode("ascii"))
    return b"".join(teile), f"multipart/form-data; boundary={grenze}"


class TelegramApi:
    def __init__(self, token: str, chat_id: int):
        self.token = token
        self.chat_id = chat_id
        self.basis = f"https://api.telegram.org/bot{token}/"

    def _fehler(self, methode: str, grund: object) -> RuntimeError:
        return RuntimeError(ohne_token(f"Telegram {methode}: {grund}", self.token))

    def _auswerten(self, methode: str, roh: bytes) -> dict:
        try:
            antwort = json.loads(roh or b"{}")
        except ValueError:
            raise self._fehler(methode, "keine JSON-Antwort") from None
        if not isinstance(antwort, dict) or not antwort.get("ok"):
            beschreibung = antwort.get("description") if isinstance(antwort, dict) else None
            raise self._fehler(methode, beschreibung)
        return antwort

    def _rufen(self, methode: str, daten: dict, zeitlimit: float = 30) -> dict:
        try:
            antwort = _post_json(self.basis + methode, daten, zeitlimit)
        except Exception as e:  # noqa: BLE001 - die Meldung kann den Token tragen, siehe ohne_token
            raise self._fehler(methode, e) from None
        return self._auswerten(methode, json.dumps(antwort).encode("utf-8"))

    def updates(self, versatz: int) -> list[dict]:
        return self._rufen("getUpdates", {"offset": versatz, "timeout": POLL_S,
                                          "allowed_updates": ["message"]}, POLL_S + 15).get("result") or []

    def senden(self, text: str, chat_id: int | None = None) -> None:
        for teil in zerlegen(text):
            self._rufen("sendMessage", {"chat_id": chat_id or self.chat_id, "text": teil})

    def datei_senden(self, art: Literal["audio", "video", "document"], name: str, daten: bytes, text: str,
                     chat_id: int | None = None) -> None:
        if art not in _FELD:
            raise ValueError(f"unbekannte Art: {art}")
        methode, feld = _FELD[art]
        koerper, inhaltstyp = _multipart(
            {"chat_id": str(chat_id or self.chat_id), "caption": text[:CAPTION_MAX]}, feld, name, daten,
            _MIME.get(Path(name).suffix.lower(), "application/octet-stream"))
        req = urllib.request.Request(self.basis + methode, data=koerper, headers={"Content-Type": inhaltstyp})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                roh = r.read()
        except Exception as e:  # noqa: BLE001 - siehe oben
            raise self._fehler(methode, e) from None
        self._auswerten(methode, roh)

    def tippt(self, chat_id: int | None = None) -> None:
        try:
            self._rufen("sendChatAction", {"chat_id": chat_id or self.chat_id, "action": "typing"}, 10)
        except RuntimeError:
            pass


# ---------------------------------------------------------------- Meldungen fertiger Auftraege


def _minuten_sekunden(sekunden: float) -> str:
    s = int(round(sekunden))
    return f"{s // 60}:{s % 60:02d}"


def _gekuerzt(text: object, n: int) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def bericht_text(a: dict) -> str:
    """Kurzbericht aus den Feldern von `auftrag_zeigen` (der Dienst hat Pfade schon entfernt)."""
    art = a.get("art", "")
    eingabe = a.get("eingabe") if isinstance(a.get("eingabe"), dict) else {}
    was = a.get("titel") or eingabe.get("titel") or eingabe.get("wunsch") or eingabe.get("timeline_id") or ""
    zeilen = [f"{ART_NAME.get(art, art)} {ZUSTAND_TEXT.get(a.get('zustand'), a.get('zustand', ''))}"
              + (f": {_gekuerzt(was, 120)}" if was else "")]
    laenge = a.get("laenge_s")
    if not isinstance(laenge, (int, float)) and art == "set" and isinstance(eingabe.get("length"), (int, float)):
        laenge = eingabe["length"] * 60
    if isinstance(laenge, (int, float)) and not isinstance(laenge, bool):
        zeilen.append(f"Länge: {_minuten_sekunden(laenge)}")
    befunde = [b for b in (a.get("befunde") or []) if isinstance(b, str)]
    if befunde:
        zeilen.append("Befunde: " + "; ".join(_gekuerzt(b, 100) for b in befunde[:3])
                      + (f" (+{len(befunde) - 3} weitere)" if len(befunde) > 3 else ""))
    if a.get("grund"):
        zeilen.append("Grund: " + _gekuerzt(a["grund"], 200))
    zeilen.append(f"Auftrag {a.get('id', '')}")
    return "\n".join(zeilen)[:CAPTION_MAX - 120]  # Platz fuer einen Hinweis darunter


def _json_holen(anfrage: Dienstanfrage, methode: str, pfad: str, koerper: dict | None = None) -> dict | None:
    """Antwort des Dienstes als dict oder None (Verbindungsfehler, Status, kein JSON)."""
    try:
        status, body, _ = anfrage(methode, pfad, koerper)
        wert = json.loads(body) if status == 200 else None
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return wert if isinstance(wert, dict) else None


def _datei_holen(anfrage: Dienstanfrage, auftrag_id: str, name: str) -> tuple[bytes | None, int]:
    """(Bytes oder None, Gesamtgroesse). Bytes sind None, wenn die Datei fehlt oder zu gross ist."""
    try:
        status, body, gesamt = anfrage("GET", f"/auftraege/{auftrag_id}/datei/{name}")
    except (OSError, urllib.error.URLError):
        return None, 0
    if status != 200:
        return None, 0
    groesse = gesamt if isinstance(gesamt, int) else len(body)
    if groesse > DATEI_MAX or len(body) > DATEI_MAX:
        return None, groesse
    return body, groesse


def _eine_meldung(api: Any, anfrage: Dienstanfrage, eintrag: dict, chat_id: int, token: str) -> bool:
    """Bericht (und Datei) eines beendeten Auftrags an Telegram. False, wenn Telegram gar nichts annahm."""
    auftrag_id = eintrag["id"]
    a = _json_holen(anfrage, "GET", f"/auftraege/{auftrag_id}")
    if not a or not a.get("ok"):
        a = {**eintrag}               # Liste hat art, zustand, grund; mehr braucht der Kurzbericht nicht
    text = bericht_text({**eintrag, **a, "id": auftrag_id})
    datei = DATEI_ZUR_ART.get(a.get("art", "")) if a.get("zustand") in FERTIG else None
    hinweis = ""
    if datei is not None:
        name, art = datei
        daten, groesse = _datei_holen(anfrage, auftrag_id, name)
        if daten is not None:
            try:
                api.datei_senden(art, name, daten, text, chat_id=chat_id)
                return True
            except RuntimeError as e:
                LOG.warning("Datei an Telegram gescheitert: %s", ohne_token(str(e), token))
                hinweis = "\n(Die Datei konnte ich nicht senden.)"
        elif groesse > DATEI_MAX:
            hinweis = f"\nDatei zu groß für Telegram ({groesse / (1024 * 1024):.0f} MB), liegt im Auftrag {auftrag_id}"
        else:
            hinweis = "\n(Die Datei war nicht abrufbar.)"
    try:
        api.senden(text + hinweis, chat_id=chat_id)
    except RuntimeError as e:
        LOG.warning("Meldung an Telegram gescheitert: %s", ohne_token(str(e), token))
        return False
    return True


def melden(api: Any, dienst_anfrage: Dienstanfrage, chat_id: int, versuche: dict[str, int] | None = None,
           token: str = "") -> int:
    """Holt die beendeten, noch nicht gemeldeten Telegram-Auftraege, schickt je Bericht und Datei und
    markiert sie als gemeldet. -> Zahl der gesendeten Meldungen.

    Nimmt Telegram eine Meldung gar nicht an, bleibt der Auftrag offen (naechste Runde), hoechstens
    MELDE_VERSUCHE Mal; danach wird er trotzdem markiert, damit nie eine Endlosschleife entsteht."""
    liste = _json_holen(dienst_anfrage, "GET", "/auftraege?kanal=telegram&offen=1")
    if not liste or not liste.get("ok"):
        return 0
    versuche = versuche if versuche is not None else {}
    gesendet = 0
    for eintrag in liste.get("auftraege") or []:
        auftrag_id = eintrag.get("id") if isinstance(eintrag, dict) else None
        if not isinstance(auftrag_id, str) or not _AUFTRAG_ID.fullmatch(auftrag_id):
            continue
        if _eine_meldung(api, dienst_anfrage, eintrag, chat_id, token):
            gesendet += 1
        else:
            versuche[auftrag_id] = versuche.get(auftrag_id, 0) + 1
            if versuche[auftrag_id] < MELDE_VERSUCHE:
                continue
            LOG.warning("Auftrag %s nach %d Versuchen aufgegeben", auftrag_id, MELDE_VERSUCHE)
        versuche.pop(auftrag_id, None)
        if _json_holen(dienst_anfrage, "POST", f"/auftraege/{auftrag_id}/gemeldet", {}) is None:
            LOG.warning("Auftrag %s liess sich nicht als gemeldet markieren", auftrag_id)
    if gesendet:
        STAND["letzte_meldung"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return gesendet


# ---------------------------------------------------------------- Nachrichten


def verarbeiten(update: dict, telegram: Any, verlauf: Verlauf, chat_id: int, system: str,
                holen: Callable[[str, list[dict], str], str] = antwort_holen) -> None:
    if not erlaubt(update, chat_id):
        return                          # Fremden wird nicht geantwortet, nicht einmal abgesagt
    text = (update["message"].get("text") or "").strip()
    STAND["letzte_nachricht"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    if not text:
        telegram.senden("Ich verstehe nur Text.", chat_id=chat_id)
        return
    befehl = text.split()[0].split("@")[0].lower()
    if befehl in ("/neu", "/new", "/reset"):
        verlauf.leeren(chat_id)
        telegram.senden("Erledigt - wir fangen neu an.", chat_id=chat_id)
        return
    if befehl in ("/start", "/hilfe", "/help"):
        telegram.senden(HILFE, chat_id=chat_id)
        return
    fertig = threading.Event()

    def _tippen() -> None:
        while not fertig.is_set():
            telegram.tippt(chat_id)
            fertig.wait(4)

    faden = threading.Thread(target=_tippen, daemon=True)
    faden.start()
    try:
        antwort = holen(text, verlauf.nachrichten(chat_id), system)
        ok = True
    except AntwortFehler as e:
        antwort, ok = str(e), False
    finally:
        fertig.set()
        faden.join(timeout=1)
    if ok:                              # Fehlertexte kommen nicht in den Verlauf
        verlauf.anhaengen(chat_id, text, antwort)
    telegram.senden(antwort, chat_id=chat_id)


class _Gesundheit(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        rumpf = json.dumps({"ok": True, **STAND}).encode("utf-8")
        self.send_response(200 if self.path in ("/", "/health") else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(rumpf)

    def log_message(self, *_: Any) -> None:
        pass


def main() -> None:
    umgebung_laden()
    token = os.environ.get("MUSIK_TELEGRAM_TOKEN", "").strip()
    try:
        chat_id = int(os.environ.get("TELEGRAM_CHAT_ID", "0").strip() or 0)
    except ValueError:
        chat_id = 0
    if not token:
        raise SystemExit("MUSIK_TELEGRAM_TOKEN fehlt (Bot-Token des Musik-Bots)")
    if not chat_id:
        raise SystemExit("TELEGRAM_CHAT_ID fehlt oder ist keine Zahl (einziger erlaubter Chat)")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for handler in logging.getLogger().handlers:
        handler.addFilter(TokenFilter(token))   # auch Fremd-Logger (urllib, ...) verraten ihn nie
    try:
        system = systemprompt()
    except OSError:
        raise SystemExit(f"Systemprompt nicht lesbar: {prompt_pfad().name} neben dem Paket fehlt") from None
    port = _port()
    threading.Thread(target=HTTPServer(("127.0.0.1", port), _Gesundheit).serve_forever, daemon=True).start()
    zustand = zustand_ordner()
    telegram, verlauf = TelegramApi(token, chat_id), Verlauf(zustand / "verlauf.json")
    versatz = versatz_lesen(zustand)
    sperre, versuche = threading.Lock(), {}

    def meldungen() -> None:
        with sperre:                    # Takt-Faden und Hauptschleife melden nie gleichzeitig
            try:
                melden(telegram, dienst_anfrage, chat_id, versuche, token)
            except Exception as e:  # noqa: BLE001 - die Meldungsrunde darf den Bot nie beenden
                LOG.warning("Meldungsrunde: %s", ohne_token(f"{type(e).__name__}: {e}", token))

    def takt() -> None:
        while True:
            time.sleep(MELDE_TAKT_S)
            meldungen()

    threading.Thread(target=takt, name="musik-melden", daemon=True).start()
    LOG.info("Musik-Bot läuft, Gesundheit :%s, Shim %s, Dienst %s", port, _llm_url(), _dienst_url())
    while True:
        meldungen()
        try:
            neue = telegram.updates(versatz)
            STAND["letzte_abfrage"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        except RuntimeError as e:
            LOG.warning("%s", e)
            time.sleep(10)
            continue
        for update in neue:
            versatz = int(update.get("update_id", 0)) + 1
            versatz_schreiben(zustand, versatz)     # vor dem Verarbeiten: ein Absturz wiederholt nichts doppelt
            try:
                verarbeiten(update, telegram, verlauf, chat_id, system)
            except Exception as e:  # noqa: BLE001 - eine Nachricht darf den Bot nicht beenden
                LOG.warning("Fehler: %s", ohne_token(f"{type(e).__name__}: {e}", token))


if __name__ == "__main__":
    main()
