"""Einsprungpunkt mit den Unterkommandos `plan`, `build`, `set`, `record`,
`import`, `aufnehmen` und `bestand`."""

import argparse
import math
import os
import sys
from datetime import date
from pathlib import Path

from djset.agents.architect import arrange, similar_pairs
from djset.agents.base import AgentClient, AgentError, Budget
from djset.agents.bestand_profiler import (
    build_profile_from_bestand,
    spielbare_laenge_s,
)
from djset.agents.curator import NotEnoughTracks, curate
from djset.agents.designer import design
from djset.agents.profiler import build_profile
from djset.agents.prompt_writer import render_markdown, write_prompts
from djset.analysis import analyse_all
from djset.bestand import Bestand, BestandFehler, SongEintrag
from djset.capture import CaptureFailed, import_folder, list_input_devices, record
from djset.contracts import (
    MAX_RATIO,
    MIN_RATIO,
    MIN_TRACKS,
    CritiqueLog,
    CritiqueRound,
    MixPlan,
    Profile,
    Setlist,
    TrackMeasurements,
)
from djset.critic import Critique, critique
from djset.export import to_mp3
from djset.qa import check_set
from djset.render.engine import ImpossibleMixPlan, render
from djset.render.stretch import (
    RenderFailed,
    RenderToolMissing,
    UnsupportedSampleRate,
)
from djset.render.tracklist import render_tracklist
from djset.report import correction_gain_db, render_qa_report, status_line
from djset.run import FINISHED_MARKER, Run, RunNotFound, slugify
from djset.tempo import TempoWahl, tempo_unsicher, waehle_tempo
from djset.watch import (
    AudioFile,
    NotEnoughMaterial,
    check_enough,
    check_sample_rate,
    scan,
)

WATCH_DIRNAME = "watch"

# Die Tempo-Wahl eines Laufs aus dem Bestand. Fehlt sie, kam der Lauf aus
# watch/ und hat keinen Tempo-Bereich zu berichten.
TEMPO_ARTIFACT = "tempo.json"

# Wo `set` den Bestand findet, wenn --daten fehlt.
DATEN_ENV = "MUSIK_DATEN"

# So lang darf der Teil des Lauf-Ordnernamens werden, der aus dem Wunsch kommt.
LAUF_SLUG_MAX = 40

# Die Laengen-Vorpruefung in `set` laeuft VOR dem Modell, die
# Uebergangsregeln des Profils gibt es da noch nicht. Bis dahin gilt diese
# feste Annahme je Naht; nach dem Entwurf rechnet bestand_profiler mit dessen
# Werten.
VORPRUEFUNG_TAKTE_JE_NAHT = 16

# Was ein Lauf aus dem Bestand dem Nutzer sagt, wenn der Kurator zu wenig
# uebrig laesst - Suno und watch/ gibt es auf diesem Weg nicht.
BESTAND_NACHSCHUB = (
    "Nimm weitere Songs in den Bestand auf (dj-set aufnehmen) oder waehle "
    "mit --songs andere aus dem Bestand."
)

# Unter diesem Namen entsteht das Set, bis der Bericht steht. Erst dann wird
# es zu FINISHED_MARKER - der Marke, an der Run.latest_unfinished einen
# fertigen Lauf erkennt.
PARTIAL_MARKER = "set.partial.wav"
MP3_NAME = "set.mp3"

PLAN_BUDGET_TOKENS = 120_000
# Wanduhrgrenze der Plan-Phase. Sie besteht aus hoechstens acht
# Modellaufrufen (Profiler plus Prompt-Writer mit seinen Wiederholungen);
# zehn Minuten sind dafuer reichlich und begrenzen trotzdem einen Lauf, der
# in Timeouts und Backoffs haengenbleibt.
PLAN_BUDGET_SECONDS = 600.0

# Budget fuer einen build-Lauf. Deutlich groesser als bei plan: hier laeuft ein
# Modellaufruf pro Track plus vier Stufen. Gilt pro Aufruf, nicht pro Lauf -
# ein resume startet frisch, sonst koennte ein Lauf, der einmal ans Limit
# gestossen ist, nie mehr fertig werden.
BUILD_BUDGET_TOKENS = 600_000
BUILD_BUDGET_SECONDS = 3600.0

# Spec Abschnitt 7: weist der Critic zweimal zurueck, wird der beste bisherige
# Plan genommen. Weder Endlosschleife noch Abbruch.
CRITIC_ROUNDS = 2


def _laenge_gueltig(length_minutes: float) -> bool:
    """--length muss eine endliche positive Zahl sein. argparse nimmt "nan"
    und "inf" als float an, und `nan <= 0` ist falsch - ohne isfinite kaeme
    beides durch. Meldet selbst, gibt nur zurueck, ob es weitergeht."""
    if math.isfinite(length_minutes) and length_minutes > 0:
        return True
    print(
        f"Fehler: --length muss eine positive Zahl sein, war {length_minutes}.",
        file=sys.stderr,
    )
    return False


def cmd_plan(
    base: Path,
    genre: str,
    length_minutes: float,
    client: AgentClient,
    today: date | None = None,
) -> int:
    # Ruling 13: argparse laesst "--length -5" und ein leeres "--genre"
    # anstandslos durch. Beides wird hier abgefangen, bevor es als rohe
    # pydantic-ValidationError aus build_profile herausfaellt.
    if not genre.strip():
        print("Fehler: --genre darf nicht leer sein.", file=sys.stderr)
        return 1
    if not _laenge_gueltig(length_minutes):
        return 1

    budget = Budget(
        max_tokens=PLAN_BUDGET_TOKENS, max_seconds=PLAN_BUDGET_SECONDS
    )
    run = Run.create(base, genre, today=today or date.today())
    # Der Ordner, in den der Nutzer gleich seine Suno-Downloads legen soll -
    # die Schlussmeldung nennt ihn, also muss er auch existieren.
    watch = base / WATCH_DIRNAME
    watch.mkdir(parents=True, exist_ok=True)

    try:
        profile = build_profile(client, genre, length_minutes, budget)
        run.write_model("profile.json", profile)

        prompt_set = write_prompts(client, profile, budget)
        run.write_text("prompts.md", render_markdown(profile, prompt_set))
    except AgentError as error:
        # Schemaverletzung nach allen Versuchen oder Budget erschoepft - beides
        # ist ein bekannter, benannter Fehlerfall, kein Programmierfehler. Eine
        # rohe ValidationError von tiefer unten bleibt bewusst unbehandelt.
        print(f"Fehler beim Erzeugen des Plans: {error}", file=sys.stderr)
        return 1

    print(f"Run: {run.run_id}")
    print(f"Profil:  {run.path('profile.json')}")
    print(f"Prompts: {run.path('prompts.md')}")
    print(
        f"\nGeneriere die {profile.track_count.request} Tracks bei Suno und "
        f"lege die Downloads in {watch} ab."
    )
    return 0


def cmd_build(
    base: Path,
    run_id: str | None,
    client: AgentClient,
    budget: Budget,
) -> int:
    """Baut aus dem Material in watch einen geprueften Mixplan."""
    try:
        run = Run.load(base, run_id) if run_id else Run.latest_unfinished(base)
    except RunNotFound as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1
    if run is None:
        print(
            "Fehler: kein unfertiger Lauf gefunden. Starte zuerst dj-set plan.",
            file=sys.stderr,
        )
        return 1

    # Ein Lauf, der beim Planen abgebrochen ist, hat kein Profil. Ohne das
    # taugt er nicht als Ausgangspunkt.
    if not run.has("profile.json"):
        print(
            f"Fehler: der Lauf {run.run_id} hat keine profile.json. "
            f"Er wurde vor dem Ende der Planungsphase abgebrochen.",
            file=sys.stderr,
        )
        return 1
    profile = run.read_model("profile.json", Profile)

    try:
        files = scan(base / WATCH_DIRNAME)
        check_enough(files, profile)
        check_sample_rate(files)
    except (NotEnoughMaterial, UnsupportedSampleRate) as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1

    return build_run(run, files, profile, client, budget)


def build_run(
    run: Run,
    files: list[AudioFile],
    profile: Profile,
    client: AgentClient,
    budget: Budget,
    messungen: dict[str, TrackMeasurements] | None = None,
) -> int:
    """Der Kern eines build-Laufs, ab der Analyse: Stufen 3 bis 9.

    Gemeinsam fuer beide Einstiege - `build` (Material aus watch/) und `set`
    (Songs aus dem Bestand). Beide haben vorher ihre Vorab-Checks gemacht;
    von hier an kosten die Stufen Tokens. Liegt tempo.json im Lauf (nur beim
    Einstieg `set`), nennt der QA-Bericht die Songs ausserhalb des
    Tempo-Bereichs.

    `messungen` bringt der Einstieg `set` mit: die gespeicherten Messungen
    der Songs. Die Analyse misst dann nicht neu, damit das Tempo im Lauf
    nicht von tempo.json abweichen kann. `build` gibt keine mit.
    """
    # Vor dem ersten Modellaufruf: ein kaputtes tempo.json soll nicht erst
    # nach dem Rendern auffallen, wenn jedes Token bezahlt ist. Liegt es da,
    # kommt der Lauf aus dem Bestand.
    tempo = (
        run.read_model(TEMPO_ARTIFACT, TempoWahl) if run.has(TEMPO_ARTIFACT) else None
    )
    nachschub = BESTAND_NACHSCHUB if tempo is not None else None
    try:
        analyses, unreadable = analyse_all(
            client, files, profile, run, budget, messungen=messungen
        )
        shortlist = curate(
            client, analyses, unreadable, profile, budget, nachschub=nachschub
        )
        run.write_model("shortlist.json", shortlist)

        best: MixPlan | None = None
        best_setlist: Setlist | None = None
        best_verdict: Critique | None = None
        best_round = 0
        rounds: list[CritiqueRound] = []
        complaints: tuple[str, ...] = ()

        for number in range(1, CRITIC_ROUNDS + 2):
            setlist = arrange(
                client, shortlist, analyses, profile, budget, complaints=complaints
            )
            plan = design(client, setlist, analyses, profile, budget)
            verdict = critique(plan, analyses, profile)
            rounds.append(
                CritiqueRound(
                    round=number,
                    order=list(setlist.order),
                    complaints=list(verdict.complaints),
                    chosen=False,
                )
            )
            if best_verdict is None or len(verdict.complaints) < len(
                best_verdict.complaints
            ):
                best, best_setlist, best_verdict = plan, setlist, verdict
                best_round = number
            if verdict.ok:
                break
            # Nur mit dem zurueck an Stufe 5, woran Stufe 5 etwas aendern kann.
            # Bleibt nichts uebrig, ist die Schleife am Ende: eine Laengen- oder
            # Downbeat-Beanstandung kostet sonst zwei volle Runden Architect und
            # Designer und liefert danach denselben Plan. Der Architect legt die
            # Reihenfolge fest - er kann kein Material herbeischaffen und keinen
            # Cue-Punkt verschieben.
            complaints = verdict.reorderable
            if not complaints:
                break
    except (NotEnoughTracks, AgentError) as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1

    if best is None or best_setlist is None or best_verdict is None:
        print("Fehler: es entstand kein Mixplan.", file=sys.stderr)
        return 1

    run.write_model("setlist.json", best_setlist)
    run.write_model("mixplan.json", best)
    # Erfolgskriterium 7: jede Beanstandung muss in den Artefakten
    # nachvollziehbar sein. Auf der Standardausgabe eines unbeaufsichtigten
    # Laufs ist sie das nicht - und die Beanstandungen der verworfenen Runden
    # sind gerade die, die zur Neuanordnung gefuehrt haben.
    critique_log = CritiqueLog(
        rounds=[
            CritiqueRound.model_validate(
                entry.model_dump() | {"chosen": entry.round == best_round}
            )
            for entry in rounds
        ],
        chosen_round=best_round,
    )
    run.write_model("critique.json", critique_log)

    # Spec Abschnitt 7: ein Fehler des Renderers ist ein Bug, kein Retry-Fall -
    # RenderFailed bleibt deshalb unbehandelt und reisst den Lauf mit vollem
    # Traceback ab, der Mixplan bleibt zum Reproduzieren liegen. Ein fehlendes
    # ffmpeg ist dagegen eine Umgebungsfrage, und ein unausfuehrbarer Mixplan
    # ist laut eigenem Docstring ein Befund UEBER den Mixplan, kein Bug im
    # Renderer - beide bekommen eine benannte Meldung statt eines Tracebacks.
    #
    # Gerendert wird unter einem Zwischennamen. set.wav ist die Marke, an der
    # Run.latest_unfinished einen fertigen Lauf erkennt (siehe run.py) - laege
    # es schon da, waehrend die QA noch laeuft, saehe ein in Stufe 9
    # abgestuerzter Lauf fertig aus und haette doch keinen Bericht. Umbenannt
    # wird erst, wenn qa-report.md geschrieben ist.
    partial = run.path(PARTIAL_MARKER)
    try:
        # Stufe 8: rendern.
        sources = {file.id: file.path for file in files}
        render(best, sources, partial)
        run.write_text(
            "tracklist.md", render_tracklist(best, {a.id: a for a in analyses})
        )

        # Stufe 9: nachmessen, und hoechstens einmal den Pegel korrigieren.
        # Der Laengen-Hinweis misst gegen die gewuenschte Laenge, nicht gegen
        # die des Plans - die stimmt fast immer mit sich selbst ueberein.
        wunsch_laenge_s = profile.length_minutes * 60.0
        qa = check_set(partial, best, wunsch_laenge_s=wunsch_laenge_s)
        correction = correction_gain_db(qa)
        # Auf `correction is not None` verzweigt, nicht auf ein danebenstehendes
        # bool: sonst kann der f-String unten ein None bekommen, sobald die
        # beiden auseinanderlaufen.
        if correction is not None:
            print(f"  QA: Pegel um {correction:.1f} dB korrigiert, rendere neu")
            render(best, sources, partial, master_gain_db=correction)
            qa = check_set(partial, best, wunsch_laenge_s=wunsch_laenge_s)
    except RenderToolMissing as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1
    except ImpossibleMixPlan as error:
        print(
            f"Fehler: der Mixplan laesst sich nicht ausfuehren: {error}\n"
            f"Er bleibt zum Nachsehen unter {run.path('mixplan.json')} liegen.",
            file=sys.stderr,
        )
        return 1

    run.write_text(
        "qa-report.md",
        render_qa_report(
            qa,
            shortlist,
            unreadable,
            critique_log,
            best,
            profile,
            correction is not None,
            tempo=tempo,
            tonarten={a.id: a.key.camelot for a in analyses},
            aehnliche=similar_pairs(
                best_setlist.order, {a.id: a for a in analyses}
            ),
        ),
    )
    # Jetzt ist der Lauf fertig - und erst jetzt traegt er die Marke dafuer.
    partial.replace(run.path(FINISHED_MARKER))

    # Die MP3 kommt NACH der Fertig-Marke: scheitert sie, bleibt der Lauf
    # trotzdem fertig, mit set.wav und Bericht. Der Grund steht als Zeile in
    # der Ausgabe, nicht als Abbruch.
    mp3_path = run.path(MP3_NAME)
    mp3_fehler: str | None = None
    try:
        to_mp3(run.path(FINISHED_MARKER), mp3_path)
    except (RenderFailed, RenderToolMissing) as error:
        mp3_fehler = str(error)

    print(f"Lauf {run.run_id}")
    print(f"  {len(analyses)} Tracks analysiert, {len(unreadable)} unlesbar")
    print(f"  {len(shortlist.accepted)} im Set, {len(shortlist.rejected)} aussortiert")
    if best_verdict.complaints:
        # Die Beanstandungen der gewaehlten Runde, nicht neu gerechnet: dasselbe
        # Ergebnis noch einmal auszurechnen ist nicht nur unnoetig, es waere auch
        # eine zweite Wahrheit neben der, die im Protokoll steht.
        print(
            f"  Critic hat {len(best_verdict.complaints)} Beanstandungen offen "
            f"gelassen:"
        )
        for text in best_verdict.texts:
            print(f"    - {text}")
    print(f"  Mixplan: {run.path('mixplan.json')}")
    print(f"  Beanstandungen aller Runden: {run.path('critique.json')}")
    print(f"  Set: {run.path(FINISHED_MARKER)}")
    if mp3_fehler is None:
        print(f"  MP3: {mp3_path}")
    else:
        print(f"  MP3: nicht geschrieben - {mp3_fehler}")
    print(f"  Tracklist: {run.path('tracklist.md')}")
    print(f"  QA-Bericht: {run.path('qa-report.md')}")
    # Die Statuszeile des Berichts und jeder Befund auch hier: ein Lauf mit
    # blockierendem Befund darf auf der Konsole nicht fertig aussehen. Exit 2
    # heisst "ausgeliefert, aber rot" - 1 bleibt der Abbruch.
    print(f"  QA: {status_line(qa, correction is not None)}")
    for finding in qa.blocking:
        print(f"    Befund ({finding.kind}): {finding.text}")
    for finding in qa.hints:
        print(f"    Hinweis ({finding.kind}): {finding.text}")
    return 2 if qa.blocking else 0


def _songs_laden(
    bestand: Bestand, daten: Path, songs: list[str] | None
) -> list[SongEintrag] | None:
    """Alle Songs oder die genannten, mit Messung. None nach einer Meldung."""
    try:
        if songs is None:
            geladen = bestand.alle()
            if not geladen:
                print(
                    f"Fehler: der Bestand unter {daten} ist leer. Nimm zuerst "
                    f"Songs auf.",
                    file=sys.stderr,
                )
                return None
        else:
            # Doppelt genannte IDs nur einmal: ein Song zweimal im Set ergaebe
            # zwei Analysen unter derselben track_id.
            geladen = [bestand.hole(song_id) for song_id in dict.fromkeys(songs)]
            if not geladen:
                print("Fehler: --songs nennt keinen Song.", file=sys.stderr)
                return None
    except BestandFehler as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return None

    gemessen: list[SongEintrag] = []
    for song in geladen:
        if song.messung is None:
            print(f"  messe {song.id} ...")
            # Jede Ausnahme, nicht nur BestandFehler: librosa, soundfile und
            # numpy werfen beim Messen eigene Fehler, und die sollen vor dem
            # ersten Modellaufruf als Meldung mit Song-ID enden, nicht als
            # Traceback.
            try:
                song = bestand.messen(song.id)
            except Exception as error:  # noqa: BLE001 - jede Messstoerung endet gleich
                print(f"Fehler: {song.id}: {error}", file=sys.stderr)
                return None
        elif song.tempo_abschnitte is None:
            # Eintraege von vor der Abschnittspruefung: nur die Abschnitte
            # nachholen, die gespeicherte Messung bleibt.
            print(f"  messe Tempo-Abschnitte von {song.id} ...")
            try:
                song = bestand.abschnitte_messen(song.id)
            except Exception as error:  # noqa: BLE001 - jede Messstoerung endet gleich
                print(f"Fehler: {song.id}: {error}", file=sys.stderr)
                return None
        gemessen.append(song)
    return gemessen


def _unsicher_zeile(song_id: str, tempo: float, abschnitte: list[float]) -> str:
    return (
        f"  Tempo unsicher: {song_id} ({tempo:.1f} BPM, Abschnitte "
        f"{min(abschnitte):.0f} bis {max(abschnitte):.0f} BPM), bitte gegenhoeren"
    )


def cmd_set(
    daten: Path,
    wunsch: str,
    length_minutes: float,
    songs: list[str] | None,
    client: AgentClient,
    budget: Budget,
    today: date,
) -> int:
    """Baut ein Set aus Songs, die schon im Bestand liegen.

    Alles bis zum Anlegen des Laufs ist Vorab-Check und kostet kein Token:
    Songs laden, fehlende Messungen nachholen, den Tempo-Bereich waehlen,
    pruefen, ob genug Songs hineinpassen und ob ihre Laenge reicht. Erst dann
    fragt der Profiler das Modell, danach laeuft dieselbe Kette wie bei build.
    """
    if not wunsch.strip():
        print("Fehler: --wunsch darf nicht leer sein.", file=sys.stderr)
        return 1
    if not _laenge_gueltig(length_minutes):
        return 1

    # Bestand() legt eingang/ und songs/ an. Ein vertippter Pfad ergaebe sonst
    # still einen neuen, leeren Bestand an der falschen Stelle.
    if not daten.is_dir():
        print(f"Fehler: {daten} ist kein Verzeichnis.", file=sys.stderr)
        return 1
    bestand = Bestand(daten)
    geladen = _songs_laden(bestand, daten, songs)
    if geladen is None:
        return 1

    # _songs_laden hat jede fehlende Messung nachgeholt.
    tempi = {song.id: song.messung.bpm.value for song in geladen if song.messung}
    # Songs, deren Tempo-Erkennung zwischen Abschnitten springt, haben kein
    # verlaessliches Gesamttempo und kommen gar nicht erst in die Wahl.
    unsicher = tempo_unsicher(
        tempi, {song.id: song.tempo_abschnitte or [] for song in geladen}
    )
    sicher = {song_id: t for song_id, t in tempi.items() if song_id not in unsicher}
    wahl = (
        waehle_tempo(sicher).model_copy(update={"unsicher": unsicher})
        if sicher
        else None
    )

    if wahl is None or len(wahl.drin) < MIN_TRACKS:
        gemessen = ", ".join(
            f"{song_id} {tempo:.1f} BPM" for song_id, tempo in tempi.items()
        )
        print(
            f"Fehler: nur {0 if wahl is None else len(wahl.drin)} Songs passen in "
            f"einen gemeinsamen Tempo-Bereich (Streckung {MIN_RATIO:.2f} bis "
            f"{MAX_RATIO:.2f}), gebraucht werden mindestens {MIN_TRACKS}. "
            f"Gemessen: {gemessen}.",
            file=sys.stderr,
        )
        for song_id, abschnitte in unsicher.items():
            print(_unsicher_zeile(song_id, tempi[song_id], abschnitte), file=sys.stderr)
        return 1

    nach_id = {song.id: song for song in geladen}
    im_bereich = [nach_id[song_id] for song_id in wahl.drin]
    dauern = [song.messung.duration_s for song in im_bereich if song.messung]
    vorhanden_s = sum(dauern)
    # Spielbar ist ein Song nur bis auf seinen Uebergang - dieselbe Rechnung,
    # mit der bestand_profiler danach track_count.needed setzt.
    spielbar_s = sum(
        spielbare_laenge_s(dauer, VORPRUEFUNG_TAKTE_JE_NAHT, wahl.bereich.target)
        for dauer in dauern
    )
    if spielbar_s < length_minutes * 60:
        print(
            f"Fehler: die {len(im_bereich)} Songs im Tempo-Bereich "
            f"{wahl.bereich.min:.1f} bis {wahl.bereich.max:.1f} BPM ergeben "
            f"zusammen {vorhanden_s / 60:.1f} Minuten, nach Abzug von je "
            f"{VORPRUEFUNG_TAKTE_JE_NAHT} Takten Uebergang spielbar "
            f"{spielbar_s / 60:.1f} Minuten, gewuenscht sind "
            f"{length_minutes:.1f} Minuten.",
            file=sys.stderr,
        )
        return 1

    try:
        files = [
            AudioFile(id=song.id, path=bestand.pfad(song.id)) for song in im_bereich
        ]
        # Der Bestand wandelt beim Aufnehmen in die Rate der Kette um. Eine von
        # Hand nach songs/ gelegte Datei taete das nicht - und fiele sonst erst
        # beim Rendern auf, nachdem jedes Token bezahlt ist.
        check_sample_rate(files)
    except (BestandFehler, UnsupportedSampleRate) as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1

    # Der Wunsch ist freier Text und kann lang sein - im Ordnernamen nur
    # gekappt, sonst sprengt er unter Windows schnell die Pfadlaenge. Das
    # Profil bekommt ihn weiter ganz.
    kurz = slugify(wunsch)[:LAUF_SLUG_MAX].rstrip("-")
    run = Run.create(daten, kurz, today=today)
    run.write_model(TEMPO_ARTIFACT, wahl)
    try:
        profile = build_profile_from_bestand(
            client, wunsch, length_minutes, im_bereich, wahl, budget
        )
    except AgentError as error:
        print(f"Fehler beim Erzeugen des Profils: {error}", file=sys.stderr)
        return 1
    run.write_model("profile.json", profile)

    print(f"Run: {run.run_id}")
    print(
        f"  Tempo-Bereich {wahl.bereich.min:.1f} bis {wahl.bereich.max:.1f} BPM "
        f"(Ziel {wahl.bereich.target:.1f}): {len(wahl.drin)} Songs"
    )
    for song_id, tempo in wahl.draussen.items():
        vermutung = wahl.oktave.get(song_id)
        zusatz = f", vermutlich {vermutung} gemessen" if vermutung else ""
        print(f"  nicht im Bereich: {song_id} ({tempo:.1f} BPM{zusatz})")
    for song_id, abschnitte in wahl.unsicher.items():
        print(_unsicher_zeile(song_id, tempi[song_id], abschnitte))
    messungen = {song.id: song.messung for song in im_bereich if song.messung}
    return build_run(run, files, profile, client, budget, messungen=messungen)


def _mm_ss(sekunden: float) -> str:
    gesamt = round(sekunden)
    return f"{gesamt // 60}:{gesamt % 60:02d}"


def cmd_aufnehmen(
    daten: Path,
    datei: str,
    titel: str,
    herkunft: str,
    suno_id: str | None,
    today: date,
) -> int:
    """Nimmt eine Datei aus eingang/ in den Bestand auf und misst sie gleich.

    Scheitert erst das Messen, bleibt der Song aufgenommen (ohne Messung) -
    `set` holt die Messung spaeter nach. Exit 1 trotzdem, damit der Fehler
    nicht untergeht.
    """
    # Wie bei set: Bestand() legt eingang/ und songs/ an, ein vertippter Pfad
    # ergaebe sonst still einen neuen, leeren Bestand.
    if not daten.is_dir():
        print(f"Fehler: {daten} ist kein Verzeichnis.", file=sys.stderr)
        return 1
    eingang_neu = not (daten / "eingang").is_dir()
    bestand = Bestand(daten)
    if eingang_neu:
        # Beim ersten Aufruf auf einem frischen --daten entsteht der Eingang
        # erst hier - "liegt nicht als Datei im Eingang" hiesse dann nur, dass
        # es ihn vorher nicht gab.
        print(
            f"Fehler: der Eingang-Ordner wurde eben angelegt: {bestand.eingang}. "
            f"Leg {datei} dort ab und ruf aufnehmen noch einmal auf.",
            file=sys.stderr,
        )
        return 1
    try:
        eintrag = bestand.aufnehmen(datei, titel, herkunft, today, suno_id=suno_id)
    except BestandFehler as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1
    try:
        eintrag = bestand.messen(eintrag.id)
    except Exception as error:  # noqa: BLE001 - jede Messstoerung endet gleich
        print(f"Fehler: {eintrag.id}: {error}", file=sys.stderr)
        return 1

    messung = eintrag.messung
    if messung is None:  # messen() traegt sie immer ein
        print(f"Fehler: {eintrag.id}: keine Messung erhalten.", file=sys.stderr)
        return 1
    print(f"Aufgenommen: {eintrag.id}")
    print(f"  Tempo:  {messung.bpm.value:.1f} BPM")
    print(f"  Tonart: {messung.key.camelot}")
    print(f"  Laenge: {_mm_ss(messung.duration_s)}")
    unsicher = tempo_unsicher(
        {eintrag.id: messung.bpm.value}, {eintrag.id: eintrag.tempo_abschnitte or []}
    )
    for song_id, abschnitte in unsicher.items():
        # Aufgenommen bleibt er - aber set laesst ihn draussen, und das soll
        # hier schon sichtbar sein, nicht erst beim Set.
        print(_unsicher_zeile(song_id, messung.bpm.value, abschnitte))
    return 0


def cmd_bestand(daten: Path) -> int:
    """Listet den Bestand: ID, Titel, Tempo, Tonart, Laenge, Herkunft."""
    if not daten.is_dir():
        print(f"Fehler: {daten} ist kein Verzeichnis.", file=sys.stderr)
        return 1
    # Bestand() legt eingang/ und songs/ an, falls sie fehlen - auf einem
    # frischen --daten zeigt bestand danach, wohin neue Songs gehoeren.
    bestand = Bestand(daten)
    try:
        songs = bestand.alle()
    except BestandFehler as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1
    if not songs:
        print(
            f"Der Bestand unter {daten} ist leer. Neue Songs nach "
            f"{bestand.eingang} legen und mit dj-set aufnehmen aufnehmen."
        )
        return 0

    kopf = ("ID", "Titel", "Tempo", "Tonart", "Laenge", "Herkunft")
    zeilen = [
        (
            song.id,
            song.titel,
            f"{song.messung.bpm.value:.1f}" if song.messung else "-",
            song.messung.key.camelot if song.messung else "-",
            _mm_ss(song.messung.duration_s) if song.messung else "-",
            song.herkunft,
        )
        for song in songs
    ]
    breiten = [
        max(len(zeile[spalte]) for zeile in [kopf, *zeilen])
        for spalte in range(len(kopf))
    ]
    for zeile in [kopf, *zeilen]:
        print(
            "  ".join(
                feld.ljust(breite) for feld, breite in zip(zeile, breiten)
            ).rstrip()
        )
    return 0


def cmd_record(
    base: Path,
    device: str | None,
    seconds: float | None,
    name: str | None,
    list_devices: bool,
) -> int:
    """Nimmt vom benannten Eingabegeraet auf und legt das Ergebnis in watch/ ab.

    `--list-devices` listet die Geraete und beendet sich, unabhaengig davon,
    ob --device nebenbei mitgegeben wurde - wer explizit auflisten will,
    meint das eindeutig, statt in einen echten Aufnahmeversuch zu fallen.
    """
    if list_devices:
        try:
            devices = list_input_devices()
        except RenderToolMissing as error:
            print(f"Fehler: {error}", file=sys.stderr)
            return 1
        if not devices:
            print("Keine Audio-Eingabegeraete gefunden.")
        else:
            print("Verfuegbare Audio-Eingabegeraete:")
            for device_name in devices:
                print(f"  - {device_name}")
        return 0

    if device is None:
        print(
            "Fehler: --device ist erforderlich (oder --list-devices ohne --device).",
            file=sys.stderr,
        )
        return 1
    if seconds is None or seconds <= 0:
        print(
            f"Fehler: --seconds muss positiv sein, war {seconds}.", file=sys.stderr
        )
        return 1

    watch = base / WATCH_DIRNAME
    base_filename = slugify(name) if name else slugify(device)
    if not base_filename:
        base_filename = "aufnahme"

    # Wie import_folder()s Kollisionsschema (djset/capture.py), nur gegen die
    # Platte statt gegen eine In-Memory-Menge geprueft: zwei Aufnahmen ohne
    # --name, mit demselben --name oder vom selben Geraet duerfen die vorige
    # Aufnahme nicht kommentarlos ueberschreiben (ffmpeg laeuft mit -y) - Aux-
    # Material laesst sich anders als ein Suno-Prompt nicht neu generieren.
    filename = base_filename
    counter = 2
    while (watch / f"{filename}.wav").exists():
        filename = f"{base_filename}-{counter}"
        counter += 1
    target = watch / f"{filename}.wav"

    try:
        record(device, target, seconds)
    except (RenderToolMissing, CaptureFailed) as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1

    print(f"Aufnahme geschrieben: {target}")
    return 0


def cmd_import(base: Path, source: Path) -> int:
    """Wandelt jede Audiodatei aus `source` in watch/ um."""
    watch = base / WATCH_DIRNAME
    try:
        written = import_folder(source, watch)
    except (RenderToolMissing, CaptureFailed) as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1

    if not written:
        print(f"Keine Audiodateien in {source} gefunden.")
        return 0

    print(f"{len(written)} Datei(en) nach {watch} uebertragen:")
    for path in written:
        print(f"  - {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dj-set")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="Genre-Profil und Suno-Prompts erzeugen")
    plan.add_argument("--genre", required=True, help="frei waehlbar, z.B. 'liquid dnb'")
    plan.add_argument("--length", type=float, required=True, help="Ziellaenge in Minuten")
    plan.add_argument("--base", type=Path, default=Path("."), help="Projektwurzel")

    build = subparsers.add_parser("build", help="Set aus dem Material in watch bauen")
    build.add_argument("--run", default=None, help="Lauf-ID, sonst der letzte unfertige")
    # Dieselbe Wurzel wie bei plan. Ohne sie sucht build seine Laeufe im
    # aktuellen Verzeichnis, waehrend plan sie nach --base geschrieben hat -
    # und der Nutzer bekommt "kein unfertiger Lauf gefunden", ohne zu erfahren,
    # dass er nur woanders gesucht hat.
    build.add_argument("--base", type=Path, default=Path("."), help="Projektwurzel")

    set_parser = subparsers.add_parser("set", help="Set aus Songs im Bestand bauen")
    set_parser.add_argument(
        "--daten",
        type=Path,
        default=None,
        help=f"Wurzel des Bestands, sonst die Umgebungsvariable {DATEN_ENV}",
    )
    set_parser.add_argument(
        "--wunsch", required=True, help="frei, z.B. 'Sommerfest, erst ruhig'"
    )
    set_parser.add_argument(
        "--length", type=float, required=True, help="Ziellaenge in Minuten"
    )
    set_parser.add_argument(
        "--songs", default=None, help="Song-IDs mit Komma getrennt, sonst alle"
    )

    record_parser = subparsers.add_parser(
        "record", help="Vom Leitungs-/Aux-Eingang in watch/ aufnehmen"
    )
    record_parser.add_argument(
        "--device", default=None, help="Name des Eingabegeraets"
    )
    record_parser.add_argument(
        "--seconds", type=float, default=None, help="Aufnahmelaenge in Sekunden"
    )
    record_parser.add_argument(
        "--name", default=None, help="Dateiname in watch/ (ohne Endung)"
    )
    record_parser.add_argument(
        "--base", type=Path, default=Path("."), help="Projektwurzel"
    )
    record_parser.add_argument(
        "--list-devices",
        action="store_true",
        help="Eingabegeraete auflisten und beenden",
    )

    import_parser = subparsers.add_parser(
        "import", help="Vorhandenes Audiomaterial in watch/ uebernehmen"
    )
    import_parser.add_argument(
        "source", type=Path, help="Ordner mit vorhandenem Audiomaterial"
    )
    import_parser.add_argument(
        "--base", type=Path, default=Path("."), help="Projektwurzel"
    )

    aufnehmen_parser = subparsers.add_parser(
        "aufnehmen", help="Eine Datei aus eingang/ in den Bestand aufnehmen"
    )
    aufnehmen_parser.add_argument(
        "--daten",
        type=Path,
        default=None,
        help=f"Wurzel des Bestands, sonst die Umgebungsvariable {DATEN_ENV}",
    )
    aufnehmen_parser.add_argument(
        "--datei", required=True, help="Dateiname in eingang/, ohne Pfad"
    )
    aufnehmen_parser.add_argument("--titel", required=True, help="Titel des Songs")
    aufnehmen_parser.add_argument(
        "--herkunft",
        required=True,
        help="suno-eigen, eigen-aufgenommen oder lizenz:<Angabe>",
    )
    aufnehmen_parser.add_argument("--suno-id", default=None, help="ID bei Suno")

    bestand_parser = subparsers.add_parser("bestand", help="Den Bestand auflisten")
    bestand_parser.add_argument(
        "--daten",
        type=Path,
        default=None,
        help=f"Wurzel des Bestands, sonst die Umgebungsvariable {DATEN_ENV}",
    )

    args = parser.parse_args(argv)
    if args.command == "plan":
        from djset.agents.client import ClaudeClient

        return cmd_plan(
            base=args.base,
            genre=args.genre,
            length_minutes=args.length,
            client=ClaudeClient(),
        )
    if args.command == "build":
        from djset.agents.client import ClaudeClient

        return cmd_build(
            args.base,
            args.run,
            ClaudeClient(),
            Budget(
                max_tokens=BUILD_BUDGET_TOKENS,
                max_seconds=BUILD_BUDGET_SECONDS,
            ),
        )
    if args.command == "set":
        daten = _daten(args.daten)
        if daten is None:
            return 1
        songs = (
            [teil.strip() for teil in args.songs.split(",") if teil.strip()]
            if args.songs is not None
            else None
        )
        from djset.agents.client import ClaudeClient

        # Dasselbe Budget wie build: der eine Profiler-Aufruf mehr faellt
        # neben einem Aufruf je Track nicht ins Gewicht.
        return cmd_set(
            daten,
            args.wunsch,
            args.length,
            songs,
            ClaudeClient(),
            Budget(
                max_tokens=BUILD_BUDGET_TOKENS,
                max_seconds=BUILD_BUDGET_SECONDS,
            ),
            date.today(),
        )
    if args.command == "record":
        return cmd_record(
            base=args.base,
            device=args.device,
            seconds=args.seconds,
            name=args.name,
            list_devices=args.list_devices,
        )
    if args.command == "import":
        return cmd_import(base=args.base, source=args.source)
    if args.command == "aufnehmen":
        daten = _daten(args.daten)
        if daten is None:
            return 1
        return cmd_aufnehmen(
            daten, args.datei, args.titel, args.herkunft, args.suno_id, date.today()
        )
    if args.command == "bestand":
        daten = _daten(args.daten)
        if daten is None:
            return 1
        return cmd_bestand(daten)
    return 1


def _daten(angegeben: Path | None) -> Path | None:
    """--daten, sonst die Umgebungsvariable DATEN_ENV. None nach einer Meldung."""
    if angegeben is not None:
        return angegeben
    aus_umgebung = os.environ.get(DATEN_ENV, "").strip()
    if aus_umgebung:
        return Path(aus_umgebung)
    print(
        f"Fehler: --daten fehlt und {DATEN_ENV} ist nicht gesetzt.",
        file=sys.stderr,
    )
    return None


if __name__ == "__main__":
    sys.exit(main())
