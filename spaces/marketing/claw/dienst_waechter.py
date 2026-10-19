"""Waechter der Marketing-Dienste: holt tote und eingefrorene Dienste zurueck und meldet es per Telegram.

WARUM (gemessen 19.10.2026): Um 02:21 lief ein fehlgeschlagenes Herunterfahren; Windows beendete dabei
9 der 12 Marketing-Dienste. Der Ketten-Waechter meldete ":8130 ist tot" - aber niemand startete neu,
und Reels, Podcast und Bilder standen acht Stunden still.

Ein Lauf (geplante Aufgabe alle 5 Minuten):
- Die Dienstliste kommt aus `marketing-dienste-starten.ps1 -Liste`. Sie steht nur dort.
- Port tot -> starten (ueber `-Nur <name>` desselben Skripts).
- Port offen, aber keine HTTP-Antwort in STUMM_GRENZE Laeufen in Folge -> beenden und neu starten,
  aber nur Dienste mit eigenem Gesundheits-Thread. Die MCP-Sidecars blockieren waehrend langer
  Werkzeugaufrufe (reel_bauen bis 20 min) - sie werden wegen Stille nie beendet, nur gemeldet.
- Beendet wird nur der Prozess am Port, und nur wenn seine Kommandozeile alle Startargumente
  des Dienstes enthaelt (sonst koennte ein fremder Dienst auf dem Port sterben).
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
OHNE_FENSTER = 0x08000000  # CREATE_NO_WINDOW: die Aufgabe laeuft unsichtbar

LOG = logging.getLogger("marketing.waechter")


@dataclass(frozen=True)
class Regel:
    pfad: str | None   # HTTP-Pfad fuer die Lebendpruefung; None = nur der Port zaehlt
    beenden: bool      # darf wegen Stille beendet werden
    starten: bool      # darf gestartet werden, wenn der Port tot ist


REGELN: dict[str, Regel] = {
    "marketing_api": Regel("/api/health", True, True),
    "marketing_claw_mcp": Regel(None, False, True),
    "marketing_claw_shim": Regel("/health", True, True),
    "marketing_vorlagen_arbeiter": Regel("/", True, True),
    "comfyui": Regel(None, False, False),
    "marketing_bild_arbeiter": Regel("/", True, True),
    "marketing_chat_arbeiter": Regel("/", True, True),
    "marketing_ton_dienst": Regel("/healthz", True, True),
    "marketing_ton_arbeiter": Regel("/health", True, True),
    "marketing_video_werkzeuge": Regel(None, False, True),
    "marketing_video_shim": Regel("/health", True, True),
    "marketing_video_claw": Regel("/", True, True),
}
# Ein neuer Dienst im Startskript wird gestartet, wenn er fehlt, aber nie wegen Stille beendet.
STANDARD = Regel(None, False, True)


def entscheiden(befund: str, regel: Regel, eintrag: dict, jetzt: float) -> tuple[str, dict]:
    """Befund ok|tot|stumm -> Aktion nichts|starten|neustart|melden|aufgeben und der neue Zustandseintrag."""
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


def lauf(dienste: list[dict], zustand: dict, jetzt: float, *,
         pruefe: Callable[[int, str | None], str],
         lauscht: Callable[[int], bool],
         beende: Callable[[dict], bool],
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
        except Exception as fehler:  # noqa: BLE001 - keine Pruefung darf den Lauf toeten
            melde(f"{name} :{port}: Pruefung gescheitert ({type(fehler).__name__}: {fehler})")
            continue
        aktion, neu[name] = entscheiden(befund, regel, zustand.get(name, {}), jetzt)
        if aktion == "starten":
            zu_starten.append(d)
        elif aktion == "neustart":
            if beende(d):
                zu_starten.append(d)
            else:
                melde(f"{d['was']} :{port} antwortet nicht, wurde aber nicht beendet "
                      "(Prozess am Port passt nicht zum Dienst)")
        elif aktion == "melden":
            zustand_text = "ist tot" if befund == "tot" else "antwortet nicht"
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
    import psutil
    return any(c.laddr and c.laddr.port == port and c.status == psutil.CONN_LISTEN
               for c in psutil.net_connections(kind="tcp"))


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


def port_besitzer(port: int) -> int | None:
    import psutil
    for c in psutil.net_connections(kind="tcp"):
        if c.laddr and c.laddr.port == port and c.status == psutil.CONN_LISTEN and c.pid:
            return c.pid
    return None


def beende_dienst(d: dict) -> bool:
    import psutil
    pid = port_besitzer(d["port"])
    if pid is None:
        return True  # inzwischen tot - dann einfach starten
    try:
        kommandozeile = psutil.Process(pid).cmdline()
    except psutil.Error:
        return False
    if not prozess_passt(kommandozeile, d["args"]):
        LOG.warning("Beende-Sperre: PID %s auf :%s passt nicht zu %s: %s", pid, d["port"], d["name"], kommandozeile)
        return False
    LOG.info("beende %s (PID %s, :%s)", d["name"], pid, d["port"])
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=OHNE_FENSTER)
    for _ in range(20):
        if not port_lauscht(d["port"]):
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
            for d in dienste:
                print(f"{d['name']:<30} :{d['port']:<5} {http_pruefen(d['port'], REGELN.get(d['name'], STANDARD).pfad)}")
            return 0
        zustand = lauf(dienste, zustand_laden(), time.time(), pruefe=http_pruefen, lauscht=port_lauscht,
                       beende=beende_dienst, starte=starte_dienste, melde=meldungen.append)
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
