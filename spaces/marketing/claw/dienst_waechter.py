"""Waechter der Marketing-Dienste: holt tote und eingefrorene Dienste zurueck und meldet es per Telegram.

WARUM (gemessen 19.10.2026): Um 02:21 lief ein fehlgeschlagenes Herunterfahren; Windows beendete dabei
9 der 12 Marketing-Dienste. Der Ketten-Waechter meldete ":8130 ist tot" - aber niemand startete neu,
und Reels, Podcast und Bilder standen acht Stunden still.

Ein Lauf (geplante Aufgabe alle 5 Minuten):
- Die Dienstliste kommt aus `marketing-dienste-starten.ps1 -Liste`. Sie steht nur dort.
- Port tot -> starten (ueber `-Nur <name>` desselben Skripts). Ob der Port lebt, zeigt nur eine echte
  Verbindung - psutil meldete die Listener hier mit Status NONE statt LISTEN (03.11.2026).
- Lebt schon eine Kopie des Dienstes (Kommandozeile), wird nie eine zweite gestartet: Windows laesst
  mehrere Prozesse denselben Port binden, und am 03.11.2026 lief ton_dienst dreimal (je ~6 GB).
  Mehrere Kopien werden gemeldet, nicht beendet.
- Port offen, aber keine HTTP-Antwort in STUMM_GRENZE Laeufen in Folge -> beenden und neu starten,
  aber nur Dienste mit eigenem Gesundheits-Thread. Die MCP-Sidecars :8130 und :8137 fuehren ihre
  Werkzeuge seit 21./22.10.2026 in Threads aus (claw/mcp_huelle.py) und haben /health - sie werden
  wie die anderen neu gestartet, wenn sie schweigen.
- Beendet wird nur eine eindeutige Kopie: der eine Prozess(baum), dessen Kommandozeile alle
  Startargumente des Dienstes enthaelt. Bei mehreren Kopien ist nicht sicher, welche haengt.
- ComfyUI wird weder beendet (Zombie im GPU-Treiber) noch gestartet (belegt die GPU) - nur gemeldet.
- Hoechstens KAPPE Neustarts je Dienst in FENSTER_S, danach eine Meldung "gebe auf".
- `logs/marketing/waechter.aus` schaltet ihn ab (z. B. wenn Dienste bewusst gestoppt sind).

Aufruf:
    python -m spaces.marketing.claw.dienst_waechter            # ein Lauf
    python -m spaces.marketing.claw.dienst_waechter --zeigen   # nur pruefen, nichts tun, nichts senden
"""
from __future__ import annotations

import json
import logging
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

OS_WURZEL = Path(__file__).resolve().parents[3]           # vibemind-os
WURZEL = OS_WURZEL.parent                                  # Vibemind_V1
LOG_ORDNER = WURZEL / "logs" / "marketing"
ZUSTANDSDATEI = LOG_ORDNER / "waechter-zustand.json"
AUS_MARKE = LOG_ORDNER / "waechter.aus"
STARTSKRIPT = OS_WURZEL / "spaces" / "marketing" / "claw" / "scripts" / "marketing-dienste-starten.ps1"
TELEGRAM = WURZEL / "scripts" / "notify_telegram.py"

STUMM_GRENZE = 3
KAPPE = 3
FENSTER_S = 6 * 3600
MELDE_ABSTAND_S = 6 * 3600
HTTP_TIMEOUT_S = 10
VERBINDEN_TIMEOUT_S = 2
OHNE_FENSTER = 0x08000000  # CREATE_NO_WINDOW: die Aufgabe laeuft unsichtbar

LOG = logging.getLogger("marketing.waechter")


@dataclass(frozen=True)
class Regel:
    pfad: str | None   # HTTP-Pfad fuer die Lebendpruefung; None = nur der Port zaehlt
    beenden: bool      # darf wegen Stille beendet werden
    starten: bool      # darf gestartet werden, wenn der Port tot ist


REGELN: dict[str, Regel] = {
    "marketing_api": Regel("/api/health", True, True),
    # 22.10.2026: auch :8130 fuehrt Werkzeuge in Threads aus und hat /health (claw/mcp_huelle.py).
    "marketing_claw_mcp": Regel("/health", True, True),
    "marketing_claw_shim": Regel("/health", True, True),
    "marketing_vorlagen_arbeiter": Regel("/", True, True),
    "comfyui": Regel(None, False, False),
    "marketing_bild_arbeiter": Regel("/", True, True),
    "marketing_chat_arbeiter": Regel("/", True, True),
    "marketing_ton_dienst": Regel("/healthz", True, True),
    "marketing_ton_arbeiter": Regel("/health", True, True),
    # 21.10.2026: Werkzeuge laufen in Threads, /health antwortet auch waehrend reel_bauen.
    "marketing_video_werkzeuge": Regel("/health", True, True),
    "marketing_video_shim": Regel("/health", True, True),
    "marketing_video_claw": Regel("/", True, True),
    "marketing_auftrag_arbeiter": Regel("/", True, True),
    "marketing_telegram_shim": Regel("/health", True, True),
    "marketing_telegram_bot": Regel("/health", True, True),
}
# Ein neuer Dienst im Startskript wird gestartet, wenn er fehlt, aber nie wegen Stille beendet.
STANDARD = Regel(None, False, True)


def entscheiden(befund: str, regel: Regel, eintrag: dict, jetzt: float) -> tuple[str, dict]:
    """Befund ok|tot|stumm|verwaist -> Aktion nichts|starten|neustart|melden|aufgeben und der neue Eintrag.

    verwaist = Port zu, aber eine Kopie lebt (laedt noch oder haengt): nie starten, nach STUMM_GRENZE melden."""
    e = dict(eintrag)
    if befund == "ok":
        e["stumm"] = 0
        e["gemeldet"] = 0
        return "nichts", e

    def gedrosselt_melden() -> tuple[str, dict]:
        if not e.get("gemeldet") or jetzt - e["gemeldet"] >= MELDE_ABSTAND_S:
            e["gemeldet"] = jetzt
            return "melden", e
        return "nichts", e

    if befund == "verwaist":
        e["stumm"] = e.get("stumm", 0) + 1
        if e["stumm"] < STUMM_GRENZE:
            return "nichts", e
        return gedrosselt_melden()

    if befund == "stumm":
        e["stumm"] = e.get("stumm", 0) + 1
        if e["stumm"] < STUMM_GRENZE:
            return "nichts", e
        if not regel.beenden:
            return gedrosselt_melden()
        aktion = "neustart"
    else:
        if not regel.starten:
            return gedrosselt_melden()
        aktion = "starten"

    neustarts = [t for t in e.get("neustarts", []) if jetzt - t < FENSTER_S]
    e["neustarts"] = neustarts
    if len(neustarts) >= KAPPE:
        if not e.get("aufgegeben") or jetzt - e["aufgegeben"] >= FENSTER_S:
            e["aufgegeben"] = jetzt
            return "aufgeben", e
        return "nichts", e
    neustarts.append(jetzt)
    e["stumm"] = 0
    return aktion, e


def prozess_passt(kommandozeile: list[str], args: list[str]) -> bool:
    """Beende-Sperre: jedes Startargument (ausser -u) muss in der Kommandozeile stehen."""
    if not kommandozeile:
        return False
    vorhanden = {teil.lower() for teil in kommandozeile}
    return all(a.lower() in vorhanden for a in args if a != "-u")


def wurzeln(prozesse: list[tuple[int, int, list[str]]], args: list[str]) -> list[int]:
    """PIDs der laufenden Kopien eines Dienstes aus (pid, ppid, kommandozeile).

    Ein venv-python.exe startet den echten Interpreter als Kind mit derselben Kommandozeile - Eltern und
    Kind sind EINE Kopie; gezaehlt wird der oberste passende Prozess."""
    passend = {pid: ppid for pid, ppid, kommandozeile in prozesse if prozess_passt(kommandozeile, args)}
    return sorted(pid for pid, ppid in passend.items() if ppid not in passend)


def lauf(dienste: list[dict], zustand: dict, jetzt: float, *,
         pruefe: Callable[[int, str | None], str],
         lauscht: Callable[[int], bool],
         beende: Callable[[dict], bool],
         kopien: Callable[[dict], list[int]],
         starte: Callable[[list[str]], None],
         melde: Callable[[str], None]) -> dict:
    """Ein Waechter-Lauf. Gibt den neuen Zustand zurueck; jede Meldung geht einzeln an `melde`."""
    neu = dict(zustand)
    zu_starten: list[dict] = []
    for d in dienste:
        name, port = d["name"], d["port"]
        regel = REGELN.get(name, STANDARD)
        try:
            befund = pruefe(port, regel.pfad)
            pids = kopien(d)
        except Exception as fehler:  # noqa: BLE001 - keine Pruefung darf den Lauf toeten
            melde(f"{name} :{port}: Pruefung gescheitert ({type(fehler).__name__}: {fehler})")
            continue
        if befund == "tot" and pids:
            befund = "verwaist"
        aktion, neu[name] = entscheiden(befund, regel, zustand.get(name, {}), jetzt)
        if befund == "ok" and len(pids) > 1:
            zuletzt = neu[name].get("doppelt_gemeldet")
            if not zuletzt or jetzt - zuletzt >= MELDE_ABSTAND_S:
                neu[name]["doppelt_gemeldet"] = jetzt
                melde(f"{d['was']} :{port} laeuft {len(pids)}-mal (PIDs {', '.join(map(str, pids))}) - "
                      "der Waechter beendet keine Kopie, bitte ansehen")
        if aktion == "starten":
            zu_starten.append(d)
        elif aktion == "neustart":
            if beende(d):
                zu_starten.append(d)
            else:
                melde(f"{d['was']} :{port} antwortet nicht, wurde aber nicht beendet "
                      "(keine eindeutige Kopie des Dienstes, siehe waechter.log)")
        elif aktion == "melden":
            zustand_text = {"tot": "ist tot", "verwaist": "Prozess laeuft, Port bleibt zu"}.get(
                befund, "antwortet nicht")
            melde(f"{d['was']} :{port} {zustand_text} - startet/beendet der Waechter bewusst nicht")
        elif aktion == "aufgeben":
            melde(f"{d['was']} :{port} kommt nicht zurueck ({KAPPE} Neustarts in "
                  f"{FENSTER_S // 3600} h) - Waechter gibt auf, bitte ansehen")
    if zu_starten:
        try:
            starte([d["name"] for d in zu_starten])
        except Exception as fehler:  # noqa: BLE001 - der Zustand (gezaehlte Neustarts) muss erhalten bleiben
            melde(f"Startskript gescheitert ({type(fehler).__name__}: {fehler})")
        for d in zu_starten:
            ergebnis = "laeuft wieder" if lauscht(d["port"]) else "START FEHLGESCHLAGEN (siehe err.log)"
            melde(f"{d['was']} :{d['port']} war ausgefallen - neu gestartet, {ergebnis}")
    return neu


# --- Anbindung an das echte System ----------------------------------------------------------

def port_lauscht(port: int) -> bool:
    """Nimmt 127.0.0.1:<port> eine Verbindung an?

    Nicht ueber psutil (03.11.2026 gemessen): Die Listener standen dort meist mit Status NONE statt
    LISTEN, :8135 in 30 von 30 Abfragen. Laufende Dienste galten als tot und wurden erneut gestartet."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=VERBINDEN_TIMEOUT_S):
            return True
    except OSError:
        return False


def http_pruefen(port: int, pfad: str | None) -> str:
    if not port_lauscht(port):
        return "tot"
    if pfad is None:
        return "ok"
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{pfad}", timeout=HTTP_TIMEOUT_S):
            return "ok"
    except urllib.error.HTTPError:
        return "ok"  # jede HTTP-Antwort heisst: der Prozess lebt
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return "stumm"


def prozesse_lesen() -> list[tuple[int, int, list[str]]]:
    import psutil
    return [(p.info["pid"], p.info["ppid"], p.info["cmdline"] or [])
            for p in psutil.process_iter(["pid", "ppid", "cmdline"])]


def laufende_kopien(d: dict) -> list[int]:
    return wurzeln(prozesse_lesen(), d["args"])


def beende_dienst(d: dict) -> bool:
    """Nicht ueber den Port-PID: netstat nannte fuer :8138 PID 0, und mehrere Kopien teilten sich den Port."""
    pids = laufende_kopien(d)
    if not pids:
        return True  # keine Kopie mehr - dann einfach starten
    if len(pids) > 1:
        LOG.warning("Beende-Sperre: %s laeuft %d-mal (PIDs %s) - welche haengt, ist nicht sicher",
                    d["name"], len(pids), pids)
        return False
    LOG.info("beende %s (PID %s, :%s)", d["name"], pids[0], d["port"])
    subprocess.run(["taskkill", "/PID", str(pids[0]), "/T", "/F"], capture_output=True, creationflags=OHNE_FENSTER)
    for _ in range(20):
        if not laufende_kopien(d):
            return True
        time.sleep(0.5)
    return False


def _startskript(*argumente: str, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(STARTSKRIPT), *argumente],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        creationflags=OHNE_FENSTER)


def dienste_lesen() -> list[dict]:
    r = _startskript("-Liste", timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"Startskript -Liste: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout)


def starte_dienste(namen: list[str]) -> None:
    """Ausgabe in eine Datei, NICHT in eine Pipe: die gestarteten Dienste erben die Ausgabe-Handles des
    Startskripts. An einer Pipe wartete der Waechter deshalb, bis die Aufgabe abgebrochen wurde
    (gemessen 19.10.2026: Dienst lief, Zustand und Log fehlten)."""
    ausgabe = LOG_ORDNER / "waechter-start.log"
    with open(ausgabe, "a", encoding="utf-8") as datei:
        datei.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} -Nur {','.join(namen)}\n")
        datei.flush()
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(STARTSKRIPT),
             "-Nur", ",".join(namen)],
            stdin=subprocess.DEVNULL, stdout=datei, stderr=subprocess.STDOUT, timeout=180,
            creationflags=OHNE_FENSTER)
    LOG.info("Startskript -Nur %s -> %s (Ausgabe: %s)", namen, r.returncode, ausgabe)


def geschwaerzt(text: str) -> str:
    """Bot-Token aus Fehlerausgaben entfernen: urllib nennt im Fehler die URL /bot<token>/sendMessage."""
    return re.sub(r"bot\d+:[A-Za-z0-9_-]+", "bot<geschwaerzt>", text)


def telegram(text: str) -> None:
    try:
        r = subprocess.run([sys.executable, str(TELEGRAM), text], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30, creationflags=OHNE_FENSTER)
        if r.returncode == 0:
            LOG.info("Telegram gesendet")
        else:
            LOG.warning("Telegram nicht gesendet (rc=%s): %s", r.returncode,
                        geschwaerzt((r.stderr or r.stdout).strip())[:300])
    except Exception as fehler:  # noqa: BLE001 - die Meldung darf den Waechter nicht toeten
        LOG.warning("Telegram gescheitert: %s", fehler)


def zustand_laden() -> dict:
    try:
        return json.loads(ZUSTANDSDATEI.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main(argv: list[str] | None = None) -> int:
    argumente = sys.argv[1:] if argv is None else argv
    zeigen = "--zeigen" in argumente
    LOG_ORDNER.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=None if zeigen else LOG_ORDNER / "waechter.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
    if AUS_MARKE.exists() and not zeigen:
        LOG.info("abgeschaltet (%s)", AUS_MARKE)
        return 0
    meldungen: list[str] = []
    try:
        dienste = dienste_lesen()
        if zeigen:
            prozesse = prozesse_lesen()
            for d in dienste:
                befund = http_pruefen(d["port"], REGELN.get(d["name"], STANDARD).pfad)
                print(f"{d['name']:<30} :{d['port']:<5} {befund:<6} Kopien {wurzeln(prozesse, d['args'])}")
            return 0
        prozesse = prozesse_lesen()  # ein Schnappschuss je Lauf, nur fuer die Entscheidung
        zustand = lauf(dienste, zustand_laden(), time.time(), pruefe=http_pruefen, lauscht=port_lauscht,
                       beende=beende_dienst, kopien=lambda d: wurzeln(prozesse, d["args"]),
                       starte=starte_dienste, melde=meldungen.append)
        ZUSTANDSDATEI.write_text(json.dumps(zustand, indent=1), encoding="utf-8")
    except Exception as fehler:  # noqa: BLE001 - ein Waechter, der abstuerzt, meldet nichts
        meldungen.append(f"Waechter-Lauf gescheitert: {type(fehler).__name__}: {fehler}")
    for m in meldungen:
        LOG.warning(m)
    if meldungen:
        telegram("Marketing-Waechter:\n" + "\n".join(f"- {m}" for m in meldungen))
    else:
        LOG.info("alles in Ordnung")
    return 0


if __name__ == "__main__":
    sys.exit(main())
