# Musik-Studio Plan 1: Erstes Set aus dem Bestand — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `djset` liegt im Musik-Space, rendert Stereo und baut per Kommandozeile aus Felix' vorhandenen Songs ein gemixtes Set von rund 20 Minuten, das er anhören kann.

**Architecture:** Das lokale Repo `C:\Users\User\Desktop\Musik` wird per `git subtree add` unter `spaces/musik/studio/` eingehängt. Ein Bestand (Ordner mit Songs und je einem JSON-Eintrag) ersetzt den `watch/`-Ordner als Quelle; ein neuer Einstieg `dj-set set` leitet das Regelwerk aus Wunsch und Material ab und benutzt danach die vorhandene Kette unverändert. Rendern und Messen werden von Mono auf Stereo umgebaut.

**Tech Stack:** Python 3.11, pydantic 2, librosa, soundfile, pyloudnorm, scipy, ffmpeg mit `rubberband` (am PC vorhanden, geprüft 23.11.), claude-agent-sdk 0.2 (Claude-Abo), pytest mit `filterwarnings = error`.

**Spec:** `docs/superpowers/specs/2026-11-23-musik-studio-design.md`

**Reihe:** Plan 1 von 3. Plan 2 = Musik zum Video (`zuschnitt`, `unter_video_legen`). Plan 3 = Werkzeug-Dienst, Auftrags-Läufer, Musik-Claw. Plan 2 und 3 werden geschrieben, wenn das erste Set gehört ist, weil der erste echte Lauf von `djset` absehbar Dinge zeigt, die sie ändern.

## Global Constraints

- Gearbeitet wird im Worktree `C:\Users\User\ClaudeWork\wt-vos-musik`, Zweig `cc/musik-studio`, ab `origin/master`. Der Haupt-Checkout von `vibemind-os` ist der PC-Betrieb und wird nicht angefasst.
- Vor Task 1: Claim in `C:\Users\User\Desktop\Vibemind_V1\WORKBOARD.md` (nur die eigene Zeile stagen), sofort committen.
- `C:\Users\User\Desktop\Musik` wird nur gelesen, nie verändert.
- Git über PowerShell, Pfade absolut. Conventional Commits. Kein Push, kein Merge nach `master`, kein Submodul-Pin ohne Felix' Ja.
- Die 25 vorhandenen Tests (`spaces/musik/agents`, `spaces/musik/api`) bleiben ohne Zusatzpakete grün.
- Studio-Tests laufen nur in `spaces/musik/studio/.venv`, mit `PYTHONDONTWRITEBYTECODE=1` und `--basetemp` auf `C:`.
- Abweichung vom Aufbau in Spec 7.1: neue Module liegen **im** Paket `djset` (`djset/bestand.py` usw.), nicht daneben. Grund: ein Paket, eine Installation, keine zweite Import-Wurzel.
- Ein Set hat **einen** Tempo-Bereich; gestreckt wird höchstens `MIN_RATIO = 0.94` bis `MAX_RATIO = 1.06` (`djset/contracts.py`).
- Herkunft eines Songs ist genau eines von `suno-eigen`, `eigen-aufgenommen`, `lizenz:<quelle>`. Ohne Herkunft wird nicht gemischt.
- Aufnahme nur aus `<MUSIK_DATEN>/eingang/`, per Dateiname ohne Pfadanteil.
- Qualitätsprüfung: blockierend sind `gap`, `true_peak`, `seam`. `length` und `seam_unmeasurable` sind Hinweise.
- Kein Song wird aus Suno geladen ohne Felix' Ja zu genau diesem Download.
- Kein `console`-/`print`-Zuwachs außerhalb der CLI; Code-Kommentare und Meldungen deutsch wie im Bestand von `djset`.

## Review Focus

1. Mono-Quelldatei im Bestand → beide Kanäle des Sets tragen sie gleich laut (Task 2).
2. Song mit 48 kHz oder als MP3 im Eingang → wird bei der Aufnahme nach 44,1 kHz Float-WAV gewandelt, Kanalzahl bleibt (Task 4).
3. Dateiname im Eingang mit `..`, `/`, `\` oder Laufwerksbuchstabe → Aufnahme lehnt ab, nichts wird gelesen (Task 4).
4. Alle Songs liegen tempomäßig verstreut, weniger als drei passen in einen Bereich → Abbruch vor dem ersten Sprachmodell-Aufruf mit den gemessenen Tempi (Task 5).
5. Derselbe Song zweimal aufgenommen (gleiche Datei, anderer Name) → zweite Aufnahme lehnt ab und nennt den vorhandenen Eintrag (Task 4).

---

### Task 1: Umzug in den Space, Umgebung, Tests grün

**Files:**
- Create (per subtree): `spaces/musik/studio/` mit `djset/`, `tests/`, `docs/`, `pyproject.toml`, `.gitignore`
- Create: `spaces/musik/conftest.py`
- Create: `spaces/musik/studio/README.md`

**Interfaces:**
- Produces: Paket `djset` installiert in `spaces/musik/studio/.venv`; Befehl `dj-set`.

- [ ] **Step 1:** Worktree anlegen: `git -C C:\Users\User\Desktop\Vibemind_V1\vibemind-os worktree add C:\Users\User\ClaudeWork\wt-vos-musik -b cc/musik-studio origin/master`. WORKBOARD-Claim setzen und committen.
- [ ] **Step 2:** Im Worktree: `git subtree add --prefix=spaces/musik/studio C:\Users\User\Desktop\Musik master` (ohne `--squash`, die Historie soll mitkommen). Prüfen: `git log --oneline -3 -- spaces/musik/studio/djset/cli.py` zeigt Commits vom August.
- [ ] **Step 3:** `spaces/musik/conftest.py` mit `collect_ignore = ["studio"]` schreiben. Prüfen im Worktree: `python -m pytest -p no:cacheprovider spaces/musik -q` → `25 passed` (System-Python, ohne librosa).
- [ ] **Step 4:** Umgebung: `python -m venv spaces\musik\studio\.venv`, dann `.venv\Scripts\python -m pip install -e ".[dev]"` in `spaces/musik/studio`.
- [ ] **Step 5:** Studio-Suite: in `spaces/musik/studio` `$env:PYTHONDONTWRITEBYTECODE=1; .venv\Scripts\python -m pytest -q --basetemp C:\Users\User\AppData\Local\Temp\djset-pytest`. Erwartet: alles grün. Rote Tests sind Befunde: mit systematic-debugging klären, Ursache im Commit nennen, keinen Test abschalten.
- [ ] **Step 6:** `spaces/musik/studio/README.md`: drei Absätze (was es ist, wie man die Umgebung baut, wie man die Tests startet), Verweis auf die Spec.
- [ ] **Step 7:** Commit `feat(musik): djset als Studio in den Space geholt`.

### Task 2: Stereo rendern und messen

**Files:**
- Modify: `spaces/musik/studio/djset/render/engine.py` (`_load_mono`, `_segment`, `render`)
- Modify: `spaces/musik/studio/djset/render/eq.py` (`split_bands`, `apply_eq`)
- Modify: `spaces/musik/studio/djset/qa.py` (`check_set`)
- Modify: `spaces/musik/studio/djset/report.py` (`_format_line`), `djset/contracts.py` (Docstring `AudioFormat`)
- Test: `spaces/musik/studio/tests/render/test_engine.py`, `tests/render/test_eq.py`, `tests/test_qa.py`, `tests/fixtures/make_audio.py`

**Interfaces:**
- Produces: `engine._load_audio(path: Path, sr: int) -> np.ndarray` mit Form `(n, 2)`, float32; ersetzt `_load_mono`. `render(...)` (Signatur unverändert) schreibt eine Stereo-Float-WAV. `apply_eq(signal, sr, curve)` nimmt `(n,)` oder `(n, 2)` und gibt dieselbe Form zurück.

- [ ] **Step 1: Failing tests.** In `tests/fixtures/make_audio.py` eine Hilfe ergänzen, die eine Stereo-Datei mit Signal nur links schreibt. Neue Tests in `tests/render/test_engine.py`:

```python
def test_render_haelt_die_kanaele_getrennt(tmp_path):
    # Quelle: Signal nur links. Plan: ein Eintrag, kein Uebergang.
    out = _render_single(tmp_path, left_only=True)
    signal, _ = sf.read(out, always_2d=True)
    assert signal.shape[1] == 2
    assert _rms_db(signal[:, 0]) > -30.0
    assert _rms_db(signal[:, 1]) < -60.0

def test_render_legt_mono_auf_beide_kanaele(tmp_path):
    out = _render_single(tmp_path, mono_source=True)
    signal, _ = sf.read(out, always_2d=True)
    assert signal.shape[1] == 2
    np.testing.assert_allclose(signal[:, 0], signal[:, 1], atol=1e-6)
```

In `tests/render/test_eq.py`: `apply_eq` mit `(n, 2)` liefert `(n, 2)`, und jeder Kanal gleicht dem Ergebnis des einzeln gefilterten Kanals (`atol=1e-6`). In `tests/test_qa.py`: `check_set` auf einem Stereo-Render meldet `audio_format.channels == 2`.

- [ ] **Step 2:** Tests laufen lassen, erwartet rot (`shape[1] == 1` bzw. `AttributeError`).
- [ ] **Step 3:** Umbau. `_load_audio` liest `always_2d=True`, verdoppelt einen einzelnen Kanal, nimmt bei mehr als zwei Kanälen die ersten beiden. `render` führt `mix` als `(total, 2)`. Hüllkurven (`envelope`) bleiben eindimensional und werden per `[:, None]` angewandt. `split_bands`/`apply_eq` filtern entlang `axis=0`. In `qa.check_set` bleiben Lücken- und Nahtmessung auf der Mono-Summe; Lautheit und True-Peak messen das Stereosignal. Die Zwischendatei für `stretch` wird mit der Kanalzahl des Stücks geschrieben; `stretch` selbst bleibt unverändert (ffmpeg erhält die Kanäle).
- [ ] **Step 4:** Ganze Studio-Suite grün. Vorhandene Tests, die Mono-Form erwarten, werden auf Stereo umgestellt, nicht gelöscht.
- [ ] **Step 5:** Die Kommentare, die Mono als „festgehaltene Auslassung" beschreiben (`contracts.py`, `report.py`, `capture.py`, `qa.py`), an den neuen Stand angleichen.
- [ ] **Step 6:** Commit `feat(musik): Studio rendert und misst Stereo`.

### Task 3: Qualitätsprüfung unterscheidet Rot und Hinweis

**Files:**
- Modify: `spaces/musik/studio/djset/contracts.py` (`QaReport`)
- Modify: `spaces/musik/studio/djset/report.py` (`_status_line`, `render_qa_report`)
- Test: `spaces/musik/studio/tests/test_contracts.py`, `tests/test_report.py`

**Interfaces:**
- Produces: `contracts.BLOCKING_KINDS: frozenset[str] = frozenset({"gap", "true_peak", "seam"})`; `QaReport.blocking -> list[QaFinding]`; `QaReport.hints -> list[QaFinding]`; `QaReport.ok` ist `not self.blocking`.

- [ ] **Step 1: Failing tests.**

```python
def test_hinweise_machen_den_bericht_nicht_rot():
    report = _report(findings=[_finding("length"), _finding("seam_unmeasurable")])
    assert report.ok
    assert [f.kind for f in report.hints] == ["length", "seam_unmeasurable"]
    assert report.blocking == []

@pytest.mark.parametrize("kind", ["gap", "true_peak", "seam"])
def test_blockierende_befunde_machen_rot(kind):
    report = _report(findings=[_finding(kind)])
    assert not report.ok
    assert [f.kind for f in report.blocking] == [kind]
```

In `tests/test_report.py`: der Bericht eines Laufs mit nur Hinweisen trägt in der Statuszeile das Wort `Hinweis` und nicht `Befund`; die Hinweise stehen unter einer eigenen Überschrift `## Hinweise`.

- [ ] **Step 2:** Rot bestätigen.
- [ ] **Step 3:** Umsetzen. `report.correction_gain_db` bleibt an `true_peak` gebunden und ändert sich nicht.
- [ ] **Step 4:** Suite grün. Commit `feat(musik): QA trennt blockierende Befunde von Hinweisen`.

### Task 4: Bestand

**Files:**
- Create: `spaces/musik/studio/djset/bestand.py`
- Test: `spaces/musik/studio/tests/test_bestand.py`

**Interfaces:**
- Consumes: `capture.convert(source: Path, target: Path, sr: int = SR) -> None`; `dsp.analyze.measure_track(path: Path, track_id: str, hint_bpm: float | None = None) -> TrackMeasurements`; `run.slugify(text: str) -> str`.
- Produces:

```python
Herkunft = str  # geprueft durch pruefe_herkunft

class BestandFehler(RuntimeError): ...

def pruefe_herkunft(text: str) -> str:
    """Gibt text zurueck, wenn 'suno-eigen', 'eigen-aufgenommen' oder
    'lizenz:<nichtleer>'; sonst BestandFehler."""

class SongEintrag(BaseModel):
    id: str                      # slugify(titel), eindeutig im Bestand
    titel: str
    datei: str                   # Dateiname unter songs/, immer <id>.wav
    herkunft: str
    suno_id: str | None = None
    aufgenommen_am: date
    pruefsumme: str              # sha256 der Eingangsdatei
    messung: TrackMeasurements | None = None

class Bestand:
    def __init__(self, wurzel: Path) -> None: ...
    eingang: Path                # wurzel / "eingang"
    songs: Path                  # wurzel / "songs"
    def aufnehmen(self, dateiname: str, titel: str, herkunft: str,
                  heute: date, suno_id: str | None = None) -> SongEintrag: ...
    def messen(self, song_id: str) -> SongEintrag: ...
    def alle(self) -> list[SongEintrag]: ...      # nach id sortiert
    def hole(self, song_id: str) -> SongEintrag: ...
    def pfad(self, song_id: str) -> Path: ...     # die WAV unter songs/
```

Ablage: `songs/<id>.wav` und `songs/<id>.json`. Die Eingangsdatei bleibt im Eingang liegen.

- [ ] **Step 1: Failing tests** (Audio aus `tests/fixtures/make_audio.py`):

```python
def test_aufnehmen_wandelt_und_traegt_ein(bestand, eingang_mp3_48k):
    eintrag = bestand.aufnehmen("lied.mp3", "Mein Lied", "suno-eigen", date(2026, 11, 23))
    assert eintrag.id == "mein-lied"
    info = sf.info(bestand.pfad("mein-lied"))
    assert (info.samplerate, info.subtype) == (44_100, "FLOAT")
    assert bestand.alle() == [eintrag]

@pytest.mark.parametrize("name", ["../x.wav", "a/b.wav", "a\\b.wav", "C:\\x.wav", ""])
def test_aufnehmen_lehnt_pfade_ab(bestand, name):
    with pytest.raises(BestandFehler):
        bestand.aufnehmen(name, "X", "suno-eigen", date(2026, 11, 23))

@pytest.mark.parametrize("herkunft", ["", "spotify", "lizenz:", "unbekannt"])
def test_aufnehmen_ohne_gueltige_herkunft(bestand, eingang_wav, herkunft):
    with pytest.raises(BestandFehler):
        bestand.aufnehmen("lied.wav", "X", herkunft, date(2026, 11, 23))

def test_dieselbe_datei_zweimal(bestand, eingang_wav):
    bestand.aufnehmen("lied.wav", "Erstes", "suno-eigen", date(2026, 11, 23))
    shutil.copy(bestand.eingang / "lied.wav", bestand.eingang / "kopie.wav")
    with pytest.raises(BestandFehler, match="erstes"):
        bestand.aufnehmen("kopie.wav", "Zweites", "suno-eigen", date(2026, 11, 23))

def test_gleicher_titel_zweimal(bestand, eingang_wav, eingang_wav_anders): ...  # BestandFehler

def test_messen_legt_die_messung_ab(bestand, eingang_wav):
    bestand.aufnehmen("lied.wav", "Lied", "suno-eigen", date(2026, 11, 23))
    eintrag = bestand.messen("lied")
    assert eintrag.messung is not None
    assert bestand.hole("lied").messung == eintrag.messung
```

- [ ] **Step 2:** Rot bestätigen. **Step 3:** Umsetzen. **Step 4:** Suite grün.
- [ ] **Step 5:** Commit `feat(musik): Bestand mit Herkunft und Messung`.

### Task 5: Einstieg „Set aus dem Bestand"

**Files:**
- Create: `spaces/musik/studio/djset/tempo.py`
- Create: `spaces/musik/studio/djset/agents/bestand_profiler.py`
- Modify: `spaces/musik/studio/djset/cli.py` (Kern von `cmd_build` herauslösen, Unterkommando `set`)
- Modify: `spaces/musik/studio/djset/report.py` (Abschnitt „Nicht im Tempo-Bereich")
- Test: `tests/test_tempo.py`, `tests/agents/test_bestand_profiler.py`, `tests/test_cli_set.py`

**Interfaces:**
- Consumes: `Bestand`, `SongEintrag` (Task 4); `contracts.Profile`, `BpmRange`, `MIN_RATIO`, `MAX_RATIO`, `MIN_TRACKS`; `agents.base.run_agent`, `AgentClient`, `Budget`; `watch.AudioFile`, `NotEnoughMaterial`; `run.Run`.
- Produces:

```python
# tempo.py
class TempoWahl(BaseModel):
    bereich: BpmRange                 # target, min = target*MIN_RATIO... siehe unten
    drin: list[str]                   # Song-IDs im Bereich
    draussen: dict[str, float]        # Song-ID -> gemessenes Tempo

def waehle_tempo(tempi: dict[str, float]) -> TempoWahl:
    """Der Bereich, in den die meisten Songs passen. Ein Song passt, wenn
    MIN_RATIO <= target / tempo <= MAX_RATIO. Bei Gleichstand gewinnt das
    Ziel, bei dem die Summe der Abweichungen am kleinsten ist. Kandidaten
    fuer target sind die gemessenen Tempi selbst und die Mitten benachbarter
    Paare. bereich.min = kleinstes Tempo in `drin`, max = groesstes."""

# agents/bestand_profiler.py
def build_profile_from_bestand(
    client: AgentClient, wunsch: str, length_minutes: float,
    songs: list[SongEintrag], wahl: TempoWahl, budget: Budget,
) -> Profile:
    """Das Modell entwirft Energieverlauf, Uebergangsregeln, harmonische
    Strenge und Stil aus Wunsch und Songtabelle. Der Code setzt danach fest:
    genre = wunsch, bpm = wahl.bereich, track_count.needed = min(Entwurf,
    len(wahl.drin)), track_count.request = needed."""

# cli.py
def build_run(run: Run, files: list[AudioFile], profile: Profile,
              client: AgentClient, budget: Budget) -> int: ...   # der bisherige Rumpf von cmd_build ab analyse_all
def cmd_set(daten: Path, wunsch: str, length_minutes: float,
            songs: list[str] | None, client: AgentClient, budget: Budget,
            today: date) -> int: ...
```

`dj-set set --daten <MUSIK_DATEN> --wunsch "..." --length 20 [--songs id,id]`. `--daten` fällt auf die Umgebungsvariable `MUSIK_DATEN` zurück; fehlt beides, Fehler. Läufe liegen unter `<MUSIK_DATEN>/runs/`. `cmd_set` schreibt `profile.json` und `tempo.json` (die `TempoWahl`) in den Lauf und ruft `build_run`.

Reihenfolge in `cmd_set`, alles vor dem ersten Sprachmodell-Aufruf: Songs laden (alle oder `--songs`) → fehlende Messungen nachholen → `waehle_tempo` → Abbruch, wenn `len(drin) < MIN_TRACKS` (Meldung nennt jedes gemessene Tempo) → Abbruch, wenn die Summe der Songlängen in `drin` kleiner ist als `length_minutes * 60` (Meldung nennt vorhandene und gewünschte Minuten).

- [ ] **Step 1: Failing tests.**

```python
def test_waehle_tempo_nimmt_die_groesste_gruppe():
    wahl = waehle_tempo({"a": 124.0, "b": 126.0, "c": 128.0, "d": 174.0, "e": 90.0})
    assert sorted(wahl.drin) == ["a", "b", "c"]
    assert wahl.draussen == {"d": 174.0, "e": 90.0}
    assert all(MIN_RATIO <= wahl.bereich.target / t <= MAX_RATIO for t in (124.0, 126.0, 128.0))
    assert (wahl.bereich.min, wahl.bereich.max) == (124.0, 128.0)

def test_waehle_tempo_ein_song():
    assert waehle_tempo({"a": 100.0}).drin == ["a"]

def test_profil_traegt_den_gemessenen_bereich(fake_client):   # tests/fixtures/fake_client.py
    profile = build_profile_from_bestand(fake_client, "Sommerfest, erst ruhig", 20, songs, wahl, budget)
    assert profile.genre == "Sommerfest, erst ruhig"
    assert profile.bpm == wahl.bereich
    assert profile.track_count.needed <= len(wahl.drin)
    assert profile.track_count.request == profile.track_count.needed

def test_set_bricht_vor_dem_modell_ab_wenn_zu_wenige_passen(tmp_path, zaehlender_client, capsys):
    # Bestand: drei Songs mit 90, 128, 174 BPM
    assert cmd_set(tmp_path, "Party", 20, None, zaehlender_client, budget, date(2026, 11, 23)) == 1
    assert zaehlender_client.aufrufe == 0
    err = capsys.readouterr().err
    assert "90" in err and "128" in err and "174" in err

def test_set_bricht_ab_wenn_das_material_zu_kurz_ist(...):   # Rueckgabe 1, 0 Aufrufe, beide Minutenzahlen in err
def test_set_ruft_build_run_nur_mit_songs_im_bereich(...):   # build_run gepatcht; files == drin
def test_cmd_build_verhaelt_sich_wie_vorher(...):            # tests/test_cli_build.py bleibt unveraendert gruen
```

In `tests/test_report.py`: liegt `tempo.json` mit nichtleerem `draussen` im Lauf, nennt `qa-report.md` unter `## Nicht im Tempo-Bereich` jede ID mit ihrem Tempo.

- [ ] **Step 2:** Rot bestätigen.
- [ ] **Step 3:** `tempo.py` umsetzen, grün.
- [ ] **Step 4:** `bestand_profiler.py` umsetzen. Der Prompt folgt dem Aufbau von `agents/profiler.py::PROMPT` und bekommt zusätzlich eine Tabelle der Songs im Bereich (ID, Tempo, Tonart, Länge, mittlere Energie). Grün.
- [ ] **Step 5:** `cmd_build` in dünne Hülle + `build_run` teilen, `cmd_set` und Unterkommando ergänzen, Bericht erweitern. Ganze Suite grün.
- [ ] **Step 6:** Commit `feat(musik): Set aus dem Bestand mit einem Tempo-Bereich`.

### Task 6: Auslieferung als MP3

**Files:**
- Create: `spaces/musik/studio/djset/export.py`
- Modify: `spaces/musik/studio/djset/cli.py` (`build_run`, nach dem Umbenennen auf `set.wav`)
- Test: `tests/test_export.py`

**Interfaces:**
- Consumes: `render.stretch.ffmpeg_path() -> str`, `RenderFailed`.
- Produces: `export.to_mp3(source: Path, target: Path, bitrate: str = "320k") -> None`; `build_run` schreibt zusätzlich `set.mp3` neben `set.wav`.

- [ ] **Step 1: Failing test:** `to_mp3` auf einer 2-s-Stereo-WAV erzeugt eine Datei, die `sf.info` als 2 Kanäle, 44 100 Hz liest, Dauer 2,0 s ± 0,1. Fehlende Quelle → `RenderFailed`.
- [ ] **Step 2:** Rot. **Step 3:** Umsetzen (ffmpeg, `libmp3lame`). **Step 4:** Grün; ein Fehler beim MP3-Schreiben lässt `set.wav` und den Bericht stehen und wird als Zeile ausgegeben, der Lauf gilt trotzdem als fertig (Test in `tests/test_cli_set.py`).
- [ ] **Step 5:** Commit `feat(musik): Set zusaetzlich als MP3`.

### Task 7: Erster echter Lauf (Abnahme 1–3 der Spec, 6 zur Hälfte)

**Files:**
- Modify: `spaces/musik/STATUS.md` (neuer Abschnitt „Studio", Beweis-Disziplin wie bisher)
- Create: `spaces/musik/docs/ABNAHME-STUDIO.md`

Kein Unit-Test-Task. Dieser Task braucht Felix: sein Ja zu den Downloads und sein Ohr.

- [ ] **Step 1:** `MUSIK_DATEN` festlegen (Vorschlag `C:\Users\User\VibemindDaten\musik`, außerhalb jedes Repos) und Felix nennen.
- [ ] **Step 2:** Felix nennt die Songs in seinem Suno-Konto (mindestens acht). Je Song: Titel und Suno-Kennung notieren, nach seinem Ja über seinen Browser herunterladen, nach `<MUSIK_DATEN>/eingang/` legen.
- [ ] **Step 3:** Jeden Song aufnehmen (`herkunft="suno-eigen"`) und messen. Gemessene Tempi und Tonarten als Tabelle in `ABNAHME-STUDIO.md`, Art *measured*, mit dem Hinweis, dass niemand sie gegengehört hat.
- [ ] **Step 4:** `dj-set set --wunsch "<Felix' Worte>" --length 20`. Passen zu wenige Songs in einen Bereich: Felix die Tempi zeigen und fragen, statt die Länge still zu kürzen.
- [ ] **Step 5:** Jeder Abbruch und jeder blockierende Befund ist ein Fund: mit systematic-debugging auf die Ursache gehen, als Test festhalten, beheben, in `ABNAHME-STUDIO.md` notieren (so wie `docs/ABNAHME.md` die vier Fehlversuche des Live-Pults festhält).
- [ ] **Step 6:** `set.mp3` an Felix schicken (SendUserFile) und zum Anhören öffnen. Seine Rückmeldung wörtlich in `ABNAHME-STUDIO.md`.
- [ ] **Step 7:** `STATUS.md` nachführen: was *measured* ist (Länge, LUFS, True-Peak, Nähte), was *gehört* ist (nur, was Felix gesagt hat), was offen bleibt. Commit `docs(musik): erster Studio-Lauf, Abnahme und Status`.
- [ ] **Step 8:** Felix fragen, ob der Zweig nach `master` soll. Kein Merge, kein Push, kein Pin ohne sein Ja. WORKBOARD-Claim schließen.
