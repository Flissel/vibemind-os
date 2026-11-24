# Musik-Studio Plan 3: Musik-Claw, App-Chat und Musik automatisch in Laura – Umsetzungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein Musik-Dienst am PC nimmt Bestellungen von drei Seiten an (Video-Claw, Musik-Claw über Telegram, App-Chat über das Gehirn), arbeitet sie im Hintergrund ab und legt Videomusik automatisch in Laura.

**Architecture:** Neues Unterpaket `djset.dienst` im Musik-Studio: Laura-Client, Auftrags-Läufer (`dj-set` als Unterprozess, ein Auftrag zur Zeit), signierter Datei-Link für den Mini-PC, Werkzeug-Funktionen, und ein Server, der dieselben Funktionen als MCP-Werkzeuge (für Agenten) und als JSON-Routen (für Code: Video-Claw, Telegram-Bot, Gehirn-Prüfung) anbietet. Der Video-Claw ruft die JSON-Routen; der Musik-Claw ist eine Shim-Instanz mit dem MCP-Endpunkt und ein Telegram-Bot; die App erreicht den Dienst über Register und Fähigkeiten des Gehirns und den OpenFang-Hub.

**Tech Stack:** Python 3.11+, `studio/.venv` (mcp 2.3.0, starlette, anyio, uvicorn schon da), FastMCP streamable-http, `urllib`, `subprocess`, `hmac`, `http.server`, pytest; Video-Claw im Root-`.venv` (mcp 1.27.0); PowerShell-Startskript.

**Spec:** `docs/superpowers/specs/2026-11-24-musik-studio-plan-3-design.md` (Teil-A-Spec `2026-11-23-musik-studio-design.md`, Vertrag `spaces/musik/docs/LAURA-VERTRAG.md`)

## Global Constraints

- Worktree `C:/Users/User/ClaudeWork/wt-vos-musik`, Zweig `cc/musik-agent`. Nie im Haupt-Checkout `C:/Users/User/Desktop/Vibemind_V1/vibemind-os` arbeiten (PC-Betrieb).
- Studio-Tests aus `spaces/musik/studio`: `.venv/Scripts/python -m pytest -q -p no:cacheprovider --basetemp C:/Users/User/AppData/Local/Temp/djset-pytest`. `filterwarnings = error` gilt. Höchstens ein pytest-Lauf zur Zeit.
- Kein Netzgriff in Tests: Laura, Telegram, Shim und Unterprozesse werden über eine einzige austauschbare Funktion bzw. einen injizierten Befehl ersetzt.
- Hard Rule 1: Kein Werkzeug nimmt einen freien Dateipfad an oder gibt einen zurück. Agenten sehen Auftrags-IDs und Dateinamen.
- Nur Songs mit Herkunft aus dem Bestand; `song_aufnehmen` nur aus `MUSIK_DATEN/eingang/`.
- Laura nur über ihre API (`LAURA_API_URL`, Header `X-Laura-Token` aus `LAURA_TOKEN`). In Laura nie etwas löschen oder vorhandene Clips verändern. Kein Fehlertext enthält den Token.
- Fail-soft an jeder Dienstgrenze: Werkzeuge werfen nicht, sie geben `{"ok": False, "fehler": "<Grund>"}` zurück (höchstens 300 Zeichen).
- Auftragszustände genau: `wartet`, `laeuft`, `fertig`, `fertig_mit_befund`, `gescheitert`, `abgebrochen`.
- Exit-Codes von `dj-set`: 0 → `fertig`, 2 → `fertig_mit_befund`, sonst `gescheitert`.
- Zeitlimits: Video 600 s, Set 2700 s, Aufnehmen 1800 s, Laura-Render 900 s; Asset-Probe 300 s.
- Speicher: vor jedem Start `CommittedBytes` gegen das aktuelle `CommitLimit` (`Win32_PerfFormattedData_PerfOS_Memory`), Abstand mindestens 8 GB, sonst bleibt der Auftrag `wartet` mit Grund.
- Datei-Link: HMAC-SHA256, gültig 900 s, nur `videomusik.wav` und `videomusik.mp3` fertiger Aufträge.
- `in_laura_legen`: `ducking_percent` immer 100, `gain_percent` Vorgabe 100, Reel mit Sprecher 25.
- Telegram: nur die Chat-ID aus `TELEGRAM_CHAT_ID`; Dateien bis 50 MB, größer nur Bericht und Link.
- Umgebungsvariablen (Vorgaben): `MUSIK_STUDIO_PORT`=8139, `MUSIK_STUDIO_URL`=`http://127.0.0.1:8139`, `MUSIK_LINK_HOST` (ohne Vorgabe; fehlt er, ist der Link aus und `in_laura_legen` meldet das), `MUSIK_LINK_PORT`=8146, `MUSIK_LINK_SCHLUESSEL`, `MUSIK_TELEGRAM_TOKEN`, `MUSIK_TELEGRAM_LLM_URL`=`http://127.0.0.1:8141/v1`, `MUSIK_TELEGRAM_PORT`=8142. Ports sind Vorschläge und werden in Task 11 per `port_kollisionen.py` bestätigt.
- Budget-Agenten: `musik-claw` (Shim), `musik-kette` (ein Eintrag je gestartetem `set`/`video`-Auftrag). Ohne `VIBEMIND_AGENT`/`VIBEMIND_BUDGET_MODUL` ist die Prüfung aus (wie in der Shim).
- Keine privaten Rechnernamen oder Adressen im Repo (öffentlich); sie stehen nur in der `.env`.
- Betriebseingriffe (Dienste starten, Firewall, OpenFang-Anmeldung, Video-Claw im Betrieb, Einträge im äußeren Repo) nur in den Controller-Tasks 4 und 11, jeweils nach Felix' Ja und mit WORKBOARD-Claim.
- Conventional Commits, jede Commit-Nachricht endet mit `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Dienst startet neu, während `dj-set` läuft:** Der verwaiste Unterprozess wird beendet, der Auftrag ist `gescheitert` mit „Dienst neu gestartet“, und der nächste Auftrag startet. → Test in Task 2.
2. **Böse IDs und Dateinamen** (`..\\`, `/`, absolute Pfade, fremde Auftrags-ID) an Link, Werkzeugen und JSON-Routen: abgelehnt, nichts außerhalb von `MUSIK_DATEN/auftraege` und `runs` wird gelesen. → Tests in Task 3 und Task 5.
3. **Laura nicht erreichbar oder Token falsch** mitten in `reel_fertigstellen`: Das Reel wird ohne Musik gerendert, die Warnung nennt den Grund. → Test in Task 7.
4. **Zeitleiste mit 29,97 fps, Musik in 30 fps bestellt**, oder die Zeitleiste wurde nach der Bestellung verlängert: neu rechnen bzw. Fehler „passt nicht“, kein Asset und kein Clip angelegt. → Test in Task 5.
5. **Zwei Telegram-Nachrichten kurz hintereinander, eine aus einem fremden Chat:** Die fremde wird ignoriert. Jede fertige Bestellung wird genau einmal gemeldet, auch nach einem Neustart des Bots. → Test in Task 8.

---

### Task 1: Laura-Client des Musik-Dienstes

**Files:**
- Create: `spaces/musik/studio/djset/dienst/__init__.py` (leer)
- Create: `spaces/musik/studio/djset/dienst/laura.py`
- Test: `spaces/musik/studio/tests/dienst/__init__.py`, `spaces/musik/studio/tests/dienst/test_laura.py`

**Interfaces:**
- Consumes: Muster aus `spaces/marketing/video_claw/laura_api.py` (fail-soft `anfrage`, `job_warten`, `asset_warten`, einziger Netzgriff `_roh`). Lauras Routen aus ihrer Source: `C:/Users/User/ClaudeWork/vibemind-betrieb/spaces/video/laura/services/local-api/src/laura/api/` (nur lesen). Für Projekte, Zeitleisten und Exporte die tatsächlichen Pfade dort nachsehen.
- Produces (alle fail-soft, Rückgabe `dict` mit `ok`):
  - `anfrage(methode: str, pfad: str, daten: dict | None = None) -> dict` → `{"ok", "daten"}`.
  - `_roh(methode, url, daten: bytes | None, kopf: dict) -> tuple[int, bytes]`, der einzige Netzgriff.
  - `zeitleiste(timeline_id: str) -> dict` → `{"ok", "project_id", "name", "frames", "fps_num", "fps_den"}`. `frames` ist das größte `seq_out_frame_exclusive` der Videoclips, die Bildrate die der Sequence des Projekts.
  - `videos_suchen(suchwort: str, hoechstens: int = 10) -> dict` → `{"ok", "treffer": [{"project_id", "projekt", "timeline_id", "zeitleiste", "sekunden", "letzter_export"}]}`. Der Vergleich ist ohne Groß-/Kleinschreibung und nimmt Teilwörter in Projekt- oder Zeitleistenname.
  - `importieren(project_id: str, url: str, display_name: str) -> dict` → `{"ok", "asset_id"}`.
  - `asset_warten(asset_id: str, max_s: float = 300, ...) -> dict` → `{"ok", "asset"}`. Erfolgreich erst, wenn `sha256` gesetzt ist **und** `type == "audio"` oder `codec_audio` gesetzt ist.
  - `audio_clips(timeline_id: str) -> dict` → `{"ok", "clips": [...]}`.
  - `clip_anlegen(timeline_id: str, koerper: dict) -> dict` → `{"ok", "clip"}`.
  - `rendern(timeline_id: str) -> dict` → `{"ok", "export_id", "job_id"}`. Hatte die Zeitleiste schon einen Export, werden dessen Optionen übernommen und der passende Endpunkt gewählt (`render` oder `render-reel`). Wie das geht, entscheidet das Lesen der Laura-Source.
  - `export_warten(export_id: str, max_s: float = 900, ...) -> dict` → `{"ok", "url", "size_bytes"}`. Den Download-Link solange erneut anfragen, bis er da ist.
  - `herunterladen(url: str, ziel: Path) -> dict` → `{"ok", "bytes"}`. Schreibt erst in eine Nachbardatei, dann `os.replace`.

- [ ] **Step 1: Failing tests.** In `test_laura.py`, mit `monkeypatch` auf `laura._roh`:
  - `test_token_steht_in_keinem_fehler`: `LAURA_TOKEN="geheim123"`, `_roh` wirft `OSError("geheim123 kaputt")` → `"geheim123" not in r["fehler"]` und `r["ok"] is False`.
  - `test_zeitleiste_liest_frames_und_bildrate`: Bei Clips mit `seq_out_frame_exclusive` 300 und 900 und einer Bildrate von 30000/1001 → `frames == 900`, `(fps_num, fps_den) == (30000, 1001)`.
  - `test_videos_suchen_findet_teilwort_ohne_gross_klein`: Bei Projekten „Sommerfest Reel“ und „Produkt“ und dem Suchwort `"sommer"` gibt es genau einen Treffer.
  - `test_asset_warten_verlangt_ton`: Ein Asset hat `sha256` gesetzt und `type == "video"` ohne `codec_audio`. Nach Ablauf der Wartezeit kommt `ok False`, und der Fehler enthält `"kein Ton"`.
  - `test_export_warten_wiederholt_bis_link_da`: Die erste Antwort von `download-url` ist 409 oder ohne Link, die zweite hat den Link → `ok True`.
  - `test_herunterladen_schreibt_atomar`: Bricht `_roh` mitten im Lesen ab, gibt es die Zieldatei nicht.
- [ ] **Step 2:** Run `.venv/Scripts/python -m pytest tests/dienst/test_laura.py -q -p no:cacheprovider --basetemp C:/Users/User/AppData/Local/Temp/djset-pytest`. Erwartet: FAIL (Modul fehlt).
- [ ] **Step 3:** `djset/dienst/laura.py` mit den Funktionen oben implementieren. Zeitlimit je HTTP-Aufruf 30 s, Fehlertext höchstens 300 Zeichen.
- [ ] **Step 4:** Tests erneut laufen lassen → PASS. Dann die ganze Studio-Suite → grün.
- [ ] **Step 5: Commit** `feat(musik): Laura-Client fuer den Musik-Dienst`

### Task 2: Aufträge und Auftrags-Läufer

**Files:**
- Create: `spaces/musik/studio/djset/dienst/auftraege.py`, `spaces/musik/studio/djset/dienst/laeufer.py`
- Test: `spaces/musik/studio/tests/dienst/test_auftraege.py`, `spaces/musik/studio/tests/dienst/test_laeufer.py`

**Interfaces:**
- Consumes: `djset.run.slugify`. `dj-set` meldet den Lauf mit einer Zeile `Run: <id>` auf stdout (`cli.py:643`, `video/befehl.py:400`), Exit-Codes siehe Global Constraints.
- Produces:
  - `ART = Literal["video", "set", "aufnehmen", "laura_render"]`, `ZUSTAND = Literal[...]` (die sechs aus Global Constraints).
  - `class Auftrag(BaseModel)` mit den Feldern:
    - `id: str` (`<yyyymmdd-hhmmss>-<art>-<4 hex>`)
    - `art`, `zustand`, `grund: str = ""`
    - `eingabe: dict` (Argumente, keine Pfade)
    - `kanal: str = "mcp"` (`"telegram"`, `"video-claw"`, `"app"`, `"mcp"`)
    - `gemeldet: bool = False`
    - `erstellt`, `gestartet: datetime | None`, `beendet: datetime | None`
    - `lauf: str | None` (Run-ID in `MUSIK_DATEN/runs`)
    - `pid: int | None`
    - `ergebnis: dict = {}`
  - `class Ablage` mit:
    - `__init__(daten: Path)`. Die Aufträge liegen in `daten/"auftraege"/<id>/auftrag.json` und werden atomar geschrieben.
    - `anlegen(art, eingabe, kanal) -> Auftrag`
    - `hole(id) -> Auftrag` (wirft `KeyError`; eine ID muss auf `^[0-9]{8}-[0-9]{6}-[a-z_]+-[0-9a-f]{4}$` passen, sonst `KeyError`)
    - `speichern(a)`
    - `alle() -> list[Auftrag]`
    - `naechster() -> Auftrag | None`. Unter den Aufträgen mit Zustand `wartet` zuerst `laura_render`, dann `video`, dann die übrigen, jeweils nach `erstellt`.
  - `class Laeufer` mit:
    - `__init__(ablage, befehl: list[str], speicher_frei_gb: Callable[[], float], budget: Callable[[str], tuple[bool, str]], buchen: Callable[[str, str, float], None], laura_render: Callable[[Auftrag], dict], uhr=time.monotonic)`. `befehl` ist der Aufruf von `dj-set`, in Tests ein Python-Ersatzskript.
    - `beim_start() -> None`: Aufträge im Zustand `laeuft` werden `gescheitert` mit dem Grund „Dienst neu gestartet“. Lebt ihre `pid` noch, wird sie beendet.
    - `ein_schritt() -> bool`: Startet den nächsten Auftrag und wartet ihn bis zum Ende oder zum Zeitlimit ab. Gibt `False` zurück, wenn nichts zu tun war.
    - `abbrechen(id) -> bool`
    - `schleife(stopp: threading.Event) -> None`
  - Aufruf je Art:
    - `video` → `dj-set video --wunsch W (--sekunden S | --frames F --fps N/D) [--songs ...] [--mit-gesang|--ohne-gesang]`
    - `set` → `dj-set set --wunsch W --length M [--songs ...] [--ohne-gesang]`
    - `aufnehmen` → `dj-set aufnehmen --datei D --titel T --herkunft H [--suno-id S]`
  - Jeder Unterprozess bekommt `MUSIK_DATEN` aus der Umgebung. stdout und stderr gehen in `auftraege/<id>/ausgabe.log`, nicht in eine Pipe (sonst erben Kindprozesse die Leitung).
  - `laura_render` ruft die injizierte Funktion `laura_render(auftrag)`, ohne Unterprozess und ohne Speicherprüfung.

- [ ] **Step 1: Failing tests.** Das Ersatzskript `tests/dienst/fake_djset.py` schreibt `Run: <id>` und legt `runs/<id>/` an. Es endet mit dem Exit-Code aus `FAKE_EXIT`, schläft `FAKE_SCHLAF` Sekunden und schreibt seine Argumente nach `runs/<id>/argv.json`.
  - `test_video_vor_wartendem_set`: Zuerst wird ein Set angelegt, dann eine Videomusik → `naechster().art == "video"`.
  - `test_exit_2_ist_fertig_mit_befund` und `test_exit_1_ist_gescheitert_mit_letzter_stderr_zeile`.
  - `test_lauf_id_aus_ausgabe`: Nach dem Schritt steht `auftrag.lauf` auf der ID aus der `Run:`-Zeile.
  - `test_zeitlimit_reisst`: Video mit `FAKE_SCHLAF=5` und einem Zeitlimit, das im Test auf 1 s gesetzt ist → `gescheitert`, der Grund enthält `"Zeitlimit"`, der Prozess lebt nicht mehr.
  - `test_wenig_speicher_bleibt_wartet`: `speicher_frei_gb` gibt 7.5 zurück → `ein_schritt()` ist `False`, der Zustand bleibt `wartet`, und der Grund enthält `"Speicher"`.
  - `test_budget_aus_bleibt_wartet_nicht`: `budget` gibt `(False, "tagesgrenze")` zurück → `gescheitert` mit dem Grund `"Budget: tagesgrenze"`. `buchen` wird nicht gerufen.
  - `test_neustart_beendet_verwaisten_prozess`: Ein Auftrag `laeuft` hat die `pid` eines lebenden Ersatzprozesses → nach `beim_start()` ist er `gescheitert` mit `"Dienst neu gestartet"`, und der Prozess ist beendet.
  - `test_abbrechen_beendet_unterprozess`: `abbrechen(id)` während des Laufs → `abgebrochen`.
  - `test_fremde_id_ist_keyerror`: `hole("..\\x")` und `hole("20261124-120000-video-zzzz")` werfen `KeyError`.
  - `test_argumente_ohne_pfade`: Bei `video` mit `frames=900, fps="30000/1001"` steht im `argv.json` `--frames 900 --fps 30000/1001` und kein `--video`.
- [ ] **Step 2:** Die neuen Tests laufen lassen → FAIL.
- [ ] **Step 3:** `auftraege.py` und `laeufer.py` implementieren. Die echte Speicherfunktion `speicher_frei_gb()` liest `Win32_PerfFormattedData_PerfOS_Memory` per `powershell -NoProfile -Command`, mit Zeitlimit 20 s. Bei einem Fehler gibt sie `0.0` zurück, und der Auftrag wartet. Das Budget kommt über `VIBEMIND_BUDGET_MODUL` (Lademuster wie `marketing_shim.py:53-90`), Agent `musik-kette`, eine Buchung je `set`- oder `video`-Start.
- [ ] **Step 4:** Die Tests laufen lassen → PASS. Danach die ganze Suite → grün.
- [ ] **Step 5: Commit** `feat(musik): Auftraege und Laeufer fuer den Musik-Dienst`

### Task 3: Signierter Datei-Link

**Files:**
- Create: `spaces/musik/studio/djset/dienst/link.py`
- Test: `spaces/musik/studio/tests/dienst/test_link.py`

**Interfaces:**
- Consumes: `Ablage`, `Auftrag` (Task 2). Die Dateien liegen in `MUSIK_DATEN/runs/<auftrag.lauf>/`.
- Produces:
  - `ERLAUBT = ("videomusik.wav", "videomusik.mp3")`, `GUELTIG_S = 900`.
  - `signieren(schluessel: bytes, auftrag_id: str, name: str, ablauf: int) -> str`: HMAC-SHA256 über `f"{auftrag_id}/{name}/{ablauf}"`, hex.
  - `link(basis: str, schluessel: bytes, auftrag_id: str, name: str, jetzt: float) -> str`: `f"{basis}/datei/{auftrag_id}/{name}?ablauf={ts}&sig={sig}"`.
  - `aufloesen(ablage, schluessel, pfad_mit_query: str, jetzt: float) -> tuple[int, Path | None]`: gibt `(200, Datei)` zurück, sonst `(403, None)` bei abgelaufen oder falscher Signatur (Vergleich mit `hmac.compare_digest`) oder `(404, None)` bei unbekanntem Auftrag oder Namen, Auftrag nicht `fertig`/`fertig_mit_befund` oder Datei fehlt.
  - `starten(host: str, port: int, ablage, schluessel) -> ThreadingHTTPServer`: beantwortet nur `GET /datei/...` (gestreamt, `Content-Type` `audio/wav` bzw. `audio/mpeg`, `Content-Length`). Alles andere bekommt 404.

- [ ] **Step 1: Failing tests:**
  - `test_gueltiger_link_200`
  - `test_abgelaufen_403` (jetzt = Ablauf + 1)
  - `test_falsche_signatur_403`
  - `test_fremder_name_404` (`bericht.md`)
  - `test_pfad_ausbruch_404` (`../auftrag.json`, `%2e%2e%2f`, `videomusik.wav/../x`)
  - `test_unfertiger_auftrag_404`
  - `test_server_liefert_bytes`: Server auf `127.0.0.1:0` starten, WAV per `urllib` holen. Die Bytes sind gleich, und es gibt keinen Netzgriff nach außen.
- [ ] **Step 2:** → FAIL. **Step 3:** implementieren. **Step 4:** → PASS, dann die Suite grün.
- [ ] **Step 5: Commit** `feat(musik): signierter Datei-Link fuer Laura`

### Task 4: WAV-Probe in Laura (Controller, Abnahme 1)

Diesen Task macht der Controller, kein Unteragent. Er muss vor Task 5 kommen, weil sein Ergebnis `asset_warten` und den Import-Weg bestätigt oder widerlegt.

- [ ] **Step 1:** Felix fragen und WORKBOARD-Claim setzen. Gefragt wird nach einer Firewall-Regel für `MUSIK_LINK_PORT` (TCP eingehend, nur von der Adresse des Mini-PC) und nach einem eigenen Testprojekt in Laura, z. B. „Musik-Probe“.
- [ ] **Step 2:** In der Root-`.env` die Einträge `MUSIK_LINK_HOST` (die Adresse des PC, die der Mini-PC erreicht) und `MUSIK_LINK_SCHLUESSEL` (32 Bytes Zufall, hex) setzen. Die Werte werden nie angezeigt (Ausgabe nur über eine Erlaubnisliste von Namen). Dann mit `link.starten` per kleinem Skript im Scratchpad und einem fertigen Lauf-6-Auftrag (`videomusik.wav`) einen Link erzeugen.
- [ ] **Step 3:** Mit `laura.importieren` ins Testprojekt importieren, dann `laura.asset_warten`. **Bestanden**, wenn das Asset Typ `audio` hat (oder `codec_audio` gesetzt ist) und `sha256` gesetzt ist. Danach einen abgelaufenen Link probieren → Laura meldet einen Importfehler.
- [ ] **Step 4:** Ergebnis in `spaces/musik/docs/ABNAHME-STUDIO.md` als Abschnitt „Plan 3, Probe 1“ eintragen. Ist sie nicht bestanden: anhalten. Mit Felix entscheiden, ob vor Task 5 ein anderer Importweg nötig ist. Der Plan ab Task 5 gilt dann nicht unverändert.

### Task 5: Werkzeug-Funktionen

**Files:**
- Create: `spaces/musik/studio/djset/dienst/werkzeuge.py`
- Test: `spaces/musik/studio/tests/dienst/test_werkzeuge.py`

**Interfaces:**
- Consumes: Task 1 (`laura.*`), Task 2 (`Ablage`, `Laeufer`), Task 3 (`link.link`). Dazu `djset.bestand.Bestand(daten).alle()`, `djset.bestand.pruefe_herkunft` und `djset.video.vertrag` (`VideomusikErgebnis`, `LABEL_MAX = 200`).
- Produces: `class Werkzeuge` mit `__init__(daten: Path, ablage, laeufer, laura_modul, link_basis: str | None, link_schluessel: bytes | None, uhr=time.time)`. Jede Methode ist fail-soft und gibt ein `dict` zurück:
  - `bestand_zeigen() -> {"ok", "songs": [{"id", "titel", "bpm", "tonart", "sekunden", "herkunft", "getrennt"}]}`
  - `song_aufnehmen(datei: str, titel: str, herkunft: str, suno_id: str = "", kanal="mcp") -> {"ok", "auftrag"}`. `datei` darf keinen Pfadtrenner enthalten und muss in `eingang/` liegen. `herkunft` wird mit `pruefe_herkunft` geprüft.
  - `set_bestellen(wunsch: str, minuten: float, songs: list[str] | None = None, ohne_gesang: bool = False, kanal="mcp") -> {"ok", "auftrag", "position"}`
  - `videomusik_bestellen(wunsch: str, sekunden: float | None = None, frames: int | None = None, fps: str | None = None, laura_timeline_id: str | None = None, songs=None, gesang: bool | None = None, kanal="mcp") -> {"ok", "auftrag", "position", "frames", "fps"}`. Genau eine Längenangabe ist erlaubt. Bei `laura_timeline_id` werden Frames und Bildrate aus `laura.zeitleiste` übernommen und in `eingabe["laura_timeline_id"]` gemerkt.
  - `auftrag_zeigen(auftrag: str) -> {"ok", "id", "art", "zustand", "grund", "kanal", "eingabe", "dateien": [Namen], "bericht": <höchstens 40 Zeilen aus bericht.md>, "befunde": [...], "laenge_s", "titel"}`. Es gibt keine Pfade zurück.
  - `auftraege_offen(kanal: str) -> {"ok", "auftraege": [...]}`: beendete Aufträge dieses Kanals mit `gemeldet == False`.
  - `gemeldet(auftrag: str) -> {"ok"}`
  - `auftrag_abbrechen(auftrag: str) -> {"ok", "zustand"}`
  - `in_laura_legen(auftrag: str, timeline_id: str, gain_percent: int = 100) -> {"ok", "clip_id", "asset_id"}`. Ablauf wie Spec 7.4:
    1. Auftrag ist `fertig`/`fertig_mit_befund`, Art `video`, und in `videomusik.json` gibt es einen `laura`-Block.
    2. Zeitleiste lesen. Weicht die Bildrate ab, Frames aus `sekunden` neu rechnen (`round(sekunden * fps_num / fps_den)`). Ist der Unterschied größer als 1 Frame, endet die Funktion mit dem Fehler `"Musik passt nicht zur Zeitleiste (X statt Y Frames)"`, und weder Import noch Clip werden angelegt.
    3. Gibt es schon einen Clip, dessen Label `musik:<auftrag>` enthält, kommt `ok True` mit seiner ID zurück, ohne neuen Import.
    4. `importieren` mit dem Link und `display_name = f"{slug(titel)}.wav"`.
    5. `asset_warten`.
    6. `clip_anlegen` mit dem `laura`-Block, `asset_id`, `gain_percent`, `ducking_percent=100` und `label = f"{titel} · musik:{auftrag}"`, auf 200 Zeichen gekürzt. Die Kürzung trifft den Titel, nie den Teil `musik:<id>`.
  - `laura_videos_suchen(suchwort: str) -> {"ok", "treffer"}`
  - `laura_rendern(timeline_id: str, kanal="mcp", musik_auftrag: str = "") -> {"ok", "auftrag"}`: legt einen Auftrag der Art `laura_render` an.
  - `render_ausfuehren(a: Auftrag) -> dict`: die Funktion, die `Laeufer` als `laura_render` bekommt. Sie ruft `laura.rendern`, dann `export_warten`, dann `herunterladen` nach `auftraege/<id>/laura-export.mp4`. Ins Ergebnis kommen `export_id`, `size_bytes` und `datei="laura-export.mp4"`, aber kein Link.

- [ ] **Step 1: Failing tests.** Mit einem Laura-Ersatzmodul, das jeden Aufruf mitschreibt:
  - `test_videomusik_aus_zeitleiste_uebernimmt_frames`
  - `test_genau_eine_laengenangabe` (keine oder zwei Angaben → `ok False`)
  - `test_song_aufnehmen_ohne_pfad` (`"..\\x.wav"`, `"C:/x.wav"` → `ok False`)
  - `test_in_laura_bildrate_29_97_rechnet_neu` (Musik mit 900 Frames bei 30 fps, Zeitleiste 30000/1001 mit 899 Frames → ok, Clip mit `seq_out_frame_exclusive == 899`)
  - `test_in_laura_falsche_laenge_legt_nichts_an` (Zeitleiste 1200 Frames → Fehlertext wie oben, das Ersatzmodul hat weder Import noch Clip gesehen)
  - `test_in_laura_zweimal_legt_einmal_an`
  - `test_in_laura_ducking_immer_100_und_label_mit_auftrag` (Titel mit 250 Zeichen → Label 200 Zeichen, endet auf `musik:<id>`)
  - `test_in_laura_ohne_link_host_fehler_ohne_import`
  - `test_auftrag_zeigen_ohne_pfade` (kein Wert im Ergebnis enthält `":\\"` oder `"/runs/"`)
  - `test_render_ausfuehren_laedt_herunter`
- [ ] **Step 2:** → FAIL. **Step 3:** implementieren. **Step 4:** → PASS, dann die Suite grün.
- [ ] **Step 5: Commit** `feat(musik): Werkzeuge des Musik-Dienstes samt Laura-Anschluss`

### Task 6: Server (MCP-Werkzeuge und JSON-Routen)

**Files:**
- Create: `spaces/musik/studio/djset/dienst/server.py`, `spaces/musik/studio/djset/dienst/huelle.py`
- Modify: `spaces/musik/studio/pyproject.toml` (`[project.scripts]` bekommt `musik-dienst = "djset.dienst.server:main"`, und `mcp>=2.3,<3` kommt als Abhängigkeit dazu)
- Test: `spaces/musik/studio/tests/dienst/test_server.py`

**Interfaces:**
- Consumes: `Werkzeuge` (Task 5), `Laeufer.schleife`, `Laeufer.beim_start` (Task 2), `link.starten` (Task 3).
- Produces:
  - `huelle.im_thread(fn)` und `huelle.gesundheit_anmelden(server)`: Kopie von `spaces/marketing/claw/mcp_huelle.py`, angepasst an das installierte mcp 2.3.0.
  - `bauen(werkzeuge) -> <FastMCP-Server>`. MCP-Werkzeuge mit genau diesen Namen und Parametern wie in Task 5: `bestand_zeigen`, `song_aufnehmen`, `set_bestellen`, `videomusik_bestellen`, `auftrag_zeigen`, `auftrag_abbrechen`, `in_laura_legen`, `laura_videos_suchen`, `laura_rendern`.
  - Der Kanal einer MCP-Bestellung kommt aus dem HTTP-Header `X-Musik-Kanal` (`telegram`, `app`), sonst `mcp`. Der Agent kann ihn nicht setzen.
  - JSON-Routen (gleiche Funktionen, Antwort ist das `dict`; nur an 127.0.0.1 gebunden):
    - `GET /health`
    - `POST /auftraege/video`
    - `POST /auftraege/set`
    - `GET /auftraege/{id}`
    - `GET /auftraege?kanal=<k>&offen=1`
    - `POST /auftraege/{id}/gemeldet`
    - `POST /auftraege/{id}/in-laura` mit `{timeline_id, gain_percent}`
    - `POST /laura/rendern`
    - `GET /laura/suchen?q=`
  - `kanal` wird in den Routen aus dem Körper gelesen.
  - `main()`:
    1. `.env` des äußeren Repos nachladen (nur fehlende Schlüssel, Muster `video_claw/server.py:19-32`).
    2. `beim_start()`.
    3. Läufer-Thread starten.
    4. Link-Server starten, falls `MUSIK_LINK_HOST` gesetzt ist.
    5. MCP streamable-http auf `127.0.0.1:MUSIK_STUDIO_PORT`, Pfad `/mcp`.

- [ ] **Step 1: Failing tests** (In-Prozess, ohne Port, mit Ersatz-`Werkzeuge`):
  - `test_werkzeugliste_genau` (Namen wie oben, nichts mehr)
  - `test_kanal_aus_header` (Bestellung mit Header `X-Musik-Kanal: telegram` → das Ersatzobjekt sah `kanal="telegram"`; ohne Header `"mcp"`)
  - `test_json_route_video` (per Starlette-`TestClient` `POST /auftraege/video` → 200 und das `dict`)
  - `test_health_antwortet_waehrend_werkzeug_laeuft` (ein Werkzeug schläft 2 s, `/health` antwortet in < 0,5 s)
  - `test_json_route_boese_id_404` (`GET /auftraege/..%5Cx` und `GET /auftraege/%2e%2e` → 404, das Ersatzobjekt wurde nicht gerufen)
- [ ] **Step 2:** → FAIL. **Step 3:** implementieren. **Step 4:** → PASS, dann die Suite grün. Außerdem `.venv/Scripts/musik-dienst.exe --help` oder ein Start mit `MUSIK_STUDIO_PORT=0` für 3 s ohne Traceback.
- [ ] **Step 5: Commit** `feat(musik): Musik-Dienst mit MCP-Werkzeugen und JSON-Routen`

### Task 7: Video-Claw legt Musik unter jedes Reel

**Files:**
- Create: `spaces/marketing/video_claw/musik_studio.py`
- Modify: `spaces/marketing/video_claw/werkzeuge.py:312-398` (`reel_bauen`), Werkzeugliste `werkzeuge.py:411-413`, `spaces/marketing/video_claw/AGENTS.md`
- Test: `spaces/marketing/tests/test_video_claw.py` (erweitern)

**Interfaces:**
- Consumes: JSON-Routen aus Task 6 (`MUSIK_STUDIO_URL`).
- Produces:
  - `musik_studio.anfrage(methode, pfad, daten=None) -> dict`, fail-soft, nach dem Muster von `laura_api.anfrage`. Der einzige Netzgriff ist `_roh`.
  - `musik_studio.bestellen(timeline_id: str, wunsch: str) -> dict` → `{"ok", "auftrag"}`. Ruft `POST /auftraege/video` mit `laura_timeline_id`, `gesang=False` und `kanal="video-claw"`.
  - `musik_studio.warten(auftrag: str, max_s: float = 600, ...) -> dict`
  - `musik_studio.einlegen(auftrag: str, timeline_id: str, gain_percent: int) -> dict`
  - `reel_bauen(..., musik: str = "")`. Ist `musik` leer oder `"aus"`, bleibt das Verhalten unverändert. Sonst wird Musik für die Zeitleiste bestellt, und die Rückgabe ist `{"ok": True, "timeline_id", "musik_auftrag", "reel": {<die render-reel-Optionen>}, "warnungen"}`, ohne Render. Die Optionen werden in der Datei `~/.vibemind/video-claw/reel-<timeline_id>.json` gemerkt, damit `reel_fertigstellen` sie nicht vom Agenten bekommen muss.
  - `reel_fertigstellen(timeline_id: str, musik_auftrag: str) -> dict` → `{"ok", "export_id", "timeline_id", "warnungen"}`. Der Ablauf:
    1. Auf die Musik warten.
    2. `einlegen` mit `gain_percent=25`. Jedes Reel hat Sprache: Jeder Beat ist entweder ein gesprochener Satz oder Originalton.
    3. `render-reel` mit den gemerkten Optionen.
    4. Scheitert 1 oder 2, wird trotzdem gerendert. Die Warnung lautet dann `"ohne Musik: <Grund>"`.
  - In `AGENTS.md` kommt die Regel: Musik immer, außer der Auftrag sagt „ohne Musik“. `musik` beschreibt Ton und Marke aus dem Briefing in einem Satz, ohne Gesang. Nach `reel_bauen` mit `musik_auftrag` folgt immer `reel_fertigstellen`, und dessen `export_id` ist die `EXPORT:`-Zeile.

- [ ] **Step 1: Failing tests:**
  - `test_reel_ohne_musik_unveraendert` (Aufrufe an Laura wie bisher)
  - `test_reel_mit_musik_rendert_nicht` (kein `render-reel`-Aufruf, Rückgabe mit `musik_auftrag`)
  - `test_fertigstellen_legt_ein_und_rendert` (Reihenfolge: warten, einlegen mit 25, `render-reel` mit den gemerkten Optionen)
  - `test_fertigstellen_musik_gescheitert_rendert_ohne` (Musik-Dienst antwortet `ok False` → `render-reel` wird trotzdem gerufen, die Warnung beginnt mit `"ohne Musik:"`)
  - `test_fertigstellen_musikdienst_aus` (`_roh` wirft `ConnectionRefusedError` → wie vorher)
- [ ] **Step 2:** → FAIL. Im Root-`.venv` aus `spaces/marketing` mit `C:/Users/User/Desktop/Vibemind_V1/.venv/Scripts/python.exe -m pytest tests/test_video_claw.py -q -p no:cacheprovider`. **Step 3:** implementieren. **Step 4:** → PASS. Dazu die ganze Marketing-Testsuite, die dieselbe Datei nutzt → grün.
- [ ] **Step 5: Commit** `feat(marketing): Video-Claw legt Musik aus dem Musik-Studio unter jedes Reel`

### Task 8: Musik-Claw mit Telegram-Bot

**Files:**
- Create: `spaces/musik/claw/AGENTS.md`, `spaces/musik/claw/musik-mcp.json`, `spaces/musik/studio/djset/claw/__init__.py`, `spaces/musik/studio/djset/claw/telegram.py`
- Modify: `spaces/musik/studio/pyproject.toml` (`musik-telegram = "djset.claw.telegram:main"`)
- Test: `spaces/musik/studio/tests/claw/__init__.py`, `spaces/musik/studio/tests/claw/test_telegram.py`

**Interfaces:**
- Consumes:
  - die Shim `spaces/marketing/claw/shim/marketing_shim.py`, unverändert als eigene Instanz. Chat-Endpunkt ist `MUSIK_TELEGRAM_LLM_URL` (`/chat/completions`, Modell `claude-code-sonnet`, Zeitlimit 900 s).
  - die JSON-Routen aus Task 6.
  - Vorlage für den Bot ist `spaces/marketing/workers/telegram_bot.py` (Long-Polling, `erlaubt`, `zerlegen`, `Verlauf`, `/health`), aber ohne Whisper und Fotos.
- Produces:
  - `musik-mcp.json`: `mcpServers.musik` mit `type: "http"`, `url: "http://127.0.0.1:8139/mcp"`, `headers: {"X-Musik-Kanal": "telegram"}`, `timeout: 1200000`. `allowedTools` enthält alle neun Werkzeuge aus Task 6.
  - `AGENTS.md`: Rolle (Musik-Assistent für Felix' eigene Songs). Die Werkzeuge und wann welches dran ist. Die Regeln:
    - Eine Bestellung sofort bestätigen und dabei Auftrag und Platz in der Warteschlange nennen.
    - Bei „unter Laura-Video X“ zuerst `laura_videos_suchen`. Bei mehreren Treffern nachfragen, bei keinem Treffer sagen, dass es nichts gibt.
    - Dann `videomusik_bestellen(laura_timeline_id=…)`. Ist die Musik fertig, `in_laura_legen` und danach `laura_rendern`.
    - Nie Pfade nennen.
    - Keine fremden Songs.
  - `telegram.py`, mit diesen Funktionen:
    - `erlaubt(update, chat_id) -> bool`
    - `antwort_holen(text, verlauf, system) -> str`
    - `class TelegramApi` mit `senden(text)`, `datei_senden(art: Literal["audio","video","document"], name: str, daten: bytes, text: str)` (multipart, nur stdlib) und `updates(offset)`
    - `melden(api, dienst_anfrage, chat_id) -> int`: holt `GET /auftraege?kanal=telegram&offen=1`. Je Auftrag schickt es den Kurzbericht und die Datei:
      - Video: MP3, bzw. `laura-export.mp4` bei `laura_render`.
      - Set: MP3.
      - Bei mehr als 50 MB nur den Bericht mit dem Satz „Datei zu groß für Telegram (X MB), liegt im Auftrag <id>“.
      - Danach `POST /auftraege/{id}/gemeldet`.
      - Rückgabe ist die Zahl der Meldungen.
    - Die Dateibytes holt der Bot über eine JSON-Route. Dafür kommt in diesem Task `GET /auftraege/{id}/datei/{name}` in `server.py` dazu. Sie liefert nur die erlaubten Namen und nur an 127.0.0.1.
  - `main()`: Long-Polling (50 s). Nach jeder Runde und spätestens alle 30 s `melden`. `/health` auf `MUSIK_TELEGRAM_PORT`. Den Zustand (`offset`, Verlauf) unter `~/.vibemind/musik-telegram/` ablegen.

- [ ] **Step 1: Failing tests:**
  - `test_fremder_chat_wird_ignoriert`
  - `test_melden_schickt_mp3_und_markiert` (Ersatz-API sieht genau ein `datei_senden("audio", ...)` und danach `gemeldet`)
  - `test_melden_zweimal_schickt_einmal` (der Dienst-Ersatz liefert nach `gemeldet` nichts mehr)
  - `test_zu_gross_nur_bericht`
  - `test_laura_render_schickt_video`
  - `test_bot_token_in_keinem_log` (`ohne_token`)
- [ ] **Step 2:** → FAIL.
- [ ] **Step 3:** Implementieren. Dazu kommt die Route `GET /auftraege/{id}/datei/{name}` in `server.py` samt einem Test in `test_server.py`, der zeigt, dass ein fremder Name 404 bekommt.
- [ ] **Step 4:** → PASS, dann die Suite grün.
- [ ] **Step 5: Commit** `feat(musik): Musik-Claw mit eigenem Telegram-Bot`

### Task 9: App-Chat über Register und Fähigkeiten (Weg A)

**Files:**
- Modify: `config/space_agent_registry.yml` (neuer Space `musik` vor `video`), `brain/the_brain/data/capabilities.yaml` (vier Fähigkeiten)
- Test: die vorhandenen Register- und Fähigkeiten-Tests im Gehirn. Gegebenenfalls kommt ein Test in `brain/the_brain/tests/` dazu, der prüft, dass `musik`-Fähigkeiten auf `mcp:brain-musik:musik-studio:<werkzeug>` zeigen.

**Interfaces:**
- Consumes: das Muster `bubbles` (`space_agent_registry.yml:21-70`) und das Muster `idea_connect` in `capabilities.yaml` (`execution_target: "mcp:<agent>:<server>:<tool>"`, `match_patterns` ist Pflicht, sonst überspringt der Router den Eintrag).
- Produces:
  - Register `musik`:
    - `agent: brain-musik`, `prefixes: [musik.]`, `enabled: true`, `mcp_servers: [musik-studio]`
    - `mcp_tools: {musik-studio: [bestand_zeigen, set_bestellen, videomusik_bestellen, auftrag_zeigen, in_laura_legen, laura_videos_suchen]}`
    - Events `musik.set.bestellen`, `musik.video.bestellen`, `musik.auftrag.zeigen`, `musik.laura.unterlegen`, jeweils mit `required_provenance: [approval_ref, cost_ref]` und `execution: { kind: mcp, server: musik-studio }`
  - Die Fähigkeiten `musik_set_bestellen`, `musik_video_bestellen`, `musik_auftrag_zeigen` und `musik_laura_unterlegen` bekommen deutsche und englische `match_patterns`:
    - „mach mir (ein|einen) set …“
    - „… sekunden musik …“
    - „leg musik unter …“
    - „wie weit ist (der|mein) musik-auftrag“
  - Dazu je `execution_target: "mcp:brain-musik:musik-studio:<werkzeug>"`.
  - Der Validator ist `kind: truth:http_ok`, `on_fail: report`, mit `postcondition: {check: http_ok, url: http://127.0.0.1:8139/health}`.
  - Wie `approval_ref` und `cost_ref` entstehen, ist nicht Teil dieser Änderung. Der Implementer liest vorher nach, wie `bubble.rate` sie bekommt (`capability_targets.py` um 1259/1321), und schreibt das Ergebnis in seinen Bericht.

- [ ] **Step 1: Failing test** `test_musik_faehigkeiten_zeigen_auf_musik_studio`: Lädt `capabilities.yaml` über den Router (`capability_router.get_capability`). Für jede der vier Fähigkeiten ist das Ergebnis nicht `None`, `execution_target` beginnt mit `mcp:brain-musik:musik-studio:`, und ein Beispielsatz („mach mir 30 Sekunden ruhige Musik“) trifft `musik_video_bestellen`.
- [ ] **Step 2:** → FAIL. Interpreter: `C:/Users/User/Desktop/Vibemind_V1/.venv/Scripts/python.exe -m pytest brain/the_brain/tests -q -p no:cacheprovider -k "registry or capabilit or musik"` aus dem Worktree-Root.
- [ ] **Step 3:** Register und Fähigkeiten eintragen. **Step 4:** Dieselben Tests laufen lassen → PASS. Kein anderer Register- oder Fähigkeiten-Test wird rot.
- [ ] **Step 5: Commit** `feat(musik): Musik-Space im Register und Faehigkeiten fuer den App-Chat`

### Task 10: Startskript und Bedienung

**Files:**
- Create: `spaces/musik/studio/scripts/musik-dienste-starten.ps1`
- Modify: `spaces/musik/studio/README.md` (Abschnitt „Musik-Dienst und Musik-Claw“), `spaces/musik/docs/LAURA-VERTRAG.md` (Abschnitt „Wie Plan 3 …“ → „So legt der Musik-Dienst …“, Ist-Stand)
- Test: `spaces/musik/studio/tests/test_startskript.py` (liest das Skript als Text)

**Interfaces:**
- Produces: `musik-dienste-starten.ps1 [-Nur <Namen>] [-Liste]`. Es ist idempotent: Ein Dienst, dessen `/health` antwortet, wird nicht neu gestartet. Gestartet wird per `Start-Process` mit Ausgabe nach `logs/musik/<name>.log` und `.err`. Die drei Dienste:
  - `musik_dienst`: `studio/.venv/Scripts/musik-dienst.exe`, Port 8139.
  - `musik_shim`: Root-`.venv` Python mit `spaces/marketing/claw/shim/marketing_shim.py --port 8141`. Die Umgebung bekommt `VIBEMIND_AGENT=musik-claw`, `VIBEMIND_BUDGET_MODUL=<Vibemind_V1>\scripts\claude_budget.py`, `SHIM_EXTRA_MCP_CONFIG=<repo>\spaces\musik\claw\musik-mcp.json` und `MCP_TOOL_TIMEOUT=900000`. Der Systemprompt kommt aus `spaces/musik/claw/AGENTS.md`, so wie der Video-Claw seinen bekommt.
  - `musik_telegram`: `studio/.venv/Scripts/musik-telegram.exe`, Port 8142.

  `-Liste` gibt Name, Port und Health-Pfad je Dienst aus (für einen späteren Wächter). Die Pfade werden aus `$PSScriptRoot` abgeleitet, nie relativ zum Prozess-cwd.

- [ ] **Step 1: Failing test** `test_startskript_nennt_drei_dienste_und_keine_adressen`: Im Skripttext stehen die drei Namen und Ports. Es gibt kein `192.168.` und kein `100.` gefolgt von einer Ziffer. `Start-Process` kommt vor, aber kein `2>&1 |`.
- [ ] **Step 2:** → FAIL. **Step 3:** Skript und Doku schreiben. **Step 4:** → PASS. `powershell -NoProfile -File ... -Liste` läuft ohne Fehler.
- [ ] **Step 5: Commit** `feat(musik): Startskript fuer Musik-Dienst, Musik-Shim und Telegram-Bot`

### Task 11: Inbetriebnahme und Abnahme (Controller)

Diesen Task macht der Controller selbst. Jeder Schritt mit Betriebseingriff kommt erst nach Felix' Ja zu genau diesem Schritt. Vorher WORKBOARD-Claim setzen und nach dem Schritt schließen.

- [ ] **Step 1: Merge und Ports.** Den Zweig nach Felix' Ja nach master bringen (wie Plan 2: Merge im Worktree auf `origin/master`, Tests, Push nach `origin`). `python scripts/port_kollisionen.py --alle` laufen lassen, Ports bestätigen oder ändern und in `vibemind.config.json` eintragen.
- [ ] **Step 2: Äußeres Repo.**
  - In `config/agent_budget.yaml` zwei Einträge: `musik-claw` (sonnet, Tagesgrenze 50, wrapper shim) und `musik-kette` (Tagesgrenze 20 Aufträge).
  - In der Root-`.env`: `MUSIK_TELEGRAM_TOKEN` (den Bot legt Felix bei BotFather an) und `MUSIK_STUDIO_URL`.
  - Nur eigene Zeilen stagen.
- [ ] **Step 3: OpenFang.**
  - Einen Block `[[mcp_servers]]` mit `name = "musik-studio"` und `transport = { type = "http", url = "http://127.0.0.1:8139/mcp" }` eintragen, sowohl in die versionierte Vorlage als auch in `~/.openfang/config.toml`.
  - Den Agenten `brain-musik` per `scripts/sync_openfang_agents.py` erzeugen und mit `scripts/verify_openfang_mcp_registration.py` prüfen.
  - Neustart nur nach den OpenFang-Regeln: nur die PID beenden, nie die Root-`.env` anfassen.
- [ ] **Step 4: Ausliefern und starten.** Haupt-Checkout per `checkout --detach` auf den Stand bringen (Felix' Ja). Danach `musik-dienste-starten.ps1` ausführen und die Marketing-Dienste `marketing_video_werkzeuge` und `marketing_video_claw` per `-Nur` neu starten. Als Wächter eine geplante Windows-Aufgabe anlegen, die das idempotente Startskript alle 5 Minuten ausführt. Sie startet nur Dienste ohne Antwort auf `/health` neu.
- [ ] **Step 5: Abnahme 2–8** nach Spec Abschnitt 8. Jeder Schritt wird gemessen und mit Lauf, Auftrags-ID und Zahlen in `ABNAHME-STUDIO.md` eingetragen. Schritt 6 deckt die Grenzen ab: Budget erschöpft, abgelaufener Link, falsche Länge.
- [ ] **Step 6:** `STATUS.md` nachziehen. Die Plan-3-Lücke wird zu Behauptungen mit Belegen. Felix' Urteil zu Abnahme 7 wird wörtlich übernommen.
- [ ] **Step 7:** WORKBOARD-Claim schließen, Doku committen und mergen bzw. pushen, beides erst nach Felix' Ja.
