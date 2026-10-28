"""Telegram-Bot des Marketing-Space (20.10.2026) - @VibeMindMarketingBot, nur fuer den Betreiber.

Liest den Bot per Long-Polling, nimmt nur Nachrichten aus dem Privatchat des Betreibers (TELEGRAM_CHAT_ID) an und
fragt Claude ueber die eigene Shim-Instanz :8143 (Claude Code CLI im Abo, Budget-Name marketing-telegram, alle
Marketing-Werkzeuge). Die Antwort geht zurueck in den Chat. Ersetzt den Telegram-Kanal von openclaw: dessen
Anfragen rechnete Anthropic als "extra usage" ab (gemessen 20.10.2026), dieser Bot ist eine eigene Claude-Code-Anwendung.

Umgebung (fehlende Werte aus den .env-Dateien des aeusseren Repos und von vibemind-os):
  TELEGRAM_BOT_TOKEN_MARKETING   Bot-Token (nie loggen)
  TELEGRAM_CHAT_ID               einziger erlaubter Chat
  MARKETING_TELEGRAM_LLM_URL     Vorgabe http://127.0.0.1:8143/v1
  MARKETING_TELEGRAM_PORT        Gesundheitsport, Vorgabe 8144
Zustand: ~/.vibemind/marketing-telegram/ (Verlauf je Chat, Update-Versatz)."""
from __future__ import annotations

import base64
import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from spaces.marketing.workers.bild_worker import REPO_ROOT

PORT = int(os.environ.get("MARKETING_TELEGRAM_PORT", "8144"))
LLM_URL = os.environ.get("MARKETING_TELEGRAM_LLM_URL", "http://127.0.0.1:8143/v1")
MODELL = "claude-code-sonnet"
LLM_ZEITLIMIT_S = 900          # Werkzeug-Laeufe (Newsletter-Chat, Kampagne) brauchen Minuten
TELEGRAM_MAX = 4000            # Grenze 4096; etwas Luft
VERLAUF_WECHSEL = 10           # Frage+Antwort-Paare je Chat
POLL_S = 50
DATEI_MAX = 10 * 1024 * 1024    # Fotos/Sprachnachrichten; die Bot-API liefert ohnehin hoechstens 20 MB
WHISPER_MODELL = os.environ.get("MARKETING_TELEGRAM_WHISPER", "small")
WORTSCHATZ = "VibeMind, fin2gether, Newsletter, Newsletter-Entwurf, Kampagne, Pult, sales-claw, Rowboat, Podcast, Reel."
ZUSTAND = Path(os.environ.get("MARKETING_TELEGRAM_ZUSTAND", Path.home() / ".vibemind" / "marketing-telegram"))
PROMPT = Path(__file__).resolve().parents[1] / "claw" / "telegram_prompt.md"
STAND = {"letzte_abfrage": None, "letzte_nachricht": None, "letztes_ergebnis": None}
HILFE = ("Ich bin der Marketing-Assistent. Schreib mir, schick ein Foto oder eine Sprachnachricht - z. B. "
         "\"Welche Newsletter-Entwuerfe gibt es?\" oder \"Mach die Ueberschrift von <Newsletter> kuerzer\".\n"
         "/neu - Gespraech vergessen und neu anfangen\n/hilfe - diese Hilfe\nVersendet wird nichts.")


def umgebung_laden() -> None:
    """Fehlende Werte aus Vibemind_V1/.env und vibemind-os/.env (der Bot-Token liegt in der zweiten)."""
    keys = ("TELEGRAM_BOT_TOKEN_MARKETING", "TELEGRAM_CHAT_ID",
            # Kampagnen-Runde (28.10.2026): der Bot fragt selbst nach offenen Kampagnen.
            "MARKETING_API_URL", "MARKETING_API_KEY", "MARKETING_AGENT_KEY")
    for datei in (REPO_ROOT / ".env", REPO_ROOT / "vibemind-os" / ".env"):
        fehlend = [k for k in keys if not os.environ.get(k)]
        if not fehlend or not datei.exists():
            continue
        for zeile in datei.read_text(encoding="utf-8", errors="replace").splitlines():
            for k in fehlend:
                if zeile.strip().startswith(k + "="):
                    os.environ[k] = zeile.split("=", 1)[1].strip().strip('"').strip("'")


def ohne_token(text: str, token: str) -> str:
    return text.replace(token, "***") if token else text


def systemprompt() -> str:
    return PROMPT.read_text(encoding="utf-8")


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


class Verlauf:
    """Letzte VERLAUF_WECHSEL Frage/Antwort-Paare je Chat, als JSON-Datei."""

    def __init__(self, datei: Path):
        self.datei = Path(datei)
        try:
            self.daten = json.loads(self.datei.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.daten = {}

    def _speichern(self) -> None:
        self.datei.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.datei.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.daten, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.datei)

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


def antwort_holen(text: str | list, verlauf: list[dict], system: str) -> tuple[str, bool]:
    """Fragt Claude ueber den Shim -> (Text fuer den Chat, ok). Wirft nie; Fehler kommen als verstaendlicher Text."""
    koerper = {"model": MODELL, "messages": [{"role": "system", "content": system}, *verlauf,
                                             {"role": "user", "content": text}]}
    try:
        antwort = _post_json(LLM_URL.rstrip("/") + "/chat/completions", koerper, LLM_ZEITLIMIT_S)
        inhalt = ((antwort.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        STAND["letztes_ergebnis"] = "ok"
        return inhalt, True
    except urllib.error.HTTPError as e:
        meldung = _fehlertext(e)
        STAND["letztes_ergebnis"] = f"http {e.code}"
        if e.code == 429 or "budget" in meldung:
            return "Die Tagesgrenze fuer den Telegram-Assistenten ist erreicht. Morgen geht es weiter.", False
        return f"Das hat nicht geklappt (HTTP {e.code}). Versuch es gleich noch einmal.", False
    except (urllib.error.URLError, TimeoutError, OSError):
        STAND["letztes_ergebnis"] = "shim nicht erreichbar"
        return "Der Assistent ist gerade nicht erreichbar (laeuft der PC?). Versuch es spaeter noch einmal.", False


class TelegramApi:
    def __init__(self, token: str):
        self.token = token
        self.basis = f"https://api.telegram.org/bot{token}/"

    def _rufen(self, methode: str, daten: dict, zeitlimit: float = 30) -> dict:
        try:
            antwort = _post_json(self.basis + methode, daten, zeitlimit)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(ohne_token(f"Telegram {methode}: {e}", self.token)) from None
        if not antwort.get("ok"):
            raise RuntimeError(ohne_token(f"Telegram {methode}: {antwort.get('description')}", self.token))
        return antwort

    def updates(self, versatz: int) -> list[dict]:
        return self._rufen("getUpdates", {"offset": versatz, "timeout": POLL_S,
                                          "allowed_updates": ["message"]}, POLL_S + 15).get("result") or []

    def senden(self, chat: int, text: str) -> None:
        for teil in zerlegen(text):
            self._rufen("sendMessage", {"chat_id": chat, "text": teil})

    def datei(self, file_id: str) -> bytes:
        pfad = self._rufen("getFile", {"file_id": file_id}).get("result", {}).get("file_path")
        if not pfad:
            raise RuntimeError("Telegram getFile: kein Pfad")
        try:
            with urllib.request.urlopen(f"https://api.telegram.org/file/bot{self.token}/{pfad}", timeout=60) as r:
                return r.read(DATEI_MAX + 1)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(ohne_token(f"Telegram Datei: {e}", self.token)) from None

    def tippt(self, chat: int) -> None:
        try:
            self._rufen("sendChatAction", {"chat_id": chat, "action": "typing"}, 10)
        except RuntimeError:
            pass


_whisper = None


def umschreiben(daten: bytes) -> str:
    """Sprachnachricht (Ogg/Opus) -> Text, am PC mit faster-whisper (CPU, int8). Kein Modell auf der VM."""
    global _whisper
    import io
    from faster_whisper import WhisperModel
    if _whisper is None:
        _whisper = WhisperModel(WHISPER_MODELL, device="cpu", compute_type="int8")
    # Wortschatz-Hinweis: ohne ihn wird "VibeMind" zu "Wie bemeint" (gemessen 20.10.2026).
    teile, _ = _whisper.transcribe(io.BytesIO(daten), language="de", vad_filter=True, initial_prompt=WORTSCHATZ)
    return " ".join(t.text.strip() for t in teile).strip()


def _zu_gross(eintrag: dict) -> bool:
    return int(eintrag.get("file_size") or 0) > DATEI_MAX


def verarbeiten(update: dict, telegram, verlauf: Verlauf, chat_id: int, system: str) -> None:
    if not erlaubt(update, chat_id):
        return                          # Fremden wird nicht geantwortet, nicht einmal abgesagt
    m = update["message"]
    text = (m.get("text") or "").strip()
    STAND["letzte_nachricht"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    inhalt: str | list = text           # was Claude bekommt
    merken = text                       # was in den Verlauf kommt (nur Text, nie Bild oder Ton)
    if not text and m.get("photo"):
        groesstes = max(m["photo"], key=lambda f: int(f.get("file_size") or 0))
        if _zu_gross(groesstes):
            telegram.senden(chat_id, "Das Foto ist zu gross (hoechstens 10 MB).")
            return
        bild = telegram.datei(groesstes["file_id"])
        if len(bild) > DATEI_MAX:
            telegram.senden(chat_id, "Das Foto ist zu gross (hoechstens 10 MB).")
            return
        beschriftung = (m.get("caption") or "").strip() or "Hier ist ein Foto. Was faellt dir dazu ein?"
        inhalt = [{"type": "text", "text": beschriftung},
                  {"type": "image_url",
                   "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(bild).decode("ascii")}}]
        merken = f"[Foto] {beschriftung}"
    elif not text and m.get("voice"):
        if _zu_gross(m["voice"]):
            telegram.senden(chat_id, "Die Sprachnachricht ist zu lang (hoechstens 10 MB).")
            return
        telegram.tippt(chat_id)
        gesagt = umschreiben(telegram.datei(m["voice"]["file_id"])).strip()
        if not gesagt:
            telegram.senden(chat_id, "Die Sprachnachricht habe ich nicht verstanden - magst du es nochmal sagen oder schreiben?")
            return
        telegram.senden(chat_id, f"🎤 Verstanden: {gesagt}")
        inhalt = merken = text = gesagt
    elif not text:
        if m.get("document") or m.get("audio") or m.get("video"):
            telegram.senden(chat_id, "Dateien kann ich noch nicht lesen - nur Text, Fotos und Sprachnachrichten.")
        else:
            telegram.senden(chat_id, "Ich verstehe Text, Fotos und Sprachnachrichten.")
        return
    befehl = text.split()[0].split("@")[0].lower() if text else ""
    if befehl in ("/neu", "/new", "/reset"):
        verlauf.leeren(chat_id)
        telegram.senden(chat_id, "Erledigt - wir fangen neu an.")
        return
    if befehl in ("/start", "/hilfe", "/help"):
        telegram.senden(chat_id, HILFE)
        return
    fertig = threading.Event()

    def _tippen():
        while not fertig.is_set():
            telegram.tippt(chat_id)
            fertig.wait(4)

    faden = threading.Thread(target=_tippen, daemon=True)
    faden.start()
    try:
        antwort, ok = antwort_holen(inhalt, verlauf.nachrichten(chat_id), system)
    finally:
        fertig.set()
        faden.join(timeout=1)
    if ok:                              # Fehlertexte kommen nicht in den Verlauf
        verlauf.anhaengen(chat_id, merken, antwort)
    telegram.senden(chat_id, antwort)


class _Gesundheit(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        rumpf = json.dumps({"ok": True, **STAND}).encode("utf-8")
        self.send_response(200 if self.path in ("/", "/health") else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(rumpf)

    def log_message(self, *_):
        pass


# Kampagnen komplett planen lassen (Betreiber 28.10.2026: „Agent komplett"). Hoechstens alle
# KAMPAGNEN_TAKT_S fragt der Bot die API nach Kampagnen in Planung; nur wenn es welche gibt, bekommt
# Claude den Auftrag (sonst kostet die Runde nichts). Claude plant mit den Marketing-Werkzeugen und
# meldet das Ergebnis in den Chat des Betreibers. Versendet wird dadurch nichts.
KAMPAGNEN_TAKT_S = int(os.environ.get("MARKETING_KAMPAGNEN_TAKT_S", "600"))
KAMPAGNEN_AUFTRAG = (
    "Es gibt Kampagnen in Planung. Rufe kampagnen_offen() auf und plane JEDE davon komplett: lies vorher "
    "plan_lesen und geschaeftsfakten, schlage mit kampagne_beitrag_vorschlagen passende Beitraege im "
    "Zeitraum vor (Zielgruppe sales = Newsletter/Mails an Kontakte, oeffentlich = Posts/Reels/Podcast "
    "auf den Kanaelen, verteiler = Newsletter), nicht zu dicht, mit klarer Notiz, was jeder Beitrag sagt, "
    "und schliesse mit kampagne_geplant ab. Antworte danach kurz: welche Kampagne, wie viele Beitraege, "
    "wann. Du versendest nichts.")


def kampagnen_runde(telegram, chat_id: int, system: str, jetzt: float, letzte: float,
                    offen=None, holen=None) -> float:
    """-> Zeitpunkt der letzten Pruefung. Fragt Claude nur, wenn eine Kampagne in Planung ist."""
    if jetzt - letzte < KAMPAGNEN_TAKT_S:
        return letzte
    if offen is None:
        from spaces.marketing.claw import werkzeuge
        offen = werkzeuge.kampagnen_offen
    stand = offen()
    if not stand.get("ok") or not stand.get("daten"):
        return jetzt
    text, ok = (holen or antwort_holen)(KAMPAGNEN_AUFTRAG, [], system)
    if text:
        telegram.senden(chat_id, ("Kampagnen geplant:\n" if ok else "") + text)
    return jetzt


def main() -> None:
    umgebung_laden()
    token = os.environ.get("TELEGRAM_BOT_TOKEN_MARKETING", "").strip()
    try:
        chat_id = int(os.environ.get("TELEGRAM_CHAT_ID", "0").strip() or 0)
    except ValueError:
        chat_id = 0
    if not token or not chat_id:
        raise SystemExit("TELEGRAM_BOT_TOKEN_MARKETING oder TELEGRAM_CHAT_ID fehlt")
    threading.Thread(target=HTTPServer(("127.0.0.1", PORT), _Gesundheit).serve_forever, daemon=True).start()
    telegram, verlauf, system = TelegramApi(token), Verlauf(ZUSTAND / "verlauf.json"), systemprompt()
    versatz_datei = ZUSTAND / "versatz.txt"
    try:
        versatz = int(versatz_datei.read_text().strip())
    except (OSError, ValueError):
        versatz = 0
    print(f"[telegram] laeuft, Gesundheit :{PORT}, Shim {LLM_URL}", flush=True)
    letzte_kampagnen = 0.0
    while True:
        try:
            letzte_kampagnen = kampagnen_runde(telegram, chat_id, system, time.time(), letzte_kampagnen)
        except Exception as e:  # noqa: BLE001 - die Runde darf den Bot nie beenden
            print(f"[telegram] Kampagnen-Runde: {ohne_token(str(e), token)}", flush=True)
            letzte_kampagnen = time.time()
        try:
            neue = telegram.updates(versatz)
            STAND["letzte_abfrage"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        except RuntimeError as e:
            print(f"[telegram] {e}", flush=True)
            time.sleep(10)
            continue
        for update in neue:
            versatz = int(update.get("update_id", 0)) + 1
            ZUSTAND.mkdir(parents=True, exist_ok=True)
            versatz_datei.write_text(str(versatz))      # vor dem Verarbeiten: ein Absturz wiederholt nichts doppelt
            try:
                verarbeiten(update, telegram, verlauf, chat_id, system)
            except Exception as e:  # noqa: BLE001 - eine Nachricht darf den Bot nicht beenden
                print(f"[telegram] Fehler: {ohne_token(str(e), token)}", flush=True)


if __name__ == "__main__":
    main()
