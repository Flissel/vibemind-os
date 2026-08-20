# DJ-Set Plan 1: Fundament und Plan-Phase — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein lauffähiges `dj-set plan --genre "<genre>" --length <minuten>`, das ein validiertes Genre-Profil und einen Satz Suno-Prompts erzeugt — auf einem getesteten DSP-Toolbelt, das die spätere Analyse trägt.

**Architecture:** Pydantic-Verträge als gemeinsames Vokabular aller Stufen. Darunter ein reines DSP-Modul ohne Agentenbezug (messen), darüber eine Agent-Basis, die strukturierte Ausgabe erzwingt (beurteilen). Zwei Agenten (Genre-Profiler, Prompt-Writer) und die CLI schließen die Plan-Phase ab. Harte Regeln aus dem Spec — Stretch-Grenze, Overshoot, Mindest-Trackzahl — werden in den Typen erzwungen, nicht in Prosa beschrieben.

**Tech Stack:** Python 3.11+, Pydantic v2, librosa, numpy, soundfile, pyloudnorm, claude-agent-sdk, pytest. ffmpeg als externes Programm.

**Spec:** `docs/superpowers/specs/2026-08-05-suno-dj-set-multiagent-design.md`

## Global Constraints

Diese Vorgaben gelten für jede Task, ohne dass sie dort wiederholt werden.

- **Python 3.11 oder neuer.** Moderne Typsyntax (`list[str]`, `X | None`), keine `typing.List`.
- **Keine `Any`.** Unbekanntes wird als `object` typisiert und eingegrenzt.
- **Timestretch-Grenze ±6 %** — `timestretch_ratio` liegt zwischen 0.94 und 1.06.
- **Mindestens 3 Tracks** in einem Set. Darunter ist es kein Set.
- **Overshoot-Faktor 1,5** — angefordert wird `ceil(needed * 1.5)`.
- **Höchstens 2 Critic-Runden** (relevant ab Plan 2).
- **Harmonische Strenge:** `strict` = gleiche Camelot-Zahl (Dur/Moll-Wechsel) oder ±1 auf dem Rad bei gleichem Buchstaben. `loose` = zusätzlich ±2.
- **Eingabeformate:** `.mp3`, `.wav`, `.flac`. **Ausgabe:** `set.wav`, 44100 Hz, 24 Bit, Stereo.
- **Run-ID-Format:** `<YYYY-MM-DD>-<genre-slug>-<NN>`, z.B. `2026-08-05-liquid-dnb-01`.
- **Timestretching über ffmpeg `atempo`**, nicht über pyrubberband. Abweichung vom Spec: Rubberband braucht unter Windows eine manuell installierte Binary, `atempo` ist bei ±6 % klanglich unauffällig und ffmpeg wird ohnehin gebraucht.
- **Deutsche Fehlermeldungen und Kommentare**, englische Bezeichner im Code.
- **Conventional Commits.** Jede Task endet mit genau einem Commit.
- **Keine Netzwerkzugriffe in Tests.** Agenten werden über einen eingespeisten Fake-Client getestet.

---

## File Structure

Neu angelegt in diesem Plan:

| Datei | Verantwortung |
|---|---|
| `pyproject.toml` | Paketdefinition, Abhängigkeiten, pytest-Konfiguration |
| `djset/contracts.py` | Alle Pydantic-Modelle. Das gemeinsame Vokabular sämtlicher Stufen |
| `djset/camelot.py` | Camelot-Rad: Parsen, Nachbarschaft, Verträglichkeit. Reine Funktionen |
| `djset/dsp/analyze.py` | Messen am Audio: BPM, Beatgrid, Tonart, Energie, Mix-Regionen, Lautheit |
| `djset/run.py` | Run-Ordner: Anlegen, Finden, Artefakte lesen und schreiben |
| `djset/agents/base.py` | Agent-Runner: strukturierte Ausgabe erzwingen, Retry, Budget |
| `djset/agents/profiler.py` | Agent 1 — Genre-Profil ableiten |
| `djset/agents/prompt_writer.py` | Agent 2 — Suno-Prompts schreiben |
| `djset/cli.py` | Einsprungpunkt, Unterkommando `plan` |
| `tests/fixtures/make_audio.py` | Synthetisches Referenzmaterial für die DSP-Tests |
| `tests/fixtures/fake_client.py` | Eingespeister Agent-Client für Tests ohne Netz |

Grenze zwischen den Schichten: `dsp/` misst und urteilt nie, `agents/` urteilt und misst nie.

---

### Task 1: Projektgerüst und Verträge

Alle Modelle entstehen in einer Task, weil sie das gemeinsame Vokabular sind — getrennt angelegt driften die Namen auseinander.

**Files:**
- Create: `pyproject.toml`
- Create: `djset/__init__.py`, `djset/contracts.py`
- Test: `tests/test_contracts.py`

**Interfaces:**
- Consumes: nichts
- Produces: `Profile`, `BpmRange`, `TransitionRules`, `EnergyPoint`, `TrackCount`, `StructureRules`, `Bpm`, `BeatGrid`, `Key`, `MixRegions`, `Loudness`, `Verdict`, `TrackMeasurements`, `TrackAnalysis`, `Rejection`, `Shortlist`, `MixEntry`, `EqPoint`, `EqCurve`, `Transition`, `MixPlan`, `Technique`

- [ ] **Step 1: Projektdatei anlegen**

`pyproject.toml`:

```toml
[project]
name = "djset"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "pydantic>=2.7",
    "librosa>=0.10.2",
    "numpy>=1.26",
    "soundfile>=0.12",
    "pyloudnorm>=0.1.1",
    "claude-agent-sdk>=0.1.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
dj-set = "djset.cli:main"

[tool.pytest.ini_options]
testpaths = ["tests"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"
```

Leere `djset/__init__.py` anlegen.

- [ ] **Step 2: Den fehlschlagenden Test schreiben**

`tests/test_contracts.py`:

```python
import pytest
from pydantic import ValidationError

from djset.contracts import (
    BpmRange, EnergyPoint, MixEntry, MixPlan, Profile, StructureRules,
    TrackCount, TransitionRules,
)


def _profile(**overrides) -> dict:
    """Ein gueltiges Profil als Bausatz; einzelne Felder ueberschreibbar."""
    base = dict(
        genre="liquid dnb",
        length_minutes=60.0,
        bpm=BpmRange(target=174.0, min=170.0, max=178.0),
        transition=TransitionRules(bars_min=16, bars_max=32, techniques=["blend"]),
        harmonic_strictness="strict",
        energy_arc=[EnergyPoint(position=0.0, energy=0.3),
                    EnergyPoint(position=1.0, energy=0.8)],
        track_count=TrackCount(needed=12, request=18),
        style_vocab=["liquid", "rolling breaks"],
        structure=StructureRules(min_intro_bars=16, min_outro_bars=16),
    )
    base.update(overrides)
    return base


def test_profile_accepts_valid_input():
    profile = Profile(**_profile())
    assert profile.bpm.target == 174.0


def test_profile_rejects_target_outside_range():
    with pytest.raises(ValidationError, match="zwischen min und max"):
        Profile(**_profile(bpm=BpmRange(target=190.0, min=170.0, max=178.0)))


def test_profile_rejects_unsorted_energy_arc():
    unsorted_arc = [EnergyPoint(position=1.0, energy=0.8),
                    EnergyPoint(position=0.0, energy=0.3)]
    with pytest.raises(ValidationError, match="aufsteigend"):
        Profile(**_profile(energy_arc=unsorted_arc))


def test_track_count_rejects_request_below_needed():
    with pytest.raises(ValidationError, match="mindestens needed"):
        TrackCount(needed=12, request=8)


def test_mix_entry_rejects_timestretch_beyond_six_percent():
    with pytest.raises(ValidationError):
        MixEntry(track_id="a", play_from_s=0.0, play_to_s=60.0,
                 timestretch_ratio=1.2, gain_db=0.0)


def test_mix_plan_requires_one_transition_less_than_entries():
    entries = [MixEntry(track_id=f"t{i}", play_from_s=0.0, play_to_s=60.0,
                        timestretch_ratio=1.0, gain_db=0.0) for i in range(3)]
    with pytest.raises(ValidationError, match="genau einen Uebergang"):
        MixPlan(target_length_s=180.0, entries=entries, transitions=[])
```

- [ ] **Step 3: Test laufen lassen, Fehlschlag bestätigen**

Run: `pytest tests/test_contracts.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'djset.contracts'`

- [ ] **Step 4: Verträge implementieren**

`djset/contracts.py`:

```python
"""Gemeinsames Vokabular aller Pipeline-Stufen.

Harte Regeln aus dem Spec werden hier als Typ-Constraints erzwungen. Was der
Typ nicht zulaesst, kann keine Stufe versehentlich produzieren.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Technique = Literal["blend", "eq_swap", "filter_sweep", "cut", "double_drop"]
Strictness = Literal["strict", "loose"]

CAMELOT_RE = r"^(1[0-2]|[1-9])[AB]$"


class BpmRange(BaseModel):
    target: float = Field(gt=0)
    min: float = Field(gt=0)
    max: float = Field(gt=0)

    @model_validator(mode="after")
    def _target_within_range(self) -> "BpmRange":
        if self.min > self.max:
            raise ValueError("bpm.min darf nicht groesser als bpm.max sein")
        if not self.min <= self.target <= self.max:
            raise ValueError("bpm.target muss zwischen min und max liegen")
        return self


class TransitionRules(BaseModel):
    bars_min: int = Field(ge=1)
    bars_max: int = Field(ge=1)
    techniques: list[Technique] = Field(min_length=1)

    @model_validator(mode="after")
    def _bars_ordered(self) -> "TransitionRules":
        if self.bars_min > self.bars_max:
            raise ValueError("transition.bars_min darf nicht groesser als bars_max sein")
        return self


class EnergyPoint(BaseModel):
    position: float = Field(ge=0.0, le=1.0)
    energy: float = Field(ge=0.0, le=1.0)


class TrackCount(BaseModel):
    needed: int = Field(ge=3)
    request: int = Field(ge=3)

    @model_validator(mode="after")
    def _request_covers_needed(self) -> "TrackCount":
        if self.request < self.needed:
            raise ValueError("track_count.request muss mindestens needed sein")
        return self


class StructureRules(BaseModel):
    min_intro_bars: int = Field(ge=0)
    min_outro_bars: int = Field(ge=0)


class Profile(BaseModel):
    genre: str = Field(min_length=1)
    length_minutes: float = Field(gt=0)
    bpm: BpmRange
    transition: TransitionRules
    harmonic_strictness: Strictness
    energy_arc: list[EnergyPoint] = Field(min_length=2)
    track_count: TrackCount
    style_vocab: list[str] = Field(min_length=1)
    structure: StructureRules

    @model_validator(mode="after")
    def _arc_sorted(self) -> "Profile":
        positions = [point.position for point in self.energy_arc]
        if positions != sorted(positions):
            raise ValueError("energy_arc muss nach position aufsteigend sortiert sein")
        return self


class Bpm(BaseModel):
    value: float = Field(gt=0)
    confidence: float = Field(ge=0.0, le=1.0)


class BeatGrid(BaseModel):
    first_downbeat_s: float = Field(ge=0)
    bar_length_s: float = Field(gt=0)


class Key(BaseModel):
    camelot: str = Field(pattern=CAMELOT_RE)
    confidence: float = Field(ge=0.0, le=1.0)


class MixRegions(BaseModel):
    intro_end_s: float = Field(ge=0)
    outro_start_s: float = Field(ge=0)

    @model_validator(mode="after")
    def _intro_before_outro(self) -> "MixRegions":
        if self.intro_end_s >= self.outro_start_s:
            raise ValueError("intro_end_s muss vor outro_start_s liegen")
        return self


class Loudness(BaseModel):
    lufs_integrated: float
    true_peak: float


class Verdict(BaseModel):
    usable: bool
    notes: str


class TrackMeasurements(BaseModel):
    """Was das DSP-Modul misst. Enthaelt bewusst kein Urteil."""

    id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    duration_s: float = Field(gt=0)
    bpm: Bpm
    beatgrid: BeatGrid
    key: Key
    energy: list[float] = Field(min_length=1)
    mix_regions: MixRegions
    loudness: Loudness

    @model_validator(mode="after")
    def _regions_within_duration(self) -> "TrackMeasurements":
        if self.mix_regions.outro_start_s > self.duration_s:
            raise ValueError("outro_start_s liegt hinter dem Trackende")
        return self


class TrackAnalysis(TrackMeasurements):
    """Messung plus das Urteil des Track-Analysten."""

    verdict: Verdict


class Rejection(BaseModel):
    track_id: str
    reason: str = Field(min_length=1)


class Shortlist(BaseModel):
    accepted: list[str]
    rejected: list[Rejection]
    shortened: bool
    note: str


class MixEntry(BaseModel):
    track_id: str = Field(min_length=1)
    play_from_s: float = Field(ge=0)
    play_to_s: float = Field(gt=0)
    # Stretch-Grenze aus dem Spec: +-6 %. Im Typ erzwungen.
    timestretch_ratio: float = Field(ge=0.94, le=1.06)
    gain_db: float

    @model_validator(mode="after")
    def _from_before_to(self) -> "MixEntry":
        if self.play_from_s >= self.play_to_s:
            raise ValueError("play_from_s muss vor play_to_s liegen")
        return self


class EqPoint(BaseModel):
    position: float = Field(ge=0.0, le=1.0)
    gain_db: float


class EqCurve(BaseModel):
    low: list[EqPoint] = Field(min_length=2)
    mid: list[EqPoint] = Field(min_length=2)
    high: list[EqPoint] = Field(min_length=2)


class Transition(BaseModel):
    from_track: str = Field(min_length=1)
    to_track: str = Field(min_length=1)
    start_at_s: float = Field(ge=0)
    bars: int = Field(ge=1)
    technique: Technique
    eq_out: EqCurve
    eq_in: EqCurve


class MixPlan(BaseModel):
    target_length_s: float = Field(gt=0)
    entries: list[MixEntry] = Field(min_length=3)
    transitions: list[Transition]

    @model_validator(mode="after")
    def _transition_between_each_pair(self) -> "MixPlan":
        if len(self.transitions) != len(self.entries) - 1:
            raise ValueError(
                "zwischen je zwei Tracks muss es genau einen Uebergang geben"
            )
        return self
```

- [ ] **Step 5: Tests laufen lassen**

Run: `pytest tests/test_contracts.py -v`
Expected: PASS, 6 Tests

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml djset/__init__.py djset/contracts.py tests/test_contracts.py
git commit -m "feat: Pydantic-Vertraege fuer alle Pipeline-Stufen"
```

---

### Task 2: Camelot-Rad

**Files:**
- Create: `djset/camelot.py`
- Test: `tests/test_camelot.py`

**Interfaces:**
- Consumes: `Strictness` aus `djset.contracts`
- Produces: `parse(camelot: str) -> tuple[int, str]`, `neighbours(camelot: str, strictness: Strictness) -> set[str]`, `is_compatible(a: str, b: str, strictness: Strictness) -> bool`, `InvalidCamelot`

- [ ] **Step 1: Den fehlschlagenden Test schreiben**

`tests/test_camelot.py`:

```python
import pytest

from djset.camelot import InvalidCamelot, is_compatible, neighbours, parse


def test_parse_splits_number_and_letter():
    assert parse("8A") == (8, "A")
    assert parse("12B") == (12, "B")


def test_parse_rejects_garbage():
    with pytest.raises(InvalidCamelot):
        parse("13A")
    with pytest.raises(InvalidCamelot):
        parse("8C")


def test_strict_neighbours_are_wheel_step_and_mode_switch():
    assert neighbours("8A", "strict") == {"8A", "7A", "9A", "8B"}


def test_wheel_wraps_at_twelve():
    assert neighbours("12A", "strict") == {"12A", "11A", "1A", "12B"}
    assert neighbours("1A", "strict") == {"1A", "12A", "2A", "1B"}


def test_loose_adds_two_steps():
    assert neighbours("8A", "loose") == {"8A", "6A", "7A", "9A", "10A", "8B"}


def test_is_compatible_uses_strictness():
    assert is_compatible("8A", "10A", "loose") is True
    assert is_compatible("8A", "10A", "strict") is False
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `pytest tests/test_camelot.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'djset.camelot'`

- [ ] **Step 3: Implementieren**

`djset/camelot.py`:

```python
"""Camelot-Rad: welche Tonarten nebeneinander funktionieren.

Zwoelf Zahlen im Kreis, je ein Buchstabe fuer Moll (A) und Dur (B).
Gleiche Zahl bei unterschiedlichem Buchstaben ist die Paralleltonart.
"""

import re

from djset.contracts import Strictness

_PATTERN = re.compile(r"^(1[0-2]|[1-9])([AB])$")
_WHEEL_SIZE = 12


class InvalidCamelot(ValueError):
    """Die Zeichenkette ist keine gueltige Camelot-Notation."""


def parse(camelot: str) -> tuple[int, str]:
    match = _PATTERN.match(camelot)
    if match is None:
        raise InvalidCamelot(f"keine gueltige Camelot-Notation: {camelot!r}")
    return int(match.group(1)), match.group(2)


def _step(number: int, offset: int) -> int:
    """Schritt auf dem Rad, 1..12 mit Umbruch."""
    return (number - 1 + offset) % _WHEEL_SIZE + 1


def neighbours(camelot: str, strictness: Strictness) -> set[str]:
    number, letter = parse(camelot)
    offsets = (-1, 0, 1) if strictness == "strict" else (-2, -1, 0, 1, 2)
    result = {f"{_step(number, offset)}{letter}" for offset in offsets}
    other_letter = "B" if letter == "A" else "A"
    result.add(f"{number}{other_letter}")
    return result


def is_compatible(a: str, b: str, strictness: Strictness) -> bool:
    parse(b)  # validiert auch die zweite Seite
    return b in neighbours(a, strictness)
```

- [ ] **Step 4: Tests laufen lassen**

Run: `pytest tests/test_camelot.py -v`
Expected: PASS, 6 Tests

- [ ] **Step 5: Commit**

```bash
git add djset/camelot.py tests/test_camelot.py
git commit -m "feat: Camelot-Rad mit Nachbarschaft und Vertraeglichkeit"
```

---

### Task 3: BPM und Beatgrid

**Files:**
- Create: `tests/fixtures/__init__.py`, `tests/fixtures/make_audio.py`
- Create: `djset/dsp/__init__.py`, `djset/dsp/analyze.py`
- Test: `tests/dsp/test_bpm.py`

**Interfaces:**
- Consumes: `Bpm`, `BeatGrid` aus `djset.contracts`
- Produces: `click_track(path: Path, bpm: float, bars: int, sr: int = 44100) -> Path` (Fixture), `detect_bpm_and_grid(path: Path, hint_bpm: float | None = None) -> tuple[Bpm, BeatGrid]`, `snap_to_range(bpm: float, low: float, high: float) -> tuple[float, bool]`

- [ ] **Step 1: Den Fixture-Generator schreiben**

`tests/fixtures/__init__.py` leer anlegen. `tests/fixtures/make_audio.py`:

```python
"""Synthetisches Referenzmaterial mit exakt bekannten Eigenschaften.

Damit sind DSP-Tests deterministisch und brauchen keine echten Audiodateien.
"""

from pathlib import Path

import numpy as np
import soundfile as sf

SR = 44100


def click_track(path: Path, bpm: float, bars: int, sr: int = SR) -> Path:
    """4/4-Clicktrack. Jeder Beat ein kurzer Impuls, Eins jeweils lauter."""
    beat_seconds = 60.0 / bpm
    beats = bars * 4
    total = int(np.ceil(beats * beat_seconds * sr)) + sr
    signal = np.zeros(total, dtype=np.float32)

    click_len = int(0.01 * sr)
    envelope = np.exp(-np.linspace(0.0, 12.0, click_len)).astype(np.float32)
    tone = np.sin(2 * np.pi * 1200.0 * np.arange(click_len) / sr).astype(np.float32)
    click = envelope * tone

    for beat in range(beats):
        start = int(beat * beat_seconds * sr)
        gain = 1.0 if beat % 4 == 0 else 0.5
        signal[start:start + click_len] += click * gain

    sf.write(path, signal, sr)
    return path
```

- [ ] **Step 2: Den fehlschlagenden Test schreiben**

`tests/dsp/test_bpm.py` (dazu `tests/dsp/__init__.py` leer anlegen):

```python
from pathlib import Path

from djset.dsp.analyze import detect_bpm_and_grid, snap_to_range
from tests.fixtures.make_audio import click_track


def test_snap_doubles_halftime_reading():
    value, corrected = snap_to_range(87.0, 170.0, 178.0)
    assert value == 174.0
    assert corrected is True


def test_snap_halves_doubletime_reading():
    value, corrected = snap_to_range(250.0, 120.0, 130.0)
    assert value == 125.0
    assert corrected is True


def test_snap_leaves_value_inside_range_untouched():
    value, corrected = snap_to_range(126.0, 120.0, 130.0)
    assert value == 126.0
    assert corrected is False


def test_snap_reports_no_correction_when_hopeless():
    value, corrected = snap_to_range(100.0, 170.0, 178.0)
    assert value == 100.0
    assert corrected is False


def test_detects_bpm_of_click_track(tmp_path: Path):
    audio = click_track(tmp_path / "click.wav", bpm=128.0, bars=16)
    bpm, _grid = detect_bpm_and_grid(audio)
    assert abs(bpm.value - 128.0) < 1.0


def test_grid_bar_length_matches_bpm(tmp_path: Path):
    audio = click_track(tmp_path / "click.wav", bpm=128.0, bars=16)
    _bpm, grid = detect_bpm_and_grid(audio)
    assert abs(grid.bar_length_s - 4 * 60.0 / 128.0) < 0.02
    assert grid.first_downbeat_s < 0.1


def test_hint_pulls_detection_into_the_right_octave(tmp_path: Path):
    audio = click_track(tmp_path / "dnb.wav", bpm=174.0, bars=16)
    bpm, _grid = detect_bpm_and_grid(audio, hint_bpm=174.0)
    assert abs(bpm.value - 174.0) < 1.0
```

- [ ] **Step 3: Test laufen lassen, Fehlschlag bestätigen**

Run: `pytest tests/dsp/test_bpm.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'djset.dsp'`

- [ ] **Step 4: Implementieren**

`djset/dsp/__init__.py` leer anlegen. `djset/dsp/analyze.py`:

```python
"""Messungen am Audio. Dieses Modul faellt keine Urteile.

Wo eine Messung mehrdeutig ist (Tempo-Oktave, Dur/Moll), gibt es eine
Konfidenz zurueck und ueberlaesst die Entscheidung dem aufrufenden Agenten.
"""

from pathlib import Path

import librosa
import numpy as np

from djset.contracts import BeatGrid, Bpm

BEATS_PER_BAR = 4


def snap_to_range(bpm: float, low: float, high: float) -> tuple[float, bool]:
    """Haelt Halftime- und Doubletime-Lesungen an.

    Liefert den korrigierten Wert und ob korrigiert wurde. Passt keine
    Verdopplung oder Halbierung, bleibt der Originalwert stehen - die
    Entscheidung darueber trifft der Agent, nicht dieses Modul.
    """
    if low <= bpm <= high:
        return bpm, False
    for factor in (2.0, 0.5, 4.0, 0.25):
        candidate = bpm * factor
        if low <= candidate <= high:
            return candidate, True
    return bpm, False


def detect_bpm_and_grid(
    path: Path, hint_bpm: float | None = None
) -> tuple[Bpm, BeatGrid]:
    """Tempo und Taktraster. Der Hinweis lenkt librosa in die richtige Oktave."""
    signal, sr = librosa.load(str(path), sr=None, mono=True)
    onset = librosa.onset.onset_strength(y=signal, sr=sr)

    kwargs: dict[str, float] = {}
    if hint_bpm is not None:
        kwargs["start_bpm"] = hint_bpm
    tempo, beat_frames = librosa.beat.beat_track(
        onset_envelope=onset, sr=sr, units="frames", **kwargs
    )
    tempo_value = float(np.atleast_1d(tempo)[0])

    beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    if beat_times.size == 0:
        raise ValueError(f"keine Beats erkennbar in {path}")

    # Konfidenz: wie gleichmaessig die Beatabstaende sind. Ein sauberer
    # Viervierteltakt liefert nahezu konstante Abstaende.
    intervals = np.diff(beat_times)
    if intervals.size == 0:
        confidence = 0.0
    else:
        spread = float(np.std(intervals) / np.mean(intervals))
        confidence = float(max(0.0, min(1.0, 1.0 - spread * 4.0)))

    bar_length = BEATS_PER_BAR * 60.0 / tempo_value
    grid = BeatGrid(first_downbeat_s=float(beat_times[0]), bar_length_s=bar_length)
    return Bpm(value=tempo_value, confidence=confidence), grid
```

- [ ] **Step 5: Tests laufen lassen**

Run: `pytest tests/dsp/test_bpm.py -v`
Expected: PASS, 7 Tests

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/ tests/dsp/ djset/dsp/
git commit -m "feat: BPM- und Beatgrid-Erkennung mit Oktav-Korrektur"
```

---

### Task 4: Tonart-Erkennung

**Files:**
- Modify: `tests/fixtures/make_audio.py` (Akkord-Generator ergänzen)
- Modify: `djset/dsp/analyze.py` (Tonart-Erkennung ergänzen)
- Test: `tests/dsp/test_key.py`

**Interfaces:**
- Consumes: `Key` aus `djset.contracts`
- Produces: `chord_tone(path: Path, semitones: list[int], seconds: float, sr: int = 44100) -> Path` (Fixture), `detect_key(path: Path) -> Key`

**Hintergrund für den Umsetzenden:** Tonart-Erkennung vergleicht das Chroma-Profil eines Stücks mit den Krumhansl-Schmuckler-Referenzprofilen für Dur und Moll, einmal für jeden der zwölf Grundtöne. Die höchste Korrelation gewinnt. Dur und Moll zu verwechseln ist der häufigste Fehler dieses Verfahrens — und beim Mixen der harmloseste, weil Paralleltonarten dieselbe Camelot-Zahl teilen. Der Test prüft deshalb die Zahl, nicht den Buchstaben.

- [ ] **Step 1: Akkord-Fixture ergänzen**

An `tests/fixtures/make_audio.py` anhängen:

```python
def chord_tone(
    path: Path, semitones: list[int], seconds: float = 4.0, sr: int = SR
) -> Path:
    """Gehaltener Akkord aus Sinustoenen. Halbtoene relativ zu C4 (261.63 Hz)."""
    t = np.arange(int(seconds * sr)) / sr
    signal = np.zeros_like(t, dtype=np.float32)
    for semitone in semitones:
        freq = 261.63 * (2.0 ** (semitone / 12.0))
        # Grundton plus zwei Obertoene, damit das Chroma-Profil realistisch faellt
        for harmonic, gain in ((1, 1.0), (2, 0.4), (3, 0.2)):
            signal += (gain * np.sin(2 * np.pi * freq * harmonic * t)).astype(np.float32)
    signal /= np.max(np.abs(signal))
    sf.write(path, signal * 0.8, sr)
    return path
```

- [ ] **Step 2: Den fehlschlagenden Test schreiben**

`tests/dsp/test_key.py`:

```python
from pathlib import Path

from djset.camelot import parse
from djset.dsp.analyze import detect_key
from tests.fixtures.make_audio import chord_tone


def test_c_major_triad_lands_on_camelot_eight(tmp_path: Path):
    """C-Dur ist 8B, A-Moll ist 8A. Dur/Moll-Verwechslung ist tolerierbar,
    eine falsche Zahl waere es nicht - sie steuert die Nachbarschaft."""
    audio = chord_tone(tmp_path / "cmaj.wav", semitones=[0, 4, 7])
    key = detect_key(audio)
    number, _letter = parse(key.camelot)
    assert number == 8


def test_g_major_triad_lands_on_camelot_nine(tmp_path: Path):
    audio = chord_tone(tmp_path / "gmaj.wav", semitones=[7, 11, 14])
    key = detect_key(audio)
    number, _letter = parse(key.camelot)
    assert number == 9


def test_confidence_is_within_bounds(tmp_path: Path):
    audio = chord_tone(tmp_path / "cmaj.wav", semitones=[0, 4, 7])
    key = detect_key(audio)
    assert 0.0 <= key.confidence <= 1.0
```

- [ ] **Step 3: Test laufen lassen, Fehlschlag bestätigen**

Run: `pytest tests/dsp/test_key.py -v`
Expected: FAIL mit `ImportError: cannot import name 'detect_key'`

- [ ] **Step 4: Implementieren**

An `djset/dsp/analyze.py` anhängen (Import `from djset.contracts import Key` oben ergänzen):

```python
# Krumhansl-Schmuckler-Profile: wie stark jede Tonstufe in Dur bzw. Moll wiegt.
_MAJOR_PROFILE = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)
_MINOR_PROFILE = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
)

# Camelot-Zahl je Grundton, Index 0 = C. Dur ist B, Moll ist A.
_MAJOR_CAMELOT = [8, 3, 10, 5, 12, 7, 2, 9, 4, 11, 6, 1]
_MINOR_CAMELOT = [5, 12, 7, 2, 9, 4, 11, 6, 1, 8, 3, 10]


def detect_key(path: Path) -> Key:
    """Tonart in Camelot-Notation ueber Chroma-Korrelation."""
    signal, sr = librosa.load(str(path), sr=None, mono=True)
    chroma = librosa.feature.chroma_cqt(y=signal, sr=sr)
    weights = np.mean(chroma, axis=1)

    scores: list[tuple[float, str]] = []
    for tonic in range(12):
        rotated = np.roll(weights, -tonic)
        for profile, table, letter in (
            (_MAJOR_PROFILE, _MAJOR_CAMELOT, "B"),
            (_MINOR_PROFILE, _MINOR_CAMELOT, "A"),
        ):
            correlation = float(np.corrcoef(rotated, profile)[0, 1])
            scores.append((correlation, f"{table[tonic]}{letter}"))

    scores.sort(reverse=True)
    best_score, best_camelot = scores[0]
    runner_up = scores[1][0]

    # Konfidenz aus dem Abstand zum Zweitplatzierten: ein knapper Sieg
    # bedeutet eine mehrdeutige Tonart.
    margin = max(0.0, best_score - runner_up)
    confidence = float(max(0.0, min(1.0, margin * 5.0)))
    return Key(camelot=best_camelot, confidence=confidence)
```

- [ ] **Step 5: Tests laufen lassen**

Run: `pytest tests/dsp/test_key.py -v`
Expected: PASS, 3 Tests

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/make_audio.py tests/dsp/test_key.py djset/dsp/analyze.py
git commit -m "feat: Tonart-Erkennung in Camelot-Notation"
```

---

### Task 5: Energie, Mix-Regionen, Lautheit und Gesamtmessung

**Files:**
- Modify: `djset/dsp/analyze.py`
- Test: `tests/dsp/test_measurements.py`

**Interfaces:**
- Consumes: `TrackMeasurements`, `MixRegions`, `Loudness` aus `djset.contracts`; `detect_bpm_and_grid`, `detect_key` aus Task 3 und 4
- Produces: `energy_curve(path: Path, grid: BeatGrid) -> list[float]`, `find_mix_regions(energy: list[float], grid: BeatGrid, duration_s: float) -> MixRegions`, `measure_loudness(path: Path) -> Loudness`, `measure_track(path: Path, track_id: str, hint_bpm: float | None = None) -> TrackMeasurements`

- [ ] **Step 1: Den fehlschlagenden Test schreiben**

`tests/dsp/test_measurements.py`:

```python
from pathlib import Path

import numpy as np
import soundfile as sf

from djset.contracts import BeatGrid
from djset.dsp.analyze import (
    energy_curve, find_mix_regions, measure_loudness, measure_track,
)
from tests.fixtures.make_audio import SR, click_track


def _fade_in_track(path: Path, seconds: float = 20.0) -> Path:
    """Rauschen, das leise beginnt, laut wird und leise endet."""
    t = np.arange(int(seconds * SR)) / SR
    rng = np.random.default_rng(seed=7)
    noise = rng.standard_normal(t.size).astype(np.float32) * 0.3
    envelope = np.sin(np.pi * t / seconds).astype(np.float32)
    sf.write(path, noise * envelope, SR)
    return path


def test_energy_curve_is_normalised(tmp_path: Path):
    audio = _fade_in_track(tmp_path / "fade.wav")
    grid = BeatGrid(first_downbeat_s=0.0, bar_length_s=2.0)
    curve = energy_curve(audio, grid)
    assert len(curve) >= 2
    assert min(curve) >= 0.0
    assert max(curve) <= 1.0


def test_energy_curve_rises_then_falls(tmp_path: Path):
    audio = _fade_in_track(tmp_path / "fade.wav")
    grid = BeatGrid(first_downbeat_s=0.0, bar_length_s=2.0)
    curve = energy_curve(audio, grid)
    peak = curve.index(max(curve))
    assert 0 < peak < len(curve) - 1


def test_mix_regions_exclude_quiet_head_and_tail():
    grid = BeatGrid(first_downbeat_s=0.0, bar_length_s=2.0)
    curve = [0.05, 0.1, 0.9, 1.0, 0.9, 0.1, 0.05]
    regions = find_mix_regions(curve, grid, duration_s=56.0)
    assert regions.intro_end_s > 0.0
    assert regions.outro_start_s < 56.0
    assert regions.intro_end_s < regions.outro_start_s


def test_loudness_returns_negative_lufs(tmp_path: Path):
    audio = _fade_in_track(tmp_path / "fade.wav")
    loudness = measure_loudness(audio)
    assert loudness.lufs_integrated < 0.0
    assert loudness.true_peak <= 0.5


def test_measure_track_fills_every_field(tmp_path: Path):
    audio = click_track(tmp_path / "click.wav", bpm=128.0, bars=32)
    measurements = measure_track(audio, track_id="t1")
    assert measurements.id == "t1"
    assert measurements.duration_s > 0
    assert abs(measurements.bpm.value - 128.0) < 1.0
    assert len(measurements.energy) >= 1
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `pytest tests/dsp/test_measurements.py -v`
Expected: FAIL mit `ImportError: cannot import name 'energy_curve'`

- [ ] **Step 3: Implementieren**

An `djset/dsp/analyze.py` anhängen (Imports oben ergänzen: `import pyloudnorm`, `import soundfile as sf`, `from djset.contracts import Loudness, MixRegions, TrackMeasurements`):

```python
BARS_PER_ENERGY_STEP = 4
_QUIET_FRACTION = 0.5  # Anteil des Medians, ab dem ein Abschnitt als "im Groove" gilt


def energy_curve(path: Path, grid: BeatGrid) -> list[float]:
    """RMS je vier Takte, auf 0..1 normiert."""
    signal, sr = librosa.load(str(path), sr=None, mono=True)
    step_seconds = grid.bar_length_s * BARS_PER_ENERGY_STEP
    step_samples = max(1, int(step_seconds * sr))

    values: list[float] = []
    for start in range(0, signal.size, step_samples):
        chunk = signal[start:start + step_samples]
        if chunk.size == 0:
            continue
        values.append(float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2))))

    if not values:
        return [0.0]
    peak = max(values)
    if peak <= 0.0:
        return [0.0 for _ in values]
    return [value / peak for value in values]


def find_mix_regions(
    energy: list[float], grid: BeatGrid, duration_s: float
) -> MixRegions:
    """Wo das Intro endet und das Outro beginnt.

    Massstab ist die halbe Spitzenenergie: darunter laeuft der Track noch nicht
    bzw. nicht mehr im vollen Groove und taugt zum Ueberblenden. Der Median
    waere hier untauglich - bei einer normierten Kurve liegt er so tief, dass
    auch die leisen Raender noch darueber liegen.
    """
    step_seconds = grid.bar_length_s * BARS_PER_ENERGY_STEP
    threshold = max(energy) * _QUIET_FRACTION

    loud = [index for index, value in enumerate(energy) if value >= threshold]
    if not loud:
        # Gleichfoermiger Track ohne erkennbaren Auf- und Abbau
        return MixRegions(intro_end_s=0.0, outro_start_s=duration_s)

    intro_end = min(loud) * step_seconds
    outro_start = min((max(loud) + 1) * step_seconds, duration_s)
    if intro_end >= outro_start:
        intro_end = 0.0
    return MixRegions(intro_end_s=intro_end, outro_start_s=outro_start)


def measure_loudness(path: Path) -> Loudness:
    """Integrierte Lautheit nach EBU R128 und True Peak."""
    signal, sr = sf.read(str(path))
    meter = pyloudnorm.Meter(sr)
    lufs = float(meter.integrated_loudness(signal))

    mono = signal if signal.ndim == 1 else np.mean(signal, axis=1)
    # True Peak naeherungsweise ueber vierfaches Oversampling
    upsampled = librosa.resample(
        mono.astype(np.float32), orig_sr=sr, target_sr=sr * 4
    )
    peak = float(np.max(np.abs(upsampled)))
    true_peak = 20.0 * np.log10(peak) if peak > 0 else -120.0
    return Loudness(lufs_integrated=lufs, true_peak=float(true_peak))


def measure_track(
    path: Path, track_id: str, hint_bpm: float | None = None
) -> TrackMeasurements:
    """Alle Messungen eines Tracks in einem Aufruf."""
    duration = float(librosa.get_duration(path=str(path)))
    bpm, grid = detect_bpm_and_grid(path, hint_bpm=hint_bpm)
    key = detect_key(path)
    energy = energy_curve(path, grid)
    regions = find_mix_regions(energy, grid, duration)
    loudness = measure_loudness(path)

    return TrackMeasurements(
        id=track_id,
        path=str(path),
        duration_s=duration,
        bpm=bpm,
        beatgrid=grid,
        key=key,
        energy=energy,
        mix_regions=regions,
        loudness=loudness,
    )
```

- [ ] **Step 4: Tests laufen lassen**

Run: `pytest tests/dsp/ -v`
Expected: PASS, alle Tests aus Task 3, 4 und 5

- [ ] **Step 5: Commit**

```bash
git add djset/dsp/analyze.py tests/dsp/test_measurements.py
git commit -m "feat: Energiekurve, Mix-Regionen, Lautheit und Gesamtmessung"
```

---

### Task 6: Run-Verwaltung

**Files:**
- Create: `djset/run.py`
- Test: `tests/test_run.py`

**Interfaces:**
- Consumes: nichts aus vorherigen Tasks außer Pydantic
- Produces: `Run` mit `Run.create(base: Path, genre: str, today: date) -> Run`, `Run.load(base: Path, run_id: str) -> Run`, `Run.latest_unfinished(base: Path) -> Run | None`, `run.path(name: str) -> Path`, `run.write_model(name: str, model: BaseModel) -> Path`, `run.read_model(name: str, model_type: type[M]) -> M`, `run.write_text(name: str, text: str) -> Path`, `run.has(name: str) -> bool`, `slugify(text: str) -> str`, `RunNotFound`

**Hintergrund:** Das Datum wird eingespeist statt aus der Uhr gelesen, damit die Run-ID im Test vorhersagbar ist.

- [ ] **Step 1: Den fehlschlagenden Test schreiben**

`tests/test_run.py`:

```python
from datetime import date
from pathlib import Path

import pytest

from djset.contracts import BpmRange
from djset.run import Run, RunNotFound, slugify

DAY = date(2026, 8, 5)


def test_slugify_makes_filesystem_safe_names():
    assert slugify("Liquid DnB") == "liquid-dnb"
    assert slugify("Hard  Techno!") == "hard-techno"


def test_create_numbers_runs_of_the_same_day(tmp_path: Path):
    first = Run.create(tmp_path, "liquid dnb", today=DAY)
    second = Run.create(tmp_path, "liquid dnb", today=DAY)
    assert first.run_id == "2026-08-05-liquid-dnb-01"
    assert second.run_id == "2026-08-05-liquid-dnb-02"


def test_create_makes_the_directory(tmp_path: Path):
    run = Run.create(tmp_path, "techno", today=DAY)
    assert run.path("").is_dir()


def test_write_and_read_model_round_trip(tmp_path: Path):
    run = Run.create(tmp_path, "techno", today=DAY)
    run.write_model("bpm.json", BpmRange(target=130.0, min=128.0, max=134.0))
    restored = run.read_model("bpm.json", BpmRange)
    assert restored.target == 130.0


def test_has_reports_missing_artifacts(tmp_path: Path):
    run = Run.create(tmp_path, "techno", today=DAY)
    assert run.has("profile.json") is False
    run.write_text("profile.json", "{}")
    assert run.has("profile.json") is True


def test_latest_unfinished_skips_runs_with_a_finished_set(tmp_path: Path):
    done = Run.create(tmp_path, "techno", today=DAY)
    done.write_text("set.wav", "nicht wirklich audio")
    pending = Run.create(tmp_path, "techno", today=DAY)
    assert Run.latest_unfinished(tmp_path).run_id == pending.run_id


def test_latest_unfinished_returns_none_when_all_are_done(tmp_path: Path):
    done = Run.create(tmp_path, "techno", today=DAY)
    done.write_text("set.wav", "nicht wirklich audio")
    assert Run.latest_unfinished(tmp_path) is None


def test_load_raises_for_unknown_run(tmp_path: Path):
    with pytest.raises(RunNotFound):
        Run.load(tmp_path, "2026-08-05-gibt-es-nicht-01")
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `pytest tests/test_run.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'djset.run'`

- [ ] **Step 3: Implementieren**

`djset/run.py`:

```python
"""Run-Ordner: jede Ausfuehrung bekommt ihr eigenes Verzeichnis.

Weil ein Lauf unbeaufsichtigt stattfindet, sind die abgelegten Artefakte das
einzige Fenster in seine Entscheidungen.
"""

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

M = TypeVar("M", bound=BaseModel)

RUNS_DIRNAME = "runs"
FINISHED_MARKER = "set.wav"


class RunNotFound(FileNotFoundError):
    """Der angeforderte Run-Ordner existiert nicht."""


def slugify(text: str) -> str:
    lowered = text.strip().lower()
    collapsed = re.sub(r"[^a-z0-9]+", "-", lowered)
    return collapsed.strip("-")


@dataclass(frozen=True)
class Run:
    base: Path
    run_id: str

    @property
    def directory(self) -> Path:
        return self.base / RUNS_DIRNAME / self.run_id

    @classmethod
    def create(cls, base: Path, genre: str, today: date) -> "Run":
        parent = base / RUNS_DIRNAME
        parent.mkdir(parents=True, exist_ok=True)
        prefix = f"{today.isoformat()}-{slugify(genre)}"

        taken = {path.name for path in parent.iterdir() if path.is_dir()}
        for number in range(1, 100):
            candidate = f"{prefix}-{number:02d}"
            if candidate not in taken:
                run = cls(base=base, run_id=candidate)
                run.directory.mkdir(parents=True)
                return run
        raise RuntimeError(f"mehr als 99 Laeufe fuer {prefix} an einem Tag")

    @classmethod
    def load(cls, base: Path, run_id: str) -> "Run":
        run = cls(base=base, run_id=run_id)
        if not run.directory.is_dir():
            raise RunNotFound(f"kein Run-Ordner fuer {run_id}")
        return run

    @classmethod
    def latest_unfinished(cls, base: Path) -> "Run | None":
        parent = base / RUNS_DIRNAME
        if not parent.is_dir():
            return None
        candidates = sorted(
            (path for path in parent.iterdir() if path.is_dir()),
            key=lambda path: path.name,
            reverse=True,
        )
        for path in candidates:
            if not (path / FINISHED_MARKER).exists():
                return cls(base=base, run_id=path.name)
        return None

    def path(self, name: str) -> Path:
        return self.directory / name if name else self.directory

    def has(self, name: str) -> bool:
        return self.path(name).exists()

    def write_text(self, name: str, text: str) -> Path:
        target = self.path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def write_model(self, name: str, model: BaseModel) -> Path:
        return self.write_text(name, model.model_dump_json(indent=2))

    def read_model(self, name: str, model_type: type[M]) -> M:
        target = self.path(name)
        if not target.exists():
            raise RunNotFound(f"Artefakt fehlt: {target}")
        return model_type.model_validate_json(target.read_text(encoding="utf-8"))
```

- [ ] **Step 4: Tests laufen lassen**

Run: `pytest tests/test_run.py -v`
Expected: PASS, 8 Tests

- [ ] **Step 5: Commit**

```bash
git add djset/run.py tests/test_run.py
git commit -m "feat: Run-Ordner mit Artefakt-Ablage und Resume-Erkennung"
```

---

### Task 7: Agent-Basis

**Files:**
- Create: `tests/fixtures/fake_client.py`
- Create: `djset/agents/__init__.py`, `djset/agents/base.py`
- Test: `tests/agents/test_base.py`

**Interfaces:**
- Consumes: nichts aus vorherigen Tasks
- Produces: `AgentClient` (Protocol mit `complete(prompt: str, schema: dict[str, object]) -> str`), `Budget`, `run_agent(client: AgentClient, prompt: str, model_type: type[M], budget: Budget, retries: int = 2) -> M`, `AgentError`, `SchemaViolation`, `BudgetExceeded`, `FakeClient` (Fixture)

**Hintergrund:** `run_agent` ist die einzige Stelle, an der ein Sprachmodell aufgerufen wird. Jeder Agent geht durch sie hindurch. Der Client wird als Protokoll eingespeist, damit Tests ohne Netz auskommen — der echte Client kommt erst in Task 9 dazu.

- [ ] **Step 1: Den Fake-Client schreiben**

`tests/fixtures/fake_client.py`:

```python
"""Eingespeister Agent-Client fuer Tests. Kein Netzzugriff."""


class FakeClient:
    """Gibt vorbereitete Antworten der Reihe nach zurueck."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    def complete(self, prompt: str, schema: dict[str, object]) -> str:
        self.prompts.append(prompt)
        if not self._responses:
            raise AssertionError("FakeClient wurde oefter gerufen als vorbereitet")
        return self._responses.pop(0)

    @property
    def calls(self) -> int:
        return len(self.prompts)
```

- [ ] **Step 2: Den fehlschlagenden Test schreiben**

`tests/agents/test_base.py` (dazu `tests/agents/__init__.py` leer anlegen):

```python
import pytest
from pydantic import BaseModel, Field

from djset.agents.base import Budget, BudgetExceeded, SchemaViolation, run_agent
from tests.fixtures.fake_client import FakeClient


class Answer(BaseModel):
    value: int = Field(ge=0)


def _budget() -> Budget:
    return Budget(max_tokens=10_000)


def test_parses_a_valid_response():
    client = FakeClient(['{"value": 7}'])
    result = run_agent(client, "gib mir sieben", Answer, _budget())
    assert result.value == 7
    assert client.calls == 1


def test_retries_on_broken_json_and_succeeds():
    client = FakeClient(["kein json", '{"value": 3}'])
    result = run_agent(client, "gib mir drei", Answer, _budget())
    assert result.value == 3
    assert client.calls == 2


def test_retry_prompt_contains_the_error():
    client = FakeClient(['{"value": -1}', '{"value": 1}'])
    run_agent(client, "gib mir eins", Answer, _budget())
    assert "greater than or equal to 0" in client.prompts[1]


def test_gives_up_after_the_retry_limit():
    client = FakeClient(["nein", "immer noch nein", "weiterhin nein"])
    with pytest.raises(SchemaViolation):
        run_agent(client, "unmoeglich", Answer, _budget(), retries=2)
    assert client.calls == 3


def test_strips_markdown_fences():
    client = FakeClient(['```json\n{"value": 5}\n```'])
    result = run_agent(client, "gib mir fuenf", Answer, _budget())
    assert result.value == 5


def test_budget_stops_further_calls():
    budget = Budget(max_tokens=10)
    client = FakeClient(['{"value": 1}'])
    with pytest.raises(BudgetExceeded):
        run_agent(client, "x" * 400, Answer, budget)
    assert client.calls == 0
```

- [ ] **Step 3: Test laufen lassen, Fehlschlag bestätigen**

Run: `pytest tests/agents/test_base.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'djset.agents'`

- [ ] **Step 4: Implementieren**

`djset/agents/__init__.py` leer anlegen. `djset/agents/base.py`:

```python
"""Der einzige Weg, auf dem ein Sprachmodell aufgerufen wird.

Erzwingt strukturierte Ausgabe: ungeprueftes JSON verlaesst diese Datei nie.
Bei Schemaverletzung wird mit der Fehlermeldung erneut gefragt, damit das
Modell weiss, was es falsch gemacht hat.
"""

import re
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

M = TypeVar("M", bound=BaseModel)

# Grobe Schaetzung: vier Zeichen je Token. Genau genug fuer eine Notbremse.
_CHARS_PER_TOKEN = 4
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class AgentError(Exception):
    """Oberklasse aller Agentenfehler."""


class SchemaViolation(AgentError):
    """Das Modell hat auch nach allen Versuchen kein gueltiges Ergebnis geliefert."""


class BudgetExceeded(AgentError):
    """Das Token-Budget des Laufs ist aufgebraucht."""


class AgentClient(Protocol):
    def complete(self, prompt: str, schema: dict[str, object]) -> str: ...


class Budget:
    """Notbremse fuer unbeaufsichtigte Laeufe."""

    def __init__(self, max_tokens: int) -> None:
        self.max_tokens = max_tokens
        self.spent = 0

    def charge(self, text: str) -> None:
        estimate = max(1, len(text) // _CHARS_PER_TOKEN)
        if self.spent + estimate > self.max_tokens:
            raise BudgetExceeded(
                f"Budget erschoepft: {self.spent} von {self.max_tokens} Tokens verbraucht"
            )
        self.spent += estimate


def _strip_fences(raw: str) -> str:
    match = _FENCE.match(raw)
    return match.group(1) if match else raw.strip()


def run_agent(
    client: AgentClient,
    prompt: str,
    model_type: type[M],
    budget: Budget,
    retries: int = 2,
) -> M:
    """Fragt das Modell, bis die Antwort dem Schema entspricht.

    `retries` zaehlt die Wiederholungen, nicht die Gesamtversuche: bei 2 wird
    hoechstens dreimal gefragt.
    """
    schema = model_type.model_json_schema()
    current = prompt
    last_error = ""

    for _attempt in range(retries + 1):
        budget.charge(current)
        raw = client.complete(current, schema)
        budget.charge(raw)
        try:
            return model_type.model_validate_json(_strip_fences(raw))
        except ValidationError as error:
            last_error = str(error)
            current = (
                f"{prompt}\n\n"
                f"Dein letzter Versuch war ungueltig:\n{raw}\n\n"
                f"Fehler:\n{last_error}\n\n"
                f"Antworte erneut, ausschliesslich mit gueltigem JSON nach dem Schema."
            )

    raise SchemaViolation(
        f"{model_type.__name__} nach {retries + 1} Versuchen nicht erhalten: {last_error}"
    )
```

- [ ] **Step 5: Tests laufen lassen**

Run: `pytest tests/agents/test_base.py -v`
Expected: PASS, 6 Tests

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/fake_client.py tests/agents/ djset/agents/
git commit -m "feat: Agent-Basis mit erzwungener Schemaausgabe und Budget"
```

---

### Task 8: Genre-Profiler

**Files:**
- Create: `djset/agents/profiler.py`
- Test: `tests/agents/test_profiler.py`

**Interfaces:**
- Consumes: `run_agent`, `Budget`, `AgentClient` aus Task 7; `Profile`, `TrackCount` aus Task 1
- Produces: `build_profile(client: AgentClient, genre: str, length_minutes: float, budget: Budget) -> Profile`, `OVERSHOOT_FACTOR`, `MIN_TRACKS`

**Hintergrund:** Zwei Felder werden dem Modell nach der Antwort aus der Hand genommen: `length_minutes` steht auf der Kommandozeile und ist keine Meinungssache, und `track_count.request` folgt aus dem Overshoot-Faktor. Der Prompt fragt sie deshalb gar nicht erst ab — das Modell liefert einen Entwurf, der Code setzt die harten Werte.

- [ ] **Step 1: Den fehlschlagenden Test schreiben**

`tests/agents/test_profiler.py`:

```python
import json

import pytest

from djset.agents.base import Budget, SchemaViolation
from djset.agents.profiler import MIN_TRACKS, build_profile
from tests.fixtures.fake_client import FakeClient

DRAFT = {
    "genre": "liquid dnb",
    "length_minutes": 1.0,
    "bpm": {"target": 174.0, "min": 170.0, "max": 178.0},
    "transition": {"bars_min": 16, "bars_max": 32, "techniques": ["blend"]},
    "harmonic_strictness": "strict",
    "energy_arc": [
        {"position": 0.0, "energy": 0.3},
        {"position": 0.6, "energy": 0.9},
        {"position": 1.0, "energy": 0.6},
    ],
    "track_count": {"needed": 12, "request": 12},
    "style_vocab": ["liquid", "rolling breaks", "warm pads"],
    "structure": {"min_intro_bars": 16, "min_outro_bars": 16},
}


def test_returns_a_valid_profile():
    client = FakeClient([json.dumps(DRAFT)])
    profile = build_profile(client, "liquid dnb", 60.0, Budget(max_tokens=100_000))
    assert profile.bpm.target == 174.0


def test_length_comes_from_the_caller_not_the_model():
    client = FakeClient([json.dumps(DRAFT)])
    profile = build_profile(client, "liquid dnb", 60.0, Budget(max_tokens=100_000))
    assert profile.length_minutes == 60.0


def test_overshoot_is_applied_by_code():
    client = FakeClient([json.dumps(DRAFT)])
    profile = build_profile(client, "liquid dnb", 60.0, Budget(max_tokens=100_000))
    assert profile.track_count.needed == 12
    assert profile.track_count.request == 18


def test_track_count_is_lifted_to_the_minimum():
    draft = json.loads(json.dumps(DRAFT))
    draft["track_count"] = {"needed": 1, "request": 1}
    client = FakeClient([json.dumps(draft)])
    profile = build_profile(client, "liquid dnb", 5.0, Budget(max_tokens=100_000))
    assert profile.track_count.needed == MIN_TRACKS


def test_the_genre_string_reaches_the_prompt():
    client = FakeClient([json.dumps(DRAFT)])
    build_profile(client, "hard techno", 60.0, Budget(max_tokens=100_000))
    assert "hard techno" in client.prompts[0]


def test_invalid_draft_is_retried_then_fails():
    broken = json.loads(json.dumps(DRAFT))
    broken["bpm"] = {"target": 300.0, "min": 170.0, "max": 178.0}
    client = FakeClient([json.dumps(broken)] * 3)
    with pytest.raises(SchemaViolation):
        build_profile(client, "liquid dnb", 60.0, Budget(max_tokens=100_000))
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `pytest tests/agents/test_profiler.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'djset.agents.profiler'`

- [ ] **Step 3: Implementieren**

`djset/agents/profiler.py`:

```python
"""Agent 1 - leitet aus einem freien Genre-Namen das Regelwerk des Laufs ab."""

import math

from djset.agents.base import AgentClient, Budget, run_agent
from djset.contracts import Profile, TrackCount

OVERSHOOT_FACTOR = 1.5
MIN_TRACKS = 3

PROMPT = """Du bist ein erfahrener DJ und kennst die Konventionen elektronischer Genres.

Erstelle ein Regelwerk fuer ein DJ-Set im Genre: {genre}
Zielllaenge des Sets: {length_minutes:.0f} Minuten

Lege fest:
- bpm: uebliches Ziel-Tempo des Genres mit brauchbarer Toleranz nach unten und oben
- transition: uebliche Uebergangslaenge in Takten und die Techniken, die in
  diesem Genre gebraeuchlich sind. Erlaubt sind: blend, eq_swap, filter_sweep,
  cut, double_drop
- harmonic_strictness: "strict" wenn das Genre harmonisches Mixen verlangt,
  "loose" wenn Tonart nachrangig ist
- energy_arc: Stuetzpunkte der Energiekurve ueber das Set, aufsteigend nach
  position von 0.0 bis 1.0, jeweils mit Zielenergie 0.0 bis 1.0
- track_count.needed: wie viele Tracks fuer diese Laenge noetig sind, ausgehend
  von der ueblichen Tracklaenge des Genres
- style_vocab: Stilbegriffe, die Suno versteht und die dieses Genre treffen
- structure: geforderte Mindestlaenge von Intro und Outro in Takten

Fuer die Felder genre, length_minutes und track_count.request setze
Platzhalter ein - sie werden nach deiner Antwort ueberschrieben.

Antworte ausschliesslich mit JSON nach dem vorgegebenen Schema."""


def build_profile(
    client: AgentClient, genre: str, length_minutes: float, budget: Budget
) -> Profile:
    """Fragt das Modell nach einem Entwurf und setzt die harten Werte selbst."""
    draft = run_agent(
        client,
        PROMPT.format(genre=genre, length_minutes=length_minutes),
        Profile,
        budget,
    )

    needed = max(MIN_TRACKS, draft.track_count.needed)
    request = max(MIN_TRACKS, math.ceil(needed * OVERSHOOT_FACTOR))

    return draft.model_copy(
        update={
            "genre": genre,
            "length_minutes": length_minutes,
            "track_count": TrackCount(needed=needed, request=request),
        }
    )
```

- [ ] **Step 4: Tests laufen lassen**

Run: `pytest tests/agents/test_profiler.py -v`
Expected: PASS, 6 Tests

- [ ] **Step 5: Commit**

```bash
git add djset/agents/profiler.py tests/agents/test_profiler.py
git commit -m "feat: Genre-Profiler mit erzwungenem Overshoot"
```

---

### Task 9: Prompt-Writer und CLI-Kommando `plan`

Diese Task schließt die Plan-Phase ab: Prompt-Writer, echter Agent-Client und CLI gehören zusammen, weil sie erst gemeinsam ein benutzbares Kommando ergeben.

**Files:**
- Create: `djset/agents/prompt_writer.py`
- Create: `djset/agents/client.py`
- Create: `djset/cli.py`
- Test: `tests/agents/test_prompt_writer.py`, `tests/test_cli_plan.py`

**Interfaces:**
- Consumes: `build_profile` aus Task 8; `run_agent`, `Budget`, `AgentClient` aus Task 7; `Run` aus Task 6; `camelot.parse` aus Task 2
- Produces: `SunoPrompt`, `PromptSet`, `write_prompts(client: AgentClient, profile: Profile, budget: Budget) -> PromptSet`, `render_markdown(profile: Profile, prompt_set: PromptSet) -> str`, `ClaudeClient`, `main(argv: list[str] | None = None) -> int`, `cmd_plan(...) -> int`

- [ ] **Step 1: Den fehlschlagenden Test für den Prompt-Writer schreiben**

`tests/agents/test_prompt_writer.py`:

```python
import json

import pytest

from djset.agents.base import Budget, SchemaViolation
from djset.agents.prompt_writer import render_markdown, write_prompts
from djset.contracts import (
    BpmRange, EnergyPoint, Profile, StructureRules, TrackCount, TransitionRules,
)
from tests.fixtures.fake_client import FakeClient


def _profile(request: int = 4) -> Profile:
    return Profile(
        genre="liquid dnb",
        length_minutes=20.0,
        bpm=BpmRange(target=174.0, min=170.0, max=178.0),
        transition=TransitionRules(bars_min=16, bars_max=32, techniques=["blend"]),
        harmonic_strictness="strict",
        energy_arc=[EnergyPoint(position=0.0, energy=0.3),
                    EnergyPoint(position=1.0, energy=0.9)],
        track_count=TrackCount(needed=3, request=request),
        style_vocab=["liquid", "rolling breaks"],
        structure=StructureRules(min_intro_bars=16, min_outro_bars=16),
    )


def _prompt_set(count: int, camelots: list[str] | None = None) -> str:
    keys = camelots or ["8A", "9A", "10A", "8B"]
    return json.dumps({
        "prompts": [
            {
                "index": i + 1,
                "title": f"Track {i + 1}",
                "style": "liquid dnb, rolling breaks",
                "target_bpm": 174,
                "target_camelot": keys[i % len(keys)],
                "notes": "warm, luftig",
            }
            for i in range(count)
        ]
    })


def test_returns_exactly_the_requested_number():
    client = FakeClient([_prompt_set(4)])
    result = write_prompts(client, _profile(request=4), Budget(max_tokens=100_000))
    assert len(result.prompts) == 4


def test_wrong_count_is_retried_then_fails():
    client = FakeClient([_prompt_set(2)] * 3)
    with pytest.raises(SchemaViolation):
        write_prompts(client, _profile(request=4), Budget(max_tokens=100_000))


def test_too_few_distinct_keys_is_rejected():
    same = ["8A", "8A", "8A", "8A"]
    client = FakeClient([_prompt_set(4, same)] * 3)
    with pytest.raises(SchemaViolation):
        write_prompts(client, _profile(request=4), Budget(max_tokens=100_000))


def test_markdown_lists_every_prompt():
    client = FakeClient([_prompt_set(4)])
    result = write_prompts(client, _profile(request=4), Budget(max_tokens=100_000))
    text = render_markdown(_profile(request=4), result)
    assert text.count("## Track") == 4
    assert "174" in text
    assert "liquid dnb" in text
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `pytest tests/agents/test_prompt_writer.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'djset.agents.prompt_writer'`

- [ ] **Step 3: Prompt-Writer implementieren**

`djset/agents/prompt_writer.py`:

```python
"""Agent 2 - schreibt die Suno-Prompts.

Zwei harte Regeln setzt der Code durch, nicht der Prompttext: die Anzahl muss
der Overshoot-Zahl entsprechen, und die Zieltonarten muessen sich verteilen.
Kaemen alle Tracks in derselben Tonart, haette der Setlist-Architect spaeter
keinen harmonischen Spielraum.
"""

from pydantic import BaseModel, Field, field_validator

from djset.agents.base import AgentClient, Budget, SchemaViolation, run_agent
from djset.camelot import parse
from djset.contracts import CAMELOT_RE, Profile

MIN_DISTINCT_KEYS = 3


class SunoPrompt(BaseModel):
    index: int = Field(ge=1)
    title: str = Field(min_length=1)
    style: str = Field(min_length=1)
    target_bpm: int = Field(gt=0)
    target_camelot: str = Field(pattern=CAMELOT_RE)
    notes: str

    @field_validator("target_camelot")
    @classmethod
    def _valid_camelot(cls, value: str) -> str:
        parse(value)
        return value


class PromptSet(BaseModel):
    prompts: list[SunoPrompt] = Field(min_length=1)


PROMPT = """Du schreibst Prompts fuer Suno, damit daraus die Tracks eines
DJ-Sets entstehen.

Genre: {genre}
Ziel-BPM: {bpm_target:.0f} (erlaubt {bpm_min:.0f} bis {bpm_max:.0f})
Stilbegriffe: {style_vocab}
Mindestlaenge Intro: {min_intro} Takte, Outro: {min_outro} Takte

Schreibe genau {count} Prompts. Anforderungen:
- Jeder Prompt bekommt eine fortlaufende index-Nummer ab 1
- target_bpm liegt im erlaubten Bereich
- target_camelot verteilt sich ueber mindestens {min_keys} verschiedene
  Tonarten, damit spaeter harmonisch gemischt werden kann. Bleib dabei in
  benachbarten Bereichen des Camelot-Rads, nicht quer verstreut
- style beschreibt den Klang so, wie Suno es im Feld "Style of Music" erwartet:
  kurze, kommagetrennte Begriffe, kein Fliesstext
- Die Tracks sollen sich in Energie unterscheiden, damit ein Spannungsbogen
  moeglich wird - von zurueckhaltenden Openern bis zu treibenden Peak-Tracks
- notes haelt in einem Satz fest, welche Rolle der Track im Set spielen soll

Antworte ausschliesslich mit JSON nach dem vorgegebenen Schema."""


def write_prompts(
    client: AgentClient, profile: Profile, budget: Budget
) -> PromptSet:
    wanted = profile.track_count.request
    prompt = PROMPT.format(
        genre=profile.genre,
        bpm_target=profile.bpm.target,
        bpm_min=profile.bpm.min,
        bpm_max=profile.bpm.max,
        style_vocab=", ".join(profile.style_vocab),
        min_intro=profile.structure.min_intro_bars,
        min_outro=profile.structure.min_outro_bars,
        count=wanted,
        min_keys=MIN_DISTINCT_KEYS,
    )

    attempt_prompt = prompt
    last_problem = ""
    for _attempt in range(3):
        result = run_agent(client, attempt_prompt, PromptSet, budget, retries=0)
        problem = _check(result, wanted)
        if problem is None:
            return result
        last_problem = problem
        attempt_prompt = f"{prompt}\n\nDein letzter Versuch war unbrauchbar: {problem}"

    raise SchemaViolation(f"Prompt-Satz nach drei Versuchen unbrauchbar: {last_problem}")


def _check(result: PromptSet, wanted: int) -> str | None:
    if len(result.prompts) != wanted:
        return f"es waren genau {wanted} Prompts verlangt, geliefert wurden {len(result.prompts)}"
    distinct = {prompt.target_camelot for prompt in result.prompts}
    if len(distinct) < MIN_DISTINCT_KEYS:
        return (
            f"nur {len(distinct)} verschiedene Tonarten, verlangt sind "
            f"mindestens {MIN_DISTINCT_KEYS}"
        )
    return None


def render_markdown(profile: Profile, prompt_set: PromptSet) -> str:
    lines = [
        f"# Suno-Prompts fuer {profile.genre}",
        "",
        f"Set-Laenge: {profile.length_minutes:.0f} Minuten - "
        f"benoetigt {profile.track_count.needed} Tracks, "
        f"angefordert {profile.track_count.request}.",
        "",
        "Generiere jeden Prompt einzeln bei Suno und lege die Downloads in `watch/` ab.",
        "",
    ]
    for prompt in prompt_set.prompts:
        lines += [
            f"## Track {prompt.index}: {prompt.title}",
            "",
            f"- **Style of Music:** {prompt.style}",
            f"- **BPM:** {prompt.target_bpm}",
            f"- **Tonart (Camelot):** {prompt.target_camelot}",
            f"- **Rolle im Set:** {prompt.notes}",
            "",
        ]
    return "\n".join(lines)
```

- [ ] **Step 4: Tests laufen lassen**

Run: `pytest tests/agents/test_prompt_writer.py -v`
Expected: PASS, 4 Tests

- [ ] **Step 5: Echten Client implementieren**

`djset/agents/client.py`:

```python
"""Der echte Agent-Client. Wird nirgends im Test benutzt."""

import json

from claude_agent_sdk import query

MODEL = "claude-opus-5"


class ClaudeClient:
    """Erfuellt das AgentClient-Protokoll aus djset.agents.base."""

    def __init__(self, model: str = MODEL) -> None:
        self._model = model

    def complete(self, prompt: str, schema: dict[str, object]) -> str:
        full = (
            f"{prompt}\n\n"
            f"JSON-Schema der erwarteten Antwort:\n"
            f"{json.dumps(schema, ensure_ascii=False)}"
        )
        chunks: list[str] = []
        for message in query(prompt=full, options={"model": self._model}):
            text = getattr(message, "text", None)
            if isinstance(text, str):
                chunks.append(text)
        return "".join(chunks)
```

- [ ] **Step 6: Den fehlschlagenden CLI-Test schreiben**

`tests/test_cli_plan.py`:

```python
import json
from pathlib import Path

from djset.cli import cmd_plan
from djset.contracts import Profile
from djset.run import Run
from tests.agents.test_profiler import DRAFT
from tests.agents.test_prompt_writer import _prompt_set
from tests.fixtures.fake_client import FakeClient


def _client() -> FakeClient:
    return FakeClient([json.dumps(DRAFT), _prompt_set(18)])


def test_plan_writes_both_artifacts(tmp_path: Path):
    code = cmd_plan(
        base=tmp_path, genre="liquid dnb", length_minutes=60.0, client=_client()
    )
    assert code == 0
    run = Run.latest_unfinished(tmp_path)
    assert run is not None
    assert run.has("profile.json")
    assert run.has("prompts.md")


def test_plan_profile_survives_the_round_trip(tmp_path: Path):
    cmd_plan(base=tmp_path, genre="liquid dnb", length_minutes=60.0, client=_client())
    run = Run.latest_unfinished(tmp_path)
    profile = run.read_model("profile.json", Profile)
    assert profile.genre == "liquid dnb"
    assert profile.length_minutes == 60.0
    assert profile.track_count.request == 18


def test_plan_prompts_markdown_has_every_track(tmp_path: Path):
    cmd_plan(base=tmp_path, genre="liquid dnb", length_minutes=60.0, client=_client())
    run = Run.latest_unfinished(tmp_path)
    text = run.path("prompts.md").read_text(encoding="utf-8")
    assert text.count("## Track") == 18
```

Hinweis: `_prompt_set` erzeugt vier verschiedene Tonarten im Wechsel und erfüllt damit die Verteilungsregel auch bei 18 Prompts.

- [ ] **Step 7: Test laufen lassen, Fehlschlag bestätigen**

Run: `pytest tests/test_cli_plan.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'djset.cli'`

- [ ] **Step 8: CLI implementieren**

`djset/cli.py`:

```python
"""Einsprungpunkt. In diesem Plan gibt es nur das Unterkommando `plan`."""

import argparse
import sys
from datetime import date
from pathlib import Path

from djset.agents.base import AgentClient, Budget
from djset.agents.profiler import build_profile
from djset.agents.prompt_writer import render_markdown, write_prompts
from djset.run import Run

PLAN_BUDGET_TOKENS = 120_000


def cmd_plan(
    base: Path,
    genre: str,
    length_minutes: float,
    client: AgentClient,
    today: date | None = None,
) -> int:
    budget = Budget(max_tokens=PLAN_BUDGET_TOKENS)
    run = Run.create(base, genre, today=today or date.today())

    profile = build_profile(client, genre, length_minutes, budget)
    run.write_model("profile.json", profile)

    prompt_set = write_prompts(client, profile, budget)
    run.write_text("prompts.md", render_markdown(profile, prompt_set))

    print(f"Run: {run.run_id}")
    print(f"Profil:  {run.path('profile.json')}")
    print(f"Prompts: {run.path('prompts.md')}")
    print(
        f"\nGeneriere die {profile.track_count.request} Tracks bei Suno und "
        f"lege die Downloads in {base / 'watch'} ab."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dj-set")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="Genre-Profil und Suno-Prompts erzeugen")
    plan.add_argument("--genre", required=True, help="frei waehlbar, z.B. 'liquid dnb'")
    plan.add_argument("--length", type=float, required=True, help="Ziellaenge in Minuten")
    plan.add_argument("--base", type=Path, default=Path("."), help="Projektwurzel")

    args = parser.parse_args(argv)
    if args.command == "plan":
        from djset.agents.client import ClaudeClient

        return cmd_plan(
            base=args.base,
            genre=args.genre,
            length_minutes=args.length,
            client=ClaudeClient(),
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 9: Gesamte Testsuite laufen lassen**

Run: `pytest -v`
Expected: PASS, alle Tests aus Task 1 bis 9

- [ ] **Step 10: Commit**

```bash
git add djset/agents/prompt_writer.py djset/agents/client.py djset/cli.py tests/agents/test_prompt_writer.py tests/test_cli_plan.py
git commit -m "feat: Prompt-Writer und CLI-Kommando plan"
```

- [ ] **Step 11: Manuelle Abnahme**

```bash
pip install -e ".[dev]" && dj-set plan --genre "liquid dnb" --length 60
```

Erwartet: ein neuer Ordner unter `runs/`, darin ein `profile.json` mit einem BPM-Korridor um 174 und eine `prompts.md` mit 18 Prompts. Die Prompts sind so gebaut, dass sie sich direkt in das Suno-Feld "Style of Music" kopieren lassen.

---

## Abgeschlossen wenn

1. `pytest` läuft vollständig grün.
2. `dj-set plan --genre "liquid dnb" --length 60` erzeugt `profile.json` und `prompts.md` in einem neuen Run-Ordner.
3. Das Profil enthält einen für das Genre plausiblen BPM-Korridor.
4. `prompts.md` enthält `ceil(needed * 1.5)` Prompts über mindestens drei verschiedene Tonarten.
5. Kein Test greift auf das Netz zu.

## Was Plan 2 aufgreift

Plan 2 baut auf `measure_track` (Task 5), `Run` (Task 6) und `run_agent` (Task 7) auf und ergänzt die Agenten 3 bis 7: Track-Analyst, Curator, Setlist-Architect, Transition-Designer und Critic. Ergebnis ist ein vom Critic freigegebenes `mixplan.json`. Voraussetzung für die Abnahme von Plan 2 sind echte Suno-Tracks in `watch/` — erzeugt aus den Prompts dieses Plans.
