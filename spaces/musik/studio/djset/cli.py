"""Einsprungpunkt mit den Unterkommandos `plan`, `build`, `set`, `record`
und `import`."""

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from djset.agents.architect import arrange
from djset.agents.base import AgentClient, AgentError, Budget
from djset.agents.bestand_profiler import build_profile_from_bestand
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
from djset.report import correction_gain_db, render_qa_report
from djset.run import FINISHED_MARKER, Run, RunNotFound, slugify
from djset.tempo import TempoWahl, waehle_tempo
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
    if length_minutes <= 0:
        print(
            f"Fehler: --length muss positiv sein, war {length_minutes}.",
            file=sys.stderr,
        )
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
) -> int:
    """Der Kern eines build-Laufs, ab der Analyse: Stufen 3 bis 9.

    Gemeinsam fuer beide Einstiege - `build` (Material aus watch/) und `set`
    (Songs aus dem Bestand). Beide haben vorher ihre Vorab-Checks gemacht;
    von hier an kosten die Stufen Tokens. Liegt tempo.json im Lauf (nur beim
    Einstieg `set`), nennt der QA-Bericht die Songs ausserhalb des
    Tempo-Bereichs.
    """
    try:
        analyses, unreadable = analyse_all(client, files, profile, run, budget)
        shortlist = curate(client, analyses, unreadable, profile, budget)
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
        qa = check_set(partial, best)
        correction = correction_gain_db(qa)
        # Auf `correction is not None` verzweigt, nicht auf ein danebenstehendes
        # bool: sonst kann der f-String unten ein None bekommen, sobald die
        # beiden auseinanderlaufen.
        if correction is not None:
            print(f"  QA: Pegel um {correction:.1f} dB korrigiert, rendere neu")
            render(best, sources, partial, master_gain_db=correction)
            qa = check_set(partial, best)
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

    tempo = (
        run.read_model(TEMPO_ARTIFACT, TempoWahl) if run.has(TEMPO_ARTIFACT) else None
    )
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
    return 0


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

        gemessen: list[SongEintrag] = []
        for song in geladen:
            if song.messung is None:
                print(f"  messe {song.id} ...")
                song = bestand.messen(song.id)
            gemessen.append(song)
        return gemessen
    except BestandFehler as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return None


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
    if length_minutes <= 0:
        print(
            f"Fehler: --length muss positiv sein, war {length_minutes}.",
            file=sys.stderr,
        )
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
    wahl = waehle_tempo(tempi)

    if len(wahl.drin) < MIN_TRACKS:
        gemessen = ", ".join(
            f"{song_id} {tempo:.1f} BPM" for song_id, tempo in tempi.items()
        )
        print(
            f"Fehler: nur {len(wahl.drin)} Songs passen in einen gemeinsamen "
            f"Tempo-Bereich (Streckung {MIN_RATIO:.2f} bis {MAX_RATIO:.2f}), "
            f"gebraucht werden mindestens {MIN_TRACKS}. Gemessen: {gemessen}.",
            file=sys.stderr,
        )
        return 1

    nach_id = {song.id: song for song in geladen}
    im_bereich = [nach_id[song_id] for song_id in wahl.drin]
    vorhanden_s = sum(song.messung.duration_s for song in im_bereich if song.messung)
    if vorhanden_s < length_minutes * 60:
        print(
            f"Fehler: die {len(im_bereich)} Songs im Tempo-Bereich "
            f"{wahl.bereich.min:.1f} bis {wahl.bereich.max:.1f} BPM ergeben "
            f"zusammen {vorhanden_s / 60:.1f} Minuten, gewuenscht sind "
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

    run = Run.create(daten, wunsch, today=today)
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
        print(f"  nicht im Bereich: {song_id} ({tempo:.1f} BPM)")
    return build_run(run, files, profile, client, budget)


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
        daten = args.daten
        if daten is None and os.environ.get(DATEN_ENV, "").strip():
            daten = Path(os.environ[DATEN_ENV].strip())
        if daten is None:
            print(
                f"Fehler: --daten fehlt und {DATEN_ENV} ist nicht gesetzt.",
                file=sys.stderr,
            )
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
    return 1


if __name__ == "__main__":
    sys.exit(main())
