"""Werkzeuge des Musik-Dienstes: die eine Schicht, durch die jedes Agenten-Werkzeug geht
(MCP-Server, Telegram-Bot, Video-Claw). Sie verbindet Laura-Client, Auftragsablage,
Laeufer und signierten Datei-Link.

Agenten sehen nur Auftrags-IDs und Dateinamen, nie einen Pfad. Jede oeffentliche Methode
ist fail-soft: sie wirft nicht, sondern gibt `{"ok": False, "fehler": <Grund>}` zurueck
(Grund hoechstens 300 Zeichen, ohne den Datenordner).
"""
from __future__ import annotations

import functools
import json
import logging
import math
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from djset.bestand import Bestand, BestandFehler, pruefe_herkunft, song_id_aus_titel
from djset.dienst import link as datei_link
from djset.dienst.auftraege import Ablage, Auftrag, reihenfolge
from djset.video.laenge import VideoFehler, aus_frames, aus_sekunden
from djset.video.vertrag import LABEL_MAX, LauraClip, VideomusikErgebnis
from djset.watch import AUDIO_SUFFIXES

LOG = logging.getLogger("djset.dienst.werkzeuge")

FEHLER_MAXLAENGE = 300
MAX_EXPORT_BYTES = 500 * 1024 * 1024  # der Download liegt im Speicher (laura.herunterladen)
BERICHT_ZEILEN = 40
EXPORT_DATEI = "laura-export.mp4"
# Bericht je Art: `dj-set video` schreibt bericht.md, `dj-set set` qa-report.md und tracklist.md.
BERICHT_DATEIEN = {"set": ("qa-report.md", "tracklist.md")}
BERICHT_VORGABE = ("bericht.md",)
GAIN_MIN, GAIN_MAX = 0, 400       # so weit laesst Laura gain_percent zu (models.py, Migration 0018)
GAIN_UNTER_SPRACHE = 25          # Musik unter Sprecher/Originalton
GAIN_ALLEIN = 100                # Musik ohne andere Tonspur
MUSIK_MARKE = "musik:"           # Labelteil, an dem die eigenen Musik-Clips erkennbar sind

_LAURA_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_LAUF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_BEENDET = ("fertig", "fertig_mit_befund", "gescheitert", "abgebrochen")
_FERTIG = ("fertig", "fertig_mit_befund")


_PFAD = re.compile(
    r"[A-Za-z]:[\\/][^\s\"']*"  # Laufwerkspfad
    r"|\\\\[^\s\"']+"  # UNC
    r"|/(?:home|Users|tmp|var|opt|etc|usr|mnt|root)/[^\s\"']*"  # POSIX
)


class _UnbekannterAuftrag(Exception):
    pass


def _fehler(text: str) -> dict:
    return {"ok": False, "fehler": text[:FEHLER_MAXLAENGE]}


def _ohne_pfade(text: str, daten: Path | None = None) -> str:
    """Absolute Pfade (und den Datenordner) aus Text fuer Agenten entfernen."""
    if daten is not None:
        for geheim in (str(daten), str(daten).replace("\\", "/")):
            text = text.replace(geheim, "<daten>")
    return _PFAD.sub("<pfad>", text)


def _weich(methode: Callable[..., dict]) -> Callable[..., dict]:
    """Fail-soft: jede Ausnahme wird zu `{"ok": False, "fehler": ...}`."""

    @functools.wraps(methode)
    def huelle(self: "Werkzeuge", *args: Any, **kwargs: Any) -> dict:
        try:
            return methode(self, *args, **kwargs)
        except _UnbekannterAuftrag:
            return _fehler("unbekannter Auftrag")
        except Exception as problem:  # noqa: BLE001 - Dienstgrenze
            LOG.warning("%s gescheitert: %s", methode.__name__, problem)
            return _fehler(_ohne_pfade(f"{type(problem).__name__}: {problem}", self.daten))

    return huelle


def _text(wert: object) -> bool:
    return isinstance(wert, str) and bool(wert.strip())


def _bare_datei(name: object) -> bool:
    return (
        isinstance(name, str)
        and bool(name)
        and not any(z in name for z in ("/", "\\", ":"))
        and ".." not in name
        and name == Path(name).name
    )


class Werkzeuge:
    def __init__(
        self,
        daten: Path,
        ablage: Ablage,
        laeufer: Any,
        laura_modul: Any,
        link_basis: str | None,
        link_schluessel: bytes | None,
        uhr: Callable[[], float] = time.time,
        *,
        reel_ordner: Path | None = None,
    ) -> None:
        self.daten = Path(daten)
        self.ablage = ablage
        self.laeufer = laeufer
        self.laura = laura_modul
        self.link_basis = link_basis
        self.link_schluessel = link_schluessel
        self.uhr = uhr
        self.reel_ordner = (
            Path(reel_ordner) if reel_ordner is not None
            else Path.home() / ".vibemind" / "video-claw"
        )
        self.bestand = Bestand(self.daten)
        self._sperren: dict[tuple[str, str], threading.Lock] = {}
        self._sperren_lock = threading.Lock()

    # ------------------------------------------------------------ Helfer

    def _hole(self, auftrag_id: str) -> Auftrag:
        try:
            return self.ablage.hole(auftrag_id)
        except KeyError:
            raise _UnbekannterAuftrag(auftrag_id) from None

    def _sperre(self, auftrag_id: str, timeline_id: str) -> threading.Lock:
        with self._sperren_lock:
            return self._sperren.setdefault((auftrag_id, timeline_id), threading.Lock())

    def _position(self, auftrag: Auftrag) -> int:
        """Wie viele Auftraege vor diesem drankommen: die wartenden, die `Ablage.naechster`
        vor ihm starten wuerde (gleiche Reihenfolge), plus 1, wenn gerade etwas laeuft.
        0 heisst: kommt als Naechster dran. Ein Richtwert, keine Zusage."""

        alle = self.ablage.alle()
        davor = sum(
            1 for a in alle
            if a.zustand == "wartet" and a.id != auftrag.id
            and reihenfolge(a) < reihenfolge(auftrag)
        )
        return davor + (1 if any(a.zustand == "laeuft" for a in alle) else 0)

    def _laufordner(self, auftrag: Auftrag) -> Path | None:
        if not auftrag.lauf or not _LAUF.fullmatch(auftrag.lauf):
            return None
        ordner = self.daten / "runs" / auftrag.lauf
        return ordner if ordner.is_dir() else None

    def _bestellt(self, art: str, eingabe: dict, kanal: str) -> dict:
        auftrag = self.ablage.anlegen(art, eingabe, kanal)
        return {"ok": True, "auftrag": auftrag.id, "position": self._position(auftrag)}

    # ------------------------------------------------------------ Bestand

    @_weich
    def bestand_zeigen(self) -> dict:
        songs = []
        for s in self.bestand.alle():
            m = s.messung
            songs.append({
                "id": s.id, "titel": s.titel,
                "bpm": round(m.bpm.value, 1) if m else None,
                "tonart": m.key.camelot if m else None,
                "sekunden": round(m.duration_s, 1) if m else None,
                "herkunft": s.herkunft,
                "getrennt": s.stimmen is not None,
            })
        return {"ok": True, "songs": songs}

    @_weich
    def song_aufnehmen(self, datei: str, titel: str, herkunft: str, suno_id: str = "",
                       kanal: str = "mcp") -> dict:
        if not _bare_datei(datei):
            return _fehler("Dateiname ungueltig: nur ein blosser Name aus dem Eingang ist erlaubt")
        if Path(datei).suffix.lower() not in AUDIO_SUFFIXES:
            return _fehler(f"Endung nicht erlaubt, erlaubt sind {', '.join(AUDIO_SUFFIXES)}")
        if not (self.bestand.eingang / datei).is_file():
            return _fehler(f"{datei} liegt nicht im Eingang")
        if not _text(titel):
            return _fehler("Titel fehlt")
        try:
            pruefe_herkunft(herkunft)
        except BestandFehler as problem:
            return _fehler(str(problem))
        eingabe = {"datei": datei, "titel": titel, "herkunft": herkunft}
        if suno_id:
            eingabe["suno_id"] = suno_id
        auftrag = self.ablage.anlegen("aufnehmen", eingabe, kanal)
        return {"ok": True, "auftrag": auftrag.id}

    # ------------------------------------------------------------ Bestellen

    @staticmethod
    def _songs_ok(songs: object) -> bool:
        return songs is None or (
            isinstance(songs, list) and all(isinstance(s, str) and s.strip() for s in songs)
        )

    @_weich
    def set_bestellen(self, wunsch: str, minuten: float, songs: list[str] | None = None,
                      ohne_gesang: bool = False, kanal: str = "mcp") -> dict:
        if not _text(wunsch):
            return _fehler("Wunsch fehlt")
        if (isinstance(minuten, bool) or not isinstance(minuten, (int, float))
                or not math.isfinite(minuten) or minuten <= 0):
            return _fehler("minuten muss eine positive Zahl sein")
        if not self._songs_ok(songs):
            return _fehler("songs muss eine Liste von Song-IDs sein")
        eingabe: dict = {"wunsch": wunsch, "length": minuten}
        if songs:
            eingabe["songs"] = list(songs)
        if ohne_gesang:
            eingabe["ohne_gesang"] = True
        return self._bestellt("set", eingabe, kanal)

    @_weich
    def videomusik_bestellen(self, wunsch: str, sekunden: float | None = None,
                             frames: int | None = None, fps: str | None = None,
                             laura_timeline_id: str | None = None,
                             songs: list[str] | None = None, gesang: bool | None = None,
                             kanal: str = "mcp") -> dict:
        if not _text(wunsch):
            return _fehler("Wunsch fehlt")
        if not self._songs_ok(songs):
            return _fehler("songs muss eine Liste von Song-IDs sein")
        angaben = [sekunden is not None, frames is not None, laura_timeline_id is not None]
        if sum(angaben) != 1:
            return _fehler("Genau eine Laengenangabe: sekunden, frames mit fps oder laura_timeline_id")
        eingabe: dict = {"wunsch": wunsch}
        try:
            if sekunden is not None:
                aus_sekunden(sekunden)
                eingabe["sekunden"] = sekunden
                frames_aus, fps_aus = None, None
            elif frames is not None:
                if fps is None:
                    return _fehler("frames braucht fps, z. B. '30' oder '30000/1001'")
                aus_frames(frames, fps)
                eingabe.update(frames=frames, fps=str(fps))
                frames_aus, fps_aus = frames, str(fps)
            else:
                if not isinstance(laura_timeline_id, str) or not _LAURA_ID.fullmatch(laura_timeline_id):
                    return _fehler("ungueltige Zeitleisten-ID")
                z = self.laura.zeitleiste(laura_timeline_id)
                if not z.get("ok"):
                    return _fehler(str(z.get("fehler", "Zeitleiste nicht lesbar")))
                fps_text = f"{int(z['fps_num'])}/{int(z['fps_den'])}"
                aus_frames(int(z["frames"]), fps_text)
                eingabe.update(frames=int(z["frames"]), fps=fps_text,
                               laura_timeline_id=laura_timeline_id)
                frames_aus, fps_aus = int(z["frames"]), fps_text
        except VideoFehler as problem:
            return _fehler(str(problem))
        if songs:
            eingabe["songs"] = list(songs)
        if gesang is True:
            eingabe["mit_gesang"] = True
        elif gesang is False:
            eingabe["ohne_gesang"] = True
        antwort = self._bestellt("video", eingabe, kanal)
        antwort.update(frames=frames_aus, fps=fps_aus)
        return antwort

    # ------------------------------------------------------------ Auftraege

    @_weich
    def auftrag_zeigen(self, auftrag: str) -> dict:
        a = self._hole(auftrag)
        dateien: list[str] = []
        bericht = ""
        befunde: list[str] = []
        laenge_s: float | None = None
        titel: str | None = None
        ordner = self._laufordner(a)
        if ordner is not None:
            dateien = sorted(p.name for p in ordner.iterdir() if p.is_file())
            zeilen: list[str] = []
            for name in BERICHT_DATEIEN.get(a.art, BERICHT_VORGABE):
                try:
                    zeilen = (ordner / name).read_text(encoding="utf-8", errors="replace").splitlines()
                    break
                except OSError:
                    continue
            zeilen = [_ohne_pfade(z, self.daten) for z in zeilen]
            bericht = "\n".join(zeilen[:BERICHT_ZEILEN])
            befunde = self._befunde(zeilen)
            try:
                vertrag = json.loads((ordner / "videomusik.json").read_text(encoding="utf-8"))
                if isinstance(vertrag, dict):
                    titel = vertrag.get("titel") if isinstance(vertrag.get("titel"), str) else None
                    sek = vertrag.get("sekunden")
                    laenge_s = float(sek) if isinstance(sek, (int, float)) else None
            except (OSError, ValueError):
                pass
        if (self.ablage.ordner(a.id) / EXPORT_DATEI).is_file():
            dateien.append(EXPORT_DATEI)
        return {
            "ok": True, "id": a.id, "art": a.art, "zustand": a.zustand,
            "grund": _ohne_pfade(a.grund, self.daten), "kanal": a.kanal, "eingabe": dict(a.eingabe), "dateien": dateien,
            "bericht": bericht, "befunde": befunde, "laenge_s": laenge_s, "titel": titel,
        }

    @staticmethod
    def _befunde(zeilen: list[str]) -> list[str]:
        """Aufzaehlung unter `## Pruefung` im bericht.md (so schreibt es `dj-set video`)."""
        gefunden: list[str] = []
        drin = False
        for zeile in zeilen:
            if zeile.startswith("## "):
                drin = zeile.strip().lower().startswith("## pruefung")
            elif drin and zeile.startswith("- "):
                text = zeile[2:].strip()
                if text and text != "ohne Befund":
                    gefunden.append(text)
        return gefunden

    @_weich
    def auftraege_offen(self, kanal: str) -> dict:
        offen = [
            {"id": a.id, "art": a.art, "zustand": a.zustand,
             "grund": _ohne_pfade(a.grund, self.daten), "beendet": a.beendet.isoformat() if a.beendet else None}
            for a in self.ablage.alle()
            if a.kanal == kanal and a.zustand in _BEENDET and not a.gemeldet
        ]
        return {"ok": True, "auftraege": offen}

    @_weich
    def gemeldet(self, auftrag: str) -> dict:
        a = self._hole(auftrag)
        if a.zustand not in _BEENDET:
            return _fehler(f"Auftrag ist noch nicht beendet (Zustand: {a.zustand})")
        if not a.gemeldet:
            a.gemeldet = True
            self.ablage.speichern(a)
        return {"ok": True}

    @_weich
    def auftrag_abbrechen(self, auftrag: str) -> dict:
        self._hole(auftrag)  # unbekannt -> _UnbekannterAuftrag
        ging = bool(self.laeufer.abbrechen(auftrag))
        zustand = self._hole(auftrag).zustand
        if ging:
            return {"ok": True, "zustand": zustand}
        return _fehler(f"Auftrag laesst sich nicht abbrechen (Zustand: {zustand})")

    # ------------------------------------------------------------ Laura

    def _ohne_link(self, text: str, url: str = "") -> str:
        """Signierten Link und Link-Basis aus einem Laura-Fehlertext nehmen (geht an Agenten)."""
        if url:
            text = text.replace(url, "<link>")
        if self.link_basis:
            text = re.sub(re.escape(self.link_basis) + r"[^\s\"'<>)]*", "<link>", text)
        return text

    def _gain(self, timeline_id: str, clips: list, wunsch: int | None) -> int:
        """Lautstaerke des Musik-Clips. Ein ausdruecklicher Wert bleibt (auf Lauras 0-400 geklemmt).
        Ohne Wert: 25, wenn unter der Musik Sprache liegt - der Video-Claw hat fuer die Zeitleiste
        ein Reel abgelegt, oder es gibt schon einen Audio-Clip, der nicht von hier stammt -, sonst 100."""
        if wunsch is not None:
            return max(GAIN_MIN, min(GAIN_MAX, wunsch))
        if (self.reel_ordner / f"reel-{timeline_id}.json").is_file():
            return GAIN_UNTER_SPRACHE
        for clip in clips:
            if isinstance(clip, dict) and MUSIK_MARKE not in str(clip.get("label") or ""):
                return GAIN_UNTER_SPRACHE
        return GAIN_ALLEIN

    @_weich
    def in_laura_legen(self, auftrag: str, timeline_id: str, gain_percent: int | None = None) -> dict:
        a = self._hole(auftrag)
        if a.art != "video" or a.zustand not in _FERTIG:
            return _fehler(f"Auftrag ist kein fertiges Video (Art {a.art}, Zustand {a.zustand})")
        bestellt_fuer = a.eingabe.get("laura_timeline_id")
        if bestellt_fuer and bestellt_fuer != timeline_id:
            return _fehler("Musik-Auftrag gehoert zu einer anderen Zeitleiste")
        ordner = self._laufordner(a)
        if ordner is None:
            return _fehler("Lauf des Auftrags nicht gefunden")
        try:
            erg = VideomusikErgebnis.model_validate_json(
                (ordner / "videomusik.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as problem:
            return _fehler(f"videomusik.json nicht lesbar: {type(problem).__name__}")
        if erg.laura is None or erg.frames is None or erg.fps_num is None or erg.fps_den is None:
            return _fehler("Kein laura-Block: die Musik wurde nach Sekunden bestellt, nicht nach Frames")
        if not (ordner / erg.datei).is_file():
            return _fehler("videomusik.wav fehlt im Lauf")
        if not self.link_basis or not self.link_schluessel:
            return _fehler("Datei-Link ist aus (MUSIK_LINK_HOST/MUSIK_LINK_SCHLUESSEL fehlen)")
        if gain_percent is not None and (isinstance(gain_percent, bool) or not isinstance(gain_percent, int)):
            return _fehler("gain_percent muss eine ganze Zahl sein (0-400) oder fehlen")
        if not isinstance(timeline_id, str) or not _LAURA_ID.fullmatch(timeline_id):
            return _fehler("ungueltige Zeitleisten-ID")

        z = self.laura.zeitleiste(timeline_id)
        if not z.get("ok"):
            return _fehler(str(z.get("fehler", "Zeitleiste nicht lesbar")))
        z_num, z_den = int(z["fps_num"]), int(z["fps_den"])
        if erg.fps_num * z_den == z_num * erg.fps_den:
            frames = erg.frames
        else:
            frames = round(erg.sekunden * z_num / z_den)
        soll = int(z["frames"])
        if frames < 1 or abs(frames - soll) > 1:
            return _fehler(f"Musik passt nicht zur Zeitleiste ({frames} statt {soll} Frames)")

        # Ein Clip darf nie ueber das Ende der Zeitleiste ragen (Laura kappt nicht).
        frames = min(frames, soll)

        marke = f"{MUSIK_MARKE}{a.id}"
        suffix = f" · {marke}"
        label = erg.titel[: max(0, LABEL_MAX - len(suffix))] + suffix

        # Pruefen, importieren, warten und anlegen als ein Schritt je (Auftrag, Zeitleiste):
        # ein zweiter Aufrufer wartet hier und findet danach den Clip des ersten.
        with self._sperre(a.id, timeline_id):
            vorhanden = self.laura.audio_clips(timeline_id)
            if not vorhanden.get("ok"):
                return _fehler(str(vorhanden.get("fehler", "Clips nicht lesbar")))
            clips = vorhanden.get("clips") or []
            for vorher in clips:
                if marke in str(vorher.get("label") or ""):
                    return {"ok": True, "clip_id": vorher.get("id"), "asset_id": vorher.get("asset_id")}
            try:
                clip = LauraClip(**{
                    **erg.laura.model_dump(),
                    "seq_out_frame_exclusive": frames,
                    "gain_percent": self._gain(timeline_id, clips, gain_percent),
                    "ducking_percent": 100,
                    "label": label,
                })
            except ValidationError as problem:
                return _fehler(f"Clip ungueltig: {problem.errors()[0].get('msg', '')}")

            url = datei_link.link(self.link_basis, self.link_schluessel, a.id, erg.datei, self.uhr())
            name = f"{song_id_aus_titel(erg.titel) or 'videomusik'}.wav"
            imp = self.laura.importieren(str(z["project_id"]), url, name)
            if not imp.get("ok"):
                return _fehler(self._ohne_link(str(imp.get("fehler", "Import fehlgeschlagen")), url))
            asset_id = imp["asset_id"]
            warten = self.laura.asset_warten(asset_id)
            if not warten.get("ok"):
                return _fehler(self._ohne_link(str(warten.get("fehler", "Asset nicht bereit")), url))
            angelegt = self.laura.clip_anlegen(timeline_id, {**clip.model_dump(), "asset_id": asset_id})
            if not angelegt.get("ok"):
                return _fehler(self._ohne_link(str(angelegt.get("fehler", "Clip nicht angelegt")), url))
            return {"ok": True, "clip_id": (angelegt.get("clip") or {}).get("id"), "asset_id": asset_id}

    @_weich
    def laura_videos_suchen(self, suchwort: str) -> dict:
        r = self.laura.videos_suchen(suchwort)
        if not r.get("ok"):
            return _fehler(str(r.get("fehler", "Suche fehlgeschlagen")))
        return {"ok": True, "treffer": r.get("treffer", [])}

    @_weich
    def laura_rendern(self, timeline_id: str, kanal: str = "mcp", musik_auftrag: str = "") -> dict:
        if not isinstance(timeline_id, str) or not _LAURA_ID.fullmatch(timeline_id):
            return _fehler("ungueltige Zeitleisten-ID")
        eingabe: dict = {"timeline_id": timeline_id}
        if musik_auftrag:
            m = self._hole(musik_auftrag)  # unbekannt -> _UnbekannterAuftrag
            if m.art != "video":
                return _fehler(f"musik_auftrag muss Videomusik sein (Art {m.art})")
            bestellt_fuer = m.eingabe.get("laura_timeline_id")
            if bestellt_fuer and bestellt_fuer != timeline_id:
                return _fehler("Musik-Auftrag gehoert zu einer anderen Zeitleiste")
            eingabe["musik_auftrag"] = musik_auftrag
        auftrag = self.ablage.anlegen("laura_render", eingabe, kanal)
        return {"ok": True, "auftrag": auftrag.id}

    def _reel_optionen(self, timeline_id: str) -> dict | None:
        """Reel-Optionen, die der Video-Claw je Zeitleiste ablegt; sonst None."""
        datei = self.reel_ordner / f"reel-{timeline_id}.json"
        try:
            if not datei.is_file():
                return None
            wert = json.loads(datei.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return wert if isinstance(wert, dict) else None

    @_weich
    def render_ausfuehren(self, a: Auftrag) -> dict:
        """Wird vom Laeufer fuer `laura_render` gerufen (spaet gebunden, siehe Server).

        Mit `musik_auftrag` legt er zuerst diese Musik ein (`in_laura_legen`, Lautstaerke
        automatisch, ueber die Marke im Label wiederholbar) und rendert erst dann. Ist die
        Musik gescheitert oder abgebrochen oder laesst sie sich nicht einlegen, scheitert der
        Render: ein Video ohne die bestellte Musik waere das falsche Ergebnis."""
        timeline_id = a.eingabe.get("timeline_id")
        if not isinstance(timeline_id, str) or not _LAURA_ID.fullmatch(timeline_id):
            return _fehler("ungueltige Zeitleisten-ID")
        musik = a.eingabe.get("musik_auftrag")
        if musik:
            try:
                m = self.ablage.hole(str(musik))
            except KeyError:
                return _fehler(f"Musik-Auftrag {musik} nicht gefunden")
            if m.zustand not in _FERTIG:
                grund = _ohne_pfade(m.grund, self.daten) or "ohne Grund"
                return _fehler(f"Musik-Auftrag {m.id} {m.zustand}: {grund}")
            gelegt = self.in_laura_legen(m.id, timeline_id)
            if not gelegt.get("ok"):
                return _fehler(f"Musik-Auftrag {m.id} nicht eingelegt: {gelegt.get('fehler', 'unbekannt')}")
        optionen = self._reel_optionen(timeline_id)
        if optionen is not None:
            start = self.laura.rendern(timeline_id, reel_optionen=optionen)
        else:
            start = self.laura.rendern(timeline_id)
        if not start.get("ok"):
            return _fehler(str(start.get("fehler", "Render nicht gestartet")))
        export_id = start["export_id"]
        bereit = self.laura.export_warten(export_id)
        if not bereit.get("ok"):
            return _fehler(str(bereit.get("fehler", "Export nicht fertig")))
        groesse = bereit.get("size_bytes")
        if isinstance(groesse, bool) or not isinstance(groesse, (int, float)):
            return _fehler(
                f"Groesse des Exports unbekannt; kein Download (Grenze {MAX_EXPORT_BYTES // 1024 ** 2} MB)"
            )
        if groesse > MAX_EXPORT_BYTES:
            return _fehler(
                f"Export ist {groesse / 1024 ** 2:.0f} MB gross, ueber der Grenze von "
                f"{MAX_EXPORT_BYTES // 1024 ** 2} MB; kein Download"
            )
        ziel = self.ablage.ordner(a.id) / EXPORT_DATEI
        geladen = self.laura.herunterladen(bereit["url"], ziel)
        if not geladen.get("ok"):
            return _fehler(str(geladen.get("fehler", "Download fehlgeschlagen")))
        return {"ok": True, "export_id": export_id, "size_bytes": groesse, "datei": EXPORT_DATEI}
