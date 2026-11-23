# Musik-Studio Plan 2: Videomusik, Laura-fertig

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `dj-set video` macht aus einem Song im Bestand ein Musikstück von genau der Länge eines Videos, das auf einem Taktanfang beginnt und auf einem Taktanfang ausklingt, und liefert es so, dass Laura es ohne Umrechnen auf eine Zeitleiste legen kann.

**Architecture:** Ein neues Paket `djset/video/` neben der Set-Kette. Der Code legt Einstieg, Taktzahl und Ausklang fest (reine Rechnung auf dem gemessenen Raster, an erkannte Beats eingerastet). Das Sprachmodell wählt nur den Song, den Einstieg (`anfang` oder `hoehepunkt`) und ob gesungen wird. Gerendert wird ohne Strecken: die Länge trifft der Ausklang, nicht das Tempo. Ergebnis ist ein Lauf-Ordner mit WAV, MP3, Bericht und `videomusik.json`, dem Vertrag für Laura.

**Tech Stack:** Python 3.12, numpy, soundfile, pyloudnorm (über `measure_loudness`), ffmpeg/ffprobe, pydantic v2, pytest. Agent über `run_agent` wie die übrigen Stufen.

**Spec:** `docs/superpowers/specs/2026-11-23-musik-studio-design.md`, Abschnitt 7.3 „`video`“, Abschnitt 8 Schritt 4 und Abschnitt 10 („Zeitpunkte im Video sind Wunsch, nicht Zusage“, „`unter_video_legen` mischt unter den Originalton“).

## Entscheidungen von Felix (23.11.)

- „Plan 2: Videomusik“. Laura soll einbezogen werden, damit später automatisch Musik unter Laura-Videos kommt.
- „Laura-fertig liefern“: Plan 2 liefert bild- und probengenau in Lauras Maß (Frames, fps) und schreibt den Vertrag. Das automatische Unterlegen in Laura (Länge der Zeitleiste lesen, Datei importieren, `POST /timelines/{tid}/audio-clips`) kommt mit dem Musik-Dienst in Plan 3. **Laura auf dem Mini-PC wird in Plan 2 nicht angefasst.**

## Was Laura heute kann (gelesen in `vibemind-os/spaces/video/laura` @ `a2e5802`, 23.11.)

- Zeitleisten rechnen in ganzen Frames (`seq_in_frame`, `seq_out_frame_exclusive`), Bildrate als Bruch `rate_num/rate_den` (Video-Claw-Projekte: 30/1). Ton in Samples.
- `POST /timelines/{tid}/audio-clips` nimmt `asset_id`, `seq_in_frame`, `seq_out_frame_exclusive`, `asset_in_frame`, `gain_percent` (0–400), `fade_in_frames`, `fade_out_frames`, `mix_mode` (`mix` | `replace_original` | `mute_original`), `ducking_percent` (0–100), `label`.
- Gerendert wird mit ffmpeg; Musik wird nicht geschleift, eine zu kurze Datei endet einfach. Optional `loudnorm I=-14`.
- Laura importiert über Pfad auf seinem eigenen Rechner oder über URL. Vom PC aus braucht es also später einen Link (Plan 3).

## Global Constraints

- Länge: das Ergebnis hat **genau** `round(ziel_s * 44100)` Samples; die Prüfung misst die Datei und verlangt ±50 ms (Spec 8, Schritt 4), praktisch 0.
- Ziel aus Frames: `ziel_s = frames * fps_den / fps_num`, ohne Rundung auf Millisekunden.
- Anfang und Schluss liegen auf Taktanfängen des Materials (Spec 7.3). Kein Tempo-Strecken in Plan 2.
- Der Schluss klingt aus: ab dem letzten Taktanfang fällt der Pegel bis zur letzten Probe auf null.
- Lautheit: Vorgabe −14 LUFS, einstellbar (`--lufs`); True Peak höchstens −1,0 dBTP (`TRUE_PEAK_CEILING_DB`).
- Kurze Stücke kommen aus **einem** Song (Spec 9). Plan 2 macht nur diesen Fall.
- Nur Songs mit Herkunft aus dem Bestand; die Herkunft steht im Vertrag und im Bericht.
- Songs mit unsicherem Tempo (`tempo_unsicher`) kommen nicht in die Wahl.
- Gesang: unter Video standardmäßig **aus** (es klingt die Begleitung). `--mit-gesang` / `--ohne-gesang` gehen vor dem Modell.
- Das Original-Video bleibt unverändert; `--unterlegen` schreibt eine neue Datei in den Lauf-Ordner.
- Der Agent bekommt kein Shell- und kein Dateiwerkzeug.
- `filterwarnings = error` gilt auch für die neuen Tests.

## Review Focus

1. **Ziel länger als jeder passende Song** (z. B. 6 Minuten bei 4-Minuten-Songs): Abbruch vor dem Modell mit Meldung, wie lang der längste Kandidat trägt. Kein stilles Kürzen. Test in Task 2 und Task 6.
2. **Sehr kurzes Ziel** (unter einem Takt plus Ausklang, z. B. 2 s): klare Meldung mit der Mindestlänge. Test in Task 2.
3. **Video ohne Videospur oder ohne lesbare Dauer** (Audiodatei, kaputte Datei): `VideoFehler` mit Dateiname, kein Traceback. Test in Task 1.
4. **Das Modell nennt einen Song, der nicht in der Liste steht** oder einen, der für die Länge zu kurz ist: Abbruch mit Meldung, kein Rendern mit falschem Song. Test in Task 4.
5. **Ohne Gesang gewünscht, aber der Song ist nicht getrennt und Demucs fehlt**: der Song zählt nicht als Kandidat; bleibt keiner, Abbruch mit `pip install`-Hinweis. Test in Task 6.

---

## Dateien

```text
spaces/musik/studio/djset/video/
  __init__.py
  laenge.py       Ziellaenge aus Sekunden, Frames+fps oder Videodatei (ffprobe)
  zuschnitt.py    Einstieg, Taktzahl, Ausklang - reine Rechnung
  klang.py        Stueck rendern: ausschneiden, einblenden, ausklingen, Pegel
  waehler.py      Modell waehlt Song, Einstieg, Gesang
  vertrag.py      VideoWahl, VideomusikErgebnis (videomusik.json), Laura-Block
  pruefung.py     Laenge, Schluss, Lautheit, True Peak messen
  unterlegen.py   Musik unter ein Video legen (ffmpeg), Original bleibt
  befehl.py       cmd_video: Ablauf, Lauf-Ordner, Ausgabe
spaces/musik/studio/djset/render/stretch.py   + ffprobe_path()
spaces/musik/studio/djset/cli.py              + Unterkommando video
spaces/musik/studio/tests/video/              Tests je Modul
spaces/musik/docs/LAURA-VERTRAG.md            der Vertrag fuer Laura / Plan 3
```

---

### Task 1: Ziellänge

**Files:**
- Create: `djset/video/__init__.py`, `djset/video/laenge.py`
- Modify: `djset/render/stretch.py` (neben `ffmpeg_path`)
- Test: `tests/video/test_laenge.py`

**Interfaces:**
- Produces:
  - `class VideoFehler(RuntimeError)` in `laenge.py` (alle erwartbaren Fehler des Pakets).
  - `@dataclass(frozen=True) class Ziellaenge: sekunden: float; frames: int | None = None; fps_num: int | None = None; fps_den: int | None = None`, mit `samples(sr: int) -> int` = `round(sekunden * sr)`.
  - `aus_sekunden(s: float) -> Ziellaenge`, `aus_frames(frames: int, fps: str) -> Ziellaenge` (`fps` wie `"30"`, `"30000/1001"`), `aus_video(pfad: Path) -> Ziellaenge`.
  - `ffprobe_path() -> str` in `stretch.py`, `RenderToolMissing` wie bei ffmpeg.

- [ ] **Step 1: Tests schreiben**
  - `test_frames_ergeben_die_exakte_dauer`: `aus_frames(900, "30000/1001").sekunden == 900 * 1001 / 30000` und `.samples(44100) == round(30.03 * 44100)`.
  - `test_ungueltige_angaben_sind_ein_videofehler`: `aus_sekunden(0)`, `aus_sekunden(float("nan"))`, `aus_frames(0, "30")`, `aus_frames(10, "abc")`, `aus_frames(10, "30/0")` werfen `VideoFehler`.
  - `test_dauer_eines_videos`: ein mit ffmpeg erzeugtes Testvideo (`testsrc` 2,5 s, 25 fps, mit `sine`-Ton) ergibt `frames == 62` oder `63` (ffprobe `nb_frames`), `fps_num/fps_den == 25/1`, `sekunden == frames / 25`. Fixture `kurzes_video(tmp_path, sekunden, fps)` in `tests/fixtures/make_video.py`.
  - `test_audiodatei_ist_kein_video`: `aus_video` auf einer WAV wirft `VideoFehler`, Text enthält den Dateinamen und „keine Videospur“.
  - `test_fehlendes_video`: nicht vorhandene Datei → `VideoFehler` mit Dateinamen.
- [ ] **Step 2: Laufen lassen, schlägt fehl** (`pytest tests/video/test_laenge.py -q`)
- [ ] **Step 3: Implementieren.** `aus_video` liest die erste Videospur mit `ffprobe -v error -select_streams v:0 -count_packets -show_entries stream=nb_read_packets,r_frame_rate -of json`; die Länge ist Frames × Bildrate, nicht `format.duration` (die enthält oft den längeren Ton).
- [ ] **Step 4: Tests grün**
- [ ] **Step 5: Commit** `feat(musik): Videomusik - Ziellaenge aus Sekunden, Frames oder Video`

---

### Task 2: Zuschnitt (reine Rechnung)

**Files:**
- Create: `djset/video/zuschnitt.py`
- Test: `tests/video/test_zuschnitt.py`

**Interfaces:**
- Consumes: `BeatGrid` (`first_downbeat_s`, `bar_length_s`), `TrackMeasurements.energy` (je 4 Takte ab Sample 0), `VideoFehler`.
- Produces:
  - `AUSKLANG_MIN_S = 0.5`
  - `EINRASTEN_SCHLAEGE = 0.25` (höchstens so weit wird ein Taktanfang auf einen erkannten Beat geschoben)
  - `Einstieg = Literal["anfang", "hoehepunkt"]`
  - `@dataclass(frozen=True) class Zuschnitt: start_s: float; schluss_s: float; ende_s: float; takte: int` – Zeiten im Song; `ende_s - start_s == ziel_s` exakt, `schluss_s` ist der letzte Taktanfang, `ende_s - schluss_s >= AUSKLANG_MIN_S`.
  - `traegt_s(grid: BeatGrid, dauer_s: float) -> float` – die längste Videomusik, die der Song ab dem ersten Taktanfang trägt.
  - `zuschnitt_planen(grid: BeatGrid, dauer_s: float, ziel_s: float, einstieg: Einstieg, energie: list[float], beats: np.ndarray | None = None) -> Zuschnitt`

Rechnung (die Tests legen sie fest, hier nur der Kern):

```text
takt = grid.bar_length_s
takte = floor((ziel_s - AUSKLANG_MIN_S) / takt)          # >= 1, sonst VideoFehler (Mindestlaenge takt + 0.5 s)
Kandidaten fuer start: Taktanfaenge first_downbeat + k*takt mit start + ziel_s <= dauer_s
  anfang:     der erste Kandidat
  hoehepunkt: der Kandidat, dessen Fenster [start, start+ziel_s) die hoechste zeitgewichtete
              mittlere Energie hat (jeder Energieschritt zaehlt mit seiner Ueberdeckung)
              (Energiewerte je 4 Takte, Schritt i deckt [i*4*takt, (i+1)*4*takt) ab Sample 0);
              bei Gleichstand der fruehere
start, schluss = start + takte*takt: jeweils auf den naechsten Beat aus `beats` eingerastet,
  wenn einer naeher als EINRASTEN_SCHLAEGE*takt/4 liegt
ende = start + ziel_s; liegt schluss danach naeher als AUSKLANG_MIN_S am ende, eine Taktzahl weniger
```

- [ ] **Step 1: Tests schreiben** (Raster 120 BPM → Takt 2,0 s, Downbeat 0,5 s, Song 60 s)
  - `test_anfang_beginnt_am_ersten_taktanfang`: Ziel 30 s → `start_s == 0.5`, `takte == 14`, `schluss_s == 28.5`, `ende_s == 30.5`.
  - `test_die_laenge_ist_exakt`: für Ziele 4.0, 7.3, 30.03, 45.0 gilt `ende_s - start_s == pytest.approx(ziel, abs=1e-9)` und `ende_s - schluss_s >= AUSKLANG_MIN_S`.
  - `test_hoehepunkt_waehlt_das_lauteste_fenster`: Energie `[0.2]*3 + [0.9]*2 + [0.2]*3` (Schritte zu 8 s) und Ziel 16 s → `start_s` liegt auf einem Taktanfang in `[24, 26]`.
  - `test_eingerastet_auf_erkannte_beats`: Beats um 0,03 s gegen das Raster verschoben → `start_s` und `schluss_s` liegen auf den Beats; bei 0,3 s Verschiebung (über der Toleranz) bleiben sie auf dem Raster.
  - `test_zu_kurzes_ziel`: Ziel 2,0 s → `VideoFehler`, Text enthält „mindestens 2.5 s“.
  - `test_ziel_laenger_als_der_song`: Ziel 70 s → `VideoFehler`, Text enthält die tragbare Länge aus `traegt_s` mit einer Nachkommastelle.
- [ ] **Step 2: Fehlschlag prüfen**
- [ ] **Step 3: Implementieren**
- [ ] **Step 4: Grün**
- [ ] **Step 5: Commit** `feat(musik): Videomusik - Zuschnitt auf Taktanfaenge mit Ausklang`

---

### Task 3: Klang (Stück rendern)

**Files:**
- Create: `djset/video/klang.py`
- Test: `tests/video/test_klang.py`

**Interfaces:**
- Consumes: `Zuschnitt`, `Ziellaenge.samples`, `measure_loudness(path, peak_per_channel=True)`, `TRUE_PEAK_CEILING_DB` aus `djset.qa`, `SR`.
- Produces:
  - `EINBLENDE_S = 0.01`
  - `@dataclass(frozen=True) class Pegel: lufs: float; true_peak: float; gain_db: float; gedeckelt: bool`
  - `videomusik_rendern(quelle: Path, zuschnitt: Zuschnitt, samples: int, ziel: Path, lufs_ziel: float = -14.0, sr: int = SR) -> Pegel`

Ablauf: Quelle laden (Stereo, `sr`), genau `samples` Proben ab `round(start_s*sr)` schneiden (fehlt am Ende etwas, mit Stille auffüllen, siehe Test), 10 ms linear einblenden, ab `schluss_s - start_s` bis zur letzten Probe mit einer Kosinus-Halbwelle auf 0 ausblenden. Danach Lautheit messen, Verstärkung `lufs_ziel - lufs`. Würde der True Peak −1,0 dBTP überschreiten, die Verstärkung so weit senken, dass er −1,0 trifft, und `gedeckelt=True` setzen. Float-WAV schreiben.

- [ ] **Step 1: Tests schreiben** (Quelle: `pulsed_tone` oder `click_track` 120 BPM, 30 Takte)
  - `test_genau_so_viele_proben`: `sf.info(ziel).frames == samples` für `samples = round(7.3 * 44100)`.
  - `test_schluss_klingt_aus`: die letzten 10 ms haben RMS unter −60 dBFS; der Pegel 50 ms nach dem Schluss liegt über dem 50 ms vor dem Ende.
  - `test_anfang_ohne_knacken`: die erste Probe ist 0.0, nach 10 ms volle Amplitude.
  - `test_lautheit_trifft_das_ziel`: gemessen `−14 ± 0,5 LUFS`; mit `lufs_ziel=-20` dann `−20 ± 0,5`.
  - `test_true_peak_wird_gedeckelt`: Quelle mit Spitzen, Ziel −6 LUFS → `pegel.gedeckelt is True`, gemessener True Peak ≤ −0,9 dBTP.
- [ ] **Step 2–4:** Fehlschlag prüfen, implementieren, grün
- [ ] **Step 5: Commit** `feat(musik): Videomusik - Stueck rendern mit Ausklang und Zielpegel`

---

### Task 4: Wähler und Vertrag

**Files:**
- Create: `djset/video/vertrag.py`, `djset/video/waehler.py`
- Test: `tests/video/test_waehler.py`, `tests/video/test_vertrag.py`

**Interfaces:**
- Consumes: `run_agent`, `Budget`, `AgentClient`, `SongEintrag`, `Einstieg`, `traegt_s`, `VideoFehler`.
- Produces (in `vertrag.py`):
  - `class VideoWahl(BaseModel): song_id: str (min_length=1); einstieg: Einstieg; gesang: bool; begruendung: str (min_length=1)`
  - `class LauraClip(BaseModel)` – genau die Felder von Lauras `audio-clips`-Body: `seq_in_frame: int = 0`, `seq_out_frame_exclusive: int`, `asset_in_frame: int = 0`, `gain_percent: int = 100`, `fade_in_frames: int = 0`, `fade_out_frames: int = 0`, `mix_mode: Literal["mix", "replace_original", "mute_original"] = "mix"`, `ducking_percent: int = 100`, `label: str`.
  - `class VideomusikErgebnis(BaseModel)`: `version: Literal[1] = 1`, `datei: str` (Name im Lauf, `videomusik.wav`), `mp3: str | None`, `sekunden: float`, `samples: int`, `sample_rate: int`, `frames: int | None`, `fps_num: int | None`, `fps_den: int | None`, `song_id: str`, `titel: str`, `herkunft: str`, `start_im_song_s: float`, `schluss_s: float` (im Stück), `takte: int`, `bpm: float`, `gesang: bool`, `lufs: float`, `true_peak: float`, `laura: LauraClip | None` (nur wenn Frames bekannt).
  - `laura_clip(ergebnis_frames: int, fps_num: int, fps_den: int, schluss_im_stueck_s: float, label: str) -> LauraClip` – `seq_out_frame_exclusive = frames`. `fade_out_frames = 0`, weil der Ausklang schon in der Datei ist. `ducking_percent = 100` heißt: Originalton bleibt; das Absenken entscheidet Plan 3.
- Produces (in `waehler.py`):
  - `kandidaten(songs: list[SongEintrag], ziel_s: float, gesang_noetig_getrennt: bool) -> list[SongEintrag]` – mit Messung, nicht tempo-unsicher, `traegt_s >= ziel_s`; braucht es Begleitung, nur getrennte.
  - `video_waehlen(client, wunsch: str, ziel_s: float, kandidaten: list[SongEintrag], budget: Budget) -> VideoWahl`

Prompt: Wunsch, Ziellänge, Tabelle der Kandidaten (ID, Titel, Tempo, Tonart, Länge, mittlere Energie, getrennt ja/nein). Regeln: `gesang` nur `true`, wenn der Wunsch ausdrücklich Gesang verlangt (unter Video liegt meist Sprache); `einstieg` `hoehepunkt`, wenn der Wunsch Energie oder Höhepunkt verlangt, sonst `anfang`.

- [ ] **Step 1: Tests schreiben**
  - `test_kandidaten_lassen_zu_kurze_und_unsichere_weg`.
  - `test_ohne_gesang_nur_getrennte_songs`.
  - `test_der_prompt_traegt_wunsch_laenge_und_tabelle` (FakeClient, wie `test_der_prompt_traegt_wunsch_und_songtabelle`).
  - `test_unbekannter_song_ist_ein_videofehler`: Modell antwortet `song_id: "gibt-es-nicht"` → `VideoFehler`, Text nennt die erlaubten IDs.
  - `test_laura_clip_traegt_frames`: 900 Frames bei 30000/1001 → `seq_out_frame_exclusive == 900`; `VideomusikErgebnis` round-trip über JSON ist gleich.
- [ ] **Step 2–4:** Fehlschlag prüfen, implementieren, grün
- [ ] **Step 5: Commit** `feat(musik): Videomusik - Modell waehlt Song, Vertrag fuer Laura`

---

### Task 5: Prüfung und Unterlegen

**Files:**
- Create: `djset/video/pruefung.py`, `djset/video/unterlegen.py`
- Modify: `djset/qa.py` (`_gaps` öffentlich als `luecken`, alter Name bleibt als Alias für die bestehenden Aufrufer)
- Test: `tests/video/test_pruefung.py`, `tests/video/test_unterlegen.py`

**Interfaces:**
- Produces:
  - `LAENGE_TOLERANZ_S = 0.05`, `LUFS_TOLERANZ = 1.0`, `AUSKLANG_STILL_DB = -60.0`
  - `class VideoBefund(BaseModel): art: Literal["laenge", "schluss", "pegel", "spitze", "luecke"]; text: str`
  - `videomusik_pruefen(datei: Path, samples: int, lufs_ziel: float, sr: int = SR) -> list[VideoBefund]` – Länge ±50 ms; letzte 10 ms unter −60 dBFS; LUFS ±1; True Peak ≤ −1,0; keine Stille über 0,5 s innerhalb des Stücks vor dem Schluss (`_gaps` aus `djset.qa`, dort öffentlich machen als `luecken`).
  - `ORIGINALTON_DB = -6.0` (Vorgabe: Musik unter dem Originalton, der bleibt hörbar)
  - `unterlegen(video: Path, musik: Path, ziel: Path, musik_db: float = -6.0, original_ersetzen: bool = False) -> None` – ffmpeg: Videospur kopiert (`-c:v copy`), Ton `amix` aus Originalton und Musik (oder nur Musik), AAC, `-shortest` NICHT gesetzt, Länge = Video. Ohne Tonspur im Video: nur Musik. Unter Zwischennamen schreiben, dann umbenennen. Das Original wird nur gelesen.

- [ ] **Step 1: Tests schreiben**
  - `test_ein_sauberes_stueck_hat_keine_befunde` (Ausgabe von Task 3).
  - `test_zu_kurz_und_hart_abgeschnitten_sind_befunde`: Datei 0,2 s zu kurz und ohne Ausklang → Arten `["laenge", "schluss"]`.
  - `test_unterlegen_behaelt_video_und_laenge`: Testvideo 2,5 s mit Ton + Musik 2,5 s → neue Datei hat eine Videospur, eine Tonspur, Dauer ±1 Frame; das Original hat danach dieselbe SHA-256 wie vorher.
  - `test_unterlegen_ohne_originalton`: Video ohne Tonspur → Ausgabe hat Musik als einzige Tonspur.
  - `test_original_ersetzen`: `original_ersetzen=True` → der Ton der Ausgabe korreliert mit der Musik (Korrelation > 0,9 nach Neuabtastung), nicht mit dem Sinus des Originals.
- [ ] **Step 2–4:** Fehlschlag prüfen, implementieren, grün
- [ ] **Step 5: Commit** `feat(musik): Videomusik - Pruefung und unter ein Video legen`

---

### Task 6: Befehl `dj-set video`

**Files:**
- Create: `djset/video/befehl.py`
- Modify: `djset/cli.py` (Unterparser `video`, Aufruf in `main`)
- Test: `tests/video/test_befehl.py`

**Interfaces:**
- Consumes: alles oben, `Bestand`, `_songs_laden`-Logik (Messungen nachholen: die Funktion aus `cli.py` nach `djset/bestand_laden.py` ziehen oder aus `cli` importieren, ohne Kreisimport – `befehl.py` importiert nicht `cli`), `_stimmen_holen` dito, `Run.create`, `to_mp3`, `beat_times`.
- Produces: `cmd_video(daten: Path, wunsch: str, ziel: Ziellaenge, songs: list[str] | None, gesang: bool | None, lufs_ziel: float, video: Path | None, unterlegen_db: float | None, original_ersetzen: bool, client: AgentClient, budget: Budget, today: date) -> int`

CLI:

```text
dj-set video --wunsch TEXT (--sekunden S | --frames N --fps F | --video PFAD)
             [--daten D] [--songs a,b] [--mit-gesang | --ohne-gesang]
             [--lufs -14] [--unterlegen] [--musik-db -6] [--original-ersetzen]
```

`--unterlegen` verlangt `--video`. Gesang: `--ohne-gesang` verlangt getrennte Kandidaten; ohne Angabe werden getrennte Kandidaten bevorzugt, und ist keiner getrennt (kein Demucs), wird mit Gesang gerendert und der Bericht sagt warum. Ablauf: Ziel bestimmen → Songs laden und messen → Stimmen nachholen (wenn Gesang aus oder offen) → Kandidaten → kein Kandidat: Abbruch mit Grund (zu kurz: längster tragbarer Song; ungetrennt: Install-Hinweis) → Lauf anlegen → Modell wählt (`gesang` aus der Kommandozeile überschreibt) → Beats des Songs erkennen (`beat_times` mit Tempo-Hinweis) → Zuschnitt → rendern aus Begleitung (Gesang aus) oder Song → prüfen → `videomusik.json`, `bericht.md`, `videomusik.mp3` → optional `video-mit-musik.mp4`. Exit 0 grün, 2 mit Befund, 1 Abbruch – wie `set`.

Lauf-Ordner `runs/<datum>-video-<wunsch>-NN/`: `ziel.json`, `wahl.json`, `zuschnitt.json`, `videomusik.wav`, `videomusik.mp3`, `videomusik.json`, `bericht.md`, optional `video-mit-musik.mp4`.

- [ ] **Step 1: Tests schreiben** (Bestand aus Click-Tracks wie `_bestand` in `test_cli_set.py`, FakeClient, `HalbTrenner`)
  - `test_video_aus_sekunden_ergibt_exakte_datei_und_vertrag`: `--sekunden 12` → Exit 0, `videomusik.wav` hat `round(12*44100)` Proben, `videomusik.json` validiert, `laura is None`.
  - `test_video_aus_frames_traegt_den_laura_block`: `--frames 360 --fps 30` → `laura.seq_out_frame_exclusive == 360`.
  - `test_gesang_aus_rendert_die_begleitung`: mit `HalbTrenner` → das Ergebnis korreliert mit der Begleitungsdatei, `gesang is False`.
  - `test_zu_lang_fuer_jeden_song_bricht_vor_dem_modell_ab`: Ziel 10 min → Exit 1, `client.aufrufe == 0`, stderr nennt die tragbare Länge.
  - `test_ohne_getrennte_songs_und_ohne_demucs_bricht_ab`: Gesang aus, `_trenner → None` → Exit 1, stderr enthält „pip install“, kein `runs/`.
  - `test_unterlegen_schreibt_neue_datei_und_laesst_das_original`: `--video`, `--unterlegen` → `video-mit-musik.mp4` existiert, Original-SHA unverändert.
  - `test_unterlegen_ohne_video_ist_ein_fehler`: argparse-Fehler oder Exit 1 mit Text „--unterlegen braucht --video“.
  - `test_main_video_reicht_die_argumente_durch` (wie `test_main_set_nimmt_daten_aus_der_umgebung`).
- [ ] **Step 2–4:** Fehlschlag prüfen, implementieren, grün; volle Suite (`pytest -q`) und äußere Suite grün
- [ ] **Step 5: Commit** `feat(musik): dj-set video - Videomusik als Lauf mit Laura-Vertrag`

---

### Task 7: Laura-Vertrag, Lauf 6, Abnahme

**Files:**
- Create: `spaces/musik/docs/LAURA-VERTRAG.md`
- Modify: `spaces/musik/docs/ABNAHME-STUDIO.md`, `spaces/musik/STATUS.md`

- [ ] **Step 1: `LAURA-VERTRAG.md` schreiben**: Felder von `videomusik.json` mit Bedeutung; wie Plan 3 die Datei an Laura bringt (Link statt PC-Pfad, weil Laura auf dem Mini-PC läuft); der `POST /timelines/{tid}/audio-clips`-Aufruf aus dem `laura`-Block; Zuständigkeit: Länge und Ausklang macht die Musik, Absenken unter Sprache entscheidet Laura (`ducking_percent`).
- [ ] **Step 2: Lauf 6 auf dem echten Bestand**: `dj-set video --daten C:\Users\User\VibemindDaten\musik --wunsch "ruhiger Hintergrund unter einem Produktvideo" --frames 900 --fps 30`. Erwartet: Exit 0, 900 Frames = 30,000 s, Prüfung ohne Befund.
- [ ] **Step 3: Zweiter Lauf mit Höhepunkt und Video**: ein kurzes eigenes Testvideo (ffmpeg `testsrc`, 20 s), `--wunsch "energiegeladen, Höhepunkt" --video … --unterlegen`. Erwartet: `einstieg == "hoehepunkt"`, `video-mit-musik.mp4` spielt.
- [ ] **Step 4: Felix schicken** (MP3 und das untergelegte Video), sein Urteil wörtlich in `ABNAHME-STUDIO.md`.
- [ ] **Step 5: STATUS.md nachziehen, Commit** `docs(musik): Laura-Vertrag und Abnahme Videomusik`

---

## Nicht in Plan 2 (bewusst)

- Mischen mehrerer Songs für lange Videos (über die Länge eines Songs hinaus). Spec 7.3 erlaubt es; der Weg wäre ein Set mit genauer Länge plus derselbe Zuschnitt auf dem Raster des Sets.
- Zeitpunkte im Video (Schnitt, Höhepunkt auf Sekunde X). Spec 10: Wunsch, nicht Zusage. Kommt, wenn Laura Szenengrenzen liefert.
- Automatisch in Laura unterlegen, Musik-Dienst, Musik-Claw: Plan 3.
- Absenken unter Sprache: Laura kann es (`ducking_percent`), die Musik nicht.
