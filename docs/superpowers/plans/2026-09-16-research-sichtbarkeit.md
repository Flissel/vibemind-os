# Research-Sichtbarkeit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein fertiger Research-Report ist als aufklappbare, formatierte Karte in der Bubble lesbar und landet dauerhaft im Rowboat-Vault.

**Architecture:** Vier unabhängige Teile. Der Ausgabepfad folgt künftig dem bestätigten Brief statt einem frisch gewürfelten Job. Die Sync-Arbeiter laufen über eine registrierte Aufgabe dauerhaft. `_persist_result` schreibt den Volltext in den Knoten, ein neuer Knoten je Lauf. Die Karte rendert Markdown, indem sie DOM-Knoten baut statt HTML zu erzeugen — eingebettetes Markup ist dadurch strukturell nicht ausführbar.

**Tech Stack:** Python 3.11 (nur Standardbibliothek), reines JavaScript im Electron-Renderer (keine neue Abhängigkeit), Tests mit `unittest` bzw. `node --test`, PowerShell für die Aufgabenregistrierung.

**Spec:** `docs/superpowers/specs/2026-09-16-research-sichtbarkeit-design.md`

## Global Constraints

- **Keine neuen Abhängigkeiten**, weder Python noch npm. Kein `marked`, kein `DOMPurify` — das ist eine ausdrückliche Entwurfsentscheidung, keine Sparmaßnahme.
- **Reporttext darf niemals über `innerHTML` in das Dokument gelangen**, auch nicht gefiltert. Struktur über `createElement`, jeder Text über `textContent`.
- **Links aus dem Report nur mit Schema `http:` oder `https:`.** Alles andere wird als Text dargestellt, nie als `href`. (`javascript:` in einem `href` ist derselbe Einbruch wie ein `<script>`.)
- Artefaktverzeichnis `~/.openfang/research-artifacts/`, Reportdatei `research_<job_id>.md`.
- `job_id` ~ `^job_v1_[0-9A-HJKMNPQRSTVWXYZ]{26}$` — von der Datenbank per CHECK erzwungen.
- Python-Tests mit `unittest` und `importlib`-Laden, Muster `spaces/ideas/tests/`. Nicht auf pytest umstellen.
- **Git: ausschließlich benannte Dateien stagen.** Dieses Repo trägt rund 58 geänderte Dateien, die anderen Leuten gehören. Niemals `git add -A` oder `git add .`.

---

### Task 1: Ausgabepfad aus dem bestätigten Brief übernehmen

Der heutige Fehler: `mcp_server.py:410` erzeugt `job_id = brief_mod.new_job_id()` **bedingungslos**; `:436` ersetzt danach nur `brief_text`. Job, Pfad und Statusabfrage hängen weiter an der neuen ID, während der Agent in den Pfad aus dem Brief schreibt. Wer dem dokumentierten Ablauf folgt, läuft garantiert hinein.

**Files:**
- Modify: `spaces/research/brief.py` (neue Funktion am Ende)
- Modify: `spaces/research/mcp_server.py:406-456` (Confirm-Zweig)
- Test: `spaces/research/tests/test_brief.py` (neue Klasse)
- Test: `spaces/research/tests/test_mcp_server.py` (neue Klasse)

**Interfaces:**
- Consumes: `brief.new_job_id()`, `_job_path(job_id)`, `_write_job_file`, `_spawn_agent_call`, `_confirm_flag`, `ToolError`
- Produces: `brief.job_id_from_brief(text: str) -> str | None` — liest die `job_id` aus einem Ausgabepfad `…/research_<job_id>.md` im Brieftext; `None`, wenn keiner drinsteht

- [ ] **Step 1: Write the failing test for the pure function**

An `spaces/research/tests/test_brief.py` anhängen:

```python
class JobIdFromBriefTests(unittest.TestCase):
    def test_reads_the_job_id_out_of_the_dictated_output_path(self) -> None:
        brief = load_brief()
        text = (
            "[Pflicht]\n"
            "1. Schreibe den Report unter EXAKT diesem Pfad: "
            "C:\\Users\\X\\.openfang\\research-artifacts\\research_job_v1_01M2N5VPP9T2PX2GJ7NG3F9XHN.md\n"
        )
        self.assertEqual(
            brief.job_id_from_brief(text), "job_v1_01M2N5VPP9T2PX2GJ7NG3F9XHN"
        )

    def test_reads_it_from_a_posix_path_too(self) -> None:
        brief = load_brief()
        text = "Pfad: /home/u/.openfang/research-artifacts/research_job_v1_0000000000000000000000000A.md"
        self.assertEqual(
            brief.job_id_from_brief(text), "job_v1_0000000000000000000000000A"
        )

    def test_returns_none_when_the_brief_carries_no_path(self) -> None:
        brief = load_brief()
        self.assertIsNone(brief.job_id_from_brief("Nur Fliesstext ohne Pfad."))

    def test_ignores_a_malformed_job_id(self) -> None:
        brief = load_brief()
        self.assertIsNone(brief.job_id_from_brief("research_job_v1_zzz.md"))

    def test_takes_the_first_when_several_appear(self) -> None:
        brief = load_brief()
        text = (
            "research_job_v1_0000000000000000000000000A.md und spaeter "
            "research_job_v1_0000000000000000000000000B.md"
        )
        self.assertEqual(
            brief.job_id_from_brief(text), "job_v1_0000000000000000000000000A"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd vibemind-os && python -m unittest spaces.research.tests.test_brief.JobIdFromBriefTests -v`
Expected: FAIL mit `AttributeError: module has no attribute 'job_id_from_brief'`.

- [ ] **Step 3: Implement the pure function**

An `spaces/research/brief.py` anhängen:

```python
# Muss zur Zeichenklasse der CHECK-Bedingung in
# 20260817_research_report_artifacts.sql passen.
_JOB_IN_PATH_RE = re.compile(r"research_(job_v1_[0-9A-HJKMNPQRSTVWXYZ]{26})\.md")


def job_id_from_brief(text: str) -> str | None:
    """Liest die job_id aus dem Ausgabepfad, den der Brief vorschreibt.

    Der bestaetigte Brief traegt den Pfad, gegen den der Agent schreibt.
    Wer stattdessen eine frische job_id erzeugt, laesst den Lauf gegen einen
    Pfad pruefen, in den niemand schreibt - genau der Fehler, den diese
    Funktion behebt.
    """
    match = _JOB_IN_PATH_RE.search(text or "")
    return match.group(1) if match else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd vibemind-os && python -m unittest spaces.research.tests.test_brief -v`
Expected: PASS, 19 Tests (14 bisherige + 5 neue).

- [ ] **Step 5: Write the failing test for the server behaviour**

An `spaces/research/tests/test_mcp_server.py` anhängen:

```python
class FinalBriefPathTests(unittest.TestCase):
    def test_confirm_with_final_brief_uses_the_job_id_from_that_brief(self) -> None:
        server = load_server()
        edited = (
            "MEIN BEARBEITETER AUFTRAG\n"
            "Schreibe nach: /tmp/research_job_v1_0000000000000000000000000A.md\n"
        )
        seen = {}
        with mock.patch.object(server, "_read_bubble", return_value=BUBBLE), \
             mock.patch.object(server, "_read_nodes", return_value=NODES), \
             mock.patch.object(server, "_spawn_agent_call"), \
             mock.patch.object(server, "_write_job_file",
                               side_effect=lambda job_id, payload: seen.update(
                                   job_id=job_id, payload=payload)):
            result = server.call_tool("research_start", {
                "bubble_id": "bub123", "brief": "x", "confirm": True,
                "final_brief": edited,
            })

        self.assertEqual(result["job_id"], "job_v1_0000000000000000000000000A")
        self.assertEqual(seen["job_id"], "job_v1_0000000000000000000000000A")
        self.assertIn("job_v1_0000000000000000000000000A", result["report_path"])
        self.assertIn("job_v1_0000000000000000000000000A", seen["payload"]["report_path"])

    def test_final_brief_without_a_path_is_rejected_before_the_run(self) -> None:
        server = load_server()
        with mock.patch.object(server, "_read_bubble", return_value=BUBBLE), \
             mock.patch.object(server, "_read_nodes", return_value=NODES), \
             mock.patch.object(server, "_spawn_agent_call") as spawn, \
             mock.patch.object(server, "_write_job_file") as write:
            with self.assertRaises(server.ToolError) as ctx:
                server.call_tool("research_start", {
                    "bubble_id": "bub123", "brief": "x", "confirm": True,
                    "final_brief": "Auftrag ohne jeden Pfad.",
                })

        self.assertTrue(str(ctx.exception).startswith("invalid_arguments"))
        spawn.assert_not_called()
        write.assert_not_called()

    def test_confirm_without_final_brief_still_uses_a_fresh_job_id(self) -> None:
        server = load_server()
        with mock.patch.object(server, "_read_bubble", return_value=BUBBLE), \
             mock.patch.object(server, "_read_nodes", return_value=NODES), \
             mock.patch.object(server, "_spawn_agent_call"), \
             mock.patch.object(server, "_write_job_file"):
            result = server.call_tool("research_start", {
                "bubble_id": "bub123", "brief": "x", "confirm": True,
            })

        self.assertRegex(result["job_id"], r"^job_v1_[0-9A-HJKMNPQRSTVWXYZ]{26}$")
        self.assertIn(result["job_id"], result["report_path"])
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd vibemind-os && python -m unittest spaces.research.tests.test_mcp_server.FinalBriefPathTests -v`
Expected: FAIL — die erste Prüfung schlägt fehl, weil `result["job_id"]` eine frisch erzeugte ID trägt statt der aus dem Brief; die zweite, weil kein Fehler geworfen wird.

- [ ] **Step 7: Implement**

In `spaces/research/mcp_server.py` den Block ab `final = arguments.get("final_brief")` (Zeile 432) bis einschließlich `_write_job_file(...)` ersetzen durch:

```python
        final = arguments.get("final_brief")
        if final is not None:
            if not isinstance(final, str) or not final.strip():
                raise ToolError("invalid_arguments: 'final_brief' must be a non-empty string")
            brief_text = final
            # Der bestaetigte Brief traegt den Pfad, gegen den der Agent
            # schreibt. Die job_id folgt IHM, nicht der oben erzeugten -
            # sonst prueft research_status einen Pfad, in den nie jemand
            # schreibt. Kein stiller Rueckfall: fehlt der Pfad, ist das ein
            # Fehler vor dem Start.
            from_brief = brief_mod.job_id_from_brief(brief_text)
            if from_brief is None:
                raise ToolError(
                    "invalid_arguments: 'final_brief' enthaelt keinen Ausgabepfad "
                    "der Form research_<job_id>.md - bearbeite die Vorschau, "
                    "ohne die Pfadzeile zu entfernen"
                )
            job_id = from_brief

        _write_job_file(job_id, {
            "job_id": job_id,
            "bubble_id": bubble_id,
            "bubble_title": bubble.get("title") or bubble_id,
            "depth": depth,
            "output_style": output_style,
            "citation_style": citation_style,
            "language": language.strip(),
            "brief": brief_text,
            "report_path": str(_job_path(job_id)),
            "started_at": time.time(),
        })
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd vibemind-os && python -m unittest discover -s spaces/research -t . -v`
Expected: PASS, alle Tests des Pakets grün, keine Warnungen.

- [ ] **Step 9: Commit**

```bash
git add spaces/research/brief.py spaces/research/mcp_server.py spaces/research/tests/test_brief.py spaces/research/tests/test_mcp_server.py
git commit -m "fix(research): Ausgabepfad folgt dem bestaetigten Brief statt einer neuen job_id"
```

---

### Task 2: Die Sync-Arbeiter überleben einen Neustart

Kein Code-Fehler, ein Betriebsmuster. Gemessen: der Outbox-Eintrag unseres Knotens trägt `applied_at NULL`, zuletzt angewendet wurde etwas am 2026-06-09, es läuft kein Arbeiter, und `~/.rowboat/knowledge/.bubble_sync_hashes.json` steht auf dem Stand vom 2026-06-19.

**Files:**
- Create: `scripts/register-bubble-sync-task.ps1`
- Create: `docs/operations/2026-09-16-bubble-sync-dauerbetrieb.md`

**Interfaces:**
- Consumes: `python -m publishing.bubble_sync.worker_db_to_fs [--once]` und `…worker_fs_to_db`, Arbeitsverzeichnis `vibemind-os/voice/python`
- Produces: registrierte Aufgabe `VibeMind-Bubble-Sync`

- [ ] **Step 1: Ausgangszustand messen und festhalten**

```bash
cd vibemind-os/voice/python
python -m publishing.bubble_sync.worker_db_to_fs --once
```

Erwartet: der Lauf arbeitet die offenen Outbox-Einträge ab und endet. Die Ausgabe wörtlich für die Betriebsnotiz festhalten — sie ist der Beleg, dass der Rückstand abgebaut wurde.

Schlägt der Aufruf fehl (Importfehler, fehlende Umgebungsvariable), **anhalten und berichten** — dann ist das ein eigener Befund und nicht Teil dieser Aufgabe.

- [ ] **Step 2: Unabhängig prüfen, dass der Rückstand weg ist**

```bash
cd /c/Users/User/Desktop/Vibemind_V1 && python - <<'PY'
import re, pathlib, urllib.request, json
env = pathlib.Path(".env").read_text(encoding="utf-8", errors="replace")
key = re.search(r'^SUPABASE_SERVICE_ROLE_KEY=(.+)$', env, re.M).group(1).strip()
req = urllib.request.Request(
    "http://127.0.0.1:54321/rest/v1/canvas_sync_outbox?select=id&applied_at=is.null",
    headers={"apikey": key, "Authorization": f"Bearer {key}"})
print("unangewendet:", len(json.load(urllib.request.urlopen(req, timeout=10))))
PY
```

Erwartet: `unangewendet: 0`. Zusätzlich prüfen, ob unter `~/.rowboat/knowledge/` jetzt eine Datei für den Sheerlay-Knoten liegt — das ist der eigentliche Beweis, nicht die Zahl.

- [ ] **Step 3: Registrierungsskript schreiben**

`scripts/register-bubble-sync-task.ps1`:

```powershell
<#
    Registriert die Aufgabe VibeMind-Bubble-Sync, die beide Sync-Arbeiter bei
    der Anmeldung startet.

    Grund: die Arbeiter werden sonst als Kindprozess gestartet und ueberleben
    keinen Neustart. Sie lagen dadurch vom 2026-06-09 bis zum 2026-09-16 still,
    ohne dass etwas scheiterte - es passierte nur nichts mehr. Dasselbe Muster
    hatte zuvor schon die Marketing-Dienste drei Tage lang stillgelegt.

    Idempotent: mehrfaches Aufrufen erzeugt keine Dubletten.
    -Pruefen berichtet nur und veraendert nichts.
#>
param([switch]$Pruefen)

$ErrorActionPreference = "Stop"
$TaskName = "VibeMind-Bubble-Sync"
$Root     = Split-Path -Parent $PSScriptRoot
$WorkDir  = Join-Path $Root "vibemind-os\voice\python"
$LogDir   = Join-Path $Root "logs\bubble-sync"

$vorhanden = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if ($Pruefen) {
    if ($vorhanden) {
        Write-Output "Aufgabe '$TaskName' ist registriert, Zustand: $($vorhanden.State)"
    } else {
        Write-Output "Aufgabe '$TaskName' ist NICHT registriert"
    }
    $laeuft = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like "*bubble_sync*" }
    Write-Output "laufende Sync-Prozesse: $(@($laeuft).Count)"
    exit 0
}

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

# Beide Richtungen als eigener Prozess, Ausgabe in Dateien statt in eine Pipe:
# Pipe-Pumper sterben mit der startenden Sitzung.
$cmd = @(
    "Set-Location '$WorkDir';",
    "Start-Process python -ArgumentList '-m','publishing.bubble_sync.worker_db_to_fs'",
    "-WindowStyle Hidden",
    "-RedirectStandardOutput '$LogDir\db_to_fs.log' -RedirectStandardError '$LogDir\db_to_fs.err.log';",
    "Start-Process python -ArgumentList '-m','publishing.bubble_sync.worker_fs_to_db'",
    "-WindowStyle Hidden",
    "-RedirectStandardOutput '$LogDir\fs_to_db.log' -RedirectStandardError '$LogDir\fs_to_db.err.log'"
) -join " "

$action  = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -Command `"$cmd`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable

if ($vorhanden) {
    Set-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings | Out-Null
    Write-Output "Aufgabe '$TaskName' aktualisiert (war bereits registriert)"
} else {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Description "Startet die Bubble-Sync-Arbeiter bei der Anmeldung" | Out-Null
    Write-Output "Aufgabe '$TaskName' registriert"
}
```

- [ ] **Step 4: Registrieren und die Idempotenz belegen**

```powershell
pwsh -NoProfile -File scripts/register-bubble-sync-task.ps1 -Pruefen
pwsh -NoProfile -File scripts/register-bubble-sync-task.ps1
pwsh -NoProfile -File scripts/register-bubble-sync-task.ps1
pwsh -NoProfile -File scripts/register-bubble-sync-task.ps1 -Pruefen
```

Erwartet: erst „NICHT registriert", dann „registriert", dann „aktualisiert (war bereits registriert)" — **kein Fehler beim zweiten Lauf**, das ist der Idempotenz-Beleg —, zuletzt „ist registriert, Zustand: Ready".

- [ ] **Step 5: Beweisen, dass die Aufgabe die Arbeiter wirklich startet**

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*bubble_sync*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
pwsh -NoProfile -File scripts/register-bubble-sync-task.ps1 -Pruefen
Start-ScheduledTask -TaskName "VibeMind-Bubble-Sync"
Start-Sleep -Seconds 8
pwsh -NoProfile -File scripts/register-bubble-sync-task.ps1 -Pruefen
```

Erwartet: nach dem Töten meldet der Prüfmodus `laufende Sync-Prozesse: 0`, nach `Start-ScheduledTask` wieder `2`. Ohne diesen Beleg gilt die Aufgabe als unbewiesen — eine registrierte Aufgabe, die nichts startet, sieht genauso aus wie eine, die funktioniert.

- [ ] **Step 6: Betriebsnotiz schreiben**

`docs/operations/2026-09-16-bubble-sync-dauerbetrieb.md` mit: dem gemessenen Ausgangszustand (Zahl der unangewendeten Einträge vorher/nachher, Datum des letzten angewendeten Eintrags), der Ausgabe aus Schritt 1, den vier Prüfmodus-Ausgaben aus Schritt 4, dem Tötungs-Beleg aus Schritt 5, und dem Hinweis, dass die Registrierung erst **bei der nächsten Anmeldung** von selbst greift — bis dahin muss `Start-ScheduledTask` einmal von Hand laufen.

- [ ] **Step 7: Commit**

```bash
git add scripts/register-bubble-sync-task.ps1 docs/operations/2026-09-16-bubble-sync-dauerbetrieb.md
git commit -m "feat(sync): Bubble-Sync-Arbeiter ueberleben einen Neustart"
```

---

### Task 3: Volltext im Knoten, ein neuer Knoten je Lauf

**Files:**
- Modify: `spaces/research/mcp_server.py:339-393` (`_persist_result`)
- Test: `spaces/research/tests/test_mcp_server.py` (`StatusTests` erweitern)

**Interfaces:**
- Consumes: `_find_existing_artifact_ref`, `_find_existing_node_id`, `_context_disclosure`, `_request`, `brief_mod.new_artifact_ref`
- Produces: unveränderte Rückgabe von `_persist_result` (`status`, `job_id`, `artifact_ref`, `citation_count`, `bubble_id`, `node_id`)

- [ ] **Step 1: Write the failing tests**

An die Klasse `StatusTests` in `spaces/research/tests/test_mcp_server.py` anhängen:

```python
    def test_node_carries_the_full_report_not_a_stub(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            server = self._server_with_artifacts(tmp)
            job_id = "job_v1_0000000000000000000000000F"
            self._job_state(server, job_id)
            report = (
                "# Titel\n\nErster Absatz mit https://example.test/a\n\n"
                "## Abschnitt\n\nZweiter Absatz, der im Stummel fehlen wuerde.\n"
            )
            server._job_path(job_id).write_text(report, encoding="utf-8")
            fake = _FakeSupabase()
            with mock.patch.object(server, "_request", side_effect=fake):
                server.call_tool("research_status", {"job_id": job_id})

        node = fake.nodes[0]
        self.assertEqual(node["content"], report)
        self.assertIn("Zweiter Absatz", node["content"])
        self.assertNotIn("Vollstaendiger Report:", node["content"])

    def test_node_title_carries_the_date_so_runs_stay_distinguishable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            server = self._server_with_artifacts(tmp)
            job_id = "job_v1_0000000000000000000000000G"
            self._job_state(server, job_id)
            server._job_path(job_id).write_text("https://example.test/a\n", encoding="utf-8")
            fake = _FakeSupabase()
            with mock.patch.object(server, "_request", side_effect=fake):
                server.call_tool("research_status", {"job_id": job_id})

        self.assertRegex(fake.nodes[0]["title"], r"^Research: Sheerlay \d{4}-\d{2}-\d{2}$")

    def test_a_second_run_on_the_same_bubble_creates_its_own_node(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            server = self._server_with_artifacts(tmp)
            fake = _FakeSupabase()
            for suffix in ("H", "J"):
                job_id = f"job_v1_0000000000000000000000000{suffix}"
                self._job_state(server, job_id)
                server._job_path(job_id).write_text(
                    f"Lauf {suffix} https://example.test/{suffix}\n", encoding="utf-8")
                with mock.patch.object(server, "_request", side_effect=fake):
                    server.call_tool("research_status", {"job_id": job_id})

        self.assertEqual(len(fake.nodes), 2)
        self.assertEqual(len(fake.artifacts), 2)
        self.assertNotEqual(fake.nodes[0]["content"], fake.nodes[1]["content"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd vibemind-os && python -m unittest spaces.research.tests.test_mcp_server.StatusTests -v`
Expected: FAIL — der Knoteninhalt trägt heute den Stummel `"…\n\nVollstaendiger Report: artifact_v1_…\nQuellen: N"` statt des Reports, und der Titel trägt kein Datum.

- [ ] **Step 3: Implement**

In `_persist_result` die Zeilen 341 und 375-383 ersetzen. Zeile 341:

```python
    # Datum im Titel, damit mehrere Laeufe an derselben Bubble
    # unterscheidbar bleiben - ein Lauf je Knoten ist der Grund, warum der
    # Knoten write-once ist und der Volltext hier ueberhaupt stehen darf.
    stamp = time.strftime("%Y-%m-%d", time.localtime())
    title = f"Research: {state.get('bubble_title') or bubble_id} {stamp}"
```

Der Knoten-Einfügeblock:

```python
    node_id = _find_existing_node_id(bubble_id, artifact_ref)
    if node_id is None:
        node = _request("POST", "canvas_nodes", body={
            "node_type": "research",
            "title": title[:120],
            # Volltext, nicht nur eine Kurzfassung: der Knoten ist der Weg,
            # auf dem der Report in den Vault gelangt (render_canvas_note
            # schreibt eine Datei je Knoten, mit User-Fence fuer
            # Annotationen). Tragbar, weil jeder Lauf seinen eigenen Knoten
            # bekommt und dieser danach nie wieder angefasst wird.
            "content": text,
            "x": 0, "y": 0,
            "linked_idea_id": bubble_id,
            "metadata": {
                "width": 320.0, "height": 220.0,
                "artifact_ref": artifact_ref,
                "citation_count": citations,
            },
        })
        node_id = node[0]["id"] if isinstance(node, list) and node else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd vibemind-os && python -m unittest discover -s spaces/research -t . -v`
Expected: PASS, das ganze Paket grün. Die bestehenden Idempotenz-Tests müssen **unverändert** halten — sie belegen, dass ein Wiederholungsversuch desselben Laufs weiterhin keinen zweiten Knoten erzeugt.

- [ ] **Step 5: Commit**

```bash
git add spaces/research/mcp_server.py spaces/research/tests/test_mcp_server.py
git commit -m "feat(research): Knoten traegt den vollen Report, ein Knoten je Lauf"
```

---

### Task 4: Markdown als DOM bauen, nicht als HTML erzeugen

Der sicherheitskritische Teil. `universe_canvas.js:563` setzt Knoteninhalt per `innerHTML` mit dem Kommentar „safe — all content originates from the Python backend". Für einen Report, dessen Text aus fremden Webseiten stammt, gilt das nicht.

Der Renderer wird zweigeteilt: eine **reine** Parse-Funktion, die eine Baumbeschreibung aus einfachen Objekten liefert (ohne DOM, ohne Browser testbar), und ein trivialer Bauer, der diese Beschreibung in DOM-Knoten übersetzt. Dadurch ist die gesamte Logik mit `node --test` prüfbar, und es entsteht an keiner Stelle ein HTML-String.

**Files:**
- Create: `voice/electron-app/renderer/lib/research-markdown.js`
- Test: `voice/electron-app/renderer/lib/research-markdown.test.js`

**Interfaces:**
- Consumes: nichts
- Produces:
  - `parseMarkdown(text: string) -> Block[]` — rein, kein DOM. `Block` ist eines von:
    `{kind:'heading', level:1..4, spans:Span[]}`, `{kind:'paragraph', spans:Span[]}`,
    `{kind:'list', ordered:boolean, items:Span[][]}`, `{kind:'code', text:string}`,
    `{kind:'quote', spans:Span[]}`, `{kind:'table', rows:Span[][][]}`
  - `Span` ist eines von: `{t:'text', v:string}`, `{t:'strong', v:string}`,
    `{t:'em', v:string}`, `{t:'code', v:string}`, `{t:'link', v:string, href:string}`
  - `SAFE_SCHEMES` — `['http:', 'https:']`
  - `buildInto(el: Element, blocks: Block[], doc: Document) -> void`

- [ ] **Step 1: Write the failing test**

`voice/electron-app/renderer/lib/research-markdown.test.js`:

```javascript
const test = require('node:test');
const assert = require('node:assert');
const { parseMarkdown } = require('./research-markdown.js');

test('Ueberschriften mit ihrer Ebene', () => {
    const blocks = parseMarkdown('# Eins\n\n### Drei\n');
    assert.deepStrictEqual(blocks[0], { kind: 'heading', level: 1, spans: [{ t: 'text', v: 'Eins' }] });
    assert.strictEqual(blocks[1].level, 3);
});

test('Absatz mit Fettung und Kursiv', () => {
    const [block] = parseMarkdown('Ein **fetter** und *schraeger* Satz.');
    assert.strictEqual(block.kind, 'paragraph');
    assert.deepStrictEqual(block.spans, [
        { t: 'text', v: 'Ein ' },
        { t: 'strong', v: 'fetter' },
        { t: 'text', v: ' und ' },
        { t: 'em', v: 'schraeger' },
        { t: 'text', v: ' Satz.' },
    ]);
});

test('Listen, geordnet und ungeordnet', () => {
    const [ul] = parseMarkdown('- eins\n- zwei\n');
    assert.strictEqual(ul.kind, 'list');
    assert.strictEqual(ul.ordered, false);
    assert.strictEqual(ul.items.length, 2);
    const [ol] = parseMarkdown('1. eins\n2. zwei\n');
    assert.strictEqual(ol.ordered, true);
});

test('Codeblock bleibt woertlich', () => {
    const [block] = parseMarkdown('```\nzeile **nicht fett**\n```\n');
    assert.strictEqual(block.kind, 'code');
    assert.strictEqual(block.text, 'zeile **nicht fett**');
});

test('Tabelle mit Kopf- und Datenzeile', () => {
    const [block] = parseMarkdown('| A | B |\n|---|---|\n| 1 | 2 |\n');
    assert.strictEqual(block.kind, 'table');
    assert.strictEqual(block.rows.length, 2);
    assert.deepStrictEqual(block.rows[1][0], [{ t: 'text', v: '1' }]);
});

test('Link mit erlaubtem Schema', () => {
    const [block] = parseMarkdown('Siehe [Quelle](https://example.test/a).');
    const link = block.spans.find(s => s.t === 'link');
    assert.strictEqual(link.href, 'https://example.test/a');
    assert.strictEqual(link.v, 'Quelle');
});

test('javascript:-Link wird zu Text, niemals zu einem href', () => {
    const [block] = parseMarkdown('Klick [hier](javascript:alert(1)).');
    assert.strictEqual(block.spans.some(s => s.t === 'link'), false);
    const zusammen = block.spans.map(s => s.v).join('');
    assert.match(zusammen, /hier/);
});

test('eingebettetes HTML bleibt Text - es entsteht nie ein Element', () => {
    const [block] = parseMarkdown('Harmlos <img src=x onerror=alert(1)> weiter');
    const alleSpans = JSON.stringify(block.spans);
    assert.match(alleSpans, /onerror/);
    assert.strictEqual(block.spans.every(s => ['text','strong','em','code','link'].includes(s.t)), true);
});

test('unbekannte Syntax faellt auf Text zurueck, nicht auf einen Fehler', () => {
    const blocks = parseMarkdown(':::warnung\nirgendwas\n:::\n');
    assert.strictEqual(blocks.length > 0, true);
    assert.strictEqual(blocks.every(b => typeof b.kind === 'string'), true);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd vibemind-os/voice/electron-app/renderer/lib && node --test`
Expected: FAIL — `Cannot find module './research-markdown.js'`.

- [ ] **Step 3: Implement the parser and the DOM builder**

`voice/electron-app/renderer/lib/research-markdown.js`:

```javascript
'use strict';
/**
 * Markdown -> DOM fuer Research-Karten.
 *
 * Erzeugt an KEINER Stelle einen HTML-String. parseMarkdown liefert eine
 * Baumbeschreibung aus einfachen Objekten, buildInto uebersetzt sie mit
 * createElement/textContent in DOM-Knoten. Eingebettetes Markup im Report
 * ist dadurch strukturell nicht ausfuehrbar - es gibt keinen Parse-Schritt,
 * der es interpretieren koennte. Der Reporttext stammt aus fremden
 * Webseiten und ist der am wenigsten vertrauenswuerdige Inhalt im System.
 */

const SAFE_SCHEMES = ['http:', 'https:'];

function safeHref(raw) {
    try {
        const u = new URL(raw, 'https://invalid.local/');
        return SAFE_SCHEMES.includes(u.protocol) ? u.href : null;
    } catch {
        return null;
    }
}

const INLINE_RE = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|\[[^\]]+\]\([^)\s]+\))/;

function parseInline(text) {
    const spans = [];
    let rest = String(text ?? '');
    while (rest.length) {
        const m = INLINE_RE.exec(rest);
        if (!m) { spans.push({ t: 'text', v: rest }); break; }
        if (m.index > 0) spans.push({ t: 'text', v: rest.slice(0, m.index) });
        const tok = m[0];
        if (tok.startsWith('**')) {
            spans.push({ t: 'strong', v: tok.slice(2, -2) });
        } else if (tok.startsWith('`')) {
            spans.push({ t: 'code', v: tok.slice(1, -1) });
        } else if (tok.startsWith('[')) {
            const cut = tok.indexOf('](');
            const label = tok.slice(1, cut);
            const href = safeHref(tok.slice(cut + 2, -1));
            // Kein erlaubtes Schema => der Link wird Text. Ein javascript:
            // in einem href ist derselbe Einbruch wie ein <script>.
            if (href) spans.push({ t: 'link', v: label, href });
            else spans.push({ t: 'text', v: tok });
        } else {
            spans.push({ t: 'em', v: tok.slice(1, -1) });
        }
        rest = rest.slice(m.index + tok.length);
    }
    return spans.length ? spans : [{ t: 'text', v: '' }];
}

function splitRow(line) {
    return line.replace(/^\||\|$/g, '').split('|').map(c => parseInline(c.trim()));
}

function parseMarkdown(text) {
    const lines = String(text ?? '').split(/\r?\n/);
    const blocks = [];
    let i = 0;

    while (i < lines.length) {
        const line = lines[i];

        if (!line.trim()) { i++; continue; }

        if (line.startsWith('```')) {
            const body = [];
            i++;
            while (i < lines.length && !lines[i].startsWith('```')) body.push(lines[i++]);
            i++; // schliessende Zeile
            blocks.push({ kind: 'code', text: body.join('\n') });
            continue;
        }

        const heading = /^(#{1,4})\s+(.*)$/.exec(line);
        if (heading) {
            blocks.push({ kind: 'heading', level: heading[1].length, spans: parseInline(heading[2]) });
            i++;
            continue;
        }

        if (/^>\s?/.test(line)) {
            blocks.push({ kind: 'quote', spans: parseInline(line.replace(/^>\s?/, '')) });
            i++;
            continue;
        }

        if (line.includes('|') && /^\s*\|/.test(line)) {
            const rows = [];
            while (i < lines.length && /^\s*\|/.test(lines[i])) {
                if (!/^\s*\|[\s:-]+\|/.test(lines[i])) rows.push(splitRow(lines[i].trim()));
                i++;
            }
            blocks.push({ kind: 'table', rows });
            continue;
        }

        const bullet = /^\s*([-*])\s+(.*)$/;
        const number = /^\s*\d+\.\s+(.*)$/;
        if (bullet.test(line) || number.test(line)) {
            const ordered = number.test(line);
            const items = [];
            while (i < lines.length) {
                const b = bullet.exec(lines[i]);
                const n = number.exec(lines[i]);
                if (ordered && n) items.push(parseInline(n[1]));
                else if (!ordered && b) items.push(parseInline(b[2]));
                else break;
                i++;
            }
            blocks.push({ kind: 'list', ordered, items });
            continue;
        }

        const para = [];
        while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|>|\s*\||\s*[-*]\s|\s*\d+\.\s)/.test(lines[i])) {
            para.push(lines[i++]);
        }
        if (para.length) blocks.push({ kind: 'paragraph', spans: parseInline(para.join(' ')) });
        else i++; // Zeile passte in kein Muster: weiter, nie haengenbleiben
    }
    return blocks;
}

function appendSpans(parent, spans, doc) {
    for (const s of spans) {
        if (s.t === 'link') {
            const a = doc.createElement('a');
            a.href = s.href;
            a.target = '_blank';
            a.rel = 'noopener noreferrer';
            a.textContent = s.v;
            parent.appendChild(a);
            continue;
        }
        const tag = s.t === 'strong' ? 'strong' : s.t === 'em' ? 'em' : s.t === 'code' ? 'code' : 'span';
        const el = doc.createElement(tag);
        el.textContent = s.v;
        parent.appendChild(el);
    }
}

function buildInto(el, blocks, doc) {
    for (const b of blocks) {
        if (b.kind === 'heading') {
            const h = doc.createElement('h' + b.level);
            appendSpans(h, b.spans, doc);
            el.appendChild(h);
        } else if (b.kind === 'code') {
            const pre = doc.createElement('pre');
            const code = doc.createElement('code');
            code.textContent = b.text;
            pre.appendChild(code);
            el.appendChild(pre);
        } else if (b.kind === 'list') {
            const list = doc.createElement(b.ordered ? 'ol' : 'ul');
            for (const item of b.items) {
                const li = doc.createElement('li');
                appendSpans(li, item, doc);
                list.appendChild(li);
            }
            el.appendChild(list);
        } else if (b.kind === 'table') {
            const table = doc.createElement('table');
            b.rows.forEach((row, idx) => {
                const tr = doc.createElement('tr');
                for (const cell of row) {
                    const td = doc.createElement(idx === 0 ? 'th' : 'td');
                    appendSpans(td, cell, doc);
                    tr.appendChild(td);
                }
                table.appendChild(tr);
            });
            el.appendChild(table);
        } else if (b.kind === 'quote') {
            const q = doc.createElement('blockquote');
            appendSpans(q, b.spans, doc);
            el.appendChild(q);
        } else {
            const p = doc.createElement('p');
            appendSpans(p, b.spans, doc);
            el.appendChild(p);
        }
    }
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = { parseMarkdown, buildInto, SAFE_SCHEMES };
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd vibemind-os/voice/electron-app/renderer/lib && node --test`
Expected: PASS, 9 Tests.

- [ ] **Step 5: Commit**

```bash
git add voice/electron-app/renderer/lib/research-markdown.js voice/electron-app/renderer/lib/research-markdown.test.js
git commit -m "feat(canvas): Markdown-Renderer, der DOM baut statt HTML zu erzeugen"
```

---

### Task 5: Die aufklappbare Research-Karte

**Files:**
- Modify: `voice/electron-app/renderer/universe_canvas.js:526-595` (Kartenaufbau), `:624-655` (`getTypeIcon`), `:671` (`getNodeContent`)
- Modify: `voice/electron-app/renderer/styles/canvas.css`
- Modify: `voice/electron-app/renderer/index.html` (Skript einbinden)

**Interfaces:**
- Consumes: `parseMarkdown`, `buildInto` aus Task 4
- Produces: `renderResearchNode(contentEl, data, doc)` als Methode der Canvas-Klasse

- [ ] **Step 1: Skript einbinden**

In `voice/electron-app/renderer/index.html` **vor** `universe_canvas.js`:

```html
<script src="lib/research-markdown.js"></script>
```

Da die Datei `module.exports` nur setzt, wenn `module` existiert, funktioniert sie im Browser als globales Skript — `parseMarkdown` und `buildInto` stehen dann auf `window`.

- [ ] **Step 2: Symbol ergänzen**

In `getTypeIcon`, im zweiten `switch` (Zeile 643-654), vor `default:` einfügen:

```javascript
            case 'research': return '🔬';
```

- [ ] **Step 3: Kartenaufbau umstellen**

In `createNodeElement` die Zeilen 561-564 ersetzen:

```javascript
        // Content area (type-specific)
        const content = document.createElement('div');
        content.className = 'node-content';
        if (data.type === 'research') {
            // Reporttext stammt aus fremden Webseiten: DOM bauen, nie
            // innerHTML. Siehe lib/research-markdown.js.
            this.renderResearchNode(content, data, document);
        } else {
            content.innerHTML = this.getNodeContent(data);
        }
        el.appendChild(content);
```

- [ ] **Step 4: Die Methode ergänzen**

Als neue Methode der Klasse einfügen — **zwischen** dem Ende von `getNodeContent` und dem
Beginn von `escapeHtml` (Zeile 699). Nicht innerhalb einer der beiden Methoden.

Randnotiz: dass die Klasse bereits ein `escapeHtml` besitzt, ist kein Grund, es hier zu
benutzen. Escapen setzt voraus, dass anschließend HTML geparst wird; dieser Weg erzeugt
gar kein HTML.

```javascript
    renderResearchNode(contentEl, data, doc) {
        const text = (typeof data.content === 'string')
            ? data.content
            : (data.content?.text || '');
        const citations = data.metadata?.citation_count;

        const meta = doc.createElement('div');
        meta.className = 'research-meta';
        meta.textContent = (citations === undefined)
            ? 'Research-Report'
            : `Research-Report · ${citations} Quellen`;
        contentEl.appendChild(meta);

        const body = doc.createElement('div');
        body.className = 'research-body collapsed';
        buildInto(body, parseMarkdown(text), doc);
        contentEl.appendChild(body);

        const toggle = doc.createElement('button');
        toggle.className = 'research-toggle';
        toggle.textContent = 'Aufklappen';
        toggle.addEventListener('click', (e) => {
            e.stopPropagation();
            const zu = body.classList.toggle('collapsed');
            toggle.textContent = zu ? 'Aufklappen' : 'Zuklappen';
        });
        contentEl.appendChild(toggle);
    }
```

- [ ] **Step 5: CSS ergänzen**

An `voice/electron-app/renderer/styles/canvas.css` anhängen:

```css
/* Research-Karten: zusammengeklappt eine Vorschau, aufgeklappt ein
   eigener Scrollbereich - ein 436-Zeilen-Report darf die Flaeche nicht
   sprengen. */
.canvas-node[data-node-type="research"] { width: 320px; }

.canvas-node[data-node-type="research"] .research-meta {
    font-size: 11px; opacity: 0.7; margin-bottom: 6px;
}

.canvas-node[data-node-type="research"] .research-body {
    overflow-y: auto; max-height: 480px; font-size: 12px; line-height: 1.45;
}

.canvas-node[data-node-type="research"] .research-body.collapsed {
    max-height: 90px; overflow: hidden;
    mask-image: linear-gradient(to bottom, black 55%, transparent 100%);
}

.canvas-node[data-node-type="research"] .research-body h1,
.canvas-node[data-node-type="research"] .research-body h2,
.canvas-node[data-node-type="research"] .research-body h3,
.canvas-node[data-node-type="research"] .research-body h4 {
    font-size: 13px; margin: 8px 0 4px;
}

.canvas-node[data-node-type="research"] .research-body table {
    border-collapse: collapse; width: 100%; font-size: 11px;
}
.canvas-node[data-node-type="research"] .research-body th,
.canvas-node[data-node-type="research"] .research-body td {
    border: 1px solid rgba(255,255,255,0.15); padding: 2px 4px; text-align: left;
}
.canvas-node[data-node-type="research"] .research-body pre {
    overflow-x: auto; font-size: 11px;
}
.canvas-node[data-node-type="research"] .research-toggle {
    margin-top: 6px; font-size: 11px; cursor: pointer;
}
```

- [ ] **Step 6: In der laufenden App prüfen**

Die Electron-App starten, die Bubble `4bdaa482-755c-4284-96b0-750c3ce5b1d6` („Sheerlay") öffnen und belegen:

1. Die Karte trägt das Symbol 🔬, den Titel mit Datum und die Quellenzahl.
2. Zusammengeklappt zeigt sie eine kurze Vorschau, der Umschalter heißt „Aufklappen".
3. Aufgeklappt ist der Report **formatiert** — Überschriften, Listen, die Strategy-Canvas-Tabelle —, scrollbar, und der Umschalter heißt „Zuklappen".
4. Ein Quellenlink öffnet sich und zeigt auf `https://…`.

Einen Screenshot der aufgeklappten Karte anfertigen und im Bericht nennen. Lässt sich die App in dieser Umgebung nicht starten, ist das **ausdrücklich zu berichten** — dann gilt Teil 3 als unbewiesen, nicht als erledigt.

- [ ] **Step 7: Den Sicherheitsbeleg am echten Knoten führen**

Einen zweiten Knoten in derselben Bubble anlegen, dessen Inhalt eingebettetes Markup trägt, und in der App nachsehen:

```bash
cd /c/Users/User/Desktop/Vibemind_V1 && python - <<'PY'
import re, pathlib, urllib.request, json
env = pathlib.Path(".env").read_text(encoding="utf-8", errors="replace")
key = re.search(r'^SUPABASE_SERVICE_ROLE_KEY=(.+)$', env, re.M).group(1).strip()
body = {
    "node_type": "research",
    "title": "XSS-Probe (Wegwerfknoten)",
    "content": "# Probe\n\nHarmlos <img src=x onerror=alert(1)> und [klick](javascript:alert(2)).\n",
    "x": 400, "y": 0,
    "linked_idea_id": "4bdaa482-755c-4284-96b0-750c3ce5b1d6",
    "metadata": {"width": 320.0, "height": 220.0},
}
req = urllib.request.Request(
    "http://127.0.0.1:54321/rest/v1/canvas_nodes",
    data=json.dumps(body).encode("utf-8"), method="POST",
    headers={"apikey": key, "Authorization": f"Bearer {key}",
             "Content-Type": "application/json", "Prefer": "return=representation"})
print(json.load(urllib.request.urlopen(req, timeout=10))[0]["id"])
PY
```

Erwartet: In der Karte erscheint `<img src=x onerror=alert(1)>` als **sichtbarer Text**, es öffnet sich kein Dialog, und „klick" ist **kein** Link. Danach den Wegwerfknoten wieder löschen (die ausgegebene ID per `DELETE` auf `canvas_nodes?id=eq.<id>`).

Erscheint stattdessen ein Dialog oder verschwindet das Markup aus der Anzeige, **sofort anhalten** — dann greift die Trennung nicht, und nichts davon darf committet werden.

- [ ] **Step 8: Commit**

```bash
git add voice/electron-app/renderer/universe_canvas.js voice/electron-app/renderer/styles/canvas.css voice/electron-app/renderer/index.html
git commit -m "feat(canvas): aufklappbare Research-Karte mit formatiertem, nicht ausfuehrbarem Report"
```

---

## Nach dem Plan

Bewusst nicht enthalten:

- Eine eigene Sync-Spur für `research_report_artifacts` (offene Entscheidung D3).
- Das 300-Sekunden-Limit im `claude-code`-Treiber — ein längerer Rechercheauftrag scheitert weiterhin.
- Die Registrierung des `spaces-research`-MCP-Servers bei einem Client.
- Ein Lesefenster neben dem Canvas.
