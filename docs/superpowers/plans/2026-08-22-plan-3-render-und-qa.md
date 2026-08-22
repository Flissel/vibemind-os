# Plan 3: Render-Engineer, QA und der fertige Lauf

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aus `mixplan.json` entsteht ein hörbares `set.wav` mit Tracklist und QA-Bericht — und `dj-set build` läuft von der Datei im watch-Ordner bis zum fertigen Set ohne jede Interaktion durch.

**Architecture:** Der Renderer führt den Mixplan aus und trifft keine einzige eigene Entscheidung. Timestretch über ffmpegs `rubberband`-Filter, alles andere — Bandzerlegung, Verstärkungshüllkurven, Summierung — in numpy, weil das deterministisch und ohne Unterprozess testbar ist. Die QA misst das fertige File nach und darf genau einen korrigierten Re-Render auslösen.

**Tech Stack:** Python 3.11+, numpy, scipy, soundfile, librosa, pyloudnorm, ffmpeg 8.1 mit einkompiliertem librubberband, Pydantic v2, pytest.

**Spec:** `docs/superpowers/specs/2026-08-05-suno-dj-set-multiagent-design.md`

**Vorgänger:** Plan 1 und Plan 2, beide auf `master`. 205 Tests grün.

## Global Constraints

- Python 3.11 oder neuer. Moderne Typsyntax, nie `typing.List`. Kein `Any`.
- Deutsche Fehlermeldungen und Kommentare, englische Bezeichner im Code.
- Conventional Commits. Jede Task endet mit genau einem Commit.
- **Kein Netzzugriff in Tests.**
- Testausgabe makellos: `filterwarnings = ["error"]` steht in `pyproject.toml`, jede Warnung ist ein Testfehler.
- Testgetrieben: erst der fallende Test, dann die Implementierung.
- Harte Regeln aus dem Spec werden im Typ erzwungen, nicht in Prosa beschrieben.
- **Nie `model_copy(update=...)`** — validiert in Pydantic v2 nicht. Immer `Model.model_validate(alt.model_dump() | {...})`.
- Die Stretch-Grenze (`MIN_RATIO` / `MAX_RATIO`) und `MIN_TRACKS` stehen in `djset/contracts.py` und werden von dort importiert, nie neu hingeschrieben.
- **Der Renderer entscheidet nichts.** Jede Zahl, nach der er handelt, steht im Mixplan. Findet sich beim Bauen eine Größe, die er selbst bestimmen müsste, ist das ein Befund über den Mixplan — melden, nicht im Renderer erfinden.

## Entscheidungen dieses Plans

**Timestretch über ffmpegs `rubberband`-Filter statt über `pyrubberband`.** Die Spec nennt pyrubberband namentlich, aber das ist nur ein dünner Wrapper um ein `rubberband`-CLI-Binary — und dieses Binary fehlt auf der Zielmaschine, ebenso wie das Python-Paket. Nachgeprüft: das installierte ffmpeg 8.1 hat `librubberband` einkompiliert und stellt den Filter bereit. Damit dieselbe Bibliothek, dieselbe Qualität, kein einziges neues Installationsartefakt auf der Maschine des Nutzers. Die Abweichung betrifft den Weg, nicht das Werkzeug.

**Mischung und EQ in numpy, nicht in einem ffmpeg-Filtergraph.** Die Spec sagt "Crossfades und EQ-Automation über ffmpeg". Dagegen sprechen drei Dinge: ein Filtergraph für dreizehn Tracks mit je drei Bändern und Hüllkurven wird unlesbar und praktisch nicht debuggbar; ffmpegs `equalizer` fährt Verstärkung über `sendcmd` nur stufig, während unsere `EqCurve` stetige Stützpunkte beschreibt; und ein Renderer, der für jeden Test einen Unterprozess startet, ist weder schnell noch gut prüfbar. Die Mischung ist Arithmetik über Samples — genau das, was numpy gut kann und was sich Sample für Sample gegen erwartete Werte prüfen lässt. Der Zeitdehner bleibt bei ffmpeg, weil dort echte Signalverarbeitung passiert, die wir nicht nachbauen wollen.

**Die Bandzerlegung rekonstruiert exakt.** `low = Tiefpass(x)`, `high = Hochpass(x)`, `mid = x - low - high`. Damit ist `low + mid + high == x` bis auf Fließkommarauschen, und ein Übergang mit flacher EQ-Kurve liefert das Eingangssignal unverändert zurück. Das ist der Test, der die ganze EQ-Stufe absichert.

**Die QA misst nach, sie rechnet nicht nach.** Sie liest `set.wav` und analysiert es, statt dem Mixplan zu glauben. Ein Renderer-Fehler, der den Plan korrekt umsetzt aber falsch schreibt, fällt nur so auf.

**Genau ein korrigierter Re-Render, und nur für den Pegel.** Spec Abschnitt 7. Eine Längenabweichung lässt sich durch erneutes Rendern nicht beheben — sie steht im Mixplan — und wandert deshalb als Vermerk in den Bericht. Ein zu hoher True Peak dagegen ist eine reine Verstärkungsfrage und wird einmal korrigiert.

## File Structure

**Neu:**

| Datei | Verantwortung |
|---|---|
| `djset/render/__init__.py` | Paketmarker |
| `djset/render/stretch.py` | Zeitdehnung einer Datei über ffmpeg |
| `djset/render/eq.py` | Bandzerlegung, Verstärkungshüllkurven, EQ-Anwendung |
| `djset/render/engine.py` | Stufe 8: den Mixplan ausführen → `set.wav` |
| `djset/render/tracklist.py` | Stufe 8: `tracklist.md` |
| `djset/qa.py` | Stufe 9: das fertige File nachmessen |
| `djset/report.py` | `qa-report.md` aus allen Artefakten |

**Geändert:**

| Datei | Änderung |
|---|---|
| `djset/critic.py` | Nachtrag aus Plan 2: `out_start` vor dem `continue` fortschreiben |
| `tests/fixtures/fake_client.py` | Nachtrag aus Plan 2: Antworten nach Promptinhalt schlüsseln |
| `djset/contracts.py` | `QaFinding`, `QaReport` |
| `djset/cli.py` | `build` läuft bis `set.wav` durch |
| `pyproject.toml` | scipy als Abhängigkeit |

---

### Task 1: Nachträge aus Plan 2

Zwei Befunde aus dem Abschlussreview von Plan 2, beide klein, beide in Dateien, die dieser Plan ohnehin anfasst. Sie kommen zuerst, damit die Tests der folgenden Tasks auf sauberem Grund stehen.

**Files:**
- Modify: `djset/critic.py`, `tests/fixtures/fake_client.py`, `tests/test_critic.py`
- Test: `tests/test_critic.py`

**Interfaces:**
- Consumes: nichts Neues
- Produces: `FakeClient` schlüsselt Antworten wahlweise nach Promptinhalt

- [ ] **Step 1: Den fallenden Test für den Critic schreiben**

Fehlt die Analyse zu einem Track, überspringt `continue` in den Prüfungen 5 und 6 die Fortschreibung von `out_start`. Jede Naht danach wird von einem veralteten Ursprung aus gemessen und erzeugt Unsinn — im Abschlussreview mit „−131,00 Takte" reproduziert.

```python
# tests/test_critic.py — ans Ende anfuegen
def test_a_missing_analysis_does_not_poison_the_following_seams(
    profile: Profile,
) -> None:
    """Ein fehlendes Analyse-Artefakt darf genau eine Beanstandung erzeugen.

    Frueher schob das `continue` den Ursprung der Ausgabe-Zeitleiste nicht
    weiter, sodass jede folgende Naht gegen einen veralteten Nullpunkt gemessen
    wurde - mit absurden negativen Ueberlappungen als Ergebnis.
    """
    tracks = many(6)
    plan = _plan(tracks, profile)
    # Die Analyse des DRITTEN Tracks entfernen, nicht des letzten: nur dann
    # liegen ueberhaupt Naehte dahinter, die vergiftet werden koennten.
    ohne = [t for t in tracks if t.id != tracks[2].id]

    verdict = critique(plan, ohne, profile)

    kinds = [c.kind for c in verdict.complaints]
    assert kinds.count("unknown_track") == 1
    assert all(
        "-" not in c.text.split("tatsaechlich liegen nur")[-1]
        for c in verdict.complaints
        if c.kind == "overlap"
    )
```

- [ ] **Step 2: Testlauf, muss fehlschlagen**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_critic.py::test_a_missing_analysis_does_not_poison_the_following_seams -v`
Expected: FAIL — eine `overlap`-Beanstandung mit negativer Taktzahl

- [ ] **Step 3: Den Critic korrigieren**

In `djset/critic.py`, in der Schleife über die Übergänge: `out_start` **vor** dem `continue` fortschreiben, damit der Ursprung der Zeitleiste auch dann weiterläuft, wenn eine Analyse fehlt.

```python
        track = by_id.get(transition.from_track)
        if track is None:
            # Der Ursprung muss trotzdem weiterlaufen, sonst misst jede
            # folgende Naht gegen einen veralteten Nullpunkt.
            out_start = transition.start_at_s
            continue
```

- [ ] **Step 4: `FakeClient` nach Promptinhalt schlüsseln**

`analyse_all` verteilt über vier Threads, also ist bei Antworten nach Aufrufreihenfolge nicht steuerbar, welcher Track welche bekommt. In Produktion harmlos — jeder Aufruf trägt seinen eigenen Prompt —, im Test ein Stolperstein.

```python
# tests/fixtures/fake_client.py — erweitern, bestehende Signatur bleibt gueltig
class FakeClient:
    """Ein Modell-Ersatz ohne Netz.

    Zwei Betriebsarten. Mit einer Liste beantwortet er Aufrufe der Reihe nach.
    Mit einer Zuordnung sucht er zu jedem Prompt den ersten Schluessel, der
    darin vorkommt, und antwortet damit - das braucht es, sobald mehrere
    Threads gleichzeitig fragen, weil die Reihenfolge dann nicht mehr feststeht.
    """

    def __init__(
        self, responses: list[str | BaseException] | dict[str, str]
    ) -> None:
        self._by_key = responses if isinstance(responses, dict) else None
        self._responses = list(responses) if self._by_key is None else []
        self._lock = threading.Lock()
        self.prompts: list[str] = []
        self.calls = 0

    def complete(self, prompt: str, schema: dict[str, object]) -> str:
        with self._lock:
            self.prompts.append(prompt)
            self.calls += 1
            if self._by_key is not None:
                for key, answer in self._by_key.items():
                    if key in prompt:
                        return answer
                raise AssertionError(
                    f"Kein Schluessel des FakeClient kommt im Prompt vor: "
                    f"{sorted(self._by_key)}"
                )
            if not self._responses:
                raise AssertionError("FakeClient wurde oefter gerufen als vorbereitet")
            answer = self._responses.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer
```

Braucht `import threading` am Dateikopf. Die bestehende Listenform bleibt unverändert gültig — kein bestehender Test darf angefasst werden.

- [ ] **Step 5: Test für die neue Betriebsart**

```python
# tests/test_analysis.py — ans Ende anfuegen
def test_responses_can_be_keyed_by_prompt(tmp_path: Path, profile: Profile) -> None:
    """Bei vier Threads steht die Aufrufreihenfolge nicht fest - der Prompt schon."""
    run = _run(tmp_path)
    files = _files(tmp_path, 3)
    client = FakeClient(
        {
            "t0": json.dumps({"usable": True, "notes": "A.", "energy_level": 0.2,
                              "remeasure_with_bpm": None}),
            "t1": json.dumps({"usable": True, "notes": "B.", "energy_level": 0.5,
                              "remeasure_with_bpm": None}),
            "t2": json.dumps({"usable": True, "notes": "C.", "energy_level": 0.8,
                              "remeasure_with_bpm": None}),
        }
    )

    analyses, _ = analyse_all(client, files, profile, run, _budget())

    energies = {a.id: a.verdict.energy_level for a in analyses}
    assert energies == {"t0": 0.2, "t1": 0.5, "t2": 0.8}
```

Der Prompt enthält die Track-ID nicht zwangsläufig — prüfe das und ergänze sie im Prompt des Analysten, falls sie fehlt. Ein Prompt, der nicht sagt, über welchen Track er spricht, ist ohnehin eine Lücke.

- [ ] **Step 6: Gesamtsuite und Commit**

```bash
./.venv/Scripts/python.exe -m pytest -q
```

```bash
git add djset/critic.py tests/fixtures/fake_client.py tests/test_critic.py tests/test_analysis.py && git commit -m "fix: Nachtraege aus dem Abschlussreview von Plan 2"
```

---

### Task 2: Zeitdehnung über ffmpeg

Die einzige Stelle, an der dieser Plan einen Unterprozess startet.

**Files:**
- Create: `djset/render/__init__.py`, `djset/render/stretch.py`
- Modify: `pyproject.toml` (scipy)
- Test: `tests/render/__init__.py`, `tests/render/test_stretch.py`

**Interfaces:**
- Consumes: ffmpeg auf dem Pfad
- Produces: `stretch(source: Path, target: Path, ratio: float, sr: int = SR) -> None`, `RenderToolMissing(RuntimeError)`, `ffmpeg_path() -> str`

- [ ] **Step 1: Den fallenden Test schreiben**

```python
# tests/render/test_stretch.py
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from djset.contracts import MAX_RATIO, MIN_RATIO
from djset.render.stretch import stretch


def _tone(path: Path, seconds: float, sr: int = 44_100) -> Path:
    t = np.linspace(0.0, seconds, int(seconds * sr), endpoint=False)
    signal = (0.3 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
    sf.write(path, signal, sr)
    return path


def _duration(path: Path) -> float:
    with sf.SoundFile(path) as handle:
        return len(handle) / handle.samplerate


@pytest.mark.parametrize("ratio", [MIN_RATIO, 1.0, MAX_RATIO])
def test_the_output_is_shorter_by_exactly_the_ratio(
    tmp_path: Path, ratio: float
) -> None:
    source = _tone(tmp_path / "in.wav", 4.0)
    target = tmp_path / "out.wav"

    stretch(source, target, ratio)

    expected = 4.0 / ratio
    assert abs(_duration(target) - expected) < 0.05


def test_a_ratio_of_one_leaves_the_length_alone(tmp_path: Path) -> None:
    source = _tone(tmp_path / "in.wav", 2.0)
    target = tmp_path / "out.wav"

    stretch(source, target, 1.0)

    assert abs(_duration(target) - 2.0) < 0.02


def test_the_pitch_survives_the_stretch(tmp_path: Path) -> None:
    """Zeitdehnung, nicht Abspielgeschwindigkeit: 440 Hz bleiben 440 Hz."""
    source = _tone(tmp_path / "in.wav", 4.0)
    target = tmp_path / "out.wav"

    stretch(source, target, MAX_RATIO)

    signal, sr = sf.read(target, dtype="float32")
    spectrum = np.abs(np.fft.rfft(signal))
    peak_hz = float(np.fft.rfftfreq(len(signal), 1 / sr)[int(np.argmax(spectrum))])
    assert abs(peak_hz - 440.0) < 5.0


def test_the_same_input_renders_bit_identically(tmp_path: Path) -> None:
    """Determinismus ist die Zusage des Spec: gleicher Mixplan, gleiches WAV."""
    source = _tone(tmp_path / "in.wav", 2.0)
    first, second = tmp_path / "a.wav", tmp_path / "b.wav"

    stretch(source, first, 1.03)
    stretch(source, second, 1.03)

    assert first.read_bytes() == second.read_bytes()


def test_an_out_of_range_ratio_is_refused(tmp_path: Path) -> None:
    source = _tone(tmp_path / "in.wav", 1.0)

    with pytest.raises(ValueError, match="0.94"):
        stretch(source, tmp_path / "out.wav", 1.5)
```

- [ ] **Step 2: Testlauf, muss fehlschlagen**

Run: `./.venv/Scripts/python.exe -m pytest tests/render/test_stretch.py -v`
Expected: FAIL mit `ModuleNotFoundError` für `djset.render.stretch`

- [ ] **Step 3: Implementieren**

```python
# djset/render/stretch.py
"""Zeitdehnung ueber ffmpegs rubberband-Filter.

Die Spec nennt pyrubberband. Das ist ein duenner Wrapper um ein
rubberband-CLI-Binary, das auf dieser Maschine nicht existiert - waehrend das
installierte ffmpeg librubberband einkompiliert hat und den Filter bereitstellt.
Gleiche Bibliothek, gleiche Qualitaet, kein neues Installationsartefakt. Die
Abweichung betrifft den Weg, nicht das Werkzeug.

Dies ist die einzige Stelle im Renderer, die einen Unterprozess startet. Alles
andere ist Arithmetik ueber Samples und bleibt in numpy - siehe eq.py.
"""

import shutil
import subprocess
from pathlib import Path

from djset.contracts import MAX_RATIO, MIN_RATIO

SR = 44_100


class RenderToolMissing(RuntimeError):
    """Ein Werkzeug, das der Renderer braucht, liegt nicht auf dem Pfad.

    Bewusst getrennt von RenderFailed: das hier ist eine Frage der Umgebung,
    die der Nutzer beheben kann. Ein gescheiterter Lauf ist etwas anderes.
    """


class RenderFailed(RuntimeError):
    """ffmpeg lief und scheiterte.

    Der Renderer ist deterministisch - laut Spec Abschnitt 7 ist ein Fehler
    hier ein Bug und kein Wiederholungsfall. Er darf nicht als Umgebungsproblem
    durchgehen, sonst sucht der Nutzer an der falschen Stelle.
    """


def ffmpeg_path() -> str:
    """Der Pfad zu ffmpeg, oder ein klarer Fehler statt eines Absturzes."""
    found = shutil.which("ffmpeg")
    if found is None:
        raise RenderToolMissing(
            "ffmpeg wurde nicht gefunden. Der Renderer braucht ffmpeg mit "
            "einkompiliertem librubberband; installiere es und lege es auf den Pfad."
        )
    return found


def stretch(source: Path, target: Path, ratio: float, sr: int = SR) -> None:
    """Zieht eine Audiodatei auf ein anderes Tempo, ohne die Tonhoehe zu aendern.

    `ratio` ist Ziel-Tempo geteilt durch Quell-Tempo, genau wie
    MixEntry.timestretch_ratio - die Ausgabedauer ist also Quelldauer geteilt
    durch ratio. ffmpegs `tempo` bedeutet dasselbe, deshalb geht der Wert
    unveraendert durch.
    """
    if not MIN_RATIO <= ratio <= MAX_RATIO:
        raise ValueError(
            f"Stretch-Verhaeltnis {ratio} liegt ausserhalb von "
            f"{MIN_RATIO} bis {MAX_RATIO}"
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg_path(),
        "-hide_banner",
        "-loglevel", "error",
        "-nostdin",
        "-y",
        "-i", str(source),
        "-filter:a", f"rubberband=tempo={ratio:.10f}",
        "-ar", str(sr),
        "-c:a", "pcm_f32le",
        str(target),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RenderFailed(
            f"ffmpeg scheiterte beim Dehnen von {source.name}: "
            f"{result.stderr.strip() or 'keine Meldung'}"
        )
```

`djset/render/__init__.py` bleibt leer bis auf einen Modul-Docstring.

In `pyproject.toml` scipy zu den Abhängigkeiten hinzufügen — Task 3 braucht es.

- [ ] **Step 4: Testlauf, muss durchlaufen**

Run: `./.venv/Scripts/python.exe -m pytest tests/render/test_stretch.py -v`
Expected: 7 passed

- [ ] **Step 5: Gesamtsuite und Commit**

```bash
./.venv/Scripts/python.exe -m pytest -q
```

```bash
git add djset/render/ tests/render/ pyproject.toml && git commit -m "feat(render): Zeitdehnung ueber ffmpegs rubberband-Filter"
```

---

### Task 3: Bandzerlegung und Verstärkungshüllkurven

Die EQ-Automation, die die `EqCurve` eines Übergangs auf echte Samples abbildet.

**Files:**
- Create: `djset/render/eq.py`
- Test: `tests/render/test_eq.py`

**Interfaces:**
- Consumes: `djset.contracts.EqCurve`, `djset.contracts.EqPoint`
- Produces: `split_bands(signal, sr) -> tuple[np.ndarray, np.ndarray, np.ndarray]`, `envelope(points, samples) -> np.ndarray`, `apply_eq(signal, sr, curve) -> np.ndarray`, `LOW_CROSSOVER_HZ`, `HIGH_CROSSOVER_HZ`

- [ ] **Step 1: Den fallenden Test schreiben**

```python
# tests/render/test_eq.py
import numpy as np

from djset.contracts import EqCurve, EqPoint
from djset.render.eq import apply_eq, envelope, split_bands

SR = 44_100


def _noise(seconds: float = 1.0) -> np.ndarray:
    generator = np.random.default_rng(7)
    return generator.standard_normal(int(seconds * SR)).astype(np.float32) * 0.1


def _flat(gain_db: float = 0.0) -> EqCurve:
    points = [EqPoint(position=0.0, gain_db=gain_db),
              EqPoint(position=1.0, gain_db=gain_db)]
    return EqCurve(low=points, mid=points, high=points)


def test_the_three_bands_add_back_up_to_the_original() -> None:
    """Die Zerlegung rekonstruiert exakt - darauf ruht die ganze EQ-Stufe."""
    signal = _noise()

    low, mid, high = split_bands(signal, SR)

    assert np.allclose(low + mid + high, signal, atol=1e-5)


def test_the_bands_actually_separate_frequencies() -> None:
    t = np.linspace(0.0, 1.0, SR, endpoint=False)
    bass = np.sin(2 * np.pi * 60.0 * t).astype(np.float32)

    low, mid, high = split_bands(bass, SR)

    assert float(np.max(np.abs(low))) > 0.8
    assert float(np.max(np.abs(high))) < 0.1


def test_a_flat_curve_returns_the_signal_unchanged() -> None:
    signal = _noise()

    assert np.allclose(apply_eq(signal, SR, _flat()), signal, atol=1e-5)


def test_full_attenuation_silences_the_signal() -> None:
    signal = _noise()

    result = apply_eq(signal, SR, _flat(gain_db=-60.0))

    assert float(np.max(np.abs(result))) < 0.01 * float(np.max(np.abs(signal)))


def test_the_envelope_interpolates_between_its_points() -> None:
    points = [EqPoint(position=0.0, gain_db=-20.0),
              EqPoint(position=1.0, gain_db=0.0)]

    values = envelope(points, 101)

    assert len(values) == 101
    assert abs(values[0] - 10 ** (-20 / 20)) < 1e-6
    assert abs(values[-1] - 1.0) < 1e-6
    assert values[50] > values[10]


def test_the_envelope_holds_before_the_first_and_after_the_last_point() -> None:
    points = [EqPoint(position=0.25, gain_db=-6.0),
              EqPoint(position=0.75, gain_db=0.0)]

    values = envelope(points, 100)

    assert abs(values[0] - 10 ** (-6 / 20)) < 1e-6
    assert abs(values[-1] - 1.0) < 1e-6


def test_a_ramp_down_really_falls() -> None:
    signal = np.ones(SR, dtype=np.float32) * 0.5
    curve = EqCurve(
        low=[EqPoint(position=0.0, gain_db=0.0), EqPoint(position=1.0, gain_db=-60.0)],
        mid=[EqPoint(position=0.0, gain_db=0.0), EqPoint(position=1.0, gain_db=-60.0)],
        high=[EqPoint(position=0.0, gain_db=0.0), EqPoint(position=1.0, gain_db=-60.0)],
    )

    result = apply_eq(signal, SR, curve)

    assert abs(float(result[0]) - 0.5) < 0.02
    assert abs(float(result[-1])) < 0.01
```

- [ ] **Step 2: Testlauf, muss fehlschlagen**

Run: `./.venv/Scripts/python.exe -m pytest tests/render/test_eq.py -v`
Expected: FAIL mit `ModuleNotFoundError` für `djset.render.eq`

- [ ] **Step 3: Implementieren**

```python
# djset/render/eq.py
"""Bandzerlegung und Verstaerkungshuellkurven fuer die Uebergaenge.

Bewusst in numpy statt in einem ffmpeg-Filtergraph. Drei Gruende: ein
Filtergraph fuer dreizehn Tracks mit je drei Baendern und Huellkurven waere
unlesbar und praktisch nicht zu debuggen; ffmpegs equalizer faehrt Verstaerkung
ueber sendcmd nur stufig, waehrend eine EqCurve stetige Stuetzpunkte
beschreibt; und ein Renderer, der fuer jeden Test einen Unterprozess startet,
ist weder schnell noch gut pruefbar. Das hier ist Arithmetik ueber Samples.

Die Zerlegung rekonstruiert exakt: mid ist definiert als das, was nach Abzug
von low und high uebrigbleibt. Damit liefert eine flache Kurve das
Eingangssignal unveraendert zurueck, und genau das ist der Test, auf dem die
ganze Stufe ruht.
"""

import numpy as np
from scipy import signal as sig

from djset.contracts import EqCurve, EqPoint

# Trennfrequenzen wie an einem DJ-Mixer: der Bass unter 200 Hz, die Hoehen ab
# 4 kHz, alles dazwischen sind die Mitten.
LOW_CROSSOVER_HZ = 200.0
HIGH_CROSSOVER_HZ = 4_000.0

_ORDER = 4


def _sos(cutoff_hz: float, sr: int, kind: str) -> np.ndarray:
    return sig.butter(_ORDER, cutoff_hz / (sr / 2), btype=kind, output="sos")


def split_bands(
    signal: np.ndarray, sr: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Zerlegt ein Signal in Tiefen, Mitten und Hoehen.

    Nullphasig gefiltert (sosfiltfilt), damit die Baender beim Summieren nicht
    gegeneinander verschoben sind. Fuer Offline-Rendering ist die Nichtkausalitaet
    ohne Belang.
    """
    low = sig.sosfiltfilt(_sos(LOW_CROSSOVER_HZ, sr, "lowpass"), signal)
    high = sig.sosfiltfilt(_sos(HIGH_CROSSOVER_HZ, sr, "highpass"), signal)
    mid = signal - low - high
    return low.astype(np.float32), mid.astype(np.float32), high.astype(np.float32)


def envelope(points: list[EqPoint], samples: int) -> np.ndarray:
    """Baut aus Stuetzpunkten eine lineare Verstaerkungshuellkurve.

    Positionen laufen von 0 bis 1 ueber die Laenge des Uebergangs. Vor dem
    ersten und nach dem letzten Stuetzpunkt wird gehalten, nicht extrapoliert -
    eine Kurve, die erst bei 0,25 anfaengt, faengt eben mit ihrem ersten Wert an.
    """
    if samples <= 0:
        return np.zeros(0, dtype=np.float32)
    positions = np.array([p.position for p in points], dtype=np.float64)
    gains = np.array([10 ** (p.gain_db / 20.0) for p in points], dtype=np.float64)
    order = np.argsort(positions)
    grid = np.linspace(0.0, 1.0, samples, endpoint=True)
    return np.interp(grid, positions[order], gains[order]).astype(np.float32)


# Unterhalb dieser Laenge kann sosfiltfilt nicht arbeiten: die nullphasige
# Filterung braucht mehr Samples als ihre Randlaenge (padlen = 15 bei
# Ordnung 4). Gemessen, nicht geschaetzt.
_MIN_FILTER_SAMPLES = 16


def apply_eq(signal: np.ndarray, sr: int, curve: EqCurve) -> np.ndarray:
    """Legt die drei Baender-Huellkurven ueber ein Signal.

    Ist das Fenster zu kurz zum Filtern, wird stattdessen der Mittelwert der
    drei Huellkurven als Breitbandverstaerkung angelegt. Bei 44,1 kHz sind das
    unter 0,36 Millisekunden - eine bandselektive Bearbeitung waere dort ohnehin
    bedeutungslos, und einen ganzen Lauf daran scheitern zu lassen waere die
    falsche Abwaegung. Fuer drei gleiche Kurven liefert der Rueckfall exakt
    dasselbe wie der normale Weg.

    Dass so ein Fenster ueberhaupt entstehen kann, haengt nicht an
    Transition.bars: der Renderer deckelt die Fensterbreite mit der Laenge des
    Abschnitts, und MixEntry erzwingt nur play_from_s < play_to_s, keine
    Mindestdauer. Ein handgeschriebener Mixplan - laut Spec ein unterstuetzter
    Testpfad - kommt also darunter.
    """
    if signal.size == 0:
        return signal.astype(np.float32)

    if signal.size < _MIN_FILTER_SAMPLES:
        broadband = (
            envelope(curve.low, signal.size)
            + envelope(curve.mid, signal.size)
            + envelope(curve.high, signal.size)
        ) / 3.0
        return (signal * broadband).astype(np.float32)

    low, mid, high = split_bands(signal, sr)
    return (
        low * envelope(curve.low, signal.size)
        + mid * envelope(curve.mid, signal.size)
        + high * envelope(curve.high, signal.size)
    ).astype(np.float32)
```

- [ ] **Step 4: Testlauf, muss durchlaufen**

Run: `./.venv/Scripts/python.exe -m pytest tests/render/test_eq.py -v`
Expected: 7 passed

- [ ] **Step 5: Gesamtsuite und Commit**

```bash
./.venv/Scripts/python.exe -m pytest -q
```

```bash
git add djset/render/eq.py tests/render/test_eq.py && git commit -m "feat(render): Bandzerlegung und Verstaerkungshuellkurven"
```

---

### Task 4: Der Renderer

Stufe 8. Führt den Mixplan aus und trifft dabei keine einzige eigene Entscheidung.

**Files:**
- Create: `djset/render/engine.py`
- Test: `tests/render/test_engine.py`

**Interfaces:**
- Consumes: `djset.contracts.MixPlan`, `djset.render.stretch.stretch`, `djset.render.eq.apply_eq`, `djset.dsp.analyze.BEATS_PER_BAR`
- Produces: `render(plan, sources, target, master_gain_db=0.0, sr=SR) -> None`, `MissingSource(RuntimeError)`, `bar_length_s(target_bpm) -> float`

**Das Modell der Zeitleiste — dieselbe Rechnung wie in Stufe 6:**

- Eintrag 0 beginnt bei 0. Eintrag *i+1* beginnt bei `transitions[i].start_at_s`.
- Ein Eintrag klingt `(play_to_s − play_from_s) / timestretch_ratio` Sekunden lang.
- Der Übergang *i* dauert `bars × 4 × 60 / target_bpm` Sekunden und beginnt genau da, wo Eintrag *i+1* einsetzt.
- `eq_out` liegt über den letzten `overlap` Sekunden von Eintrag *i*, `eq_in` über den ersten `overlap` Sekunden von Eintrag *i+1*. Dazwischen läuft jeder Track flach.

- [ ] **Step 1: Den fallenden Test schreiben**

```python
# tests/render/test_engine.py
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from djset.contracts import (
    EqCurve,
    EqPoint,
    MixEntry,
    MixPlan,
    Transition,
)
from djset.render.engine import MissingSource, bar_length_s, render

SR = 44_100


def _tone(path: Path, seconds: float, hz: float = 220.0) -> Path:
    t = np.linspace(0.0, seconds, int(seconds * SR), endpoint=False)
    sf.write(path, (0.4 * np.sin(2 * np.pi * hz * t)).astype(np.float32), SR)
    return path


def _flat(gain_db: float = 0.0) -> EqCurve:
    points = [EqPoint(position=0.0, gain_db=gain_db),
              EqPoint(position=1.0, gain_db=gain_db)]
    return EqCurve(low=points, mid=points, high=points)


def _plan(bars: int = 1, target_bpm: float = 174.0) -> MixPlan:
    overlap = bars * bar_length_s(target_bpm)
    entries = [
        MixEntry(track_id=f"t{i}", play_from_s=0.0, play_to_s=10.0,
                 timestretch_ratio=1.0, gain_db=0.0)
        for i in range(3)
    ]
    starts = [10.0 - overlap, 2 * (10.0 - overlap)]
    transitions = [
        Transition(from_track=f"t{i}", to_track=f"t{i + 1}", start_at_s=starts[i],
                   bars=bars, technique="blend", eq_out=_flat(), eq_in=_flat())
        for i in range(2)
    ]
    return MixPlan(
        target_length_s=starts[-1] + 10.0,
        target_bpm=target_bpm,
        entries=entries,
        transitions=transitions,
    )


def _sources(tmp_path: Path) -> dict[str, Path]:
    return {f"t{i}": _tone(tmp_path / f"t{i}.wav", 10.0, hz=220.0 * (i + 1))
            for i in range(3)}


def test_the_output_has_the_planned_length(tmp_path: Path) -> None:
    plan = _plan()
    target = tmp_path / "set.wav"

    render(plan, _sources(tmp_path), target)

    with sf.SoundFile(target) as handle:
        assert abs(len(handle) / handle.samplerate - plan.target_length_s) < 0.05


def test_the_output_is_not_silent_anywhere(tmp_path: Path) -> None:
    """Keine ungewollten Luecken - jede halbe Sekunde traegt Signal."""
    plan = _plan()
    target = tmp_path / "set.wav"

    render(plan, _sources(tmp_path), target)

    signal, sr = sf.read(target, dtype="float32")
    window = sr // 2
    blocks = [signal[i:i + window] for i in range(0, len(signal) - window, window)]
    assert all(float(np.max(np.abs(b))) > 0.01 for b in blocks)


def test_the_same_plan_renders_bit_identically(tmp_path: Path) -> None:
    plan = _plan()
    sources = _sources(tmp_path)

    render(plan, sources, tmp_path / "a.wav")
    render(plan, sources, tmp_path / "b.wav")

    assert (tmp_path / "a.wav").read_bytes() == (tmp_path / "b.wav").read_bytes()


def test_the_master_gain_scales_the_whole_set(tmp_path: Path) -> None:
    plan = _plan()
    sources = _sources(tmp_path)
    render(plan, sources, tmp_path / "loud.wav")
    render(plan, sources, tmp_path / "quiet.wav", master_gain_db=-6.0)

    loud, _ = sf.read(tmp_path / "loud.wav", dtype="float32")
    quiet, _ = sf.read(tmp_path / "quiet.wav", dtype="float32")
    ratio = float(np.max(np.abs(quiet))) / float(np.max(np.abs(loud)))
    assert abs(ratio - 10 ** (-6 / 20)) < 0.02


def test_a_missing_source_is_named(tmp_path: Path) -> None:
    plan = _plan()
    sources = _sources(tmp_path)
    del sources["t1"]

    with pytest.raises(MissingSource, match="t1"):
        render(plan, sources, tmp_path / "set.wav")


def test_the_bar_length_follows_the_target_tempo() -> None:
    assert abs(bar_length_s(174.0) - 4 * 60.0 / 174.0) < 1e-9
    assert abs(bar_length_s(120.0) - 2.0) < 1e-9
```

- [ ] **Step 2: Testlauf, muss fehlschlagen**

Run: `./.venv/Scripts/python.exe -m pytest tests/render/test_engine.py -v`
Expected: FAIL mit `ModuleNotFoundError` für `djset.render.engine`

- [ ] **Step 3: Implementieren**

```python
# djset/render/engine.py
"""Stufe 8: fuehrt den Mixplan aus.

Der Renderer trifft keine eigenen Entscheidungen - das ist die
Determinismus-Grenze der Spec: gleicher Mixplan, gleiches WAV. Jede Zahl, nach
der er handelt, steht im Plan. Findet sich beim Bauen eine Groesse, die er
selbst bestimmen muesste, ist das ein Befund ueber den Mixplan.

Die Zeitleiste ist dieselbe wie in Stufe 6: Eintrag 0 beginnt bei null, jeder
weitere bei start_at_s seines eingehenden Uebergangs, und ein Eintrag klingt
(play_to_s - play_from_s) / timestretch_ratio Sekunden lang.
"""

import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from djset.contracts import EqCurve, MixPlan
from djset.dsp.analyze import BEATS_PER_BAR
from djset.render.eq import apply_eq
from djset.render.stretch import SR, stretch


class MissingSource(RuntimeError):
    """Zu einem Eintrag des Mixplans fehlt die Audiodatei."""


def bar_length_s(target_bpm: float) -> float:
    """Wie lang ein Takt im fertigen Set dauert."""
    return BEATS_PER_BAR * 60.0 / target_bpm


def _load_mono(path: Path, sr: int) -> np.ndarray:
    signal, file_sr = sf.read(path, dtype="float32", always_2d=True)
    mono = signal.mean(axis=1)
    if file_sr != sr:
        raise MissingSource(
            f"{path.name} liegt mit {file_sr} Hz vor, gebraucht werden {sr} Hz"
        )
    return mono.astype(np.float32)


def _segment(source: Path, entry: MixEntry, sr: int, work: Path) -> np.ndarray:
    """Der gedehnte, verstaerkte Abschnitt eines Eintrags."""
    signal = _load_mono(source, sr)
    start = int(round(entry.play_from_s * sr))
    stop = min(len(signal), int(round(entry.play_to_s * sr)))
    piece = signal[start:stop]

    if entry.timestretch_ratio != 1.0:
        # Die Schnittdatei entsteht nur, wenn wirklich gedehnt wird. Sie immer
        # zu schreiben waere bei dreizehn Tracks eines Sechzig-Minuten-Sets ein
        # voller ueberfluessiger Schreibdurchlauf ueber das gesamte Material.
        cut = work / f"{entry.track_id}-cut.wav"
        out = work / f"{entry.track_id}-stretched.wav"
        sf.write(cut, piece, sr)
        stretch(cut, out, entry.timestretch_ratio, sr=sr)
        piece = _load_mono(out, sr)

    return piece * (10 ** (entry.gain_db / 20.0))


def render(
    plan: MixPlan,
    sources: dict[str, Path],
    target: Path,
    master_gain_db: float = 0.0,
    sr: int = SR,
) -> None:
    """Rendert den Mixplan in eine einzelne Audiodatei."""
    missing = [e.track_id for e in plan.entries if e.track_id not in sources]
    if missing:
        raise MissingSource(
            f"Zu diesen Eintraegen fehlt die Audiodatei: {', '.join(missing)}"
        )

    bar = bar_length_s(plan.target_bpm)
    starts = [0.0] + [t.start_at_s for t in plan.transitions]
    total = int(round(plan.target_length_s * sr))
    mix = np.zeros(total, dtype=np.float32)

    with tempfile.TemporaryDirectory() as raw:
        work = Path(raw)
        for index, entry in enumerate(plan.entries):
            block = _segment(sources[entry.track_id], entry, sr, work)

            # Der eingehende Uebergang faerbt den Kopf, der ausgehende den
            # Schwanz. Dazwischen laeuft der Track flach.
            if index > 0:
                incoming = plan.transitions[index - 1]
                width = min(block.size, int(round(incoming.bars * bar * sr)))
                if width > 0:
                    block[:width] = apply_eq(block[:width], sr, incoming.eq_in)
            if index < len(plan.transitions):
                outgoing = plan.transitions[index]
                width = min(block.size, int(round(outgoing.bars * bar * sr)))
                if width > 0:
                    # Ohne diese Bedingung waere block[-0:] das GANZE Array,
                    # nicht das leere - eine klassische Python-Falle.
                    block[-width:] = apply_eq(block[-width:], sr, outgoing.eq_out)

            offset = int(round(starts[index] * sr))
            end = min(total, offset + block.size)
            if end > offset:
                mix[offset:end] += block[: end - offset]

    mix *= 10 ** (master_gain_db / 20.0)
    target.parent.mkdir(parents=True, exist_ok=True)
    sf.write(target, mix, sr, subtype="FLOAT")
```

Der Import von `EqCurve` wird dann nicht mehr gebraucht — nimm ihn aus der Importliste, bevor du committest.

- [ ] **Step 4: Testlauf, muss durchlaufen**

Run: `./.venv/Scripts/python.exe -m pytest tests/render/test_engine.py -v`
Expected: 6 passed

- [ ] **Step 5: Gesamtsuite und Commit**

```bash
./.venv/Scripts/python.exe -m pytest -q
```

```bash
git add djset/render/engine.py tests/render/test_engine.py && git commit -m "feat(render): Mixplan zu set.wav ausfuehren"
```

---

### Task 5: Tracklist

Erfolgskriterium 6: jeder Track mit Zeitstempel.

**Files:**
- Create: `djset/render/tracklist.py`
- Test: `tests/render/test_tracklist.py`

**Interfaces:**
- Consumes: `djset.contracts.MixPlan`, `djset.contracts.TrackAnalysis`
- Produces: `render_tracklist(plan, analyses) -> str`, `timestamp(seconds) -> str`

- [ ] **Step 1: Den fallenden Test schreiben**

```python
# tests/render/test_tracklist.py
from djset.render.tracklist import render_tracklist, timestamp
from tests.fixtures.analyses import many


def test_timestamps_are_minutes_and_seconds() -> None:
    assert timestamp(0.0) == "0:00"
    assert timestamp(61.4) == "1:01"
    assert timestamp(3599.0) == "59:59"
    assert timestamp(3661.0) == "61:01"


def test_every_track_appears_with_its_start(profile) -> None:
    from djset.agents.designer import TransitionChoice, build_mixplan
    from djset.contracts import Setlist

    tracks = many(4)
    setlist = Setlist(order=[t.id for t in tracks], note="")
    plan = build_mixplan(
        setlist,
        tracks,
        [TransitionChoice(technique="blend", bars=16) for _ in range(3)],
        profile,
    )

    text = render_tracklist(plan, {t.id: t for t in tracks})

    for track in tracks:
        assert track.id in text
    assert text.count("\n|") >= 4
    assert "0:00" in text


def test_the_starts_are_in_order(profile) -> None:
    from djset.agents.designer import TransitionChoice, build_mixplan
    from djset.contracts import Setlist

    tracks = many(4)
    setlist = Setlist(order=[t.id for t in tracks], note="")
    plan = build_mixplan(
        setlist,
        tracks,
        [TransitionChoice(technique="blend", bars=16) for _ in range(3)],
        profile,
    )

    text = render_tracklist(plan, {t.id: t for t in tracks})
    rows = [line for line in text.splitlines() if line.startswith("| ")][1:]
    starts = [row.split("|")[1].strip() for row in rows if ":" in row.split("|")[1]]

    assert starts == sorted(starts, key=lambda s: [int(p) for p in s.split(":")])
```

- [ ] **Step 2: Testlauf, muss fehlschlagen**

Run: `./.venv/Scripts/python.exe -m pytest tests/render/test_tracklist.py -v`
Expected: FAIL mit `ModuleNotFoundError`

- [ ] **Step 3: Implementieren**

```python
# djset/render/tracklist.py
"""Stufe 8: die Tracklist zum fertigen Set."""

from pathlib import Path

from djset.contracts import MixPlan, TrackAnalysis


def timestamp(seconds: float) -> str:
    """Minuten und Sekunden, wie man sie in eine Tracklist schreibt."""
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def render_tracklist(
    plan: MixPlan, analyses: dict[str, TrackAnalysis]
) -> str:
    """Die Tracklist als Markdown-Tabelle."""
    starts = [0.0] + [t.start_at_s for t in plan.transitions]
    lines = [
        f"# Tracklist ({timestamp(plan.target_length_s)}, {plan.target_bpm:.0f} BPM)",
        "",
        "| Zeit | Track | Tonart | Uebergang danach |",
        "| --- | --- | --- | --- |",
    ]
    for index, entry in enumerate(plan.entries):
        analysis = analyses.get(entry.track_id)
        key = analysis.key.camelot if analysis else "?"
        name = Path(analysis.path).stem if analysis else entry.track_id
        if index < len(plan.transitions):
            transition = plan.transitions[index]
            after = f"{transition.technique}, {transition.bars} Takte"
        else:
            after = "-"
        lines.append(
            f"| {timestamp(starts[index])} | {name} ({entry.track_id}) "
            f"| {key} | {after} |"
        )
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Testlauf, muss durchlaufen**

Run: `./.venv/Scripts/python.exe -m pytest tests/render/test_tracklist.py -v`
Expected: 3 passed

- [ ] **Step 5: Gesamtsuite und Commit**

```bash
./.venv/Scripts/python.exe -m pytest -q
```

```bash
git add djset/render/tracklist.py tests/render/test_tracklist.py && git commit -m "feat(render): Tracklist mit Zeitstempeln"
```

---

### Task 6: QA-Messungen

Stufe 9. Misst das fertige File nach, statt dem Mixplan zu glauben.

**Files:**
- Modify: `djset/contracts.py` (`QaFinding`, `QaReport`)
- Create: `djset/qa.py`
- Test: `tests/test_qa.py`

**Interfaces:**
- Consumes: `djset.dsp.analyze.measure_loudness`, `djset.dsp.analyze.detect_bpm_and_grid`, `djset.contracts.MixPlan`
- Produces: `QaFinding`, `QaReport`, `check_set(path, plan) -> QaReport`, `TRUE_PEAK_CEILING_DB`, `LENGTH_TOLERANCE`, `SEAM_BPM_TOLERANCE`, `GAP_SECONDS`

- [ ] **Step 1: Die Verträge ergänzen**

```python
# djset/contracts.py
QaKind = Literal["length", "true_peak", "seam", "gap"]


class QaFinding(BaseModel):
    kind: QaKind
    text: str = Field(min_length=1)


class QaReport(BaseModel):
    """Was die QA am fertigen File gemessen hat.

    `ok` heisst: nichts gefunden. Ein Befund verhindert die Auslieferung nicht -
    laut Spec wird nach hoechstens einem korrigierten Re-Render ausgeliefert und
    der Befund vermerkt.
    """

    length_s: float = Field(gt=0)
    target_length_s: float = Field(gt=0)
    lufs_integrated: float = Field(allow_inf_nan=False)
    true_peak: float = Field(allow_inf_nan=False)
    findings: list[QaFinding]

    @property
    def ok(self) -> bool:
        return not self.findings
```

- [ ] **Step 2: Den fallenden Test schreiben**

```python
# tests/test_qa.py
from pathlib import Path

import numpy as np
import soundfile as sf

from djset.contracts import MixPlan
from djset.qa import TRUE_PEAK_CEILING_DB, check_set
from djset.render.engine import render
from tests.fixtures.render import simple_plan, sources_for

SR = 44_100


def test_a_clean_render_has_no_findings(tmp_path: Path) -> None:
    plan = _plan()
    target = tmp_path / "set.wav"
    render(plan, _sources(tmp_path), target, master_gain_db=-12.0)

    report = check_set(target, plan)

    assert [f.kind for f in report.findings] == []
    assert report.ok is True


def test_a_too_loud_render_is_caught(tmp_path: Path) -> None:
    plan = _plan()
    target = tmp_path / "set.wav"
    render(plan, _sources(tmp_path), target, master_gain_db=+12.0)

    report = check_set(target, plan)

    assert any(f.kind == "true_peak" for f in report.findings)
    assert report.true_peak > TRUE_PEAK_CEILING_DB


def test_a_length_deviation_is_caught(tmp_path: Path) -> None:
    plan = _plan()
    target = tmp_path / "set.wav"
    render(plan, _sources(tmp_path), target, master_gain_db=-12.0)
    stretched = MixPlan.model_validate(
        plan.model_dump() | {"target_length_s": plan.target_length_s * 2}
    )

    report = check_set(target, stretched)

    assert any(f.kind == "length" for f in report.findings)


def test_a_silent_stretch_is_caught(tmp_path: Path) -> None:
    plan = _plan()
    target = tmp_path / "set.wav"
    render(plan, _sources(tmp_path), target, master_gain_db=-12.0)
    signal, sr = sf.read(target, dtype="float32")
    signal[int(1.0 * sr) : int(3.0 * sr)] = 0.0
    sf.write(target, signal, sr)

    report = check_set(target, plan)

    assert any(f.kind == "gap" for f in report.findings)


def test_the_report_carries_the_measured_numbers(tmp_path: Path) -> None:
    plan = _plan()
    target = tmp_path / "set.wav"
    render(plan, _sources(tmp_path), target, master_gain_db=-12.0)

    report = check_set(target, plan)

    assert abs(report.length_s - plan.target_length_s) < 0.1
    assert report.target_length_s == plan.target_length_s
    assert report.lufs_integrated < 0.0
    assert report.true_peak < 0.0
```

- [ ] **Step 3: Testlauf, muss fehlschlagen**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_qa.py -v`
Expected: FAIL mit `ModuleNotFoundError` für `djset.qa`

- [ ] **Step 4: Implementieren**

```python
# djset/qa.py
"""Stufe 9: prueft das fertige File.

Bewusst messend, nicht rechnend: sie liest set.wav und analysiert es, statt dem
Mixplan zu glauben. Ein Renderer-Fehler, der den Plan korrekt umsetzt aber
falsch schreibt, faellt nur so auf.
"""

import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from djset.contracts import MixPlan, QaFinding, QaReport
from djset.dsp.analyze import detect_bpm_and_grid, measure_loudness
from djset.render.engine import bar_length_s

# Erfolgskriterium 3: Ziellaenge plus/minus 5 Prozent.
LENGTH_TOLERANCE = 0.05

# Erfolgskriterium 5: hoechstens minus 1 dBTP.
TRUE_PEAK_CEILING_DB = -1.0

# Erfolgskriterium 4: an der Naht darf das Tempo nicht wegbrechen. Ein Fenster
# um jede Naht wird nachanalysiert; weicht das gemessene Tempo weiter als das
# ab, laufen die beiden Tracks dort nicht mehr zusammen.
SEAM_BPM_TOLERANCE = 3.0
SEAM_WINDOW_BARS = 8

# Laenger als das darf nirgends Stille stehen.
GAP_SECONDS = 0.5
_GAP_FLOOR = 10 ** (-60.0 / 20.0)


def _gaps(signal: np.ndarray, sr: int) -> list[float]:
    """Anfaenge aller Stillen, die laenger als GAP_SECONDS dauern."""
    quiet = np.abs(signal) < _GAP_FLOOR
    if not quiet.any():
        return []
    edges = np.diff(quiet.astype(np.int8))
    starts = list(np.flatnonzero(edges == 1) + 1)
    stops = list(np.flatnonzero(edges == -1) + 1)
    if quiet[0]:
        starts.insert(0, 0)
    if quiet[-1]:
        stops.append(len(quiet))
    needed = int(GAP_SECONDS * sr)
    return [start / sr for start, stop in zip(starts, stops) if stop - start >= needed]


def check_set(path: Path, plan: MixPlan) -> QaReport:
    """Misst das fertige Set und sammelt, was nicht stimmt."""
    signal, sr = sf.read(path, dtype="float32", always_2d=True)
    mono = signal.mean(axis=1)
    length_s = len(mono) / sr
    loudness = measure_loudness(path)
    findings: list[QaFinding] = []

    low = plan.target_length_s * (1.0 - LENGTH_TOLERANCE)
    high = plan.target_length_s * (1.0 + LENGTH_TOLERANCE)
    if not low <= length_s <= high:
        findings.append(
            QaFinding(
                kind="length",
                text=f"Das Set ist {length_s / 60:.1f} Minuten lang, geplant waren "
                f"{plan.target_length_s / 60:.1f} plus/minus "
                f"{LENGTH_TOLERANCE * 100:.0f} Prozent.",
            )
        )

    if loudness.true_peak > TRUE_PEAK_CEILING_DB:
        findings.append(
            QaFinding(
                kind="true_peak",
                text=f"True Peak liegt bei {loudness.true_peak:.1f} dB, erlaubt sind "
                f"hoechstens {TRUE_PEAK_CEILING_DB:.1f} dB.",
            )
        )

    for start in _gaps(mono, sr):
        findings.append(
            QaFinding(
                kind="gap",
                text=f"Bei {start / 60:.0f}:{start % 60:04.1f} steht laenger als "
                f"{GAP_SECONDS:.1f} Sekunden Stille.",
            )
        )

    window_s = SEAM_WINDOW_BARS * bar_length_s(plan.target_bpm)
    # Die Ausschnitte gehen in ein Temp-Verzeichnis, nicht neben set.wav: der
    # Run-Ordner ist laut Spec das Fenster in die Entscheidungen des Laufs, und
    # ein Abbruch mittendrin soll dort keine Bruchstuecke hinterlassen.
    with tempfile.TemporaryDirectory() as raw:
        work = Path(raw)
        for index, transition in enumerate(plan.transitions):
            begin = max(0, int((transition.start_at_s - window_s / 2) * sr))
            end = min(len(mono), int((transition.start_at_s + window_s / 2) * sr))
            if end - begin < sr:
                continue
            seam = work / f"seam-{index}.wav"
            sf.write(seam, mono[begin:end], sr)
            try:
                bpm, _grid = detect_bpm_and_grid(seam)
            except Exception:  # noqa: BLE001 - eine unmessbare Naht ist selbst ein Befund
                bpm = None
            if bpm is None or abs(bpm.value - plan.target_bpm) > SEAM_BPM_TOLERANCE:
                gemessen = "nicht messbar" if bpm is None else f"{bpm.value:.1f} BPM"
                findings.append(
                    QaFinding(
                        kind="seam",
                        text=f"An der Naht von {transition.from_track} nach "
                        f"{transition.to_track} bei "
                        f"{transition.start_at_s / 60:.0f}:"
                        f"{transition.start_at_s % 60:04.1f} misst sich "
                        f"{gemessen}, erwartet waren {plan.target_bpm:.1f}.",
                    )
                )

    return QaReport(
        length_s=length_s,
        target_length_s=plan.target_length_s,
        lufs_integrated=loudness.lufs_integrated,
        true_peak=loudness.true_peak,
        findings=findings,
    )
```

- [ ] **Step 5: Testlauf, muss durchlaufen**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_qa.py -v`
Expected: 5 passed

Die Fixture-Tracks sind reine Sinustöne ohne Beat, also wird die Nahtprüfung dort anschlagen. Ist das der Fall, ersetze die Töne in `tests/render/test_engine.py` durch `click_track`-artiges Material mit erkennbarem Puls bei 174 BPM, damit die Nahtprüfung etwas zu messen hat. Berichte, was du gemessen hast, bevor du etwas änderst.

- [ ] **Step 6: Gesamtsuite und Commit**

```bash
./.venv/Scripts/python.exe -m pytest -q
```

```bash
git add djset/contracts.py djset/qa.py tests/test_qa.py && git commit -m "feat(qa): das fertige Set nachmessen"
```

---

### Task 7: QA-Bericht und der eine korrigierte Re-Render

Spec Abschnitt 7: „QA findet Clipping oder Längenabweichung → Ein einziger korrigierter Re-Render, danach Auslieferung mit Vermerk."

**Files:**
- Create: `djset/report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `djset.contracts.QaReport`, `Shortlist`, `CritiqueLog`, `UnreadableTrack`, `MixPlan`
- Produces: `correction_gain_db(report) -> float | None`, `render_qa_report(...) -> str`

- [ ] **Step 1: Den fallenden Test schreiben**

```python
# tests/test_report.py
from djset.contracts import QaFinding, QaReport, Rejection, Shortlist
from djset.report import correction_gain_db, render_qa_report


def _report(true_peak: float, findings: list[QaFinding]) -> QaReport:
    return QaReport(
        length_s=3600.0,
        target_length_s=3600.0,
        lufs_integrated=-9.0,
        true_peak=true_peak,
        findings=findings,
    )


def test_a_clean_report_needs_no_correction() -> None:
    assert correction_gain_db(_report(-2.0, [])) is None


def test_a_hot_render_gets_exactly_the_gain_it_needs() -> None:
    report = _report(+1.5, [QaFinding(kind="true_peak", text="zu laut")])

    gain = correction_gain_db(report)

    assert gain is not None
    assert abs(gain - (-1.0 - 1.5)) < 1e-6


def test_a_length_deviation_is_not_correctable_by_re_rendering() -> None:
    """Die Laenge steht im Mixplan - erneutes Rendern aendert daran nichts."""
    report = _report(-3.0, [QaFinding(kind="length", text="zu kurz")])

    assert correction_gain_db(report) is None


def test_the_report_names_every_rejection_and_finding() -> None:
    shortlist = Shortlist(
        accepted=["a", "b", "c"],
        rejected=[Rejection(track_id="d", reason="am Genre vorbei")],
        shortened=False,
        note="passt",
    )
    report = _report(-2.0, [QaFinding(kind="seam", text="Naht bei 3:20 wackelt")])

    text = render_qa_report(report, shortlist, [], None)

    assert "am Genre vorbei" in text
    assert "Naht bei 3:20 wackelt" in text
    assert "d" in text
```

- [ ] **Step 2: Testlauf, muss fehlschlagen**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_report.py -v`
Expected: FAIL mit `ModuleNotFoundError`

- [ ] **Step 3: Implementieren**

```python
# djset/report.py
"""Der Bericht ueber einen Lauf, und die eine erlaubte Korrektur.

Spec Abschnitt 7: findet die QA Clipping oder eine Laengenabweichung, gibt es
genau einen korrigierten Re-Render, danach wird ausgeliefert und vermerkt. Nur
der Pegel ist so korrigierbar - die Laenge steht im Mixplan, und erneutes
Rendern aendert daran nichts.
"""

from djset.contracts import (
    CritiqueLog,
    MixPlan,
    QaReport,
    Shortlist,
    UnreadableTrack,
)
from djset.qa import TRUE_PEAK_CEILING_DB


def correction_gain_db(report: QaReport) -> float | None:
    """Die Verstaerkung fuer den einen Re-Render, oder None wenn keiner hilft."""
    if not any(finding.kind == "true_peak" for finding in report.findings):
        return None
    return TRUE_PEAK_CEILING_DB - report.true_peak


def render_qa_report(
    qa: QaReport,
    shortlist: Shortlist,
    unreadable: list[UnreadableTrack],
    critique: CritiqueLog | None,
    corrected: bool = False,
) -> str:
    """Der Bericht als Markdown."""
    lines = ["# QA-Bericht", ""]

    lines += [
        "## Das fertige Set",
        "",
        f"- Laenge: {qa.length_s / 60:.1f} Minuten (geplant "
        f"{qa.target_length_s / 60:.1f})",
        f"- Lautheit: {qa.lufs_integrated:.1f} LUFS",
        f"- True Peak: {qa.true_peak:.1f} dB",
    ]
    if corrected:
        lines.append("- Einmal mit korrigiertem Pegel neu gerendert.")
    lines.append("")

    lines += ["## Befunde der QA", ""]
    if qa.findings:
        lines += [f"- **{f.kind}**: {f.text}" for f in qa.findings]
    else:
        lines.append("- Keine.")
    lines.append("")

    lines += ["## Aussortiertes Material", ""]
    if shortlist.rejected:
        lines += [f"- `{r.track_id}`: {r.reason}" for r in shortlist.rejected]
    else:
        lines.append("- Nichts aussortiert.")
    if shortlist.shortened:
        lines += ["", f"{shortlist.note}"]
    lines.append("")

    if unreadable:
        lines += ["## Nicht lesbare Dateien", ""]
        lines += [f"- `{u.id}` ({u.path}): {u.error}" for u in unreadable]
        lines.append("")

    lines += ["## Beanstandungen des Critics", ""]
    if critique is None or not critique.rounds:
        lines.append("- Keine.")
    else:
        for round_ in critique.rounds:
            # CritiqueRound traegt sein eigenes `chosen`-Flag und zaehlt seine
            # Runde ab eins. Nicht gegen einen Schleifenindex vergleichen -
            # chosen_round ist ebenfalls einsbasiert.
            mark = " (uebernommen)" if round_.chosen else ""
            lines.append(f"### Runde {round_.round}{mark}")
            if round_.complaints:
                lines += [f"- **{c.kind}**: {c.text}" for c in round_.complaints]
            else:
                lines.append("- Keine.")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"
```

`CritiqueLog` hat `rounds` und `chosen_round`, `CritiqueRound` hat `round`, `order`, `complaints` und `chosen` — nachgeprüft in `djset/contracts.py`.

- [ ] **Step 4: Testlauf, muss durchlaufen**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_report.py -v`
Expected: 4 passed

- [ ] **Step 5: Gesamtsuite und Commit**

```bash
./.venv/Scripts/python.exe -m pytest -q
```

```bash
git add djset/report.py tests/test_report.py && git commit -m "feat(report): QA-Bericht und die eine erlaubte Pegelkorrektur"
```

---

### Task 8: `build` läuft bis zum Ende durch

Erfolgskriterium 2: nach Ablage der Tracks in `watch/` läuft `dj-set build` ohne jede Interaktion durch.

**Files:**
- Modify: `djset/cli.py`
- Test: `tests/test_cli_build.py`

**Interfaces:**
- Consumes: alles aus den Tasks 2 bis 7
- Produces: `cmd_build` schreibt zusätzlich `set.wav`, `tracklist.md`, `qa-report.md`

- [ ] **Step 1: Den fallenden Test schreiben**

```python
# tests/test_cli_build.py — ans Ende anfuegen
def test_a_full_build_renders_the_set(tmp_path: Path, profile: Profile) -> None:
    """Der ganze Weg: watch-Ordner rein, hoerbares Set raus."""
    run = _prepared_with_mixable_tracks(tmp_path, profile, 4)
    client = FakeClient(_full_run_responses())

    code = cmd_build(tmp_path, run.run_id, client, _budget())

    assert code == 0
    assert run.has("set.wav")
    assert run.has("tracklist.md")
    assert run.has("qa-report.md")

    with sf.SoundFile(run.path("set.wav")) as handle:
        assert len(handle) > 0

    bericht = run.path("qa-report.md").read_text(encoding="utf-8")
    assert "True Peak" in bericht


def test_a_finished_run_is_no_longer_unfinished(
    tmp_path: Path, profile: Profile
) -> None:
    """Run.latest_unfinished erkennt einen Lauf an set.wav als fertig."""
    run = _prepared_with_mixable_tracks(tmp_path, profile, 4)
    cmd_build(tmp_path, run.run_id, FakeClient(_full_run_responses()), _budget())

    assert Run.latest_unfinished(tmp_path) is None
```

- [ ] **Step 2: Testlauf, muss fehlschlagen**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_cli_build.py -v`
Expected: FAIL — `set.wav` wird nicht geschrieben

- [ ] **Step 3: Implementieren**

In `djset/cli.py`, nachdem `mixplan.json` geschrieben wurde, den Lauf zu Ende führen:

```python
    # Stufe 8: rendern.
    sources = {file.id: file.path for file in files}
    render(best, sources, run.path(FINISHED_MARKER))
    run.write_text("tracklist.md", render_tracklist(best, {a.id: a for a in analyses}))

    # Stufe 9: nachmessen, und hoechstens einmal den Pegel korrigieren.
    qa = check_set(run.path(FINISHED_MARKER), best)
    correction = correction_gain_db(qa)
    corrected = correction is not None
    if corrected:
        print(f"  QA: Pegel um {correction:.1f} dB korrigiert, rendere neu")
        render(best, sources, run.path(FINISHED_MARKER), master_gain_db=correction)
        qa = check_set(run.path(FINISHED_MARKER), best)

    run.write_text(
        "qa-report.md",
        render_qa_report(qa, shortlist, unreadable, critique_log, corrected),
    )
```

`FINISHED_MARKER` kommt aus `djset/run.py` und ist `set.wav` — genau der Name, an dem `Run.latest_unfinished` einen fertigen Lauf erkennt. Damit schliesst sich der Kreis aus Plan 1.

Fehler des Renderers werden **nicht** abgefangen: Spec Abschnitt 7 sagt „Fehler beim Rendern → Kein Retry. Der Renderer ist deterministisch, ein Fehler ist ein Bug. Abbruch, der Mixplan bleibt zum Reproduzieren liegen." Fange nur `RenderToolMissing` ab, weil ein fehlendes ffmpeg kein Bug ist, sondern eine Umgebungsfrage — und gib dafür eine Meldung mit Exit-Code 1.

Die Abschlussausgabe um die neuen Artefakte ergänzen.

- [ ] **Step 4: Testlauf, muss durchlaufen**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_cli_build.py -v`
Expected: alle Tests grün

- [ ] **Step 5: Prüfen, dass der Client weiterhin faul importiert wird**

Run: `./.venv/Scripts/python.exe -c "import sys, djset.cli; assert 'djset.agents.client' not in sys.modules; print('faul')"`
Expected: `faul`

- [ ] **Step 6: Gesamtsuite und Commit**

```bash
./.venv/Scripts/python.exe -m pytest -q
```

```bash
git add djset/cli.py tests/test_cli_build.py && git commit -m "feat(cli): build laeuft bis zum fertigen Set durch"
```

---

## Was dieser Plan bewusst offen lässt

- **Stereo.** Der Renderer mischt zu Mono. Suno liefert Stereo, und ein DJ-Set in Mono ist eine spürbare Einschränkung. Die Erweiterung ist mechanisch — jede Operation pro Kanal statt einmal — aber sie verdoppelt Speicher und Laufzeit und gehört sauber getestet, nicht nebenbei eingebaut.
- **`DOWNBEAT_TOLERANCE_BARS`** sind 69 ms bei 174 BPM. Als Tor für handgeschriebene Mixpläne locker.
- **`overlap` ist reorderable**, aber bei gleichmäßig lautem Material kann keine Reihenfolge Outro-Material erzeugen — die drei Critic-Runden laufen dort weiterhin ins Leere.
- **Kein README.** Dass `dj-set` eine angemeldete Claude-CLI und ein ffmpeg mit librubberband braucht, steht nirgends. Eigene Aufgabe.

## Selbstprüfung dieses Plans

**Spec-Abdeckung:** Stufe 8 in den Tasks 2 bis 5, Stufe 9 in Task 6, der eine korrigierte Re-Render und `qa-report.md` in Task 7, die durchlaufende Pipeline in Task 8. Die Nachträge aus Plan 2 in Task 1. Erfolgskriterium 3 (Länge ±5 %) und 5 (True Peak ≤ −1 dBTP) prüft Task 6, Kriterium 4 (Beat-Kontinuität) ebenfalls Task 6 über die Nahtanalyse, Kriterium 6 (Tracklist mit Zeitstempeln) Task 5, Kriterium 7 (Nachvollziehbarkeit) Task 7.

**Typkonsistenz:** `stretch` und `apply_eq` liefern beide `np.ndarray` in float32; `render` nimmt `dict[str, Path]` und schreibt eine Datei; `check_set` nimmt denselben `MixPlan`, den `render` ausgeführt hat, und liefert `QaReport`; `correction_gain_db` nimmt genau diesen Bericht.

**Wo ein Reviewer zuerst hinsehen sollte:** auf `render` in Task 4. Die Zeitleiste dort muss dieselbe sein wie die, die Stufe 6 gerechnet hat — sonst klingt das Set anders, als der Critic geprüft hat, und niemand merkt es, weil beide Seiten für sich stimmig sind. Konkret: dass `starts` aus `transitions[i].start_at_s` gebaut wird und nicht neu aus Dauern aufaddiert, und dass die EQ-Fenster genau `bars × bar_length` breit sind.
