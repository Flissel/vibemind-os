"""Higgsfield-Arbeiter der Video-Werkbank am PC (24.11.2026). Eigener kleiner Dienst neben dem Werkbank-Arbeiter,
damit ein Clip, der Minuten braucht, das Rendern der Szenen nicht aufhaelt. Gesundheits-Port 8156.

Je Durchlauf zwei Dinge:
1. VORSCHLAGEN. Fuer jede Stelle, an der ein Uebergang vorgesehen ist und beide Szenen fertig sind: letztes Bild
   der einen und erstes Bild der naechsten Szene ziehen und hochladen, einen Auftragstext aus der festen Sammlung
   waehlen, den Preis abfragen (kostet nichts) und den Vorschlag anlegen. Bestellt wird dabei nichts.
2. BESTELLEN. Hoechstens einen Clip, den ein Mensch auf der Sales-Seite freigegeben hat: Preis noch einmal
   abfragen und mit dem freigegebenen vergleichen, bestellen, Higgsfields Auftragsnummer SOFORT vermerken, warten,
   laden, auf das Format der Szenen bringen (1920x1080, 30 Bilder, stumme Tonspur, rund 2 Sekunden), hochladen, melden.

Regeln, die das Geld schuetzen (die Datenbank setzt sie ebenfalls durch, Migration 102):
- Ohne freigegebenen Clip von der API wird nie bestellt. Weicht der Preis ab, wird nicht bestellt.
- Ein Clip, der schon eine Auftragsnummer hat, wird nie neu bestellt: es wird nur nach der Nummer gefragt.
- Kommt nach dem Bestellen keine Nummer zurueck, wird sie in Higgsfields Liste gesucht. Findet sie sich nicht, geht
  der Clip auf Fehler und wartet auf einen Menschen.
- Jede Bestellung steht mit Zeit, Nummer und Preis im Tagebuch am PC (ohne Auftragstext).
Texte an den Betreiber sind kurze deutsche Saetze ohne Pfade, Auftragstexte oder Ausnahmetexte."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from spaces.marketing.claw import hf_motive
from spaces.marketing.workers import higgsfield_cli
from spaces.marketing.workers.bild_worker import ApiFehler, umgebung_laden
from spaces.marketing.workers.higgsfield_cli import HfFehler, HfUnklar
from spaces.marketing.workers.video_rendern import FPS, RenderFehler, _lauf, pruefen
from spaces.marketing.workers.werkbank_api import WerkbankApi

LOG = logging.getLogger("werkbank_hf")
PORT = 8156
TAKT_S = 30
DAUER_S = 3                      # bestellte Laenge (kuerzeste des Modells)
ZIEL_S = 2.0                     # Laenge im Film: der Clip wird beschleunigt, nicht abgeschnitten (das Endbild bleibt)
WARTEN_S = 20 * 60               # so lange wird je Durchlauf auf Higgsfield gewartet
ABFRAGE_S = 10
LEIHE_S = 60                     # so oft wird die Leihe beim Warten verlaengert
PARK_S = 600                     # eine Stelle, deren Vorschlag scheiterte, ruht so lange
LADE_VERSUCHE = 3                # so oft wird ein fertiger Clip geholt, bevor er auf Fehler geht
MELDE_VERSUCHE = 5
LIEFERFRIST_S = 60 * 60          # liefert Higgsfield so lange nach dem Bestellen nicht, geht der Clip auf Fehler
RAND_S = 0.4                     # Standbilder so weit vom Rand der Szene (eine Blende laesst den Rand leer)
LADEN_MAX = 200 * 1024 ** 2
BILD_MAX = 2 * 1024 ** 2
HOSTS = (".cloudfront.net", ".higgsfield.ai")
SZENEN_CLIP = re.compile(r"wb-([0-9a-f]{8})-s([0-9]{2,3})-f([0-9]{1,3})\.mp4")
STANDBILD = re.compile(r"wb-([0-9a-f]{8})-s([0-9]{2,3})-f([0-9]{1,3})-([az])\.jpg")
SATZ_UNKLAR = "Die Bestellung ist unklar. Bitte bei Higgsfield nachsehen, ob der Clip dort entstanden ist."
SATZ_HF_GESCHEITERT = "Higgsfield konnte den Clip nicht erzeugen."
SATZ_LADEN = "Der Clip ist bei Higgsfield fertig, ließ sich aber nicht laden oder umwandeln."
SATZ_LIEFERFRIST = ("Higgsfield hat den bestellten Clip nach einer Stunde nicht geliefert. Die Auftragsnummer ist "
                    "vermerkt; bitte bei Higgsfield nachsehen, bevor du neu bestellst.")
SATZ_ANMELDUNG = "Higgsfield-Übergänge warten: Das Programm am PC nennt keinen Preis. Bitte die Anmeldung prüfen."
STAND: dict = {"dienst": "werkbank_hf", "letztes_ergebnis": None, "letzter_lauf": None}
GEDAECHTNIS: dict = {"geparkt": {}, "versuche": {}}


def wurzel() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")) / "VibeMind" / "werkbank"


def arbeitsordner(aid8: str) -> Path:
    """%LOCALAPPDATA%\\VibeMind\\werkbank\\<aid8>\\hf"""
    return wurzel() / aid8 / "hf"


def _offen_datei() -> Path:
    return wurzel() / "hf-offen.json"


def offen_lesen() -> dict:
    """Bestellungen, deren Nummer die API noch nicht angenommen hat: {clip: {nummer, preis}}. Ueberlebt einen Neustart."""
    try:
        daten = json.loads(_offen_datei().read_text(encoding="utf-8"))
        return daten if isinstance(daten, dict) else {}
    except (OSError, ValueError):
        return {}


def offen_schreiben(offen: dict) -> None:
    try:
        wurzel().mkdir(parents=True, exist_ok=True)
        teil = _offen_datei().with_suffix(".json.teil")
        teil.write_text(json.dumps(offen), encoding="utf-8")
        os.replace(teil, _offen_datei())
    except OSError as e:
        LOG.warning("Offene Meldungen ließen sich nicht schreiben (%s)", type(e).__name__)


def tagebuch(clip: str, auftrag8: str, nummer: str, preis: float) -> None:
    """Eine Zeile je Bestellung (Zeit, Clip, Auftrag, Higgsfield-Nummer, Preis). Kein Auftragstext."""
    try:
        ordner = wurzel()
        ordner.mkdir(parents=True, exist_ok=True)
        with (ordner / "hf-bestellungen.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"zeit": datetime.now(timezone.utc).isoformat(timespec="seconds"), "clip": clip,
                                "auftrag": auftrag8, "nummer": nummer, "preis": preis}) + "\n")
    except OSError as e:
        LOG.warning("Tagebuch ließ sich nicht schreiben (%s)", type(e).__name__)


# --- ffmpeg ------------------------------------------------------------------------------------

def standbild_argumente(clip: Path, ziel: Path, seite: str, guete: int = 2) -> list[str]:
    """Bild vom Anfang (`a`) oder Ende (`z`) eines Clips als JPEG, je 0,4 s vom Rand: eine Szene mit Blende ist in
    ihren ersten und letzten 0,2 s nur Hintergrund."""
    stelle = ["-ss", str(RAND_S)] if seite == "a" else ["-sseof", f"-{RAND_S}"]
    return ["ffmpeg", "-y", "-loglevel", "error", *stelle, "-i", str(clip), "-frames:v", "1", "-update", "1",
            "-q:v", str(guete), str(ziel)]


def uebergang_argumente(quelle: Path, ziel: Path, faktor: float) -> list[str]:
    """Higgsfield-Clip ins Einheitsformat der Szenen-Clips (`video_rendern.normal_argumente`: H.264 yuv420p, 30 fps,
    1920x1080, AAC 48 kHz Stereo), mit stummer Tonspur und um `faktor` (<= 1) beschleunigt."""
    return ["ffmpeg", "-y", "-loglevel", "error", "-i", str(quelle),
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-map", "0:v:0", "-map", "1:a:0",
            "-vf", f"setpts={faktor:.4f}*PTS,scale=1920:1080,setsar=1", "-r", str(FPS),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2",
            "-shortest", "-movflags", "+faststart", str(ziel)]


def ffmpeg(args: list[str], zeitlimit_s: float = 300) -> None:
    if _lauf(args, zeitlimit_s, None, dict(os.environ)) != 0:
        raise RenderFehler("ffmpeg ist gescheitert.")


def standbild(lauf, clip: Path, ziel: Path, seite: str) -> None:
    """Standbild ziehen; ist es groesser als 2 MB (die API nimmt nicht mehr), noch einmal mit staerkerer Pressung."""
    for guete in (2, 6, 12):
        lauf(standbild_argumente(clip, ziel, seite, guete))
        if ziel.is_file() and 0 < ziel.stat().st_size <= BILD_MAX:
            return
    raise RenderFehler("Das Standbild ließ sich nicht ziehen.")


def angleichen(lauf, roh: Path, ziel: Path) -> float:
    """Roh-Clip von Higgsfield -> Uebergang im Format der Szenen; gibt die Dauer zurueck."""
    dauer = pruefen(roh)["dauer_s"]
    if dauer <= 0:
        raise RenderFehler("Der Clip von Higgsfield ist leer.")
    lauf(uebergang_argumente(roh, ziel, min(1.0, ZIEL_S / dauer)))
    p = pruefen(ziel)
    if (p["breite"], p["hoehe"]) != (1920, 1080) or p["fps"] != FPS or not p["ton"] or p["dauer_s"] <= 0:
        ziel.unlink(missing_ok=True)
        raise RenderFehler("Der Übergang hat nicht das Format der Szenen.")
    return round(p["dauer_s"], 2)


# --- Laden von Higgsfield ----------------------------------------------------------------------

def _host_erlaubt(url: str) -> bool:
    teile = urllib.parse.urlsplit(url)
    host = (teile.hostname or "").lower()
    return teile.scheme == "https" and any(host.endswith(h) for h in HOSTS)


class _NurErlaubt(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _host_erlaubt(newurl):
            raise urllib.error.HTTPError(newurl, code, "Weiterleitung auf fremden Host", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def url_laden(url: str, ziel: Path) -> int:
    """Ergebnis eines Auftrags laden: nur https von Higgsfields Hosts, hoechstens 200 MB, ueber eine .teil-Datei."""
    if not _host_erlaubt(url):
        raise HfFehler("Die Adresse des Clips gehört nicht zu Higgsfield.")
    teil = ziel.with_name(ziel.name + ".teil")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "werkbank"})
        with urllib.request.build_opener(_NurErlaubt).open(req, timeout=120) as r, teil.open("wb") as f:
            geladen = 0
            while True:
                stueck = r.read(1024 * 1024)
                if not stueck:
                    break
                geladen += len(stueck)
                if geladen > LADEN_MAX:
                    raise HfFehler("Der Clip von Higgsfield ist zu groß.")
                f.write(stueck)
        if geladen == 0:
            raise HfFehler("Der Clip von Higgsfield ist leer.")
        teil.replace(ziel)
        return geladen
    except BaseException:
        teil.unlink(missing_ok=True)
        raise


# --- Durchlauf ---------------------------------------------------------------------------------

class _KeinPreis(Exception):
    """Das Programm nannte beim Vorschlagen keinen Preis (meist: Anmeldung abgelaufen)."""


class _Lauf:
    def __init__(self, api, cli, lauf, laden, warte, jetzt, ordner, gedaechtnis):
        self.api, self.cli, self.lauf, self.laden = api, cli, lauf, laden
        self.warte, self.jetzt, self.ordner, self.g = warte, jetzt, ordner, gedaechtnis


def _aid8(aid: str) -> str:
    return str(aid).replace("-", "")[:8]


def _szenen_clip(name, aid8: str, nr: int, variante: int) -> str:
    m = SZENEN_CLIP.fullmatch(str(name or ""))
    if not m or m.group(1) != aid8 or int(m.group(2)) != nr or int(m.group(3)) != variante:
        raise HfFehler("Der Clip der Szene passt nicht zur Stelle.")
    return m.group(0)


def _vorschlagen(c: _Lauf, werk: dict, f: dict) -> str:
    """Einen fehlenden Vorschlag anlegen. Bestellt nichts."""
    aid = str(werk["auftrag"])
    aid8 = _aid8(aid)
    von_nr, nach_nr = int(f["von_nr"]), int(f["nach_nr"])
    von_v, nach_v = int(f["von_variante"]), int(f["nach_variante"])
    von_clip = _szenen_clip(f.get("von_datei"), aid8, von_nr, von_v)
    nach_clip = _szenen_clip(f.get("nach_datei"), aid8, nach_nr, nach_v)
    ordner = c.ordner(aid8)
    ordner.mkdir(parents=True, exist_ok=True)
    start = ordner / f"wb-{aid8}-s{von_nr:02d}-f{von_v}-z.jpg"
    ende = ordner / f"wb-{aid8}-s{nach_nr:02d}-f{nach_v}-a.jpg"
    geholt: list[Path] = []
    try:
        for clip, bild, seite in ((von_clip, start, "z"), (nach_clip, ende, "a")):
            pfad = ordner / clip
            geholt.append(pfad)
            c.api.holen(aid, clip, pfad)
            standbild(c.lauf, pfad, bild, seite)
            c.api.bild_ablegen(aid, bild.name, bild)
    finally:
        for p in geholt:
            p.unlink(missing_ok=True)
    prompt = hf_motive.auftragstext(werk.get("titel"), werk.get("thema"), f.get("von_kurz"), f.get("nach_kurz"))
    try:
        preis = c.cli.preis(prompt, start, ende, DAUER_S)
    except HfFehler:
        raise _KeinPreis() from None
    c.api.hf_vorschlag(aid, {"nach_nr": nach_nr, "von_variante": von_v, "nach_variante": nach_v,
                             "start_bild": start.name, "end_bild": ende.name, "prompt": prompt,
                             "dauer_s": DAUER_S, "preis": preis, "modell": higgsfield_cli.MODELL})
    return "vorschlag"


def _alle_vorschlagen(c: _Lauf) -> str | None:
    ergebnis = None
    for werk in c.api.hf_abgleich():
        for f in werk.get("fehlend") or []:
            schluessel = (str(werk.get("auftrag")), f.get("nach_nr"), f.get("von_variante"), f.get("nach_variante"))
            if c.g["geparkt"].get(schluessel, 0) > c.jetzt():
                continue
            try:
                ergebnis = _vorschlagen(c, werk, f)
            except _KeinPreis:
                c.g["geparkt"][schluessel] = c.jetzt() + PARK_S
                LOG.warning("Vorschlag scheiterte: kein Preis")
                _hinweis(c, str(werk.get("auftrag")), "hf_anmeldung", SATZ_ANMELDUNG)
                ergebnis = "fehler"
            except Exception as e:  # noqa: BLE001 - nur der Typname; Texte koennen Auftragstexte enthalten
                c.g["geparkt"][schluessel] = c.jetzt() + PARK_S
                LOG.warning("Vorschlag scheiterte: %s", type(e).__name__)
                ergebnis = "fehler"
    return ergebnis


def _hinweis(c: _Lauf, aid: str, key: str, satz: str) -> None:
    try:
        c.api.hinweis(aid, key, satz)
    except Exception as e:  # noqa: BLE001
        LOG.warning("Hinweis scheiterte: %s", type(e).__name__)


def _fehler_melden(c: _Lauf, hid: str, satz: str) -> str:
    try:
        c.api.hf_fehler(hid, satz)
    except Exception as e:  # noqa: BLE001 - die Leihe laeuft ab; ohne Nummer setzt die Datenbank den Fehler selbst
        LOG.warning("Fehlermeldung scheiterte: %s", type(e).__name__)
    return "fehler"


def _bilder(c: _Lauf, clip: dict, aid: str, aid8: str) -> tuple[Path, Path]:
    ordner = c.ordner(aid8)
    ordner.mkdir(parents=True, exist_ok=True)
    pfade = []
    for feld in ("start_bild", "end_bild"):
        m = STANDBILD.fullmatch(str(clip.get(feld) or ""))
        if not m or m.group(1) != aid8:
            raise HfFehler("Ein Standbild des Clips passt nicht zum Auftrag.")
        pfad = ordner / m.group(0)
        if not pfad.is_file():
            c.api.bild_holen(aid, m.group(0), pfad)
        pfade.append(pfad)
    return pfade[0], pfade[1]


def _nummer_senden(c: _Lauf, hid: str, nummer: str, preis: float) -> bool | None:
    """True = vermerkt. False = die API lehnt sie endgueltig ab (der Clip nimmt keine Nummer mehr an). None = nicht
    durchgekommen (Netz, Serverfehler, unlesbare Antwort): spaeter noch einmal."""
    try:
        return bool(c.api.hf_auftrag(hid, nummer, preis))
    except ApiFehler as e:
        LOG.warning("Auftragsnummer nicht angenommen: API %s", e.code)
        return False if 400 <= e.code < 500 else None
    except Exception as e:  # noqa: BLE001 - jede andere Stoerung ist voruebergehend; die Nummer bleibt vorgemerkt
        LOG.warning("Auftragsnummer nicht gemeldet: %s", type(e).__name__)
        return None


def _nummer_melden(c: _Lauf, hid: str, nummer: str, preis: float) -> bool:
    """Die Nummer vermerken. Sie steht vorher schon in hf-offen.json: kommt die Meldung jetzt nicht durch, traegt
    `_nachmelden` sie in den naechsten Durchlaeufen nach (die Datenbank nimmt sie auch noch an, wenn der Clip
    inzwischen als unterbrochen auf Fehler steht). So wird ein bezahlter Clip nicht verwaist."""
    offen = offen_lesen()
    offen[hid] = {"nummer": nummer, "preis": preis}
    offen_schreiben(offen)
    for i in range(MELDE_VERSUCHE):
        ergebnis = _nummer_senden(c, hid, nummer, preis)
        if ergebnis is not None:
            offen = offen_lesen()
            offen.pop(hid, None)
            offen_schreiben(offen)
            return ergebnis
        c.warte(min(30, 2 * (i + 1)))
    return False


def _nachmelden(c: _Lauf) -> None:
    """Vorgemerkte Nummern nachtragen (nach Netzausfall oder Neustart)."""
    offen = offen_lesen()
    for hid, e in list(offen.items()):
        if not isinstance(e, dict) or not isinstance(e.get("nummer"), str):
            offen.pop(hid, None)
            continue
        if _nummer_senden(c, hid, e["nummer"], e.get("preis")) is not None:
            offen.pop(hid, None)
    if offen != offen_lesen():
        offen_schreiben(offen)


def _bestellen(c: _Lauf, clip: dict) -> str:
    """Einen freigegebenen Clip bestellen (oder, mit Nummer, weiter auf ihn warten), laden, angleichen, melden."""
    hid, aid = str(clip["id"]), str(clip["auftrag"])
    aid8 = _aid8(aid)
    nummer = clip.get("hf_auftrag")
    if not nummer:
        try:
            prompt, dauer, modell = str(clip["prompt"]), int(clip["dauer_s"]), str(clip["modell"])
            start, ende = _bilder(c, clip, aid, aid8)
            preis = c.cli.preis(prompt, start, ende, dauer, modell)
        except HfFehler as e:
            return _fehler_melden(c, hid, f"Nicht bestellt: {e.grund}")
        except Exception as e:  # noqa: BLE001
            LOG.warning("Vor dem Bestellen gescheitert: %s", type(e).__name__)
            return _fehler_melden(c, hid, "Nicht bestellt: Die Bilder oder der Preis ließen sich nicht holen.")
        if not c.api.hf_preis(hid, preis):          # Preis weicht ab oder der Clip ist nicht mehr dran: NICHT bestellen
            return "preis"
        seit = (datetime.now(timezone.utc) - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%S")
        try:
            nummer = c.cli.bestellen(prompt, start, ende, dauer, modell)
        except HfUnklar:
            nummer = c.cli.nummer_suchen(prompt, seit, modell)
            if not nummer:
                return _fehler_melden(c, hid, SATZ_UNKLAR)
        except HfFehler as e:
            return _fehler_melden(c, hid, f"Nicht bestellt: {e.grund}")
        tagebuch(hid, aid8, nummer, preis)
        if not _nummer_melden(c, hid, nummer, preis):
            return "fehler"
    return _abholen(c, clip, hid, aid, aid8, str(nummer))


def _zu_alt(clip: dict) -> bool:
    """Ist die Bestellung aelter als die Lieferfrist?"""
    try:
        bestellt = datetime.fromisoformat(str(clip.get("bestellt_am")).replace("Z", "+00:00"))
        if bestellt.tzinfo is None:
            bestellt = bestellt.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - bestellt).total_seconds() > LIEFERFRIST_S


def _abholen(c: _Lauf, clip: dict, hid: str, aid: str, aid8: str, nummer: str) -> str:
    ende = c.jetzt() + WARTEN_S
    leihe = c.jetzt() + LEIHE_S
    url = None
    while url is None:
        try:
            z = c.cli.stand(nummer)
        except HfFehler:
            z = {"zustand": "laeuft"}
        if z["zustand"] == "fertig":
            url = z["url"]
            break
        if z["zustand"] == "gescheitert":
            return _fehler_melden(c, hid, SATZ_HF_GESCHEITERT)
        if _zu_alt(clip):                            # kein Dauerwarten: der Mensch entscheidet (Nummer bleibt vermerkt)
            return _fehler_melden(c, hid, SATZ_LIEFERFRIST)
        if c.jetzt() >= ende:
            return "wartet"                          # der Clip bleibt bestellt; spaeter wird nach der Nummer gefragt
        c.warte(ABFRAGE_S)
        if c.jetzt() >= leihe:
            leihe = c.jetzt() + LEIHE_S
            try:
                if not c.api.hf_weiter(hid):
                    return "abgegeben"
            except Exception as e:  # noqa: BLE001
                LOG.warning("Leihe nicht verlaengert: %s", type(e).__name__)
    ordner = c.ordner(aid8)
    ordner.mkdir(parents=True, exist_ok=True)
    name = f"wb-{aid8}-h{int(clip['nach_nr']):02d}-f{int(clip['versuch'])}.mp4"
    roh, ziel = ordner / (name + ".roh.mp4"), ordner / name
    try:
        c.laden(url, roh)
        dauer = angleichen(c.lauf, roh, ziel)
        c.api.hochladen(aid, name, ziel)
        if not c.api.hf_fertig(hid, name, dauer):
            raise ApiFehler(409, "nicht angenommen")
    except Exception as e:  # noqa: BLE001 - der Clip ist bezahlt: erst mehrmals holen, dann Fehler fuer den Menschen
        n = c.g["versuche"].get(hid, 0) + 1
        c.g["versuche"][hid] = n
        LOG.warning("Abholen scheiterte (%s. Versuch): %s", n, type(e).__name__)
        if n >= LADE_VERSUCHE:
            return _fehler_melden(c, hid, SATZ_LADEN)
        return "spaeter"
    finally:
        roh.unlink(missing_ok=True)
        ziel.unlink(missing_ok=True)
    c.g["versuche"].pop(hid, None)
    return "clip"


def ein_durchlauf(api, cli=higgsfield_cli, *, lauf=ffmpeg, laden=url_laden, warte=time.sleep, jetzt=time.monotonic,
                  ordner=arbeitsordner, gedaechtnis: dict | None = None) -> str:
    """Erst fehlende Vorschlaege anlegen, dann hoechstens einen freigegebenen Clip bestellen. Rueckgabe: leer,
    vorschlag, clip, preis, wartet, spaeter, abgegeben oder fehler."""
    c = _Lauf(api, cli, lauf, laden, warte, jetzt, ordner, GEDAECHTNIS if gedaechtnis is None else gedaechtnis)
    ergebnis = "leer"
    _nachmelden(c)
    try:
        ergebnis = _alle_vorschlagen(c) or ergebnis
    except Exception as e:  # noqa: BLE001 - das Bestellen freigegebener Clips haengt nicht am Vorschlagen
        LOG.warning("Abgleich scheiterte: %s", type(e).__name__)
        ergebnis = "fehler"
    clip = api.hf_nehmen()
    if isinstance(clip, dict) and clip.get("id"):
        ergebnis = _bestellen(c, clip)
    return ergebnis


class _Gesundheit(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        rumpf = json.dumps(STAND, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(rumpf)

    def log_message(self, *_):
        pass


def main() -> None:
    umgebung_laden()
    basis, schluessel = os.environ.get("MARKETING_BILD_URL", ""), os.environ.get("MARKETING_BILD_KEY", "")
    if not basis or not schluessel:
        raise SystemExit("MARKETING_BILD_URL/MARKETING_BILD_KEY fehlen in Vibemind_V1/.env")
    try:
        higgsfield_cli.programm()
        STAND["programm"] = "gefunden"
    except HfFehler as e:
        STAND["programm"] = e.grund          # der Dienst laeuft weiter und meldet es je Vorschlag
    api = WerkbankApi(basis, schluessel)
    threading.Thread(target=HTTPServer(("127.0.0.1", PORT), _Gesundheit).serve_forever, daemon=True).start()
    while True:
        try:
            STAND["letztes_ergebnis"] = ein_durchlauf(api)
        except Exception as e:  # noqa: BLE001 - nichts darf die Schleife toeten
            STAND["letztes_ergebnis"] = f"fehler: {type(e).__name__}"
        STAND["letzter_lauf"] = time.strftime("%Y-%m-%d %H:%M:%S")
        print(STAND, flush=True)
        time.sleep(TAKT_S)


if __name__ == "__main__":
    main()
