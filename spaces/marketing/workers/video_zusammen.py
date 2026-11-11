"""Fertigstellen eines Werkbank-Videos (11.11.2026): die freigegebenen Szenen-Clips vom Server holen, ohne
Neukodierung zusammenfuegen, den Ton in zwei Durchgaengen auf -14 LUFS bringen, das Ergebnis gegen die Guideline
messen, hochladen und mit Titel, Beschreibung, Kapitelmarken, Messnotiz und Warnungen melden.

Das Verbinden per Stream-Copy geht nur, weil jeder Clip dieselben Stromparameter hat (`video_rendern.normal_argumente`);
das wird vorher mit `pruefen` kontrolliert. ffmpeg-Ausgaben gehen in Dateien, nie in Pipes; Zeitlimits beenden den
Prozessbaum ueber die PID (Hilfen aus `video_rendern`). Texte an den Betreiber sind kurze deutsche Saetze ohne Pfade,
Sprechtexte oder Ausnahmetexte."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
from pathlib import Path

from spaces.marketing.claw import video_texte
from spaces.marketing.workers import video_claw
from spaces.marketing.workers.video_rendern import RenderFehler, _lauf, pruefen

LOG = logging.getLogger("video_zusammen")

LUFS_ZIEL = -14.0
WPM_ZIEL = (190, 230)
TRUEPEAK = -1.5
LRA_ZIEL = 11
MUSIK_PEGEL = 0.06
MESSNOTIZ_MAX = 1500             # wie die Werkbank-API
PLATZ_FAKTOR = 3                 # frei sein muss: Summe der Clip-Groessen x 3 (Clips, Verbindung, Endfassung)
PLATZ_START_BYTES = 512 * 1024 ** 2   # vor dem ersten Clip, dessen Groesse noch niemand kennt
SATZ_VIDEO = "Das Video ließ sich nicht zusammenfügen."
_CLIP_NR = re.compile(r"-s(\d{2,3})-f\d+\.mp4$")


class ZusammenFehler(Exception):
    """Zusammenfuegen oder Lautheit fehlgeschlagen; `grund` ist ein kurzer deutscher Satz ohne Pfade."""

    def __init__(self, grund: str):
        super().__init__(grund)
        self.grund = grund


# --- ffmpeg-Argumente --------------------------------------------------------------------------

def concat_argumente(liste: Path, ziel: Path) -> list[str]:
    return ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(liste),
            "-c", "copy", "-movflags", "+faststart", str(ziel)]


def _lautheit_filter(extra: str = "") -> str:
    return f"loudnorm=I={LUFS_ZIEL:g}:TP={TRUEPEAK:g}:LRA={LRA_ZIEL}{extra}"


def _ton_kette(filter_: str, musik: Path | None) -> list[str]:
    """Argumente ab den Eingaengen: ohne Musik `-af`, mit Musik das Bett (leise, endlos) per amix daruntergelegt
    und auf die Laenge des Videos geschnitten (duration=first), dann der Lautheitsfilter auf der Mischung."""
    if musik is None:
        return ["-af", filter_]
    return ["-filter_complex",
            f"[1:a]volume={MUSIK_PEGEL}[bett];[0:a][bett]amix=inputs=2:duration=first:normalize=0[mix];[mix]{filter_}[a]"]


def _eingaben(quelle: Path, musik: Path | None) -> list[str]:
    a = ["-i", str(quelle)]
    if musik is not None:
        a += ["-stream_loop", "-1", "-i", str(musik)]
    return a


def lautheit_messen_argumente(quelle: Path, musik: Path | None = None) -> list[str]:
    """Erster Durchgang: misst die Lautheit (JSON im Protokoll); mit `musik` an der Mischung."""
    a = ["ffmpeg", "-hide_banner", "-nostats", *_eingaben(quelle, musik), "-vn",
         *_ton_kette(_lautheit_filter(":print_format=json"), musik)]
    if musik is not None:
        a += ["-map", "[a]"]
    return a + ["-f", "null", "-"]


def lautheit_argumente(quelle: Path, ziel: Path, gemessen: dict, musik: Path | None = None) -> list[str]:
    """Zweiter Durchgang mit den Werten des ersten (`measured_*`); Bild kopiert, Ton AAC 48 kHz."""
    messwerte = (f":measured_I={gemessen['input_i']}:measured_TP={gemessen['input_tp']}"
                 f":measured_LRA={gemessen['input_lra']}:measured_thresh={gemessen['input_thresh']}"
                 f":offset={gemessen['target_offset']}:linear=true:print_format=summary")
    karte = ["-map", "0:v:0", "-map", "[a]" if musik is not None else "0:a:0"]
    return ["ffmpeg", "-y", "-loglevel", "error", *_eingaben(quelle, musik),
            *_ton_kette(_lautheit_filter(messwerte), musik), *karte,
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", str(ziel)]


def musik_datei(mandant: str) -> Path | None:
    """`MARKETING_VIDEO_MUSIK_ORDNER/<mandant>.mp3`, sonst None (dann kein Bett)."""
    ordner = os.environ.get("MARKETING_VIDEO_MUSIK_ORDNER", "")
    if not ordner or not re.fullmatch(r"[\w.-]+", mandant or "") or ".." in mandant:
        return None
    pfad = Path(ordner) / f"{mandant}.mp3"
    return pfad if pfad.is_file() else None


# --- Zusammenfuegen ----------------------------------------------------------------------------

def _nr(clip: Path, stelle: int) -> int:
    m = _CLIP_NR.search(clip.name)
    return int(m.group(1)) if m else stelle


def _ffmpeg(args: list[str], zeitlimit_s: float, schritt: str, protokoll: Path | None = None) -> None:
    try:
        code = _lauf(args, zeitlimit_s, None, dict(os.environ), protokoll)
    except RenderFehler as e:
        raise ZusammenFehler(f"{schritt} ist gescheitert: {e.grund}") from None
    if code != 0:
        raise ZusammenFehler(f"{schritt} ist gescheitert (ffmpeg Exit {code}).")


def _liste_zeile(pfad: Path) -> str:
    return "file '" + str(pfad.resolve()).replace("\\", "/").replace("'", "'\\''") + "'"


def _lautheit_lesen(protokoll: Path) -> dict:
    text = protokoll.read_text(encoding="utf-8", errors="replace")
    blocks = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", text)
    if not blocks:
        raise ZusammenFehler("Die Lautheit ließ sich nicht messen.")
    try:
        werte = json.loads(blocks[-1])
        gemessen = {k: str(werte[k]) for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
        if any(v.strip().lower() in ("-inf", "inf", "nan") for v in gemessen.values()):
            raise ValueError
        for v in gemessen.values():
            float(v)
    except (ValueError, KeyError, TypeError):
        raise ZusammenFehler("Die Lautheit ließ sich nicht messen (kein Ton?).") from None
    return gemessen


def zusammenfuegen(clips: list[Path], ziel: Path, musik: Path | None = None, zeitlimit_s: float = 1800) -> None:
    """Clips (in Reihenfolge) ohne Neukodierung verbinden, dann auf -14 LUFS bringen (Bild kopiert). Vorher wird
    geprueft, dass alle Clips 1920x1080, 30 fps und Ton haben; sonst `ZusammenFehler` mit der Szenennummer."""
    clips, ziel = [Path(c) for c in clips], Path(ziel)
    if not clips:
        raise ZusammenFehler("Es gibt keine Clips zum Zusammenfügen.")
    for i, clip in enumerate(clips, 1):
        nr = _nr(clip, i)
        try:
            p = pruefen(clip)
        except RenderFehler:
            raise ZusammenFehler(f"Der Clip der Szene {nr} ließ sich nicht prüfen.") from None
        if (p["breite"], p["hoehe"]) != (1920, 1080):
            raise ZusammenFehler(f"Szene {nr} hat {p['breite']}x{p['hoehe']} statt 1920x1080.")
        if p["fps"] != 30:
            raise ZusammenFehler(f"Szene {nr} hat {p['fps']} statt 30 Bilder pro Sekunde.")
        if not p["ton"]:
            raise ZusammenFehler(f"Szene {nr} hat keinen Ton.")
    ziel.parent.mkdir(parents=True, exist_ok=True)
    liste = ziel.with_name(ziel.stem + ".liste.txt")
    roh = ziel.with_name(ziel.stem + ".roh.mp4")
    protokoll = ziel.with_name(ziel.stem + ".lautheit.log")
    try:
        liste.write_text("\n".join(_liste_zeile(c) for c in clips) + "\n", encoding="utf-8")
        _ffmpeg(concat_argumente(liste, roh), zeitlimit_s, "Das Verbinden der Clips")
        _ffmpeg(lautheit_messen_argumente(roh, musik), zeitlimit_s, "Das Messen der Lautheit", protokoll)
        gemessen = _lautheit_lesen(protokoll)
        _ffmpeg(lautheit_argumente(roh, ziel, gemessen, musik), zeitlimit_s, "Das Angleichen der Lautheit")
        if not ziel.is_file():
            raise ZusammenFehler("Das fertige Video wurde nicht geschrieben.")
    except BaseException:
        ziel.unlink(missing_ok=True)
        raise
    finally:
        for f in (liste, roh, protokoll):
            f.unlink(missing_ok=True)


# --- Messnotiz ---------------------------------------------------------------------------------

def _tempo_satz(wpm: int) -> str:
    lo, hi = WPM_ZIEL
    ziel = f"{wpm} Wörter/min, Ziel {lo}–{hi}"
    if wpm < lo:
        return f"Sprechtempo: {ziel} ({lo - wpm} darunter)"
    if wpm > hi:
        return f"Sprechtempo: {ziel} ({wpm - hi} darüber)"
    return f"Sprechtempo: {ziel} (im Ziel)"


def messnotiz(werte: dict | None, szenen: list[dict], aufrufe: int) -> tuple[str, list[str]]:
    """(Notiz fuer den Betreiber, Warnungen). Die Notiz ist schlichtes Deutsch, hoechstens 1500 Zeichen: die
    Messung gegen die Guideline, das Sprechtempo als Abweichung vom Ziel und die Zahl der Modell-Aufrufe."""
    warnungen: list[str] = []
    teile: list[str] = []
    if werte is None:
        warnungen.append("Messung nicht möglich")
        teile.append("Messung nicht möglich: Das Video ließ sich nicht gegen die Guideline messen.")
    else:
        teile.append(video_claw.messung_text(werte))
    woerter = sum(len(str(s.get("sprechtext") or "").split()) for s in szenen)
    dauer = sum(float(s["dauer_s"]) for s in szenen
                if isinstance(s.get("dauer_s"), (int, float)) and not isinstance(s.get("dauer_s"), bool))
    if dauer <= 0 and werte:
        dauer = float(werte.get("dauer_s") or 0)
    if dauer > 0 and woerter:
        teile.append(_tempo_satz(round(woerter / (dauer / 60))))
    teile.append(f"Modell-Aufrufe: {int(aufrufe)}")
    lokal = sum(1 for s in szenen if s.get("engine") == "chatterbox")
    eleven = sum(1 for s in szenen if s.get("engine") == "elevenlabs")
    if lokal and eleven:
        warnungen.append(f"Stimmen gemischt: {lokal} Szenen lokal, {eleven} Szenen ElevenLabs")
    return "\n".join(teile)[:MESSNOTIZ_MAX], warnungen


# --- Fertigstellen -----------------------------------------------------------------------------

def arbeitsordner(aid8: str) -> Path:
    """%LOCALAPPDATA%\\VibeMind\\werkbank\\<aid8>\\video"""
    wurzel = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(wurzel) / "VibeMind" / "werkbank" / aid8 / "video"


def _frei(ordner: Path) -> int:
    return shutil.disk_usage(ordner).free


def _aufraeumen(ordner: Path, namen: list[str], leeren: bool) -> None:
    for n in namen:
        for f in (ordner / n, ordner / (n + ".teil")):
            f.unlink(missing_ok=True)
    if leeren:
        try:
            ordner.rmdir()       # nur, wenn nichts anderes darin liegt
        except OSError:
            pass


def _hinweis(api, aid: str, key: str, satz: str) -> None:
    try:
        api.hinweis(aid, key, satz)
    except Exception as e:  # noqa: BLE001 - ein verlorener Hinweis darf das Ergebnis nicht verdecken
        LOG.warning("Hinweis scheiterte: %s", type(e).__name__)


def fertigstellen(api, werk: dict, ordner: Path | None = None) -> str:
    """Alle Szenen sind fertig: Clips holen, verbinden, Lautheit, messen, hochladen, melden. Rueckgabe `video`
    oder `fehler` (dann ein Hinweis; die Szenen bleiben fertig, auf dem Server wird nichts geloescht)."""
    aid = str(werk["auftrag"])
    aid8 = aid.replace("-", "")[:8]
    fassung = int(werk.get("video_fassung") or 0) + 1
    name = f"wb-{aid8}-video-f{fassung}.mp4"
    key = f"video_f{fassung}"
    szenen = sorted((s for s in werk.get("szenen") or [] if isinstance(s, dict)), key=lambda s: int(s["nr"]))
    eigener = ordner is None      # der Standardordner gehoert uns und geht mit; ein uebergebener bleibt
    ordner = arbeitsordner(aid8) if eigener else Path(ordner)
    angelegt: list[str] = []
    try:
        if not szenen or any(not s.get("clip_datei") for s in szenen):
            raise ZusammenFehler("Ein Szenen-Clip fehlt.")
        ordner.mkdir(parents=True, exist_ok=True)
        clips, geladen = [], 0
        for i, s in enumerate(szenen):
            ziel = ordner / str(s["clip_datei"])
            if i == 0:
                benoetigt = PLATZ_START_BYTES
            else:               # die Groesse der restlichen Clips folgt aus den bisherigen (gleiches Format)
                benoetigt = geladen / i * len(szenen) * PLATZ_FAKTOR - geladen
            if _frei(ordner) < benoetigt:
                raise ZusammenFehler("Auf dem PC ist nicht genug Platz für das Video frei.")
            angelegt.append(ziel.name)
            geladen += api.holen(aid, ziel.name, ziel)
            clips.append(ziel)
        angelegt.append(name)
        video = ordner / name
        musik = musik_datei(str(werk.get("mandant") or ""))
        dauer = sum(float(s["dauer_s"]) for s in szenen if isinstance(s.get("dauer_s"), (int, float)))
        zusammenfuegen(clips, video, musik, zeitlimit_s=max(1800.0, 2 * dauer))
        gesamt = pruefen(video)["dauer_s"]
        log = video_claw.video_messen(str(video), zeitlimit_s=max(120, 2 * gesamt),
                                      melden=lambda g: LOG.warning("Messung: %s", g))
        werte = video_claw.messwerte(log) if log else None
        notiz, warnungen = messnotiz(werte, szenen, int(werk.get("aufrufe") or 0))
        kopf = video_texte.kopf(werk.get("gliederung") or {}, szenen)
        api.hochladen(aid, name, video)
        api.fertig(aid, {"datei": name, "bytes": video.stat().st_size, "dauer_s": round(gesamt, 2),
                         "titel": kopf["titel"], "beschreibung": kopf["beschreibung"],
                         "zeitmarken": kopf["zeitmarken"], "messnotiz": notiz, "warnungen": warnungen})
    except ZusammenFehler as e:
        LOG.warning("Video %s: %s", aid8, e.grund)
        _hinweis(api, aid, key, f"{SATZ_VIDEO} {e.grund}")
        return "fehler"
    except Exception as e:  # noqa: BLE001 - Holen, Messen, Hochladen, Melden: nur der Typname geht ins Protokoll
        LOG.warning("Video %s scheitert: %s", aid8, type(e).__name__)
        _hinweis(api, aid, key, SATZ_VIDEO)
        return "fehler"
    finally:
        _aufraeumen(ordner, angelegt, eigener)
    return "video"
