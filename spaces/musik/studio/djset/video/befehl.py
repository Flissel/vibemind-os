"""Der Befehl `dj-set video`: ein Stueck genau in Videolaenge aus EINEM Song.

Alles bis zum Anlegen des Laufs ist Vorab-Check und kostet kein Token: Songs
laden und messen, Kandidaten filtern, fehlende Stimmen trennen. Erst dann
waehlt das Modell (ohne Shell oder Datei-Werkzeug, es sieht nur die Tabelle),
den Rest erledigt Code: Zuschnitt, Rendern, Pruefen, Vertrag fuer Laura.

Dieses Modul importiert `djset.cli` als Modul und ruft `_songs_laden` und
`_stimmen_holen` ueber das Modul-Attribut auf. So wirken Tests, die
`cli._trenner` ersetzen, auch hier. `cli` selbst importiert dieses Modul nur
traege in `main()`, damit kein Kreisimport entsteht.
"""

import dataclasses
import json
import math
import sys
from datetime import date
from pathlib import Path

import numpy as np
from pydantic import ValidationError

from djset import cli
from djset.agents.base import AgentClient, AgentError, Budget
from djset.bestand import Bestand, BestandFehler, SongEintrag
from djset.dsp.analyze import beat_times
from djset.dsp.trennen import INSTALLATION, trenner_fehlt
from djset.export import to_mp3
from djset.render.stretch import SR, RenderFailed, RenderToolMissing
from djset.run import Run, slugify
from djset.tempo import tempo_unsicher
from djset.video.fenster import fenster_messen, tauglich
from djset.video.klang import videomusik_rendern
from djset.video.laenge import (
    VideoFehler,
    Ziellaenge,
    aus_frames,
    aus_sekunden,
    aus_video,
)
from djset.video.pruefung import LUFS_TOLERANZ, videomusik_pruefen
from djset.video.unterlegen import ORIGINALTON_DB, unterlegen
from djset.video.vertrag import VideomusikErgebnis, VideoWahl, laura_clip
from djset.video.waehler import kandidaten, video_waehlen
from djset.video.zuschnitt import (
    AUSKLANG_MIN_S,
    abrunden,
    aufrunden,
    schlusstakte,
    taktstarts,
    traegt_s,
    zuschnitt_planen,
)

WAV = "videomusik.wav"
MP3 = "videomusik.mp3"
UNTERLEGT = "video-mit-musik.mp4"

# Fehler, die der Befehl als Meldung mit Exit 1 beendet, nie als Traceback.
_ERWARTET = (
    VideoFehler,
    RenderToolMissing,
    RenderFailed,
    BestandFehler,
    AgentError,
    ValidationError,
)


def ziel_bestimmen(
    sekunden: float | None, frames: int | None, fps: str | None, video: Path | None
) -> Ziellaenge | None:
    """Die Ziellaenge aus genau einer der drei Angaben; None nach einer Meldung."""
    try:
        if sekunden is not None:
            return aus_sekunden(sekunden)
        if frames is not None:
            if fps is None:
                raise VideoFehler("--frames braucht --fps (z. B. 30 oder 30000/1001).")
            return aus_frames(frames, fps)
        if fps is not None:
            raise VideoFehler("--fps gehoert zu --frames; bei --sekunden und --video unnoetig.")
        if video is None:
            raise VideoFehler("Die Laenge fehlt: --sekunden, --frames mit --fps oder --video.")
        return aus_video(video)
    except (VideoFehler, RenderToolMissing) as fehler:
        print(f"Fehler: {fehler}", file=sys.stderr)
        return None


def _kein_kandidat(geladen: list[SongEintrag], ziel_s: float) -> str:
    """Der Grund, warum kein Song das Ziel tragen kann, mit der tragbaren Laenge."""
    gemessen = [s for s in geladen if s.messung is not None]
    unsicher = tempo_unsicher(
        {s.id: s.messung.bpm.value for s in gemessen if s.messung},
        {s.id: s.tempo_abschnitte or [] for s in gemessen},
    )
    sicher = [s for s in gemessen if s.id not in unsicher]
    if not sicher:
        return (
            "kein Song mit sicherem Tempo: "
            + (", ".join(f"{i} (Tempo unsicher)" for i in unsicher) or "kein Song gemessen")
        )
    # Mindestwerte aufrunden, tragbare Laengen abrunden (je auf 0,1 s): der genannte
    # Wert geht beim Wiederholen wirklich.
    def tragbar(s: SongEintrag) -> float:
        return traegt_s(s.messung.beatgrid, s.messung.duration_s) if s.messung else 0.0

    def mindest(s: SongEintrag) -> float:
        return aufrunden(s.messung.beatgrid.bar_length_s + AUSKLANG_MIN_S) if s.messung else math.inf

    lang_genug = [s for s in sicher if tragbar(s) >= ziel_s]
    if lang_genug:  # es liegt am Ziel, das kuerzer ist als ein Takt plus Ausklang
        # Nur Songs, die den genannten Wert auch tragen, sonst ginge er beim Wiederholen nicht.
        geht = [s for s in lang_genug if tragbar(s) >= mindest(s)] or lang_genug
        kuerzester = min(geht, key=mindest)
        return (
            f"das Ziel von {ziel_s:.2f} s ist zu kurz: ein Takt plus Ausklang braucht "
            f"mindestens {mindest(kuerzester):.1f} s (Song {kuerzester.id})"
        )
    laengster = max(sicher, key=tragbar)
    return (
        f"kein Song traegt {ziel_s:.1f} s Musik; der laengste tragbare Song "
        f"({laengster.id}) traegt {abrunden(tragbar(laengster)):.1f} s"
    )


def _bericht(
    wunsch: str,
    ergebnis: VideomusikErgebnis,
    wahl: VideoWahl,
    hinweise: list[str],
    befunde: list[str],
    unterlegt: str | None,
    am_deckel: bool = False,
) -> str:
    zeilen = [
        f"# Videomusik: {wunsch}",
        "",
        f"- Song: {ergebnis.titel} (`{ergebnis.song_id}`), Herkunft: {ergebnis.herkunft}",
        f"- Begruendung des Modells: {wahl.begruendung}",
        f"- Laenge: {ergebnis.sekunden:.3f} s = {ergebnis.samples} Proben bei {ergebnis.sample_rate} Hz",
    ]
    if ergebnis.frames is not None:
        zeilen.append(
            f"- Bild: {ergebnis.frames} Frames bei {ergebnis.fps_num}/{ergebnis.fps_den} fps"
        )
    zeilen += [
        f"- Ausschnitt: ab {ergebnis.start_im_song_s:.2f} s im Song, {ergebnis.takte} Takte "
        f"({ergebnis.bpm:.1f} BPM), letzter Taktanfang bei {ergebnis.schluss_s:.2f} s im Stueck, "
        f"danach Ausklang",
        f"- Gesang: {'ja, ganzer Song' if ergebnis.gesang else 'nein, nur die Begleitung'}",
        f"- Pegel: {ergebnis.lufs:.1f} LUFS, True Peak {ergebnis.true_peak:.1f} dBTP"
        + (" (am Spitzendeckel, in Toleranz)" if am_deckel else ""),
        f"- Dateien: `{ergebnis.datei}`" + (f", `{ergebnis.mp3}`" if ergebnis.mp3 else ""),
    ]
    if unterlegt:
        zeilen.append(f"- Unter das Video gelegt: `{unterlegt}`")
    if hinweise:
        zeilen += ["", "## Hinweise", ""] + [f"- {h}" for h in hinweise]
    zeilen += ["", "## Pruefung", ""]
    zeilen += [f"- {b}" for b in befunde] if befunde else ["- ohne Befund"]
    return "\n".join(zeilen) + "\n"


def cmd_video(
    daten: Path,
    wunsch: str,
    ziel: Ziellaenge,
    songs: list[str] | None,
    gesang: bool | None,
    lufs_ziel: float,
    video: Path | None,
    unterlegen_db: float | None,
    original_ersetzen: bool,
    client: AgentClient,
    budget: Budget,
    today: date,
) -> int:
    """Baut die Videomusik als Lauf. Exit 0 gruen, 2 mit Befund, 1 Abbruch.

    `gesang`: True/False kommt von der Kommandozeile und schlaegt das Modell,
    None laesst das Modell entscheiden. In `videomusik.json` steht immer, was
    wirklich gerendert wurde.
    """
    try:
        return _video(
            daten, wunsch, ziel, songs, gesang, lufs_ziel, video,
            unterlegen_db, original_ersetzen, client, budget, today,
        )
    except _ERWARTET as fehler:
        print(f"Fehler: {fehler}", file=sys.stderr)
        return 1


def _video(
    daten: Path,
    wunsch: str,
    ziel: Ziellaenge,
    songs: list[str] | None,
    gesang: bool | None,
    lufs_ziel: float,
    video: Path | None,
    unterlegen_db: float | None,
    original_ersetzen: bool,
    client: AgentClient,
    budget: Budget,
    today: date,
) -> int:
    if not wunsch.strip():
        print("Fehler: --wunsch darf nicht leer sein.", file=sys.stderr)
        return 1
    if not math.isfinite(lufs_ziel) or lufs_ziel >= 0:
        print(f"Fehler: --lufs muss eine endliche Zahl unter 0 sein, nicht {lufs_ziel}.", file=sys.stderr)
        return 1
    if unterlegen_db is not None and video is None:
        print("Fehler: --unterlegen braucht --video.", file=sys.stderr)
        return 1
    if unterlegen_db is not None and not math.isfinite(unterlegen_db):
        print(f"Fehler: --musik-db muss eine endliche Zahl sein, nicht {unterlegen_db}.", file=sys.stderr)
        return 1
    if video is not None and not video.is_file():
        print(f"Fehler: Video nicht gefunden: {video}", file=sys.stderr)
        return 1
    if not daten.is_dir():
        print(f"Fehler: {daten} ist kein Verzeichnis.", file=sys.stderr)
        return 1

    ziel_s = ziel.sekunden
    samples = ziel.samples(SR)
    bestand = Bestand(daten)
    geladen = cli._songs_laden(bestand, daten, songs)
    if geladen is None:
        return 1

    # Was ueberhaupt tragen kann, ohne Blick auf die Stimmen: nur dafuer lohnt
    # sich das Trennen, und ist hier nichts uebrig, kostet der Abbruch nichts.
    tragfaehig = kandidaten(geladen, ziel_s, False)
    if not tragfaehig:
        print(f"Fehler: {_kein_kandidat(geladen, ziel_s)}.", file=sys.stderr)
        return 1

    hinweise: list[str] = []
    if gesang is not True:
        if cli._stimmen_holen(bestand, tragfaehig) is None:
            return 1
        # Der Eintrag wurde beim Trennen neu geschrieben; die geladene Kopie ist veraltet.
        tragfaehig = [bestand.hole(s.id) for s in tragfaehig]
    getrennt = [s for s in tragfaehig if s.stimmen is not None]

    if gesang is False:
        if not getrennt:
            print(
                "Fehler: --ohne-gesang braucht die getrennte Begleitung, aber keiner der "
                f"tragfaehigen Songs ({', '.join(s.id for s in tragfaehig)}) ist getrennt. "
                f"{trenner_fehlt() or 'Demucs ist bereit, die Trennung ist gescheitert.'} "
                f"Installation: {INSTALLATION}",
                file=sys.stderr,
            )
            return 1
        auswahl = getrennt
    elif gesang is None and getrennt:
        auswahl = getrennt
    else:
        auswahl = tragfaehig
        if gesang is None:
            hinweise.append(
                "Kein tragfaehiger Song hat eine getrennte Stimmspur (Demucs fehlt oder die "
                "Trennung scheiterte), deshalb klingt das Stueck mit Gesang."
            )

    kurz = slugify(wunsch)[: cli.LAUF_SLUG_MAX].rstrip("-")
    run = Run.create(daten, f"video-{kurz}", today=today)
    run.write_text(
        "ziel.json",
        json.dumps({**dataclasses.asdict(ziel), "samples": samples, "sample_rate": SR}, indent=2),
    )

    wahl = video_waehlen(client, wunsch, ziel_s, auswahl, budget)
    run.write_model("wahl.json", wahl)
    song = next(s for s in auswahl if s.id == wahl.song_id)
    messung = song.messung
    if messung is None:  # kandidaten() filtert schon; das schuetzt nur den Typ
        raise VideoFehler(f"Song {song.id!r} hat keine Messung")

    mit_gesang = wahl.gesang if gesang is None else gesang
    if not mit_gesang and song.stimmen is None:
        mit_gesang = True
        hinweise.append(
            f"{song.id} hat keine getrennte Begleitung; das Stueck klingt mit Gesang, "
            "auch wenn das Modell ohne gewaehlt hatte."
        )
    song_wav = bestand.pfad(song.id)
    if mit_gesang:
        quelle = song_wav
    else:
        pfade = bestand.stimmen_pfade(song.id)
        if pfade is None:
            raise VideoFehler(f"{song.id}: Begleitung fehlt")
        quelle = pfade[1]

    beats: np.ndarray | None
    try:
        beats = beat_times(song_wav, messung.bpm.value)
    except Exception as fehler:  # noqa: BLE001 - ohne Beats rastet der Zuschnitt nur nicht ein
        beats = None
        hinweise.append(f"Beats nicht erkannt ({fehler}); der Schnitt liegt auf dem Raster.")
    # Nur Starts, deren Fenster in der wirklich gerenderten Quelle den Zielpegel
    # unter dem Spitzendeckel erreichen koennen und vor dem Schluss keine Luecke
    # haben. Gibt es keinen, gilt der alte Weg mit allen Starts, samt Hinweis.
    alle_starts = taktstarts(messung.beatgrid, messung.duration_s, ziel_s)
    schluss_im_stueck = schlusstakte(messung.beatgrid.bar_length_s, ziel_s) * messung.beatgrid.bar_length_s
    fenster = fenster_messen(quelle, alle_starts, ziel_s, schluss_im_stueck, lufs_ziel=lufs_ziel)
    erlaubt = [f.start_s for f in fenster if tauglich(f, lufs_ziel)]
    if not erlaubt:
        bester = max((f.erreichbar_lufs for f in fenster), default=-math.inf)
        erreichbar = f"{bester:.1f}" if math.isfinite(bester) else "keiner"
        hinweise.append(
            f"kein Ausschnitt von {song.titel} erreicht {lufs_ziel:.1f} LUFS unter dem "
            f"Spitzendeckel; bester erreichbarer Pegel {erreichbar} LUFS"
        )
    zuschnitt = zuschnitt_planen(
        messung.beatgrid, messung.duration_s, ziel_s, wahl.einstieg, messung.energy, beats,
        erlaubt=erlaubt or None,
    )
    if erlaubt:
        # Was die Einstiegsregel ohne den Pegel- und Luecken-Filter gewaehlt haette: weicht der
        # Start ab, sagt der Bericht, warum, sonst widerspraeche er dem Wunsch ("anfang").
        ohne = zuschnitt_planen(
            messung.beatgrid, messung.duration_s, ziel_s, wahl.einstieg, messung.energy, beats,
        )
        if abs(ohne.start_s - zuschnitt.start_s) > messung.beatgrid.bar_length_s / 2:
            verworfen = min(fenster, key=lambda f: abs(f.start_s - ohne.start_s))
            grund = (
                "hat der Song vor dem Schluss eine Luecke (Stille ueber 0,5 s)"
                if verworfen.luecke
                else f"erreicht der Song {lufs_ziel:.1f} LUFS nicht unter dem Spitzendeckel"
            )
            hinweise.append(
                f"Ab {ohne.start_s:.2f} s {grund}; Start auf {zuschnitt.start_s:.2f} s verschoben."
            )
    run.write_text("zuschnitt.json", json.dumps(dataclasses.asdict(zuschnitt), indent=2))

    wav = run.path(WAV)
    pegel = videomusik_rendern(quelle, zuschnitt, samples, wav, lufs_ziel)
    am_deckel = pegel.gedeckelt and abs(pegel.lufs - lufs_ziel) <= LUFS_TOLERANZ
    if pegel.gedeckelt and not am_deckel:
        hinweise.append(
            f"Der Pegel ist am True-Peak-Deckel begrenzt: {pegel.lufs:.1f} LUFS statt {lufs_ziel:.1f}."
        )
    befunde = videomusik_pruefen(
        wav, samples, lufs_ziel, schluss_s=zuschnitt.schluss_s - zuschnitt.start_s
    )
    to_mp3(wav, run.path(MP3))

    clip = (
        laura_clip(ziel.frames, ziel.fps_num, ziel.fps_den, song.titel)
        if ziel.frames is not None and ziel.fps_num is not None and ziel.fps_den is not None
        else None
    )
    ergebnis = VideomusikErgebnis(
        datei=WAV,
        mp3=MP3,
        sekunden=ziel_s,
        samples=samples,
        sample_rate=SR,
        frames=ziel.frames,
        fps_num=ziel.fps_num,
        fps_den=ziel.fps_den,
        song_id=song.id,
        titel=song.titel,
        herkunft=song.herkunft,
        start_im_song_s=zuschnitt.start_s,
        schluss_s=zuschnitt.schluss_s - zuschnitt.start_s,
        takte=zuschnitt.takte,
        bpm=messung.bpm.value,
        gesang=mit_gesang,
        lufs=pegel.lufs,
        true_peak=pegel.true_peak,
        laura=clip,
    )
    run.write_model("videomusik.json", ergebnis)

    unterlegt: str | None = None
    unterlegen_fehler: str | None = None
    if unterlegen_db is not None and video is not None:
        try:
            unterlegen(video, wav, run.path(UNTERLEGT), unterlegen_db, original_ersetzen)
            unterlegt = UNTERLEGT
        except (VideoFehler, RenderToolMissing) as fehler:
            unterlegen_fehler = str(fehler)
            hinweise.append(f"Unterlegen gescheitert: {fehler}")

    run.write_text(
        "bericht.md",
        _bericht(wunsch, ergebnis, wahl, hinweise, [b.text for b in befunde], unterlegt, am_deckel),
    )

    print(f"Run: {run.run_id}")
    print(
        f"  {song.id}: {zuschnitt.takte} Takte ab {zuschnitt.start_s:.2f} s, "
        f"{ziel_s:.3f} s, {pegel.lufs:.1f} LUFS, {'mit' if mit_gesang else 'ohne'} Gesang"
    )
    for hinweis in hinweise:
        print(f"  Hinweis: {hinweis}")
    for befund in befunde:
        print(f"  Befund ({befund.art}): {befund.text}")
    if unterlegen_fehler is not None:
        print(f"Fehler: Unterlegen gescheitert: {unterlegen_fehler}", file=sys.stderr)
        return 1
    return 2 if befunde else 0


__all__ = ["cmd_video", "ziel_bestimmen", "ORIGINALTON_DB"]
