# Laura als VibeMind Video-Space

_Design-Spec, 2026-08-22. Entscheidungen mit dem User abgestimmt (Session Laura/Narrated-Reel):
alte Pipe weitgehend ersetzen, Rowboat-Publish mit Template, Konfiguration über die
Space-Registry → OpenFang, Event-Mapping „wie unser Chat", Laura-UI zunächst als Embed._

## Kontext

Laura (Repo `vibemind-lab/lauras_star`) ist der frame-genaue, local-first KI-Videoeditor:
FastAPI-Backend auf `127.0.0.1:8765`, eigener MCP-Server (`services/mcp`, 28 Tools inkl.
`build_narrated_reel`), Chatterbox-TTS-Sidecar (`services/tts-sidecar`, Port 8898) und der
neue Narrated-Reel-Endpoint: Beat-Liste → Collage-Timeline mit Clone-Voice, Karaoke-Captions
und Render in einem Job. Der VibeMind-`video`-Space existiert bereits (Python-Backend-Agent
auf `events:tasks:video` + `brain-video`-Eintrag in der Registry), führt aber das alte
vibevideo-Stack (Team-Pipeline, Demo-Builder, eigenes Chatterbox) bzw. db_*-Platzhalter aus.

## Ziel

Der `video`-Space fährt Laura. Sprache/Chat („Mach ein Produktvideo aus …") → Brain-Intent →
`video.*`-Event → Laura-Tool. Fertige Videos landen automatisch als saubere Rowboat-Notizen
mit Summary und Metadaten. Die Laura-UI erscheint als Space-Tab in der VibeMind-Shell.

## Entscheidungen (bindend)

### 1. Alte Pipe: Ersetzen mit gezieltem Behalten

| Alt-Tool | Entscheidung |
|---|---|
| `team_run_step`, `team_pipeline_status` | **ersetzen** durch Laura-Production (deprecaten, Events umziehen) |
| `demo_analyze`, `demo_build` | **ersetzen** durch `video.reel` (Narrated-Reel kann das besser) |
| `voice_clone`, `voice_tts` | **umbiegen** auf den tts-sidecar (EINE Chatterbox-Instanz, ein GPU-Lock; altes In-Process-Chatterbox stilllegen) |
| `lipsync_run`, `lipsync_analyze` (MuseTalk) | **behalten** (eigenständig; späterer Merge mit Lauras `ai.lipsync` ist ein eigener Arc) |
| `vision_generate` (Sora) | **behalten als experimental** — `video.vision` bleibt aufs alte CLI verdrahtet (`spaces/video/vibevideo/sora/`); Substanz-Audit separat |
| `scan_video_outputs`, `import_videos`, `video_status` | **behalten und erweitern** (Filing + Health inkl. Laura/Sidecar) |
| `publish_videos_to_rowboat` | **behalten und auf das neue Template umbauen** (siehe 3.) |

### 2. Laura-Anbindung: MCP-Scope statt neuem Tool-Wrapper

OpenFang bekommt in `openfang/openfang.vibemind.toml` einen MCP-Server-Eintrag **`laura`**
(stdio: `uv run --directory <Laura>/services/mcp laura-mcp`, Env `LAURA_TOKEN`) — analog
`vibemind-db`. Damit stehen alle 28 Laura-Tools (inkl. `build_narrated_reel`,
`import_media`, `job_status`, `get_export`, `laura_api`) ohne eigenen Adapter zur Verfügung.
Der Python-`VideoBackendAgent` (Redis-Lane) behält nur die Behalten-Tools aus 1.; neue
Events laufen über die Registry/OpenFang-Lane.

**Projekt anlegen/wechseln per Toolcall:** Ja. Anlegen geht heute in einem Call
(`POST /projects` braucht seit dem Narrated-Reel-Arc nur `{"name"}`; via `laura_api`-Tool).
Laura ist stateless — „wechseln" heißt: der Space-Agent hält `current_video_project` im
Kontext (`default_context`) und reicht die `project_id` in jeden Call. Dafür bekommt der
Laura-MCP zwei dedizierte Tools `create_project` / `select_project` (select = reine
Kontext-Operation im Agenten, kein Laura-State) und die Registry die Events
`video.project_create` / `video.project_switch`.

### 3. Rowboat-Publish mit Template (Summary + Metadaten)

Neues Notiz-Template in `spaces/video/tools/video_note_template.py` (ersetzt
`_build_video_note`), damit jedes Video **immer gleich** einsortiert wird:

```markdown
# {title}

## Summary
{summary}            ← bei Narrated Reels: die Beat-Zeilen (Narrationstext) — die beste
                       Zusammenfassung existiert schon; sonst 2-3 Sätze aus Transkript/VLM.

## Metadaten
| Feld | Wert |
|---|---|
| Space/Produkt | {product}        ← z. B. rowboat.space, Ideas.Space |
| Dauer | {duration_s} s |
| Erstellt | {created} |
| Quelle | Laura: project {project_id} / timeline {timeline_id} / export {export_id} |
| Stimme | {voice_backend} ({voice_ref}) |
| Datei | {file_path_oder_link} |

Tags: [video, {product}, {pipeline}]   node_type: video
```

`publish_videos_to_rowboat` nutzt das Template; Laura-Exporte werden nach Render-Erfolg
in die `VideoRepository` eingetragen (neues kleines Tool `register_laura_export`), damit
der bestehende Publish-Fluss (Mongo → Rowboat-Source) sie mitnimmt.

### 4. Event-Mapping + Brain („wie unser Chat")

`config/space_agent_registry.yml`, Sektion `video`, wird real verdrahtet
(`mcp_servers: [laura, vibemind-db]`), danach `scripts/sync_openfang_agents.py`:

```yaml
video.status:          { tool: video_status,        required_params: [] }
video.project_create:  { tool: create_project,      required_params: [name] }
video.project_switch:  { tool: select_project,      required_params: [name] }
video.import:          { tool: import_media,        required_params: [source] }
video.reel:            { tool: build_narrated_reel, required_params: [beats] }
video.job_status:      { tool: job_status,          required_params: [job_id] }
video.export_get:      { tool: get_export,          required_params: [export_id] }
video.publish:         { tool: publish_videos_to_rowboat, required_params: [] }
video.vision:          { tool: vision_generate,     required_params: [] }        # experimental
video.lipsync:         { tool: lipsync_run,         required_params: [person] }
```

Deutsche Aliasse im PARAM_MAPPING-Stil (`datei`→source, `projekt`→name, `text`→beats-Hilfe).
**Brain:** für jedes Event 3–5 Intent-Trainingsbeispiele über `vibemind_brain_train`
(„importier das Video aus …", „bau ein Produktvideo über X", „wie weit ist der Render?",
„veröffentliche die Videos in Rowboat"). Beat-Planung v1: Beats kommen aus dem Chat/vom
Aufrufer; ein Planungs-Agent (Material sichten → Fenster frame-index-verifizieren →
Zeilen texten) ist ein eigener Folge-Arc.

### 5. Stack/Launcher

`laura-backend` (8765; Env `LAURA_WORKSPACE`, `LAURA_TOKEN`, `LAURA_VOICEOVER_URL`) und
`tts-sidecar` (8898; **`HF_HUB_OFFLINE=1`**, `CHATTERBOX_VOICE_REF`) werden als verwaltete
Prozesse in den VibeMind-Launcher aufgenommen (Preset „video"), `video_status` prüft beide
Healthz. Secrets bleiben in `.env`-Dateien, nie in der Registry.

### 6. UI: Embed zuerst, dann angleichen

Phase 1: Lauras bestehende UI als **Webview-Tab** in der VibeMind-Shell (`video`-Space-Tab
lädt die lokale Laura-App; Token-Handling wie bei der Desktop-App). Phase 2 (separater
Arc): native Space-View im VibeMind-Stil (Beat-Editor, Job-Liste, Export-Player über den
vorhandenen Media-Server) — dabei die vibevideo-Reste (Sora-Panel, Lipsync) einhängen.

## Nicht-Ziele

OpenFang-Treiber-Code (fremder Claim `claude-codexsub`), Lipsync-Merge, Sora-Ausbau,
native Space-View (Phase 2), Auto-Beat-Planungs-Agent, Deploy/Gitlink-Bump aus dieser Arbeit.

## Fehlerfälle

Laura/Sidecar down → `video_status` meldet es, Events antworten mit klarer Meldung statt
Timeout (Healthz-Vorab-Check im Agenten-Hint). Render-Fehler → Laura räumt selbst auf
(Checkpoint-Rollback, seit Narrated-Reel-Arc); der Space meldet den Klartext-Fehler.
Publish ohne Mongo → bestehender Fallback des Alt-Tools bleibt.

## Tests

Registry-Sync erzeugt `brain-video/agent.toml` mit `laura`-Scope; Template-Unit-Test
(Summary aus Beats, Metadaten vollständig); `register_laura_export` legt VideoRepository-
Zeile mit allen Template-Feldern an; Event-Roundtrip-Test `video.reel` gegen gemocktes
Laura; Launcher-Preset startet/prüft beide Prozesse. Live-Gate: ein Reel per Sprache/Chat
aus VibeMind heraus, Notiz erscheint in Rowboat mit Summary + Metadaten.
