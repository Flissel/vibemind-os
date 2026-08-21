# Plan 2: build-Phase, Entscheidungsstufen 3 bis 7

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aus den in `watch/` abgelegten Suno-Tracks entsteht vollautomatisch ein geprüfter `mixplan.json` — analysiert, aussortiert, sortiert, mit entworfenen Übergängen und bestandener Kontrolle.

**Architecture:** Fünf Stufen hintereinander, alle über `run_agent` aus Plan 1. Der Track-Analyst ist die einzige Stufe, die Audio öffnet; ab Stufe 4 sieht niemand mehr etwas anderes als JSON. Der Analyst ist **orchestriert, nicht werkzeugnutzend**: Python ruft die DSP-Messung, reicht die Zahlen ans Modell und dreht die Schleife selbst. Der Critic ist bewusst gar kein Agent, sondern eine deterministische Prüffunktion.

**Tech Stack:** Python 3.11+, Pydantic v2, librosa, numpy, soundfile, pyloudnorm, claude-agent-sdk 0.2.x, pytest.

**Spec:** `docs/superpowers/specs/2026-08-05-suno-dj-set-multiagent-design.md`

**Vorgänger:** `docs/superpowers/plans/2026-08-05-plan-1-fundament-und-plan-phase.md` — abgeschlossen, auf `master`, 121 Tests grün.

## Global Constraints

- Python 3.11 oder neuer. Moderne Typsyntax, nie `typing.List`. Kein `Any`.
- Deutsche Fehlermeldungen und Kommentare, englische Bezeichner im Code. Agenten-Prompts sind deutsch, weil sie so verschickt werden.
- Conventional Commits. Jede Task endet mit genau einem Commit.
- **Kein Netzzugriff in Tests.** Jeder Test spritzt `FakeClient` ein. Kein Test darf `djset.agents.client` importieren.
- Testausgabe makellos: `filterwarnings = ["error"]` steht in `pyproject.toml`, jede Warnung ist ein Testfehler.
- Testgetrieben: erst der fallende Test, dann die Implementierung.
- Harte Regeln aus dem Spec werden im Typ erzwungen, nicht in Prosa beschrieben.
- **Nie `model_copy(update=...)`** — validiert in Pydantic v2 nicht. Immer `Model.model_validate(alt.model_dump() | {...})`. In Plan 1 teuer gelernt.
- Die Stretch-Grenze ist ±6 Prozent (`timestretch_ratio` zwischen 0.94 und 1.06), die Trackuntergrenze ist `MIN_TRACKS = 3`. Beides steht in `djset/contracts.py` und wird von dort importiert, nie neu hingeschrieben.

## Entscheidungen dieses Plans

**Der Track-Analyst ist orchestriert.** Vom Nutzer entschieden. Die Spec beschreibt ihn werkzeugnutzend, aber die Entscheidung, die er zu treffen hat, ist eng — Halftime ja oder nein — und die Korrektur dafür steht als `snap_to_range` fertig aus Plan 1. Orchestriert bleibt alles offline mit `FakeClient` testbar, das synchrone `AgentClient`-Protokoll bleibt unangetastet, und die geforderte Parallelität pro Datei ist ein Thread-Pool statt N SDK-Subprozesse. Der Analyst stößt die Neumessung weiterhin an, nur per Feld in seiner strukturierten Antwort.

**Der Critic ist kein Agent.** Die Spec listet vier Prüfungen: hält die Energiekurve, liegen alle Stretches in der Toleranz, sitzen die Cue-Punkte auf Downbeats, stimmt die Gesamtlänge. Alle vier sind Arithmetik über den Mixplan. Ein Modell dafür zu fragen wäre teurer, langsamer und weniger verlässlich als eine Funktion. Die Spec nennt ihn eine Stufe, keinen Agenten.

**Die Energie-Skala wird über den Analysten geradegezogen.** Das Abschlussreview von Plan 1 fand, dass `profile.energy_arc` (Bögen von etwa 0,30 bis 0,90) und die gemessene Energiekurve (echtes gemastertes Material zwischen 0,75 und 0,87) nicht auf derselben Skala liegen. Statt im DSP zu rechnen vergibt der Analyst einen `energy_level` auf genau der Skala, auf der das Profil seinen Bogen beschreibt. Die gemessene Kurve bleibt trackrelativ und zuständig für die Mix-Regionen.

**Das Budget gilt pro Aufruf, nicht pro Lauf.** `build --resume` startet mit frischem Budget. Andernfalls könnte ein Lauf, der einmal ans Limit gestoßen ist, nie mehr fertig werden — die Notbremse würde zur Sackgasse.

**Der Mixplan wird gerechnet, nicht erfunden.** Der Transition-Designer wählt pro Übergang nur Technik und Taktzahl. Cue-Punkte, Timestretch-Verhältnisse, Timeline-Positionen und EQ-Kurven rechnet Code aus Vorlagen. Damit ist der aufwendigste Teil der Stufe deterministisch und ohne Modell testbar.

## File Structure

**Neu:**

| Datei | Verantwortung |
|---|---|
| `djset/watch.py` | Material im watch-Ordner finden, stabile IDs, Vorab-Check gegen das Profil |
| `djset/agents/analyst.py` | Stufe 3: einen Track messen und beurteilen |
| `djset/analysis.py` | Stufe 3: Auffächerung über alle Dateien, Wiederaufnahme aus Artefakten |
| `djset/agents/curator.py` | Stufe 4: aussortieren mit Begründung |
| `djset/agents/architect.py` | Stufe 5: Reihenfolge festlegen |
| `djset/agents/designer.py` | Stufe 6: Übergänge wählen, Mixplan rechnen |
| `djset/critic.py` | Stufe 7: deterministisches Gate vor dem Rendern |
| `tests/fixtures/analyses.py` | Erfundene Track-Analysen als Eingabe für die Stufen 4 bis 7 |

**Geändert:**

| Datei | Änderung |
|---|---|
| `djset/contracts.py` | `Verdict.energy_level`, `UnreadableTrack`, `Setlist`; Klarstellung am `energy`-Feld |
| `djset/agents/base.py` | `Budget` wird threadsicher |
| `djset/cli.py` | Kommando `build` |
| `conftest.py` | Gemeinsame `profile`-Fixture |

**Nicht in diesem Plan:** Render-Engineer, QA, `set.wav`, `tracklist.md`, `qa-report.md`. Das ist Plan 3. `build` endet hier mit einem geschriebenen, geprüften `mixplan.json`.

---

### Task 1: Watch-Ordner und Vorab-Check

Findet das Material und bricht ab, bevor der erste Agent Tokens verbraucht. Spec Abschnitt 7: `build` zählt zuerst die Dateien in `watch/` und vergleicht mit dem Profil.

**Files:**
- Create: `djset/watch.py`
- Modify: `conftest.py`
- Test: `tests/test_watch.py`

**Interfaces:**
- Consumes: `djset.contracts.Profile`, `djset.run.slugify`
- Produces: `AudioFile` (frozen dataclass, Felder `id: str`, `path: Path`), `scan(watch_dir: Path) -> list[AudioFile]`, `check_enough(files: list[AudioFile], profile: Profile) -> None`, `NotEnoughMaterial(RuntimeError)`, `AUDIO_SUFFIXES: tuple[str, ...]`

- [ ] **Step 1: Die gemeinsame Profil-Fixture anlegen**

Alle folgenden Tasks brauchen ein gültiges Profil. Es gehört in die wurzelnahe `conftest.py`.

```python
# conftest.py — ans Ende anfuegen
import pytest

from djset.contracts import (
    BpmRange,
    EnergyPoint,
    Profile,
    StructureRules,
    TrackCount,
    TransitionRules,
)


@pytest.fixture
def profile() -> Profile:
    """Ein realistisches Liquid-DnB-Profil, wie der Profiler es liefert."""
    return Profile(
        genre="liquid dnb",
        length_minutes=60.0,
        bpm=BpmRange(target=174.0, min=168.0, max=176.0),
        transition=TransitionRules(
            bars_min=16, bars_max=32, techniques=["blend", "eq_swap", "filter_sweep"]
        ),
        harmonic_strictness="strict",
        energy_arc=[
            EnergyPoint(position=0.0, energy=0.3),
            EnergyPoint(position=0.5, energy=0.8),
            EnergyPoint(position=1.0, energy=0.5),
        ],
        track_count=TrackCount(needed=13, request=20),
        style_vocab=["liquid drum and bass", "rhodes chords", "warm sub bass"],
        structure=StructureRules(min_intro_bars=16, min_outro_bars=16),
    )
```

- [ ] **Step 2: Den fallenden Test schreiben**

```python
# tests/test_watch.py
from pathlib import Path

import pytest

from djset.contracts import Profile
from djset.watch import AudioFile, NotEnoughMaterial, check_enough, scan


def _touch(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_bytes(b"")
    return path


def test_scan_finds_audio_and_ignores_the_rest(tmp_path: Path) -> None:
    _touch(tmp_path, "erster.wav")
    _touch(tmp_path, "zweiter.FLAC")
    _touch(tmp_path, "dritter.mp3")
    _touch(tmp_path, "notizen.txt")
    (tmp_path / "unterordner").mkdir()

    found = scan(tmp_path)

    assert [f.path.name for f in found] == ["dritter.mp3", "erster.wav", "zweiter.FLAC"]


def test_scan_derives_stable_ids_from_filenames(tmp_path: Path) -> None:
    _touch(tmp_path, "First Light Over Water.wav")

    assert scan(tmp_path) == [
        AudioFile(
            id="first-light-over-water",
            path=tmp_path / "First Light Over Water.wav",
        )
    ]


def test_scan_keeps_colliding_ids_apart(tmp_path: Path) -> None:
    _touch(tmp_path, "Morning Glass.wav")
    _touch(tmp_path, "morning glass.mp3")

    assert sorted(f.id for f in scan(tmp_path)) == ["morning-glass", "morning-glass-2"]


def test_scan_of_a_missing_folder_is_empty_not_an_error(tmp_path: Path) -> None:
    assert scan(tmp_path / "gibtsnicht") == []


def test_too_few_files_abort_before_any_agent_runs(
    tmp_path: Path, profile: Profile
) -> None:
    files = [AudioFile(id=f"t{i}", path=tmp_path / f"t{i}.wav") for i in range(3)]

    with pytest.raises(NotEnoughMaterial) as caught:
        check_enough(files, profile)

    message = str(caught.value)
    assert "3" in message
    assert str(profile.track_count.needed) in message


def test_exactly_enough_files_pass(tmp_path: Path, profile: Profile) -> None:
    needed = profile.track_count.needed
    files = [AudioFile(id=f"t{i}", path=tmp_path / f"t{i}.wav") for i in range(needed)]

    check_enough(files, profile)
```

- [ ] **Step 3: Testlauf, muss fehlschlagen**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_watch.py -v`
Expected: FAIL mit `ModuleNotFoundError` für `djset.watch`

- [ ] **Step 4: Implementieren**

```python
# djset/watch.py
"""Findet das Material im watch-Ordner und prueft vorab, ob es reicht.

Der Vorab-Check steht bewusst vor dem ersten Agentenaufruf: zu wenig Material
ist ein Zustand, den kein Agent reparieren kann, und ein Lauf, der das erst
nach der halben Analyse merkt, hat Tokens fuer nichts verbraucht.
"""

from dataclasses import dataclass
from pathlib import Path

from djset.contracts import Profile
from djset.run import slugify

AUDIO_SUFFIXES = (".wav", ".flac", ".mp3", ".m4a", ".aiff", ".aif", ".ogg")


class NotEnoughMaterial(RuntimeError):
    """Zu wenig Dateien im watch-Ordner, um das Profil zu erfuellen."""


@dataclass(frozen=True)
class AudioFile:
    id: str
    path: Path


def scan(watch_dir: Path) -> list[AudioFile]:
    """Alle Audiodateien im Ordner, stabil sortiert, mit eindeutiger ID.

    Die ID kommt aus dem Dateinamen und nicht aus einem Zaehler, damit ein
    zweiter Lauf ueber denselben Ordner dieselben Artefaktnamen erzeugt - das
    ist die Voraussetzung dafuer, dass build --resume vorhandene Analysen
    wiedererkennt.
    """
    if not watch_dir.is_dir():
        return []

    found = sorted(
        path
        for path in watch_dir.iterdir()
        if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES
    )

    files: list[AudioFile] = []
    used: set[str] = set()
    for path in found:
        base = slugify(path.stem) or "track"
        track_id = base
        counter = 2
        while track_id in used:
            track_id = f"{base}-{counter}"
            counter += 1
        used.add(track_id)
        files.append(AudioFile(id=track_id, path=path))
    return files


def check_enough(files: list[AudioFile], profile: Profile) -> None:
    """Harter Abbruch, wenn das Material die benoetigte Trackzahl unterschreitet."""
    needed = profile.track_count.needed
    if len(files) < needed:
        raise NotEnoughMaterial(
            f"Nur {len(files)} Audiodateien im watch-Ordner, gebraucht werden "
            f"{needed}. Generiere die fehlenden Tracks bei Suno und lege sie dort ab."
        )
```

- [ ] **Step 5: Testlauf, muss durchlaufen**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_watch.py -v`
Expected: 6 passed

- [ ] **Step 6: Gesamtsuite und Commit**

```bash
./.venv/Scripts/python.exe -m pytest -q
```

```bash
git add djset/watch.py tests/test_watch.py conftest.py && git commit -m "feat(watch): Material finden und vorab gegen das Profil pruefen"
```

---

### Task 2: Track-Analyst — ein Track

Stufe 3 für eine einzelne Datei. Misst über das DSP-Modul, korrigiert Tempo-Oktaven deterministisch, lässt das Modell urteilen und höchstens einmal eine Neumessung anfordern.

**Files:**
- Modify: `djset/contracts.py`
- Create: `djset/agents/analyst.py`
- Test: `tests/agents/test_analyst.py`

**Interfaces:**
- Consumes: `djset.dsp.analyze.measure_track`, `djset.dsp.analyze.snap_to_range`, `djset.agents.base.run_agent`, `djset.agents.base.Budget`, `djset.agents.base.AgentClient`, `djset.contracts.Profile`, `djset.contracts.TrackMeasurements`, `djset.contracts.TrackAnalysis`, `djset.contracts.Verdict`
- Produces: `djset.contracts.UnreadableTrack`, `AnalystAnswer`, `analyse_track(client, path, track_id, profile, budget, max_rounds=MAX_ROUNDS) -> TrackAnalysis | UnreadableTrack`, `SNAP_CONFIDENCE_PENALTY: float`, `MAX_ROUNDS: int`

- [ ] **Step 1: Die Verträge erweitern**

`Verdict` in `djset/contracts.py` ersetzen und `UnreadableTrack` direkt darunter ergänzen:

```python
class Verdict(BaseModel):
    """Das Urteil des Track-Analysten ueber einen Track.

    `energy_level` ordnet den Track auf derselben 0..1-Skala ein, auf der das
    Profil seinen energy_arc beschreibt. Bewusst vom Agenten vergeben und nicht
    aus TrackMeasurements.energy gerechnet: die gemessene Kurve ist trackrelativ
    und ueber Tracks hinweg nicht vergleichbar, waehrend der Bogen absolute
    Zielenergien nennt. Dasselbe Modell, das den Bogen geschrieben hat, ordnet
    die Tracks darauf ein - das ist die einzige Stelle, an der beide Skalen
    zuverlaessig zusammenfinden.
    """

    usable: bool
    notes: str = Field(min_length=1)
    energy_level: float = Field(ge=0.0, le=1.0)


class UnreadableTrack(BaseModel):
    """Eine Datei, die sich nicht messen liess.

    Spec Abschnitt 7: eine kaputte Einzeldatei darf den Lauf nicht sprengen.
    Sie bekommt trotzdem ein Artefakt, damit nachvollziehbar bleibt, warum sie
    nicht im Set ist.
    """

    id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    error: str = Field(min_length=1)
```

Und in `TrackMeasurements` über dem `energy`-Feld:

```python
    # ACHTUNG: trackrelativ. Die Kurve ist gegen eine feste dBFS-Referenz
    # normiert und damit innerhalb eines Tracks aussagekraeftig - fuer die
    # Mix-Regionen. Sie ist NICHT die Groesse, an der Stufe 5 Tracks
    # miteinander vergleicht; dafuer gibt es Verdict.energy_level.
    energy: list[float] = Field(min_length=1)
```

- [ ] **Step 2: Den fallenden Test schreiben**

```python
# tests/agents/test_analyst.py
import json
from pathlib import Path

import numpy as np
import soundfile as sf

from djset.agents.analyst import SNAP_CONFIDENCE_PENALTY, analyse_track
from djset.agents.base import Budget
from djset.contracts import Profile, TrackAnalysis, UnreadableTrack
from tests.fixtures.fake_client import FakeClient
from tests.fixtures.make_audio import click_track

ANSWER = {
    "usable": True,
    "notes": "Sauberes Raster, passt ins Genre.",
    "energy_level": 0.62,
    "remeasure_with_bpm": None,
}


def _budget() -> Budget:
    return Budget(max_tokens=50_000, max_seconds=60.0)


def test_a_measured_track_gets_a_verdict(tmp_path: Path, profile: Profile) -> None:
    path = click_track(tmp_path / "t.wav", bpm=174.0, seconds=20.0)
    client = FakeClient([json.dumps(ANSWER)])

    result = analyse_track(client, path, "t", profile, _budget())

    assert isinstance(result, TrackAnalysis)
    assert result.id == "t"
    assert result.verdict.usable is True
    assert result.verdict.energy_level == 0.62
    assert client.calls == 1


def test_a_halftime_reading_is_snapped_before_the_model_sees_it(
    tmp_path: Path, profile: Profile
) -> None:
    """87 statt 174: die Korrektur ist Arithmetik gegen den Korridor, kein Urteil."""
    path = click_track(tmp_path / "t.wav", bpm=87.0, seconds=20.0)
    client = FakeClient([json.dumps(ANSWER)])

    result = analyse_track(client, path, "t", profile, _budget())

    assert isinstance(result, TrackAnalysis)
    assert profile.bpm.min <= result.bpm.value <= profile.bpm.max
    assert "174" in client.prompts[0]


def test_snapping_lowers_the_confidence(tmp_path: Path, profile: Profile) -> None:
    halftime = click_track(tmp_path / "t.wav", bpm=87.0, seconds=20.0)
    plain = click_track(tmp_path / "u.wav", bpm=174.0, seconds=20.0)

    snapped = analyse_track(
        FakeClient([json.dumps(ANSWER)]), halftime, "t", profile, _budget()
    )
    unsnapped = analyse_track(
        FakeClient([json.dumps(ANSWER)]), plain, "u", profile, _budget()
    )

    assert isinstance(snapped, TrackAnalysis)
    assert isinstance(unsnapped, TrackAnalysis)
    assert snapped.bpm.confidence < unsnapped.bpm.confidence


def test_the_model_may_ask_for_one_remeasure(tmp_path: Path, profile: Profile) -> None:
    path = click_track(tmp_path / "t.wav", bpm=174.0, seconds=20.0)
    asks_again = json.dumps(dict(ANSWER, remeasure_with_bpm=174.0))
    client = FakeClient([asks_again, json.dumps(ANSWER)])

    result = analyse_track(client, path, "t", profile, _budget())

    assert isinstance(result, TrackAnalysis)
    assert client.calls == 2


def test_the_remeasure_loop_is_bounded(tmp_path: Path, profile: Profile) -> None:
    """Ein Modell, das immer weiter messen will, darf den Lauf nicht aufhalten."""
    path = click_track(tmp_path / "t.wav", bpm=174.0, seconds=20.0)
    asks_again = json.dumps(dict(ANSWER, remeasure_with_bpm=174.0))
    client = FakeClient([asks_again, asks_again])

    result = analyse_track(client, path, "t", profile, _budget(), max_rounds=2)

    assert isinstance(result, TrackAnalysis)
    assert client.calls == 2


def test_an_unreadable_file_does_not_stop_the_run(
    tmp_path: Path, profile: Profile
) -> None:
    broken = tmp_path / "kaputt.wav"
    broken.write_bytes(b"das ist kein wav")
    client = FakeClient([])

    result = analyse_track(client, broken, "kaputt", profile, _budget())

    assert isinstance(result, UnreadableTrack)
    assert result.id == "kaputt"
    assert result.error
    assert client.calls == 0


def test_a_silent_file_is_unreadable_not_an_exception(
    tmp_path: Path, profile: Profile
) -> None:
    """Digitale Stille bringt librosa zum Warnen - das darf keinen Lauf reissen."""
    silent = tmp_path / "still.wav"
    sf.write(silent, np.zeros(44_100 * 5, dtype=np.float32), 44_100)
    client = FakeClient([])

    result = analyse_track(client, silent, "still", profile, _budget())

    assert isinstance(result, UnreadableTrack)
    assert client.calls == 0
```

- [ ] **Step 3: Testlauf, muss fehlschlagen**

Run: `./.venv/Scripts/python.exe -m pytest tests/agents/test_analyst.py -v`
Expected: FAIL mit `ModuleNotFoundError` für `djset.agents.analyst`

- [ ] **Step 4: Implementieren**

```python
# djset/agents/analyst.py
"""Stufe 3: misst jeden Track und faellt ein Urteil ueber die Brauchbarkeit.

Orchestriert, nicht werkzeugnutzend. Python ruft die DSP-Messung, reicht die
Zahlen an das Modell und dreht die Schleife selbst; das Modell darf eine
Neumessung mit Tempo-Hinweis anfordern, ruft die Messung aber nicht auf. Damit
bleibt die Stufe ohne Netz testbar, und die Auffaecherung ueber viele Dateien
ist ein Thread-Pool statt N SDK-Subprozesse.

Was Arithmetik ist, bleibt Arithmetik: die Oktavkorrektur laeuft ueber
snap_to_range gegen den Profilkorridor, bevor das Modell die Zahlen sieht. Das
Modell urteilt ueber Brauchbarkeit und Energiepegel, nicht ueber Faktor zwei.
"""

import warnings
from pathlib import Path

from pydantic import BaseModel, Field

from djset.agents.base import AgentClient, Budget, run_agent
from djset.contracts import (
    Profile,
    TrackAnalysis,
    TrackMeasurements,
    UnreadableTrack,
    Verdict,
)
from djset.dsp.analyze import measure_track, snap_to_range

# Wurde die Oktave korrigiert, stammt der Wert nicht mehr aus der Messung
# allein - die Konfidenz muss das abbilden.
SNAP_CONFIDENCE_PENALTY = 0.5

# Hoechstens so viele Modellfragen pro Track.
MAX_ROUNDS = 2


class AnalystAnswer(BaseModel):
    """Was der Analyst zurueckgibt.

    remeasure_with_bpm ist der orchestrierte Ersatz fuer einen Werkzeugaufruf:
    das Modell sagt, mit welchem Tempo-Hinweis neu gemessen werden soll, und
    Python fuehrt das aus.
    """

    usable: bool
    notes: str = Field(min_length=1)
    energy_level: float = Field(ge=0.0, le=1.0)
    remeasure_with_bpm: float | None = None


PROMPT = """Du bist ein DJ und beurteilst, ob ein Track in ein Set passt.

Das Set: {genre}, Ziel-BPM {bpm_target}, erlaubt {bpm_min} bis {bpm_max},
harmonische Strenge {strictness}. Der Energiebogen des Sets laeuft ueber diese
Stuetzpunkte aus Position und Zielenergie: {arc}.

Gemessen wurde:
- Dauer: {duration:.1f} Sekunden
- BPM: {bpm_value:.1f}, Rastersicherheit {bpm_conf:.2f}
- Taktraster: erster Downbeat bei {downbeat:.2f} s, Taktlaenge {bar:.3f} s
- Tonart: {camelot}, Sicherheit {key_conf:.2f}
- Energiekurve in 4-Takt-Schritten: {energy}
- Intro endet bei {intro:.1f} s, Outro beginnt bei {outro:.1f} s
- Lautheit: {lufs:.1f} LUFS, True Peak {peak:.1f} dB

Beurteile:

1. usable - passt der Track ins Set? Achte darauf, ob das Tempo im Korridor
   liegt, ob das Raster stabil genug fuer Cue-Punkte ist, ob es ueberhaupt ein
   Intro und ein Outro zum Mixen gibt und ob die Tonart aufs Camelot-Rad passt.
2. notes - eine knappe Begruendung in einem Satz. Bei usable false muss dort
   stehen, woran es liegt.
3. energy_level - wo dieser Track auf der Energieskala des Bogens steht,
   zwischen 0 und 1. Die gemessene Kurve ist trackrelativ und taugt dafuer
   nicht; ordne den Track so ein, wie du ihn im Vergleich zu den Zielenergien
   des Bogens hoerst.
4. remeasure_with_bpm - normalerweise null. Nur setzen, wenn die Zahlen auf ein
   falsch erkanntes Raster deuten und mit einem Tempo-Hinweis etwas Besseres
   herauskaeme. Nenne dann das vermutete Tempo.

Antworte ausschliesslich mit dem geforderten JSON."""


def _snap_octave(measurements: TrackMeasurements, profile: Profile) -> TrackMeasurements:
    """Haelt Halftime- und Doubletime-Lesungen an den Profilkorridor an."""
    snapped, corrected = snap_to_range(
        measurements.bpm.value, profile.bpm.min, profile.bpm.max
    )
    if not corrected:
        return measurements
    # model_validate statt model_copy(update=...): letzteres validiert nicht.
    return TrackMeasurements.model_validate(
        measurements.model_dump()
        | {
            "bpm": {
                "value": snapped,
                "confidence": measurements.bpm.confidence * SNAP_CONFIDENCE_PENALTY,
            }
        }
    )


def _prompt(measurements: TrackMeasurements, profile: Profile) -> str:
    arc = ", ".join(f"({p.position:.2f}, {p.energy:.2f})" for p in profile.energy_arc)
    energy = ", ".join(f"{value:.2f}" for value in measurements.energy)
    return PROMPT.format(
        genre=profile.genre,
        bpm_target=profile.bpm.target,
        bpm_min=profile.bpm.min,
        bpm_max=profile.bpm.max,
        strictness=profile.harmonic_strictness,
        arc=arc,
        duration=measurements.duration_s,
        bpm_value=measurements.bpm.value,
        bpm_conf=measurements.bpm.confidence,
        downbeat=measurements.beatgrid.first_downbeat_s,
        bar=measurements.beatgrid.bar_length_s,
        camelot=measurements.key.camelot,
        key_conf=measurements.key.confidence,
        energy=energy,
        intro=measurements.mix_regions.intro_end_s,
        outro=measurements.mix_regions.outro_start_s,
        lufs=measurements.loudness.lufs_integrated,
        peak=measurements.loudness.true_peak,
    )


def _measure(path: Path, track_id: str, hint_bpm: float | None) -> TrackMeasurements:
    """Misst und macht librosa-Warnungen bei entartetem Material zu Fehlern.

    Unter filterwarnings error waere eine Warnung ohnehin ein Absturz; hier wird
    sie zu einem sauberen Messfehler, den der Aufrufer als UnreadableTrack
    verbuchen kann, statt den ganzen Lauf zu reissen.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        return measure_track(path, track_id, hint_bpm=hint_bpm)


def analyse_track(
    client: AgentClient,
    path: Path,
    track_id: str,
    profile: Profile,
    budget: Budget,
    max_rounds: int = MAX_ROUNDS,
) -> TrackAnalysis | UnreadableTrack:
    """Misst einen Track und holt das Urteil des Analysten dazu."""
    try:
        measurements = _measure(path, track_id, None)
    except Exception as error:  # noqa: BLE001 - jede Messstoerung endet gleich
        return UnreadableTrack(id=track_id, path=str(path), error=str(error))

    rounds = max(1, max_rounds)
    measurements = _snap_octave(measurements, profile)
    answer = run_agent(client, _prompt(measurements, profile), AnalystAnswer, budget)

    for _extra in range(rounds - 1):
        if answer.remeasure_with_bpm is None:
            break
        try:
            measurements = _measure(path, track_id, answer.remeasure_with_bpm)
        except Exception as error:  # noqa: BLE001
            return UnreadableTrack(id=track_id, path=str(path), error=str(error))
        measurements = _snap_octave(measurements, profile)
        answer = run_agent(client, _prompt(measurements, profile), AnalystAnswer, budget)

    return TrackAnalysis(
        **measurements.model_dump(),
        verdict=Verdict(
            usable=answer.usable,
            notes=answer.notes,
            energy_level=answer.energy_level,
        ),
    )
```

- [ ] **Step 5: Testlauf, muss durchlaufen**

Run: `./.venv/Scripts/python.exe -m pytest tests/agents/test_analyst.py -v`
Expected: 7 passed

- [ ] **Step 6: Gesamtsuite und Commit**

```bash
./.venv/Scripts/python.exe -m pytest -q
```

```bash
git add djset/contracts.py djset/agents/analyst.py tests/agents/test_analyst.py && git commit -m "feat(analyst): Track messen und beurteilen, Oktave deterministisch korrigiert"
```

---

### Task 3: Auffächerung und Wiederaufnahme

Spec: "Fächert pro Datei parallel auf" und "die Track-Analyse ist der teuerste Schritt. Nach einem Abbruch wird ab dem letzten vorhandenen Artefakt weitergearbeitet." Beides hier.

**Files:**
- Modify: `djset/agents/base.py` (`Budget` threadsicher)
- Create: `djset/analysis.py`
- Test: `tests/test_analysis.py`

**Interfaces:**
- Consumes: `djset.agents.analyst.analyse_track`, `djset.run.Run`, `djset.watch.AudioFile`, `djset.contracts.TrackAnalysis`, `djset.contracts.UnreadableTrack`
- Produces: `analyse_all(client, files, profile, run, budget, workers=DEFAULT_WORKERS) -> tuple[list[TrackAnalysis], list[UnreadableTrack]]`, `artifact_name(track_id: str) -> str`, `DEFAULT_WORKERS: int`

- [ ] **Step 1: `Budget` threadsicher machen**

Bei paralleler Analyse rufen mehrere Threads `charge()` gleichzeitig. `self.spent += tokens` ist kein atomarer Vorgang — ohne Sperre gehen Buchungen verloren und die Notbremse greift zu spät.

In `djset/agents/base.py`:

```python
import threading
```

Im `Budget.__init__` ergänzen:

```python
        # charge() wird bei paralleler Track-Analyse aus mehreren Threads
        # gerufen. Ohne Sperre gehen Buchungen verloren und die Notbremse
        # greift zu spaet.
        self._lock = threading.Lock()
```

Und `charge()` komplett unter die Sperre stellen — Lesen, Pruefen und Schreiben muessen zusammen atomar sein:

```python
    def charge(self, text: str) -> None:
        tokens = max(1, len(text) // _CHARS_PER_TOKEN)
        with self._lock:
            if self.elapsed_s > self.max_seconds:
                raise BudgetExceeded(
                    f"Zeitbudget erschoepft: {self.elapsed_s:.0f} s von "
                    f"{self.max_seconds:.0f} s verbraucht"
                )
            if self.spent + tokens > self.max_tokens:
                raise BudgetExceeded(
                    f"Tokenbudget erschoepft: {self.spent} von {self.max_tokens} "
                    f"verbraucht, {tokens} weitere angefordert"
                )
            self.spent += tokens
```

- [ ] **Step 2: Den fallenden Test schreiben**

```python
# tests/test_analysis.py
import json
from datetime import date
from pathlib import Path

from djset.agents.base import Budget
from djset.analysis import analyse_all, artifact_name
from djset.contracts import Profile, TrackAnalysis
from djset.run import Run
from djset.watch import AudioFile
from tests.fixtures.fake_client import FakeClient
from tests.fixtures.make_audio import click_track

ANSWER = json.dumps(
    {
        "usable": True,
        "notes": "Passt.",
        "energy_level": 0.5,
        "remeasure_with_bpm": None,
    }
)


def _run(tmp_path: Path) -> Run:
    return Run.create(tmp_path, "liquid dnb", date(2026, 8, 21))


def _files(tmp_path: Path, count: int) -> list[AudioFile]:
    return [
        AudioFile(
            id=f"t{i}",
            path=click_track(tmp_path / f"t{i}.wav", bpm=174.0, seconds=12.0),
        )
        for i in range(count)
    ]


def test_every_file_gets_analysed_and_written(tmp_path: Path, profile: Profile) -> None:
    run = _run(tmp_path)
    files = _files(tmp_path, 3)
    client = FakeClient([ANSWER] * 3)

    analyses, unreadable = analyse_all(client, files, profile, run, _budget())

    assert sorted(a.id for a in analyses) == ["t0", "t1", "t2"]
    assert unreadable == []
    for track_id in ("t0", "t1", "t2"):
        assert run.has(artifact_name(track_id))


def test_existing_artifacts_are_reused_not_reanalysed(
    tmp_path: Path, profile: Profile
) -> None:
    """Die Analyse ist der teuerste Schritt - resume darf sie nicht wiederholen."""
    run = _run(tmp_path)
    files = _files(tmp_path, 3)

    analyse_all(FakeClient([ANSWER] * 3), files, profile, run, _budget())
    second = FakeClient([])

    analyses, unreadable = analyse_all(second, files, profile, run, _budget())

    assert second.calls == 0
    assert sorted(a.id for a in analyses) == ["t0", "t1", "t2"]
    assert unreadable == []


def test_a_partial_run_only_analyses_what_is_missing(
    tmp_path: Path, profile: Profile
) -> None:
    run = _run(tmp_path)
    files = _files(tmp_path, 3)
    analyse_all(FakeClient([ANSWER]), files[:1], profile, run, _budget())

    second = FakeClient([ANSWER] * 2)
    analyses, _ = analyse_all(second, files, profile, run, _budget())

    assert second.calls == 2
    assert len(analyses) == 3


def test_a_broken_file_is_recorded_and_the_rest_continues(
    tmp_path: Path, profile: Profile
) -> None:
    run = _run(tmp_path)
    files = _files(tmp_path, 2)
    broken = tmp_path / "kaputt.wav"
    broken.write_bytes(b"kein wav")
    files.append(AudioFile(id="kaputt", path=broken))

    analyses, unreadable = analyse_all(
        FakeClient([ANSWER] * 2), files, profile, run, _budget()
    )

    assert len(analyses) == 2
    assert [u.id for u in unreadable] == ["kaputt"]
    assert run.has(artifact_name("kaputt"))


def test_unreadable_artifacts_are_reused_too(tmp_path: Path, profile: Profile) -> None:
    run = _run(tmp_path)
    broken = tmp_path / "kaputt.wav"
    broken.write_bytes(b"kein wav")
    files = [AudioFile(id="kaputt", path=broken)]

    analyse_all(FakeClient([]), files, profile, run, _budget())
    analyses, unreadable = analyse_all(FakeClient([]), files, profile, run, _budget())

    assert analyses == []
    assert [u.id for u in unreadable] == ["kaputt"]


def _budget() -> Budget:
    return Budget(max_tokens=200_000, max_seconds=120.0)
```

- [ ] **Step 3: Testlauf, muss fehlschlagen**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_analysis.py -v`
Expected: FAIL mit `ModuleNotFoundError` für `djset.analysis`

- [ ] **Step 4: Implementieren**

```python
# djset/analysis.py
"""Stufe 3 ueber alle Dateien: parallel analysieren, Vorhandenes wiederverwenden.

Die Analyse ist der teuerste Schritt des Laufs - sie oeffnet Audio, rechnet DSP
und fragt pro Track ein Modell. Deshalb zwei Dinge: sie faechert ueber Threads
auf, und jedes Ergebnis landet sofort als Artefakt auf der Platte. Ein
abgebrochener Lauf verliert damit hoechstens die Tracks, die gerade in Arbeit
waren, statt aller bereits bezahlten.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import ValidationError

from djset.agents.analyst import analyse_track
from djset.agents.base import AgentClient, Budget
from djset.contracts import Profile, TrackAnalysis, UnreadableTrack
from djset.run import Run
from djset.watch import AudioFile

# Vier gleichzeitige Analysen. Jede haelt einen SDK-Aufruf und rechnet DSP;
# mehr Threads bringen wenig, weil librosa ohnehin BLAS-parallel rechnet.
DEFAULT_WORKERS = 4


def artifact_name(track_id: str) -> str:
    """Der Artefaktpfad einer Trackanalyse, relativ zum Run-Ordner."""
    return f"tracks/{track_id}.json"


def _read_existing(run: Run, track_id: str) -> TrackAnalysis | UnreadableTrack | None:
    """Liest ein vorhandenes Artefakt, falls es eines gibt.

    Beide Formen liegen unter demselben Namen: erst die vollstaendige Analyse
    versuchen, sonst den Fehlvermerk. Was sich als keines von beidem lesen
    laesst, gilt als nicht vorhanden und wird neu analysiert.
    """
    name = artifact_name(track_id)
    if not run.has(name):
        return None
    for model_type in (TrackAnalysis, UnreadableTrack):
        try:
            return run.read_model(name, model_type)
        except ValidationError:
            continue
    return None


def analyse_all(
    client: AgentClient,
    files: list[AudioFile],
    profile: Profile,
    run: Run,
    budget: Budget,
    workers: int = DEFAULT_WORKERS,
) -> tuple[list[TrackAnalysis], list[UnreadableTrack]]:
    """Analysiert alle Dateien und schreibt je ein Artefakt."""

    def one(file: AudioFile) -> TrackAnalysis | UnreadableTrack:
        existing = _read_existing(run, file.id)
        if existing is not None:
            return existing
        result = analyse_track(client, Path(file.path), file.id, profile, budget)
        run.write_model(artifact_name(file.id), result)
        return result

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(one, files))

    analyses = [r for r in results if isinstance(r, TrackAnalysis)]
    unreadable = [r for r in results if isinstance(r, UnreadableTrack)]
    return analyses, unreadable
```

- [ ] **Step 5: Testlauf, muss durchlaufen**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_analysis.py -v`
Expected: 5 passed

- [ ] **Step 6: Gesamtsuite und Commit**

```bash
./.venv/Scripts/python.exe -m pytest -q
```

```bash
git add djset/analysis.py djset/agents/base.py tests/test_analysis.py && git commit -m "feat(analysis): parallele Trackanalyse mit Wiederaufnahme aus Artefakten"
```

---

### Task 4: Fixture-Analysen für die Entscheidungsstufen

Spec Abschnitt 8: "Kreative Agenten gegen Fixture-JSON. Ein Satz erfundener Track-Analysen als Eingabe." Ab hier arbeitet keine Stufe mehr auf Audio, also brauchen die Tests keins mehr. Diese Task legt das Material dafür an — und wird von den Tasks 5 bis 8 benutzt.

**Files:**
- Create: `tests/fixtures/analyses.py`
- Test: `tests/fixtures/test_analyses.py`

**Interfaces:**
- Consumes: `djset.contracts.TrackAnalysis` und die darin enthaltenen Teilverträge
- Produces: `make_analysis(track_id, *, bpm=174.0, camelot="8A", energy=0.5, duration_s=240.0) -> TrackAnalysis`, `wheel_walk(count: int) -> list[str]`, `many(count: int) -> list[TrackAnalysis]`

- [ ] **Step 1: Den fallenden Test schreiben**

```python
# tests/fixtures/test_analyses.py
from djset.camelot import is_compatible
from djset.contracts import TrackAnalysis
from tests.fixtures.analyses import make_analysis, many, wheel_walk


def test_a_made_analysis_is_a_valid_contract() -> None:
    analysis = make_analysis("t1")

    assert isinstance(analysis, TrackAnalysis)
    assert analysis.id == "t1"
    assert analysis.verdict.usable is True


def test_the_wheel_walk_stays_harmonically_adjacent() -> None:
    keys = wheel_walk(8)

    assert len(keys) == 8
    for first, second in zip(keys, keys[1:]):
        assert is_compatible(first, second, "strict")


def test_many_gives_distinct_ids_and_varied_energy() -> None:
    tracks = many(6)

    assert len({t.id for t in tracks}) == 6
    assert len({t.verdict.energy_level for t in tracks}) > 1
```

- [ ] **Step 2: Testlauf, muss fehlschlagen**

Run: `./.venv/Scripts/python.exe -m pytest tests/fixtures/test_analyses.py -v`
Expected: FAIL mit `ModuleNotFoundError` für `tests.fixtures.analyses`

- [ ] **Step 3: Implementieren**

```python
# tests/fixtures/analyses.py
"""Erfundene Track-Analysen als Eingabe fuer die Stufen 4 bis 7.

Ab Stufe 4 sieht keine Stufe mehr Audio, nur noch JSON. Diese Fixtures sind
deshalb die vollstaendige Testeingabe fuer Curator, Architect, Designer und
Critic - schnell, deterministisch, ohne eine einzige Audiodatei.
"""

from djset.contracts import (
    BeatGrid,
    Bpm,
    Key,
    Loudness,
    MixRegions,
    TrackAnalysis,
    Verdict,
)

# Ein Gang ueber das Camelot-Rad, der die strikte Nachbarschaft nie verletzt:
# gleiche Zahl A/B wechseln oder eine Position weiter.
_WALK = ["8A", "8B", "9B", "9A", "10A", "10B", "11B", "11A", "12A", "12B", "1B", "1A"]


def wheel_walk(count: int) -> list[str]:
    """count Tonarten, bei denen je zwei aufeinanderfolgende harmonisch passen."""
    return [_WALK[index % len(_WALK)] for index in range(count)]


def make_analysis(
    track_id: str,
    *,
    bpm: float = 174.0,
    camelot: str = "8A",
    energy: float = 0.5,
    duration_s: float = 240.0,
    usable: bool = True,
) -> TrackAnalysis:
    """Eine vollstaendige, gueltige Trackanalyse mit sprechenden Vorgaben."""
    bar_length = 4 * 60.0 / bpm
    return TrackAnalysis(
        id=track_id,
        path=f"/watch/{track_id}.wav",
        duration_s=duration_s,
        bpm=Bpm(value=bpm, confidence=0.95),
        beatgrid=BeatGrid(
            first_downbeat_s=0.0, bar_length_s=bar_length, confidence=0.95
        ),
        key=Key(camelot=camelot, confidence=0.9),
        energy=[0.80, 0.84, 0.86, 0.85, 0.82],
        mix_regions=MixRegions(
            intro_end_s=bar_length * 16,
            outro_start_s=duration_s - bar_length * 16,
        ),
        loudness=Loudness(lufs_integrated=-9.5, true_peak=-1.2),
        verdict=Verdict(
            usable=usable, notes="Fixture-Track.", energy_level=energy
        ),
    )


def many(count: int) -> list[TrackAnalysis]:
    """count zusammenpassende Tracks: harmonisch benachbart, gestaffelte Energie."""
    keys = wheel_walk(count)
    return [
        make_analysis(
            f"t{index}",
            camelot=keys[index],
            energy=round(0.3 + 0.5 * index / max(1, count - 1), 2),
            bpm=170.0 + (index % 5),
        )
        for index in range(count)
    ]
```

- [ ] **Step 4: Testlauf, muss durchlaufen**

Run: `./.venv/Scripts/python.exe -m pytest tests/fixtures/test_analyses.py -v`
Expected: 3 passed

- [ ] **Step 5: Gesamtsuite und Commit**

```bash
./.venv/Scripts/python.exe -m pytest -q
```

```bash
git add tests/fixtures/analyses.py tests/fixtures/test_analyses.py && git commit -m "test(fixtures): erfundene Trackanalysen fuer die Entscheidungsstufen"
```

---

### Task 5: Curator

Stufe 4. Sortiert aus, was nicht passt, jede Ablehnung mit Begründung. Danach steht fest, mit wie vielen Tracks das Set gebaut wird.

**Files:**
- Create: `djset/agents/curator.py`
- Test: `tests/agents/test_curator.py`

**Interfaces:**
- Consumes: `djset.agents.base.run_agent`, `djset.contracts.Shortlist`, `djset.contracts.Rejection`, `djset.contracts.MIN_TRACKS`, `djset.contracts.TrackAnalysis`, `djset.contracts.UnreadableTrack`
- Produces: `curate(client, analyses, unreadable, profile, budget) -> Shortlist`, `NotEnoughTracks(RuntimeError)`

- [ ] **Step 1: Den fallenden Test schreiben**

```python
# tests/agents/test_curator.py
import json

import pytest

from djset.agents.base import Budget
from djset.agents.curator import NotEnoughTracks, curate
from djset.contracts import MIN_TRACKS, Profile, UnreadableTrack
from tests.fixtures.analyses import many
from tests.fixtures.fake_client import FakeClient


def _budget() -> Budget:
    return Budget(max_tokens=50_000, max_seconds=60.0)


def _answer(accepted: list[str], rejected: list[tuple[str, str]]) -> str:
    return json.dumps(
        {
            "accepted": accepted,
            "rejected": [{"track_id": i, "reason": r} for i, r in rejected],
            "note": "Fixture-Urteil.",
        }
    )


def test_the_curator_keeps_what_it_accepts(profile: Profile) -> None:
    tracks = many(14)
    ids = [t.id for t in tracks]
    client = FakeClient([_answer(ids[:13], [(ids[13], "am Genre vorbei")])])

    shortlist = curate(client, tracks, [], profile, _budget())

    assert shortlist.accepted == ids[:13]
    assert [r.track_id for r in shortlist.rejected] == [ids[13]]
    assert shortlist.shortened is False


def test_unreadable_files_are_rejected_without_asking_the_model(
    profile: Profile,
) -> None:
    """Eine unlesbare Datei ist kein Urteilsfall - sie ist einfach nicht da."""
    tracks = many(13)
    broken = UnreadableTrack(id="kaputt", path="/w/k.wav", error="kein wav")
    client = FakeClient([_answer([t.id for t in tracks], [])])

    shortlist = curate(client, tracks, [broken], profile, _budget())

    rejected = {r.track_id: r.reason for r in shortlist.rejected}
    assert "kaputt" in rejected
    assert "kein wav" in rejected["kaputt"]
    assert "kaputt" not in client.prompts[0]


def test_fewer_than_needed_shortens_the_set_with_a_note(profile: Profile) -> None:
    tracks = many(8)
    ids = [t.id for t in tracks]
    client = FakeClient([_answer(ids, [])])

    shortlist = curate(client, tracks, [], profile, _budget())

    assert shortlist.shortened is True
    assert shortlist.note


def test_below_the_floor_is_a_hard_abort(profile: Profile) -> None:
    tracks = many(5)
    ids = [t.id for t in tracks]
    client = FakeClient([_answer(ids[:2], [(i, "zu schwach") for i in ids[2:]])])

    with pytest.raises(NotEnoughTracks) as caught:
        curate(client, tracks, [], profile, _budget())

    assert str(MIN_TRACKS) in str(caught.value)


def test_an_invented_track_id_is_re_asked(profile: Profile) -> None:
    """Der Curator darf nur ueber Tracks urteilen, die es gibt."""
    tracks = many(13)
    ids = [t.id for t in tracks]
    client = FakeClient([_answer(ids + ["erfunden"], []), _answer(ids, [])])

    shortlist = curate(client, tracks, [], profile, _budget())

    assert shortlist.accepted == ids
    assert client.calls == 2
    assert "erfunden" in client.prompts[1]
```

- [ ] **Step 2: Testlauf, muss fehlschlagen**

Run: `./.venv/Scripts/python.exe -m pytest tests/agents/test_curator.py -v`
Expected: FAIL mit `ModuleNotFoundError` für `djset.agents.curator`

- [ ] **Step 3: Implementieren**

```python
# djset/agents/curator.py
"""Stufe 4: sortiert aus, was nicht ins Set passt - jede Ablehnung begruendet.

Unlesbare Dateien laufen an dem Modell vorbei: dass eine Datei kaputt ist, ist
kein Urteilsfall, sondern ein Fakt. Sie landen trotzdem als Ablehnung in der
Shortlist, damit im Report nachvollziehbar bleibt, warum sie fehlen.
"""

from pydantic import BaseModel, Field

from djset.agents.base import AgentClient, Budget, run_agent
from djset.contracts import (
    MIN_TRACKS,
    Profile,
    Rejection,
    Shortlist,
    TrackAnalysis,
    UnreadableTrack,
)

MAX_ROUNDS = 3


class NotEnoughTracks(RuntimeError):
    """Nach dem Aussortieren bleiben weniger als MIN_TRACKS uebrig."""


class CuratorAnswer(BaseModel):
    accepted: list[str]
    rejected: list[Rejection]
    note: str = Field(min_length=1)


PROMPT = """Du stellst das Material fuer ein DJ-Set zusammen und sortierst aus,
was nicht passt.

Das Set: {genre}, Ziel-BPM {bpm_target}, erlaubt {bpm_min} bis {bpm_max},
harmonische Strenge {strictness}. Gebraucht werden {needed} Tracks; es liegen
mehr vor, damit du waehlerisch sein kannst.

Die analysierten Tracks:

{table}

Sortiere aus, was nicht taugt: falsche BPM-Familie, kaputte Struktur (kein
brauchbares Intro oder Outro), zu kurz fuer einen Uebergang, am Genre vorbei,
oder ein Urteil des Analysten, das dagegen spricht.

Regeln:
- Jede track_id kommt genau einmal vor, entweder in accepted oder in rejected.
- Erfinde keine IDs; benutze nur die aus der Tabelle.
- Jede Ablehnung braucht eine konkrete Begruendung in einem Satz.
- Nimm hoechstens {needed} Tracks an. Sind weniger brauchbar, nimm die
  brauchbaren und begruende den Rest.

Antworte ausschliesslich mit dem geforderten JSON."""


def _table(analyses: list[TrackAnalysis]) -> str:
    lines = []
    for a in analyses:
        lines.append(
            f"- {a.id}: {a.bpm.value:.1f} BPM (Raster {a.beatgrid.confidence:.2f}), "
            f"{a.key.camelot}, Energie {a.verdict.energy_level:.2f}, "
            f"{a.duration_s:.0f} s, Intro bis {a.mix_regions.intro_end_s:.0f} s, "
            f"Outro ab {a.mix_regions.outro_start_s:.0f} s, "
            f"brauchbar {a.verdict.usable}, Analyst: {a.verdict.notes}"
        )
    return "\n".join(lines)


def _check(answer: CuratorAnswer, known: set[str], needed: int) -> str | None:
    """Prueft die Antwort gegen das, was es tatsaechlich gibt."""
    named = set(answer.accepted) | {r.track_id for r in answer.rejected}
    invented = named - known
    if invented:
        return f"Diese IDs gibt es nicht: {', '.join(sorted(invented))}."
    missing = known - named
    if missing:
        return f"Diese Tracks fehlen in deiner Antwort: {', '.join(sorted(missing))}."
    both = set(answer.accepted) & {r.track_id for r in answer.rejected}
    if both:
        return f"Diese IDs stehen doppelt: {', '.join(sorted(both))}."
    if len(set(answer.accepted)) != len(answer.accepted):
        return "In accepted steht eine ID mehrfach."
    if len(answer.accepted) > needed:
        return f"Du hast {len(answer.accepted)} Tracks angenommen, erlaubt sind {needed}."
    return None


def curate(
    client: AgentClient,
    analyses: list[TrackAnalysis],
    unreadable: list[UnreadableTrack],
    profile: Profile,
    budget: Budget,
) -> Shortlist:
    """Waehlt aus den analysierten Tracks die aus, mit denen gebaut wird."""
    needed = profile.track_count.needed
    known = {a.id for a in analyses}
    prompt = PROMPT.format(
        genre=profile.genre,
        bpm_target=profile.bpm.target,
        bpm_min=profile.bpm.min,
        bpm_max=profile.bpm.max,
        strictness=profile.harmonic_strictness,
        needed=needed,
        table=_table(analyses),
    )

    current = prompt
    answer = run_agent(client, current, CuratorAnswer, budget, retries=1)
    for _round in range(MAX_ROUNDS - 1):
        problem = _check(answer, known, needed)
        if problem is None:
            break
        current = f"{prompt}\n\nDein letzter Versuch war unbrauchbar: {problem}"
        answer = run_agent(client, current, CuratorAnswer, budget, retries=1)
    else:
        problem = _check(answer, known, needed)
        if problem is not None:
            raise NotEnoughTracks(
                f"Der Curator lieferte nach {MAX_ROUNDS} Versuchen keine "
                f"brauchbare Auswahl: {problem}"
            )

    rejected = list(answer.rejected) + [
        Rejection(track_id=u.id, reason=f"nicht lesbar: {u.error}") for u in unreadable
    ]

    if len(answer.accepted) < MIN_TRACKS:
        raise NotEnoughTracks(
            f"Nach dem Aussortieren bleiben {len(answer.accepted)} Tracks, "
            f"mindestens {MIN_TRACKS} werden gebraucht. Generiere mehr Material "
            f"bei Suno und lege es in watch ab."
        )

    shortened = len(answer.accepted) < needed
    note = answer.note
    if shortened:
        note = (
            f"{note} Das Set wird gekuerzt: {len(answer.accepted)} statt "
            f"{needed} Tracks."
        )

    return Shortlist(
        accepted=answer.accepted,
        rejected=rejected,
        shortened=shortened,
        note=note,
    )
```

- [ ] **Step 4: Testlauf, muss durchlaufen**

Run: `./.venv/Scripts/python.exe -m pytest tests/agents/test_curator.py -v`
Expected: 5 passed

- [ ] **Step 5: Gesamtsuite und Commit**

```bash
./.venv/Scripts/python.exe -m pytest -q
```

```bash
git add djset/agents/curator.py tests/agents/test_curator.py && git commit -m "feat(curator): Material aussortieren mit begruendeten Ablehnungen"
```

---

### Task 6: Setlist-Architect

Stufe 5. Legt die Reihenfolge fest. Drei harte Regeln aus dem Spec, alle im Code geprüft und bei Verstoß mit konkreter Beanstandung neu gefragt.

**Files:**
- Modify: `djset/contracts.py` (`Setlist`)
- Create: `djset/agents/architect.py`
- Test: `tests/agents/test_architect.py`

**Interfaces:**
- Consumes: `djset.camelot.is_compatible`, `djset.agents.base.run_agent`, `djset.contracts.Shortlist`, `djset.contracts.TrackAnalysis`
- Produces: `djset.contracts.Setlist`, `arrange(client, shortlist, analyses, profile, budget, complaints=()) -> Setlist`, `SIMILAR_BPM: float`, `SIMILAR_ENERGY: float`

- [ ] **Step 1: Den Vertrag ergänzen**

```python
# djset/contracts.py
class Setlist(BaseModel):
    """Die Reihenfolge, in der die Tracks laufen.

    Eigenes Artefakt, obwohl die Spec es nicht auflistet: der Critic schickt
    Beanstandungen an diese Stufe zurueck, und Erfolgskriterium 7 verlangt, dass
    jede Beanstandung in den Artefakten nachvollziehbar ist. Ohne die Setlist auf
    der Platte waere nicht zu sehen, was der Critic eigentlich bemaengelt hat.
    """

    order: list[str] = Field(min_length=MIN_TRACKS)
    note: str

    @model_validator(mode="after")
    def _no_track_twice(self) -> "Setlist":
        if len(set(self.order)) != len(self.order):
            raise ValueError("jede track_id darf nur einmal in der Setlist vorkommen")
        return self
```

- [ ] **Step 2: Den fallenden Test schreiben**

```python
# tests/agents/test_architect.py
import json

from djset.agents.architect import arrange
from djset.agents.base import Budget
from djset.camelot import is_compatible
from djset.contracts import Profile, Shortlist
from tests.fixtures.analyses import make_analysis, many
from tests.fixtures.fake_client import FakeClient


def _budget() -> Budget:
    return Budget(max_tokens=50_000, max_seconds=60.0)


def _answer(order: list[str]) -> str:
    return json.dumps({"order": order, "note": "Fixture-Reihenfolge."})


def _shortlist(ids: list[str]) -> Shortlist:
    return Shortlist(accepted=ids, rejected=[], shortened=False, note="")


def test_the_architect_returns_the_order_it_chose(profile: Profile) -> None:
    tracks = many(6)
    ids = [t.id for t in tracks]
    client = FakeClient([_answer(ids)])

    setlist = arrange(client, _shortlist(ids), tracks, profile, _budget())

    assert setlist.order == ids


def test_a_harmonic_break_is_re_asked_with_the_specific_pair(
    profile: Profile,
) -> None:
    tracks = [
        make_analysis("a", camelot="8A", energy=0.3),
        make_analysis("b", camelot="2B", energy=0.5),
        make_analysis("c", camelot="8B", energy=0.7),
    ]
    ids = ["a", "b", "c"]
    assert not is_compatible("8A", "2B", "strict")
    client = FakeClient([_answer(ids), _answer(["a", "c", "b"])])

    setlist = arrange(client, _shortlist(ids), tracks, profile, _budget())

    assert client.calls == 2
    assert "8A" in client.prompts[1] and "2B" in client.prompts[1]
    assert setlist.order == ["a", "c", "b"]


def test_two_too_similar_tracks_may_not_touch(profile: Profile) -> None:
    """Gleiche Tonart, fast gleiches Tempo, fast gleiche Energie: bequem, aber Stillstand."""
    tracks = [
        make_analysis("a", camelot="8A", bpm=174.0, energy=0.50),
        make_analysis("b", camelot="8A", bpm=174.3, energy=0.52),
        make_analysis("c", camelot="8B", bpm=171.0, energy=0.80),
    ]
    client = FakeClient([_answer(["a", "b", "c"]), _answer(["a", "c", "b"])])

    setlist = arrange(client, _shortlist(["a", "b", "c"]), tracks, profile, _budget())

    assert client.calls == 2
    assert setlist.order == ["a", "c", "b"]


def test_an_id_outside_the_shortlist_is_re_asked(profile: Profile) -> None:
    tracks = many(4)
    ids = [t.id for t in tracks]
    client = FakeClient([_answer(ids[:3] + ["fremd"]), _answer(ids)])

    setlist = arrange(client, _shortlist(ids), tracks, profile, _budget())

    assert client.calls == 2
    assert "fremd" in client.prompts[1]
    assert setlist.order == ids


def test_critic_complaints_reach_the_prompt(profile: Profile) -> None:
    tracks = many(5)
    ids = [t.id for t in tracks]
    client = FakeClient([_answer(ids)])

    arrange(
        client,
        _shortlist(ids),
        tracks,
        profile,
        _budget(),
        complaints=("Die Energiekurve faellt in der Mitte ab.",),
    )

    assert "Energiekurve faellt in der Mitte ab" in client.prompts[0]
```

- [ ] **Step 3: Testlauf, muss fehlschlagen**

Run: `./.venv/Scripts/python.exe -m pytest tests/agents/test_architect.py -v`
Expected: FAIL mit `ModuleNotFoundError` für `djset.agents.architect`

- [ ] **Step 4: Implementieren**

```python
# djset/agents/architect.py
"""Stufe 5: legt die Reihenfolge der Tracks fest.

Drei harte Regeln aus dem Spec, alle im Code geprueft: die Reihenfolge benutzt
nur Tracks aus der Shortlist, je zwei aufeinanderfolgende passen harmonisch
zusammen, und keine zwei zu aehnlichen Tracks stehen nebeneinander. Was das
Modell liefert, wird gegen diese Regeln gehalten und bei Verstoss mit der
konkreten Beanstandung neu gefragt - ein blosses "nochmal" verschenkt den Zug.

Der Geschmack bleibt beim Modell: ob die Reihenfolge schoen ist, prueft hier
niemand. Das ist kein Testfall.
"""

from pydantic import BaseModel, Field

from djset.agents.base import AgentClient, Budget, run_agent
from djset.camelot import is_compatible
from djset.contracts import Profile, Setlist, Shortlist, TrackAnalysis

MAX_ROUNDS = 3

# Zu aehnlich im Sinne des Spec: gleiche Camelot-Tonart UND BPM-Differenz unter
# 1 UND Energiedifferenz unter 0,1. Alle drei zusammen ergeben einen bequemen
# Uebergang und Stillstand im Set.
SIMILAR_BPM = 1.0
SIMILAR_ENERGY = 0.1


class ArchitectAnswer(BaseModel):
    order: list[str] = Field(min_length=1)
    note: str = Field(min_length=1)


PROMPT = """Du baust die Reihenfolge eines DJ-Sets.

Das Set: {genre}, harmonische Strenge {strictness}. Der Energiebogen laeuft ueber
diese Stuetzpunkte aus Position im Set und Zielenergie: {arc}.

Diese Tracks stehen zur Verfuegung, benutze genau sie alle:

{table}

Regeln, die eingehalten werden muessen:
1. Setze die Energiekurve des Bogens um: der erste Track liegt nahe der
   Startenergie, die Mitte nahe dem Hochpunkt, das Ende nahe der Schlussenergie.
2. Je zwei aufeinanderfolgende Tracks muessen harmonisch zusammenpassen. Bei
   Strenge strict heisst das: gleiche Camelot-Zahl, oder eine Position weiter
   auf dem Rad. Bei loose sind zwei Positionen erlaubt.
3. Keine zwei zu aehnlichen Tracks nebeneinander. Zu aehnlich heisst: gleiche
   Tonart UND weniger als {similar_bpm} BPM Unterschied UND weniger als
   {similar_energy} Energieunterschied. Alle drei zusammen ergeben einen
   bequemen Uebergang und Stillstand im Set.

{complaints}Antworte ausschliesslich mit dem geforderten JSON."""


def _table(analyses: list[TrackAnalysis], accepted: set[str]) -> str:
    return "\n".join(
        f"- {a.id}: {a.bpm.value:.1f} BPM, {a.key.camelot}, "
        f"Energie {a.verdict.energy_level:.2f}, {a.duration_s:.0f} s"
        for a in analyses
        if a.id in accepted
    )


def _check(
    order: list[str],
    accepted: set[str],
    by_id: dict[str, TrackAnalysis],
    profile: Profile,
) -> str | None:
    """Haelt die Reihenfolge gegen die drei harten Regeln."""
    used = set(order)
    unknown = used - accepted
    if unknown:
        return f"Diese IDs stehen nicht in der Shortlist: {', '.join(sorted(unknown))}."
    missing = accepted - used
    if missing:
        return f"Diese Tracks fehlen in der Reihenfolge: {', '.join(sorted(missing))}."
    if len(used) != len(order):
        return "Eine ID kommt mehrfach vor."

    for first_id, second_id in zip(order, order[1:]):
        first, second = by_id[first_id], by_id[second_id]
        if not is_compatible(
            first.key.camelot, second.key.camelot, profile.harmonic_strictness
        ):
            return (
                f"{first_id} ({first.key.camelot}) und {second_id} "
                f"({second.key.camelot}) passen harmonisch nicht zusammen."
            )
        same_key = first.key.camelot == second.key.camelot
        close_bpm = abs(first.bpm.value - second.bpm.value) < SIMILAR_BPM
        close_energy = (
            abs(first.verdict.energy_level - second.verdict.energy_level)
            < SIMILAR_ENERGY
        )
        if same_key and close_bpm and close_energy:
            return (
                f"{first_id} und {second_id} sind zu aehnlich: gleiche Tonart, "
                f"fast gleiches Tempo, fast gleiche Energie."
            )
    return None


def arrange(
    client: AgentClient,
    shortlist: Shortlist,
    analyses: list[TrackAnalysis],
    profile: Profile,
    budget: Budget,
    complaints: tuple[str, ...] = (),
) -> Setlist:
    """Legt die Reihenfolge fest und haelt sie gegen die harten Regeln."""
    accepted = set(shortlist.accepted)
    by_id = {a.id: a for a in analyses if a.id in accepted}
    arc = ", ".join(f"({p.position:.2f}, {p.energy:.2f})" for p in profile.energy_arc)

    complaint_block = ""
    if complaints:
        listed = "\n".join(f"- {c}" for c in complaints)
        complaint_block = (
            f"Ein frueherer Entwurf wurde beanstandet. Beheb das:\n{listed}\n\n"
        )

    prompt = PROMPT.format(
        genre=profile.genre,
        strictness=profile.harmonic_strictness,
        arc=arc,
        table=_table(analyses, accepted),
        similar_bpm=SIMILAR_BPM,
        similar_energy=SIMILAR_ENERGY,
        complaints=complaint_block,
    )

    current = prompt
    answer = run_agent(client, current, ArchitectAnswer, budget, retries=1)
    for _round in range(MAX_ROUNDS - 1):
        problem = _check(answer.order, accepted, by_id, profile)
        if problem is None:
            break
        current = f"{prompt}\n\nDein letzter Versuch war unbrauchbar: {problem}"
        answer = run_agent(client, current, ArchitectAnswer, budget, retries=1)

    return Setlist(order=answer.order, note=answer.note)
```

- [ ] **Step 5: Testlauf, muss durchlaufen**

Run: `./.venv/Scripts/python.exe -m pytest tests/agents/test_architect.py -v`
Expected: 5 passed

- [ ] **Step 6: Gesamtsuite und Commit**

```bash
./.venv/Scripts/python.exe -m pytest -q
```

```bash
git add djset/contracts.py djset/agents/architect.py tests/agents/test_architect.py && git commit -m "feat(architect): Reihenfolge nach Energiebogen und harmonischer Nachbarschaft"
```

---

### Task 7: Transition-Designer

Stufe 6. Das Modell wählt pro Übergang nur Technik und Taktzahl; Cue-Punkte, Stretch-Verhältnisse, Timeline-Positionen und EQ-Kurven rechnet Code. Damit ist der aufwendigste Teil der Stufe deterministisch.

**Files:**
- Create: `djset/agents/designer.py`
- Test: `tests/agents/test_designer.py`

**Interfaces:**
- Consumes: `djset.agents.base.run_agent`, `djset.contracts.Setlist`, `djset.contracts.TrackAnalysis`, `djset.contracts.MixPlan`, `djset.contracts.MixEntry`, `djset.contracts.Transition`, `djset.contracts.EqCurve`, `djset.contracts.EqPoint`, `djset.dsp.analyze.BEATS_PER_BAR`
- Produces: `design(client, setlist, analyses, profile, budget) -> MixPlan`, `build_mixplan(setlist, analyses, choices, profile) -> MixPlan`, `TransitionChoice`, `snap_to_downbeat(time_s, grid) -> float`, `MIN_RATIO: float`, `MAX_RATIO: float`

**Zeitrechnung — das Modell dieser Stufe:**

- `timestretch_ratio` heißt Ziel-Tempo geteilt durch Quell-Tempo. Ausgabedauer eines Abschnitts ist also Quelldauer geteilt durch das Verhältnis.
- Jeder Track wird auf `profile.bpm.target` gezogen. Liegt das nötige Verhältnis außerhalb 0.94 bis 1.06, bleibt er ungestreckt und der Übergang **in ihn hinein** wird zum `cut` — genau der Ausweg, den Spec Abschnitt 7 nennt.
- Der erste Track startet am ersten Downbeat, jeder weitere so früh, dass die letzten `bars` Takte seines Intros unter dem Outro des Vorgängers laufen.
- `start_at_s` eines Übergangs ist die Position in der Ausgabe-Timeline, an der der Vorgänger sein Outro erreicht. Der Nachfolger beginnt genau dort.

- [ ] **Step 1: Den fallenden Test schreiben**

```python
# tests/agents/test_designer.py
import json

from djset.agents.base import Budget
from djset.agents.designer import MAX_RATIO, MIN_RATIO, design, snap_to_downbeat
from djset.contracts import BeatGrid, MixPlan, Profile, Setlist
from tests.fixtures.analyses import make_analysis, many
from tests.fixtures.fake_client import FakeClient


def _budget() -> Budget:
    return Budget(max_tokens=50_000, max_seconds=60.0)


def _choices(count: int, technique: str = "blend", bars: int = 16) -> str:
    return json.dumps(
        {"choices": [{"technique": technique, "bars": bars} for _ in range(count)]}
    )


def test_snap_to_downbeat_lands_on_the_grid() -> None:
    grid = BeatGrid(first_downbeat_s=0.5, bar_length_s=2.0, confidence=1.0)

    assert snap_to_downbeat(0.1, grid) == 0.5
    assert snap_to_downbeat(4.4, grid) == 4.5
    assert snap_to_downbeat(5.6, grid) == 6.5


def test_the_plan_has_one_transition_between_each_pair(profile: Profile) -> None:
    tracks = many(4)
    setlist = Setlist(order=[t.id for t in tracks], note="")
    client = FakeClient([_choices(3)])

    plan = design(client, setlist, tracks, profile, _budget())

    assert isinstance(plan, MixPlan)
    assert len(plan.entries) == 4
    assert len(plan.transitions) == 3
    assert [e.track_id for e in plan.entries] == setlist.order


def test_every_cue_point_sits_on_a_downbeat(profile: Profile) -> None:
    tracks = many(4)
    setlist = Setlist(order=[t.id for t in tracks], note="")
    plan = design(FakeClient([_choices(3)]), setlist, tracks, profile, _budget())
    by_id = {t.id: t for t in tracks}

    for entry in plan.entries:
        grid = by_id[entry.track_id].beatgrid
        offset = entry.play_from_s - grid.first_downbeat_s
        bars = offset / grid.bar_length_s
        assert abs(bars - round(bars)) < 1e-6


def test_every_stretch_stays_inside_the_limit(profile: Profile) -> None:
    tracks = many(5)
    setlist = Setlist(order=[t.id for t in tracks], note="")

    plan = design(FakeClient([_choices(4)]), setlist, tracks, profile, _budget())

    for entry in plan.entries:
        assert MIN_RATIO <= entry.timestretch_ratio <= MAX_RATIO


def test_an_unstretchable_track_gets_a_cut_and_no_stretch(profile: Profile) -> None:
    """Spec: ist der Stretch zu gross, loest der Designer den Uebergang anders."""
    tracks = [
        make_analysis("a", bpm=174.0, camelot="8A", energy=0.3),
        make_analysis("b", bpm=140.0, camelot="8B", energy=0.6),
        make_analysis("c", bpm=174.0, camelot="9B", energy=0.8),
    ]
    setlist = Setlist(order=["a", "b", "c"], note="")

    plan = design(FakeClient([_choices(2)]), setlist, tracks, profile, _budget())

    by_id = {e.track_id: e for e in plan.entries}
    assert by_id["b"].timestretch_ratio == 1.0
    assert plan.transitions[0].technique == "cut"


def test_the_timeline_runs_forward_without_gaps(profile: Profile) -> None:
    tracks = many(4)
    setlist = Setlist(order=[t.id for t in tracks], note="")

    plan = design(FakeClient([_choices(3)]), setlist, tracks, profile, _budget())

    starts = [t.start_at_s for t in plan.transitions]
    assert starts == sorted(starts)
    assert starts[0] > 0.0


def test_a_technique_outside_the_profile_is_re_asked(profile: Profile) -> None:
    tracks = many(4)
    setlist = Setlist(order=[t.id for t in tracks], note="")
    bad = json.dumps(
        {"choices": [{"technique": "double_drop", "bars": 16} for _ in range(3)]}
    )
    client = FakeClient([bad, _choices(3)])

    design(client, setlist, tracks, profile, _budget())

    assert client.calls == 2
    assert "double_drop" in client.prompts[1]
```

- [ ] **Step 2: Testlauf, muss fehlschlagen**

Run: `./.venv/Scripts/python.exe -m pytest tests/agents/test_designer.py -v`
Expected: FAIL mit `ModuleNotFoundError` für `djset.agents.designer`

- [ ] **Step 3: Implementieren**

```python
# djset/agents/designer.py
"""Stufe 6: entwirft jeden Uebergang und rechnet daraus den Mixplan.

Aufgabenteilung: das Modell waehlt pro Uebergang Technik und Taktzahl - das ist
Geschmack und Genrekenntnis. Alles andere rechnet Code: Cue-Punkte auf
Downbeats, Stretch-Verhaeltnisse gegen das Ziel-Tempo, Positionen in der
Ausgabe-Timeline, EQ-Kurven aus Vorlagen je Technik. Damit ist der aufwendigste
Teil der Stufe deterministisch und ohne Modell testbar - und der Mixplan
erfuellt die Determinismus-Grenze der Spec, ohne dass ein Modell Zahlen erfindet.

Zeitrechnung: timestretch_ratio ist Ziel-Tempo geteilt durch Quell-Tempo, die
Ausgabedauer eines Abschnitts also Quelldauer geteilt durch das Verhaeltnis.
"""

from pydantic import BaseModel, Field

from djset.agents.base import AgentClient, Budget, run_agent
from djset.contracts import (
    EqCurve,
    EqPoint,
    MixEntry,
    MixPlan,
    Profile,
    Setlist,
    Technique,
    TrackAnalysis,
    Transition,
)
from djset.dsp.analyze import BEATS_PER_BAR

MAX_ROUNDS = 3

# Die Stretch-Grenze aus dem Spec, hier als Rechengroesse. Der Vertrag
# MixEntry.timestretch_ratio erzwingt dieselben Werte.
MIN_RATIO = 0.94
MAX_RATIO = 1.06


class TransitionChoice(BaseModel):
    technique: Technique
    bars: int = Field(ge=1)


class DesignerAnswer(BaseModel):
    choices: list[TransitionChoice] = Field(min_length=1)


PROMPT = """Du entwirfst die Uebergaenge eines DJ-Sets.

Das Set: {genre}, Ziel-BPM {bpm_target}. Erlaubte Techniken: {techniques}.
Erlaubte Uebergangslaenge: {bars_min} bis {bars_max} Takte.

Die Reihenfolge steht fest. Diese Uebergaenge sind zu entwerfen:

{table}

Waehle fuer jeden Uebergang eine Technik und eine Taktzahl. Gib genau
{count} Eintraege in derselben Reihenfolge zurueck.

Woran du dich orientierst:
- Ein grosser Energiesprung vertraegt einen kuerzeren, haerteren Uebergang.
- Zwei ruhige Tracks nebeneinander wollen einen langen, weichen Blend.
- Ein eq_swap taugt, wenn beide Tracks eine tragende Bassline haben.
- Ein filter_sweep ueberbrueckt einen Wechsel der Klangfarbe.
- Steht bei einem Uebergang "Stretch nicht moeglich", muss die Technik cut
  sein: die Tempi liegen zu weit auseinander, um sie anzugleichen.

Antworte ausschliesslich mit dem geforderten JSON."""


def snap_to_downbeat(time_s: float, grid) -> float:
    """Die naechstgelegene Taktgrenze zu einem Zeitpunkt."""
    if time_s <= grid.first_downbeat_s:
        return grid.first_downbeat_s
    bars = round((time_s - grid.first_downbeat_s) / grid.bar_length_s)
    return grid.first_downbeat_s + bars * grid.bar_length_s


def _ratio(track: TrackAnalysis, target_bpm: float) -> float:
    """Das Verhaeltnis, mit dem der Track auf das Ziel-Tempo gezogen wird.

    Liegt es ausserhalb der Grenze, bleibt der Track ungestreckt - der Uebergang
    in ihn hinein wird dann zum cut.
    """
    ratio = target_bpm / track.bpm.value
    if MIN_RATIO <= ratio <= MAX_RATIO:
        return ratio
    return 1.0


def _eq_curve(technique: Technique, outgoing: bool) -> EqCurve:
    """EQ-Verlauf je Technik, getrennt fuer aus- und eingehenden Track."""
    full, silent = 0.0, -60.0
    start, end = (full, silent) if outgoing else (silent, full)

    def ramp(mid_gain: float) -> list[EqPoint]:
        return [
            EqPoint(position=0.0, gain_db=start),
            EqPoint(position=0.5, gain_db=mid_gain),
            EqPoint(position=1.0, gain_db=end),
        ]

    if technique == "cut":
        # Harter Schnitt: die Umschaltung passiert in der letzten Zehntel.
        hard = [
            EqPoint(position=0.0, gain_db=start),
            EqPoint(position=0.9, gain_db=start),
            EqPoint(position=1.0, gain_db=end),
        ]
        return EqCurve(low=hard, mid=hard, high=hard)

    if technique == "eq_swap":
        # Die Baesse tauschen frueh und schnell, damit sie sich nie stapeln.
        fast = [
            EqPoint(position=0.0, gain_db=start),
            EqPoint(position=0.35, gain_db=end),
            EqPoint(position=1.0, gain_db=end),
        ]
        return EqCurve(low=fast, mid=ramp(-6.0), high=ramp(-3.0))

    if technique == "filter_sweep":
        # Die Hoehen fuehren die Bewegung, der Bass folgt spaet.
        late = [
            EqPoint(position=0.0, gain_db=start),
            EqPoint(position=0.65, gain_db=start),
            EqPoint(position=1.0, gain_db=end),
        ]
        return EqCurve(low=late, mid=ramp(-6.0), high=ramp(-2.0))

    # blend und double_drop: gleichmaessige Blende ueber alle Baender.
    even = ramp(-6.0)
    return EqCurve(low=even, mid=even, high=even)


def build_mixplan(
    setlist: Setlist,
    analyses: list[TrackAnalysis],
    choices: list[TransitionChoice],
    profile: Profile,
) -> MixPlan:
    """Rechnet aus Reihenfolge und Technikwahl den vollstaendigen Mixplan."""
    by_id = {a.id: a for a in analyses}
    order = setlist.order
    target_bpm = profile.bpm.target

    ratios = {tid: _ratio(by_id[tid], target_bpm) for tid in order}

    # Ein Track, der nicht angeglichen werden kann, bekommt einen harten
    # Schnitt in sich hinein - Blenden wuerde nach Takt zwei auseinanderlaufen.
    effective: list[TransitionChoice] = []
    for index, choice in enumerate(choices):
        incoming = order[index + 1]
        if ratios[incoming] == 1.0 and by_id[incoming].bpm.value != target_bpm:
            effective.append(TransitionChoice(technique="cut", bars=choice.bars))
        else:
            effective.append(choice)

    entries: list[MixEntry] = []
    for index, track_id in enumerate(order):
        track = by_id[track_id]
        grid = track.beatgrid
        first = index == 0
        last = index == len(order) - 1

        if first:
            play_from = grid.first_downbeat_s
        else:
            bars_in = effective[index - 1].bars
            raw = track.mix_regions.intro_end_s - bars_in * grid.bar_length_s
            play_from = snap_to_downbeat(max(0.0, raw), grid)

        if last:
            play_to = track.duration_s
        else:
            bars_out = effective[index].bars
            raw = track.mix_regions.outro_start_s + bars_out * grid.bar_length_s
            play_to = min(track.duration_s, snap_to_downbeat(raw, grid))

        entries.append(
            MixEntry(
                track_id=track_id,
                play_from_s=play_from,
                play_to_s=play_to,
                timestretch_ratio=ratios[track_id],
                gain_db=0.0,
            )
        )

    transitions: list[Transition] = []
    out_start = 0.0
    for index, choice in enumerate(effective):
        entry = entries[index]
        track = by_id[order[index]]
        # Position in der Ausgabe, an der der Vorgaenger sein Outro erreicht.
        into_outro = track.mix_regions.outro_start_s - entry.play_from_s
        start_at = out_start + into_outro / entry.timestretch_ratio
        transitions.append(
            Transition(
                from_track=order[index],
                to_track=order[index + 1],
                start_at_s=start_at,
                bars=choice.bars,
                technique=choice.technique,
                eq_out=_eq_curve(choice.technique, outgoing=True),
                eq_in=_eq_curve(choice.technique, outgoing=False),
            )
        )
        out_start = start_at

    last_entry = entries[-1]
    total = out_start + (
        last_entry.play_to_s - last_entry.play_from_s
    ) / last_entry.timestretch_ratio

    return MixPlan(target_length_s=total, entries=entries, transitions=transitions)


def _table(setlist: Setlist, by_id: dict[str, TrackAnalysis], profile: Profile) -> str:
    lines = []
    for index in range(len(setlist.order) - 1):
        a = by_id[setlist.order[index]]
        b = by_id[setlist.order[index + 1]]
        stretchable = MIN_RATIO <= profile.bpm.target / b.bpm.value <= MAX_RATIO
        note = "" if stretchable else "  [Stretch nicht moeglich]"
        lines.append(
            f"{index + 1}. {a.id} ({a.bpm.value:.1f} BPM, {a.key.camelot}, "
            f"Energie {a.verdict.energy_level:.2f}) nach {b.id} "
            f"({b.bpm.value:.1f} BPM, {b.key.camelot}, "
            f"Energie {b.verdict.energy_level:.2f}){note}"
        )
    return "\n".join(lines)


def _check(answer: DesignerAnswer, wanted: int, profile: Profile) -> str | None:
    if len(answer.choices) != wanted:
        return f"Du hast {len(answer.choices)} Uebergaenge geliefert, gebraucht werden {wanted}."
    allowed = set(profile.transition.techniques)
    for index, choice in enumerate(answer.choices):
        if choice.technique not in allowed:
            return (
                f"Uebergang {index + 1} benutzt {choice.technique}, erlaubt sind nur: "
                f"{', '.join(sorted(allowed))}."
            )
        if not (
            profile.transition.bars_min <= choice.bars <= profile.transition.bars_max
        ):
            return (
                f"Uebergang {index + 1} hat {choice.bars} Takte, erlaubt sind "
                f"{profile.transition.bars_min} bis {profile.transition.bars_max}."
            )
    return None


def design(
    client: AgentClient,
    setlist: Setlist,
    analyses: list[TrackAnalysis],
    profile: Profile,
    budget: Budget,
) -> MixPlan:
    """Waehlt die Uebergaenge und rechnet daraus den Mixplan."""
    by_id = {a.id: a for a in analyses}
    wanted = len(setlist.order) - 1
    prompt = PROMPT.format(
        genre=profile.genre,
        bpm_target=profile.bpm.target,
        techniques=", ".join(profile.transition.techniques),
        bars_min=profile.transition.bars_min,
        bars_max=profile.transition.bars_max,
        table=_table(setlist, by_id, profile),
        count=wanted,
    )

    current = prompt
    answer = run_agent(client, current, DesignerAnswer, budget, retries=1)
    for _round in range(MAX_ROUNDS - 1):
        problem = _check(answer, wanted, profile)
        if problem is None:
            break
        current = f"{prompt}\n\nDein letzter Versuch war unbrauchbar: {problem}"
        answer = run_agent(client, current, DesignerAnswer, budget, retries=1)

    return build_mixplan(setlist, analyses, answer.choices, profile)
```

- [ ] **Step 4: Testlauf, muss durchlaufen**

Run: `./.venv/Scripts/python.exe -m pytest tests/agents/test_designer.py -v`
Expected: 7 passed

- [ ] **Step 5: Gesamtsuite und Commit**

```bash
./.venv/Scripts/python.exe -m pytest -q
```

```bash
git add djset/agents/designer.py tests/agents/test_designer.py && git commit -m "feat(designer): Uebergaenge waehlen, Mixplan deterministisch rechnen"
```

---

### Task 8: Critic

Stufe 7. Das Gate vor dem Rendern — deterministisch, ohne Modell. Vier Prüfungen aus dem Spec, jede mit konkreter Beanstandung für die Rückkopplung an Stufe 5.

**Files:**
- Create: `djset/critic.py`
- Test: `tests/test_critic.py`

**Interfaces:**
- Consumes: `djset.contracts.MixPlan`, `djset.contracts.TrackAnalysis`, `djset.contracts.Profile`
- Produces: `Critique` (Pydantic, Felder `ok: bool`, `complaints: list[str]`), `critique(plan, analyses, profile) -> Critique`, `LENGTH_TOLERANCE: float`, `ARC_TOLERANCE: float`

- [ ] **Step 1: Den fallenden Test schreiben**

```python
# tests/test_critic.py
from djset.agents.designer import TransitionChoice, build_mixplan
from djset.contracts import MixEntry, Profile, Setlist
from djset.critic import critique
from tests.fixtures.analyses import make_analysis, many


def _plan(tracks, profile, bars: int = 16):
    setlist = Setlist(order=[t.id for t in tracks], note="")
    choices = [
        TransitionChoice(technique="blend", bars=bars) for _ in range(len(tracks) - 1)
    ]
    return build_mixplan(setlist, tracks, choices, profile)


def test_a_sound_plan_passes(profile: Profile) -> None:
    tracks = many(13)
    plan = _plan(tracks, profile)

    verdict = critique(plan, tracks, profile)

    assert verdict.ok is True
    assert verdict.complaints == []


def test_a_plan_that_is_far_too_short_is_complained_about(profile: Profile) -> None:
    tracks = many(4)
    plan = _plan(tracks, profile)

    verdict = critique(plan, tracks, profile)

    assert verdict.ok is False
    assert any("Laenge" in c for c in verdict.complaints)


def test_a_cue_point_off_the_grid_is_caught(profile: Profile) -> None:
    tracks = many(13)
    plan = _plan(tracks, profile)
    broken = plan.model_dump()
    broken["entries"][2]["play_from_s"] += 0.37

    verdict = critique(
        type(plan).model_validate(broken), tracks, profile
    )

    assert verdict.ok is False
    assert any("Downbeat" in c for c in verdict.complaints)


def test_a_falling_energy_arc_is_caught(profile: Profile) -> None:
    """Der Bogen steigt zur Mitte; eine absteigende Reihenfolge verletzt ihn."""
    tracks = [
        make_analysis(f"t{i}", camelot=k, energy=e, bpm=174.0)
        for i, (k, e) in enumerate(
            zip(["8A", "8B", "9B", "9A", "10A"], [0.9, 0.8, 0.6, 0.4, 0.2])
        )
    ]
    plan = _plan(tracks, profile)

    verdict = critique(plan, tracks, profile)

    assert verdict.ok is False
    assert any("Energie" in c for c in verdict.complaints)


def test_the_critique_names_the_offending_track(profile: Profile) -> None:
    tracks = many(13)
    plan = _plan(tracks, profile)
    broken = plan.model_dump()
    broken["entries"][5]["play_from_s"] += 0.41

    verdict = critique(type(plan).model_validate(broken), tracks, profile)

    assert any(plan.entries[5].track_id in c for c in verdict.complaints)
```

- [ ] **Step 2: Testlauf, muss fehlschlagen**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_critic.py -v`
Expected: FAIL mit `ModuleNotFoundError` für `djset.critic`

- [ ] **Step 3: Implementieren**

```python
# djset/critic.py
"""Stufe 7: das Gate vor dem Rendern.

Bewusst kein Agent. Die Spec listet vier Pruefungen - haelt die Energiekurve,
liegen alle Stretches in der Toleranz, sitzen die Cue-Punkte auf Downbeats,
stimmt die Gesamtlaenge - und alle vier sind Arithmetik ueber den Mixplan. Ein
Modell dafuer zu fragen waere teurer, langsamer und weniger verlaesslich als
eine Funktion. Die Spec nennt den Critic eine Stufe, keinen Agenten.

Die Beanstandungen sind so formuliert, dass Stufe 5 damit etwas anfangen kann:
sie nennen den Track und das konkrete Problem, nicht nur die Regel.
"""

from pydantic import BaseModel

from djset.agents.designer import MAX_RATIO, MIN_RATIO
from djset.contracts import MixPlan, Profile, TrackAnalysis

# Erfolgskriterium 3 der Spec: Ziellaenge plus/minus 5 Prozent.
LENGTH_TOLERANCE = 0.05

# Wie weit ein Track von der Zielenergie des Bogens abweichen darf. Grosszuegig,
# weil der Bogen eine Absicht beschreibt und kein Messziel - er soll die grobe
# Dramaturgie erzwingen, nicht jeden Track festnageln.
ARC_TOLERANCE = 0.35

# Ein Cue-Punkt gilt als auf dem Downbeat, wenn er weniger als das davon
# entfernt liegt. Ein Zwanzigstel Takt ist unterhalb der Hoerschwelle.
DOWNBEAT_TOLERANCE_BARS = 0.05


class Critique(BaseModel):
    ok: bool
    complaints: list[str]


def _arc_target(profile: Profile, position: float) -> float:
    """Die Zielenergie des Bogens an einer Position, linear interpoliert."""
    points = profile.energy_arc
    if position <= points[0].position:
        return points[0].energy
    for left, right in zip(points, points[1:]):
        if position <= right.position:
            span = right.position - left.position
            if span <= 0:
                return right.energy
            share = (position - left.position) / span
            return left.energy + share * (right.energy - left.energy)
    return points[-1].energy


def critique(
    plan: MixPlan, analyses: list[TrackAnalysis], profile: Profile
) -> Critique:
    """Prueft den Mixplan gegen die vier harten Kriterien der Spec."""
    by_id = {a.id: a for a in analyses}
    complaints: list[str] = []

    # 1. Gesamtlaenge
    target_s = profile.length_minutes * 60.0
    low = target_s * (1.0 - LENGTH_TOLERANCE)
    high = target_s * (1.0 + LENGTH_TOLERANCE)
    if not low <= plan.target_length_s <= high:
        complaints.append(
            f"Laenge verfehlt: {plan.target_length_s / 60:.1f} Minuten statt "
            f"{profile.length_minutes:.0f} plus/minus "
            f"{LENGTH_TOLERANCE * 100:.0f} Prozent."
        )

    # 2. Stretch-Grenzen
    for entry in plan.entries:
        if not MIN_RATIO <= entry.timestretch_ratio <= MAX_RATIO:
            complaints.append(
                f"{entry.track_id} braucht einen Stretch von "
                f"{entry.timestretch_ratio:.3f}, erlaubt sind {MIN_RATIO} bis "
                f"{MAX_RATIO}."
            )

    # 3. Cue-Punkte auf Downbeats
    for entry in plan.entries:
        track = by_id.get(entry.track_id)
        if track is None:
            complaints.append(f"{entry.track_id} steht im Plan, aber es gibt keine Analyse dazu.")
            continue
        grid = track.beatgrid
        offset = (entry.play_from_s - grid.first_downbeat_s) / grid.bar_length_s
        if abs(offset - round(offset)) > DOWNBEAT_TOLERANCE_BARS:
            complaints.append(
                f"Der Einstieg von {entry.track_id} bei {entry.play_from_s:.2f} s "
                f"sitzt nicht auf einem Downbeat."
            )

    # 4. Energiekurve
    count = len(plan.entries)
    for index, entry in enumerate(plan.entries):
        track = by_id.get(entry.track_id)
        if track is None:
            continue
        position = index / max(1, count - 1)
        target = _arc_target(profile, position)
        actual = track.verdict.energy_level
        if abs(actual - target) > ARC_TOLERANCE:
            complaints.append(
                f"Energie verfehlt an Position {index + 1}: {entry.track_id} liegt "
                f"bei {actual:.2f}, der Bogen verlangt dort etwa {target:.2f}."
            )

    return Critique(ok=not complaints, complaints=complaints)
```

- [ ] **Step 4: Testlauf, muss durchlaufen**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_critic.py -v`
Expected: 5 passed

- [ ] **Step 5: Gesamtsuite und Commit**

```bash
./.venv/Scripts/python.exe -m pytest -q
```

```bash
git add djset/critic.py tests/test_critic.py && git commit -m "feat(critic): deterministisches Gate vor dem Rendern"
```

---

### Task 9: Kommando `build`

Verdrahtet die Stufen 1 bis 7 zu einem Lauf, der ohne Interaktion durchläuft. Endet mit `mixplan.json`; das Rendern kommt in Plan 3.

**Files:**
- Modify: `djset/cli.py`
- Test: `tests/test_cli_build.py`

**Interfaces:**
- Consumes: alles aus den Tasks 1 bis 8
- Produces: `cmd_build(base, run_id, client, budget) -> int`, `BUILD_BUDGET_TOKENS: int`, `BUILD_BUDGET_SECONDS: float`, `CRITIC_ROUNDS: int`

- [ ] **Step 1: Den fallenden Test schreiben**

```python
# tests/test_cli_build.py
import json
from datetime import date
from pathlib import Path

from djset.agents.base import Budget
from djset.cli import cmd_build
from djset.contracts import MixPlan, Profile, Shortlist
from djset.run import Run
from tests.fixtures.fake_client import FakeClient
from tests.fixtures.make_audio import click_track


def _budget() -> Budget:
    return Budget(max_tokens=400_000, max_seconds=300.0)


def _prepared(tmp_path: Path, profile: Profile, count: int) -> Run:
    """Ein Lauf mit Profil und genug Material im watch-Ordner."""
    run = Run.create(tmp_path, "liquid dnb", date(2026, 8, 21))
    small = Profile.model_validate(
        profile.model_dump() | {"track_count": {"needed": count, "request": count}}
    )
    run.write_model("profile.json", small)
    watch = tmp_path / "watch"
    watch.mkdir(exist_ok=True)
    for index in range(count):
        click_track(watch / f"t{index}.wav", bpm=174.0, seconds=12.0)
    return run


def test_build_without_a_profile_fails_cleanly(tmp_path: Path, capsys) -> None:
    Run.create(tmp_path, "liquid dnb", date(2026, 8, 21))

    code = cmd_build(tmp_path, None, FakeClient([]), _budget())

    assert code != 0
    assert "profile.json" in capsys.readouterr().err


def test_build_with_too_little_material_stops_before_any_agent(
    tmp_path: Path, profile: Profile, capsys
) -> None:
    run = _prepared(tmp_path, profile, 3)
    for leftover in (tmp_path / "watch").iterdir():
        leftover.unlink()
    client = FakeClient([])

    code = cmd_build(tmp_path, run.run_id, client, _budget())

    assert code != 0
    assert client.calls == 0
    assert "watch" in capsys.readouterr().err


def test_a_full_build_writes_every_artefact(
    tmp_path: Path, profile: Profile
) -> None:
    run = _prepared(tmp_path, profile, 4)
    analyst = json.dumps(
        {"usable": True, "notes": "Passt.", "energy_level": 0.5,
         "remeasure_with_bpm": None}
    )
    curator = json.dumps(
        {"accepted": ["t0", "t1", "t2", "t3"], "rejected": [], "note": "Alles gut."}
    )
    architect = json.dumps({"order": ["t0", "t1", "t2", "t3"], "note": "Aufbau."})
    designer = json.dumps(
        {"choices": [{"technique": "blend", "bars": 16} for _ in range(3)]}
    )
    client = FakeClient([analyst] * 4 + [curator, architect, designer])

    code = cmd_build(tmp_path, run.run_id, client, _budget())

    assert code == 0
    assert run.has("shortlist.json")
    assert run.has("setlist.json")
    assert run.has("mixplan.json")
    plan = run.read_model("mixplan.json", MixPlan)
    assert [e.track_id for e in plan.entries] == ["t0", "t1", "t2", "t3"]
    shortlist = run.read_model("shortlist.json", Shortlist)
    assert shortlist.accepted == ["t0", "t1", "t2", "t3"]
```

Die drei Tests decken die drei Wege ab, die schiefgehen können, bevor Tokens fließen, und den Weg, der durchläuft. Der Critic wird in diesem Aufbau wegen der kurzen Fixture-Tracks Beanstandungen zur Länge haben — der Lauf nimmt dann nach `CRITIC_ROUNDS` den besten Plan und läuft trotzdem durch, was Spec Abschnitt 7 so verlangt.

- [ ] **Step 2: Testlauf, muss fehlschlagen**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_cli_build.py -v`
Expected: FAIL mit `ImportError: cannot import name 'cmd_build'`

- [ ] **Step 3: Implementieren**

In `djset/cli.py` ergänzen:

```python
# Budget fuer einen build-Lauf. Deutlich groesser als bei plan: hier laeuft ein
# Modellaufruf pro Track plus vier Stufen. Gilt pro Aufruf, nicht pro Lauf -
# ein resume startet frisch, sonst koennte ein Lauf, der einmal ans Limit
# gestossen ist, nie mehr fertig werden.
BUILD_BUDGET_TOKENS = 600_000
BUILD_BUDGET_SECONDS = 3600.0

# Spec Abschnitt 7: weist der Critic zweimal zurueck, wird der beste bisherige
# Plan genommen. Weder Endlosschleife noch Abbruch.
CRITIC_ROUNDS = 2


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
    except NotEnoughMaterial as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1

    try:
        analyses, unreadable = analyse_all(client, files, profile, run, budget)
        shortlist = curate(client, analyses, unreadable, profile, budget)
        run.write_model("shortlist.json", shortlist)

        best: MixPlan | None = None
        best_setlist: Setlist | None = None
        fewest = -1
        complaints: tuple[str, ...] = ()

        for _round in range(CRITIC_ROUNDS + 1):
            setlist = arrange(
                client, shortlist, analyses, profile, budget, complaints=complaints
            )
            plan = design(client, setlist, analyses, profile, budget)
            verdict = critique(plan, analyses, profile)
            if best is None or fewest < 0 or len(verdict.complaints) < fewest:
                best, best_setlist, fewest = plan, setlist, len(verdict.complaints)
            if verdict.ok:
                break
            complaints = tuple(verdict.complaints)
    except (NotEnoughTracks, AgentError) as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1

    if best is None or best_setlist is None:
        print("Fehler: es entstand kein Mixplan.", file=sys.stderr)
        return 1

    run.write_model("setlist.json", best_setlist)
    run.write_model("mixplan.json", best)

    print(f"Lauf {run.run_id}")
    print(f"  {len(analyses)} Tracks analysiert, {len(unreadable)} unlesbar")
    print(f"  {len(shortlist.accepted)} im Set, {len(shortlist.rejected)} aussortiert")
    if fewest:
        print(f"  Critic hat {fewest} Beanstandungen offen gelassen:")
        for complaint in critique(best, analyses, profile).complaints:
            print(f"    - {complaint}")
    print(f"  Mixplan: {run.path('mixplan.json')}")
    print("  Das Rendern kommt in einer spaeteren Ausbaustufe.")
    return 0
```

Dazu die Imports am Dateikopf und ein Unterkommando in `main()`:

```python
    build = sub.add_parser("build", help="Set aus dem Material in watch bauen")
    build.add_argument("--run", default=None, help="Lauf-ID, sonst der letzte unfertige")
```

und im Dispatch:

```python
    if args.command == "build":
        from djset.agents.client import ClaudeClient

        return cmd_build(
            Path.cwd(),
            args.run,
            ClaudeClient(),
            Budget(
                max_tokens=BUILD_BUDGET_TOKENS,
                max_seconds=BUILD_BUDGET_SECONDS,
            ),
        )
```

Der Import von `djset.agents.client` bleibt wie bei `plan` innerhalb der Funktion — kein Test darf ihn auslösen.

- [ ] **Step 4: Testlauf, muss durchlaufen**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_cli_build.py -v`
Expected: 3 passed

- [ ] **Step 5: Prüfen, dass der Client weiterhin faul importiert wird**

Run: `./.venv/Scripts/python.exe -c "import sys, djset.cli; assert 'djset.agents.client' not in sys.modules; print('faul')"`
Expected: `faul`

- [ ] **Step 6: Gesamtsuite und Commit**

```bash
./.venv/Scripts/python.exe -m pytest -q
```

```bash
git add djset/cli.py tests/test_cli_build.py && git commit -m "feat(cli): Kommando build bis zum geprueften Mixplan"
```

---

## Was dieser Plan bewusst offen lässt

Diese Punkte stammen aus dem Abschlussreview von Plan 1 und sind hier begründet nicht behoben:

- **`ClaudeClient.complete` hat kein Timeout.** Das Zeitbudget greift zwischen den Aufrufen; ein einzelner hängender Aufruf produziert keine Ausnahme, an der es greifen könnte. Gehört in Plan 3 zusammen mit dem Render-Timeout, weil beides dieselbe Frage ist.
- **`Loudness.true_peak` hat keinen Bodenwert**, anders als `lufs_integrated`. Endlich und JSON-fest, also kein Fehler — nur eine Unsymmetrie.
- **`energy_curve` beginnt seine 4-Takt-Blöcke bei Sample 0**, nicht am ersten Downbeat. Die Blöcke sind also nicht wirklich taktgenau. Für die Mix-Regionen macht das nichts, weil die Rückrechnung dieselbe Annahme benutzt.
- **Kein README.** Dass `dj-set` eine installierte und angemeldete Claude-CLI braucht, steht nirgends. Eigene Aufgabe, kein Plan-Schritt.

## Selbstprüfung dieses Plans

**Spec-Abdeckung Stufen 3 bis 7:** Stufe 3 in Tasks 2 und 3, Stufe 4 in Task 5, Stufe 5 in Task 6, Stufe 6 in Task 7, Stufe 7 in Task 8. Der Vorab-Check aus Abschnitt 7 in Task 1, die Auffächerung und `--resume` in Task 3, die Overshoot-Kürzung und der harte Abbruch unter drei Tracks in Task 5, die Stretch-Grenze mit Ausweg auf `cut` in Task 7, die höchstens zwei Critic-Runden mit Bestplan-Rückfall in Task 9. Die Fixture-JSON-Teststrategie aus Abschnitt 8 in Task 4. Stufen 8 und 9 sowie die Erfolgskriterien 3 bis 6 gehören zu Plan 3 und sind hier ausdrücklich nicht abgedeckt.

**Typkonsistenz:** `analyse_track` liefert `TrackAnalysis | UnreadableTrack`, `analyse_all` trennt beide und gibt sie als Tupel zurück, `curate` nimmt genau dieses Paar entgegen. `arrange` liefert `Setlist`, das `design` als Eingabe nimmt und `MixPlan` zurückgibt, den `critique` prüft. `Verdict.energy_level` entsteht in Task 2 und wird in den Tasks 5, 6 und 8 gelesen.

**Keine Platzhalter:** kein Schritt sagt „implementiere hier", jeder Code-Schritt trägt den Code, den er meint, und jede Prüfung nennt das Kriterium statt „behandle Randfälle".

**Was ein Reviewer dieses Plans zuerst prüfen sollte:** die Zeitrechnung in Task 7. Sie ist der einzige Teil, der nicht offensichtlich richtig ist — `start_at_s` als kumulierte Ausgabeposition, die Ausgabedauer als Quelldauer geteilt durch das Stretch-Verhältnis, und der Übergang, der beim Outro des Vorgängers beginnt und den Nachfolger genau dort einsetzen lässt. Wenn dort etwas falsch ist, merkt es der Critic in Task 8 an der Gesamtlänge, aber erst nachdem alles gebaut wurde.
