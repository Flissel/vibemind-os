# Research-Bubble-Kopplung — Design

**Datum:** 2026-09-16
**Status:** Entwurf, zur Freigabe
**Betrifft:** `spaces/research`, `spaces/ideas`, `brain/the_brain/data/capabilities.yaml`, `config/space_agent_registry.yml`, `supabase/migrations/20260817_research_report_artifacts.sql`

## Ziel

Ein Research-Auftrag wird aus einer Bubble heraus ausgelöst, bezieht den Inhalt dieser
Bubble in den Auftrag ein, und legt seinen Report als Research-Doc **in genau dieser
Bubble** ab. Der Nutzer kann den zusammengesetzten Auftrag vor dem Start sehen und
bearbeiten.

## Ausgangslage — gemessen, nicht angenommen

Alle Aussagen dieses Abschnitts sind am laufenden System belegt (2026-09-16).

### Der Research-Space ist strukturell tot, nicht bloß unverdrahtet

`spaces/research/execution_target.py:63-65` fordert als Beleg für einen echten
Recherchelauf ein nichtleeres `tool_calls`:

```python
tool_calls = result.get("tool_calls")
if not isinstance(tool_calls, list) or not tool_calls:
    return self._unverified("external tool evidence is missing")
```

Die tatsächliche Antwort von `POST /api/agents/<id>/message` enthält dieses Feld nicht:

```json
{"cost_usd":0.054, "input_tokens":14, "iterations":1, "output_tokens":3603, "response":"..."}
```

Ursache: `openfang/crates/openfang-runtime/src/drivers/claude_code.rs:579` gibt hart
`tool_calls: Vec::new()` zurück. Der Treiber *protokolliert* die Werkzeugnutzung
(Z. 544-553), reicht sie aber nie an den Kernel weiter.

**Folge:** Für jeden Agenten mit Provider `claude-code` ist die Prüfung unerfüllbar.
Auch bei perfekter Recherche liefert der Space immer
`"unverified research result: external tool evidence is missing"`. Das ist der Fehler,
den dieses Design behebt — nicht der fehlende Daemon.

### Die Recherche selbst funktioniert

Rauchtest am 2026-09-16 gegen `researcher-hand`
(`agent_id 52a6d4df-6eb0-5c55-a200-b984514886ab`), 80 s, `cost_usd` 0,054:

- Treiber-Log: `Claude Code CLI invoked tools/skills count=7
  tools=ToolSearch,WebSearch,WebFetch,WebFetch,WebFetch,Bash,Write`
- Report geschrieben, drei echte Quellen, Kreuzvergleich, Konfidenzeinstufung
- **Auf Deutsch** trotz eingebackenem `language: english`
- **APA mit Abrufdatum** trotz eingebackenem `citation_style: inline_url`
- Unter **exakt dem vorgegebenen Dateinamen**

**Daraus folgt der wichtigste Design-Hebel:** Pro-Lauf-Überschreibung über den Brief
wirkt. Eine eigens konfigurierte Hand-Instanz ist nicht nötig.

### Der Report landet im Arbeitsverzeichnis des Daemons, nicht im Hand-Workspace

Der `Write` kommt von den **eigenen Werkzeugen der Claude-Code-CLI**, nicht von
OpenFangs gesandkastetem `file_write`. Deshalb landete die Datei in
`vibemind-os/openfang/` (dem `WorkingDirectory` des Daemons) und **nicht** in
`~/.openfang/workspaces/researcher-hand/` — dieses Verzeichnis ist seit 2026-06-23 leer.
Ein Polling des Hand-Workspace würde nie etwas finden.

### Der Event-Weg ist unbrauchbar

Die HAND.toml weist die Hand an, ein `research_complete`-Event mit dem Report-Pfad zu
publizieren. Das Tool existiert, aber kein Subscriber kann es matchen: `describe_event`
reduziert `EventPayload::Custom` auf `"Custom event (N bytes)"`
(`openfang-kernel/src/triggers.rs:453-455`), und `ContentMatch` (Z. 362-364) vergleicht
gegen diesen String. Event-Typ und Pfad sind darin nicht enthalten. Push-Zustellung
scheidet aus.

### Bubble-Datenmodell

Bubble = Zeile in `public.ideas` mit `parent_id IS NULL`. Inhalte = `canvas_nodes` mit
`linked_idea_id → ideas.id`. `idea_create` (`spaces/ideas/mcp_server.py:686-720`)
verlangt `bubble_id` und legt einen `canvas_nodes`-Eintrag an; `research.to_idea` führt
`bubble_id` aber nicht als `required_params`
(`config/space_agent_registry.yml:200-204`) — der erzwungene Rückbezug zur auslösenden
Bubble fehlt heute.

### Zwei Supabase-Instanzen

| | lokal (Swarm) | VM `vibemind-offload-1` |
|---|---|---|
| Adresse | `127.0.0.1:54321` | `192.168.178.65`, Tailnet `100.67.177.45` |
| Bubbles | 12 | 14 |
| `research_report_artifacts` | fehlt (404) | ungeprüft |
| Rolle | Probe | Produktion (`.env` zeigt hierher) |

Die LAN-Ports der VM sind seit 2026-09-15 gesperrt (Claim `cc-vm-lan-abschottung`);
Zugriff nur über das Tailnet.

### Nicht existente Voraussetzungen

Die Migration `20260817_research_report_artifacts.sql` beruft sich auf eine
Contract-Familie `ResearchJobV1` / `ResearchResultV1` / `ResearchIdeaHandoffV1` und auf
zwei Companion-Dokumente. **Keines davon existiert** — geprüft im äußeren Repo und in
`vibemind-os`. Die ULID-Formate der CHECK-Constraints müssen selbst erzeugt werden.

## Nicht-Ziele

- Keine UI-Arbeit in der Electron-App. Auslöser ist ein MCP-Tool.
- Keine Reparatur des `claude-code`-Treibers, damit er `tool_calls` durchreicht. Das
  wäre ein Eingriff in den OpenFang-Kern mit weit größerem Radius.
- Keine Wiederbelebung des Event-Wegs.
- Kein Umbau der bestehenden `research.*`-Registry-Events. Sie bleiben, wie sie sind;
  der neue Weg tritt daneben.

## Architektur

### 1. Nachweis auf messbare Signale umstellen

`ResearchTarget` prüft künftig nicht mehr `tool_calls`, sondern:

1. Die Report-Datei existiert am **vorgegebenen absoluten Pfad**.
2. Aus ihrem Inhalt werden Quell-URLs **selbst gezählt** (`≥ 1`, sonst Fehlschlag).
Die Treiberzeile `Claude Code CLI invoked tools/skills` in
`logs/openfang/openfang.err.log` wird **nicht** ausgewertet. Sie ist als Diagnose
nützlich, wenn ein Lauf fehlschlägt, aber sie ist kein Teil der Entscheidung — das
Parsen eines 20-MB-Logs als Erfolgskriterium wäre spröde und an das Log-Format
gebunden.

Kein Signal stammt aus dem Selbstbericht des Agenten. Fehlt die Datei oder die Quelle,
schlägt der Lauf fehl — das fail-closed-Verhalten des bisherigen Codes bleibt erhalten,
nur auf einem Beleg, der eintreten kann.

### 2. Ausgabepfad diktieren

Artefakt-Verzeichnis: `~/.openfang/research-artifacts/`, außerhalb aller Repos. Der
Brief gibt `<verzeichnis>/research_<job_id>.md` als absoluten Pfad vor. Das macht die
Abholung eindeutig (kein „suche die neueste Datei") und verhindert, dass Reports den
Arbeitsbaum verschmutzen, wie es beim Rauchtest geschah.

### 3. Asynchron mit Job-ID

`POST /api/agents/<id>/message` blockiert ohne serverseitigen Timeout. Der Rauchtest
brauchte 80 s für eine triviale Frage; ein Brief über zehn Wettbewerber läuft Minuten.
Deshalb:

- `research_start(...)` erzeugt `job_id`, stößt den Aufruf im Hintergrund an, kehrt
  sofort zurück.
- `research_status(job_id)` prüft die Zieldatei und liefert `pending` / `done` / `failed`.

Der Job-Zustand liegt als Datei neben dem Report (`research_<job_id>.job.json`), damit
ein Neustart des Aufrufers ihn nicht verliert.

### 4. Vorschau-Gate

`research_start` läuft zweistufig:

1. **Ohne `confirm`**: liest Bubble-Titel und ihre `canvas_nodes`, baut daraus den
   Auftrag und gibt ihn als Text zurück, **ohne** den Lauf zu starten.
2. **Mit `confirm` und ggf. bearbeitetem Text**: startet den Lauf.

Aufbau des Auftrags:

```
[Hintergrund aus Bubble <titel>]      <- aus canvas_nodes
[Auftrag]                              <- Brief des Nutzers, unverändert
[Erwartetes Ergebnis]                  <- siehe unten
[Vorgaben für diesen Lauf]             <- Tiefe/Stil/Zitierweise/Sprache
[Pflicht]                              <- absoluter Ausgabepfad, ≥1 Quelle, nichts erfinden
```

Der Abschnitt „Vorgaben für diesen Lauf" überschreibt die eingebackene
`User Configuration` — am 2026-09-16 als wirksam nachgewiesen.

**Zu „Erwartetes Ergebnis": kein LLM-Vorlauf.** Ein vorgeschalteter Modellaufruf, der
den Brief zu Akzeptanzkriterien verdichtet, würde Kosten und einen zusätzlichen
Fehlermodus einführen, ohne dass jemand das Ergebnis prüft. Stattdessen rein
mechanisch: enthält der Brief eine erkennbare Ergebnis-Überschrift (`Ergebnis`,
`Aufgabe`, `Anforderungen`, `Deliverables` o. ä.), werden deren Zeilen übernommen;
sonst bleibt der Abschnitt leer und trägt den Hinweis, dass der Nutzer ihn in der
Vorschau selbst füllen soll. Genau dafür ist das Vorschau-Gate da — die Verdichtung
macht der Mensch, nicht ein ungeprüfter Vorlauf.

### 5. Ablage

Migration `20260817_research_report_artifacts.sql` anwenden — **erst lokal, dann VM**.
Additives DDL, neue Tabelle, keine fremde Tabelle berührt, Rollback im Dateikopf
dokumentiert.

Nach erfolgreichem Lauf:

- **Volltext** → `research_report_artifacts` mit `subject_type='bubble'`,
  `bubble_id=<auslösende Bubble>`, `citation_count=<selbst gezählt>`,
  `depth`/`output_style` wie im Auftrag vorgegeben.
- **Kurzfassung** → `canvas_nodes` in derselben Bubble über den bestehenden
  `idea_create`-Weg, mit Verweis auf `artifact_ref`.

Der vorhandene Bubble-Sync trägt die Kurzfassung von dort ins Markdown; die
Artefakt-Tabelle hängt bewusst an keinem Outbox-Lauf (offene Entscheidung D3 der
Migration bleibt offen).

IDs: `artifact_v1_<ULID>` und `job_v1_<ULID>`, Crockford-Base32, 26 Zeichen, passend zu
den CHECK-Constraints. Erzeugung im eigenen Code, da die Contract-Familie fehlt.

### 6. Aufrufweg

Zwei MCP-Tools im Research-Space:

- `research_start(bubble_id, brief, depth?, output_style?, citation_style?, language?, confirm?)`
- `research_status(job_id)`

## Datenfluss

```
research_start(bubble_id, brief)          [ohne confirm]
  -> ideas + canvas_nodes lesen
  -> Auftrag zusammensetzen
  -> Vorschau zurück, KEIN Lauf

research_start(..., confirm=true)
  -> job_id = job_v1_<ULID>
  -> Hintergrund: POST /api/agents/52a6d4df.../message
  -> sofort job_id zurück

research_status(job_id)
  -> Datei ~/.openfang/research-artifacts/research_<job_id>.md da?
     nein  -> pending
     ja    -> URLs zählen
              0 Quellen -> failed (fail-closed)
              >=1       -> research_report_artifacts INSERT
                           + canvas_node in der Bubble
                           -> done
```

## Fehlerbehandlung

| Fall | Verhalten |
|---|---|
| OpenFang nicht erreichbar | `failed`, Grund benannt, kein Artefakt |
| Datei fehlt nach Lauf | `failed: report file missing` |
| Datei ohne Quelle | `failed: no citations` — CHECK der Tabelle würde ohnehin greifen |
| `bubble_id` unbekannt | Abbruch vor dem Lauf, kein Token verbrannt |
| Lauf läuft noch | `pending` mit Laufzeit |

## Tests

- Auftragsbau aus Bubble-Inhalt (reine Funktion, ohne Netz)
- ULID-Erzeugung gegen die Regex der CHECK-Constraints
- Zitatzählung: 0 Quellen → `failed`
- Vorschau-Stufe startet keinen Lauf (Agent-Aufruf gemockt, Aufrufzähler 0)
- `research_status` auf fehlende Datei → `pending`, nicht `failed`
- Ersatz für `test_research_execution_contract.py`: die beiden Tests, die heute
  nichtleere `tool_calls` fordern, werden gegen die neuen Belege ersetzt

## Validierung

Echter Lauf mit dem Sheerlay-Brief des Nutzers (Wettbewerbsanalyse, Blue-Ocean,
APA-Quellen, Deutsch):

1. Migration lokal anwenden, Tabelle prüfen
2. Bubble „Sheerlay" lokal anlegen, Projektkontext als Knoten
3. `research_start` ohne `confirm` → Vorschau begutachten
4. mit `confirm` starten, `research_status` bis `done`
5. Prüfen: Artefakt vorhanden, `citation_count` plausibel, Knoten in der richtigen
   Bubble, Report auf Deutsch mit APA-Quellen
6. Erst danach dieselbe Migration auf der VM anwenden

## Risiken

- **Laufzeit und Kosten unbekannt.** Der Rauchtest kostete 0,054 USD für eine triviale
  Frage in 80 s. Ein `exhaustive`-Lauf über zehn Anbieter ist um Größenordnungen
  größer. Vor dem Sheerlay-Lauf sollte `depth` bewusst gewählt werden.
- **Die CLI schreibt mit `--dangerously-skip-permissions`** (`claude_code.rs:357`). Der
  diktierte Pfad ist eine Absprache, keine Schranke. Ein fehlgeleiteter Schreibvorgang
  ist möglich; deshalb liegt das Artefaktverzeichnis außerhalb der Repos.
- **Der lokale Bubble-Bestand ist womöglich veraltet** (neueste Bubble 2026-07-14). Die
  lokale Stufe validiert den Mechanismus, nicht den Datenstand.
- **`poc-canary-sentinel`** wirft im Daemon-Log dauerhaft `JSONDecodeError` und füllt
  `openfang.err.log` (20 MB). Unabhängig von diesem Vorhaben, aber es erschwert die
  Log-Auswertung.
- **OpenFang :4200 steht unter fremdem Claim** (`cc-openfang-4200`). Der Daemon wurde am
  2026-09-16 mit ausdrücklicher Freigabe des Betreibers neu gestartet, nachdem er um
  04:05 ohne sauberes Herunterfahren gestorben war. Ursache nicht untersucht — ein
  erneuter Ausfall während eines langen Laufs ist möglich.

## Offene Entscheidungen

- D3 der Migration (eigener Sync-Lauf für Artefakte) bleibt bewusst offen.
- Ob die `research.*`-Registry-Events später auf denselben Weg umgehängt werden, ist
  nicht Teil dieses Entwurfs.
