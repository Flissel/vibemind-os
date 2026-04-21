# Portion 3 — Datenbank, Supabase & Data Layer

> **Teil 3 von 10** der VibeMind-OS-Systemdokumentation.
> Dependencies: Portion 1 (System-Überblick), Portion 2 (LLM-Config — v.a. das Embedding-System).

---

## TL;DR

VibeMind-OS persistiert alles in einer einzigen PostgreSQL-Datenbank, die lokal
über Supabase läuft (Port `54321` REST, `54322` DB, `54323` Studio). Das Schema
umfasst **22 Tabellen** in 4 logischen Gruppen (Ideas/Canvas, Conversations,
Space-spezifisch, Infrastruktur) und nutzt **pgvector** mit einem HNSW-Index für
semantische Suche über 384-dimensionale Embeddings. Daten können per
`migrate_sqlite_to_supabase.py` aus der alten SQLite-DB (`voice/python/vibemind.db`)
importiert werden. Ein Self-Healing-Script (`supabase/heal.sh`) startet abgestürzte
Container automatisch neu — es enthält aktuell noch einen **hardgecodeten Anon-Key**,
der auf eine Env-Variable umgestellt werden muss.

---

## 1. Schema-Überblick (22 Tabellen)

Die Datenbank ist nach Subsystemen gruppiert. Die Farben markieren, aus welchem
Teil des Systems die Tabellen primär geschrieben werden:

```
┌─────────────────────────── Core (Voice, Ideas-Explorer) ──────────────────┐
│  ideas              projects            canvas_nodes       canvas_edges    │
│  conversation_sessions                  conversation_history               │
└────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────── Pipeline (Shuttles, Exploration) ──────────────┐
│  shuttles           exploration_sessions                                   │
│  exploration_nodes  discovered_edges     mermaid_diagrams                  │
└────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────── Space-spezifisch ──────────────────────────────┐
│  scheduled_tasks                                                           │
│  flowzen_checkins   flowzen_activity     flowzen_diary                     │
│  videos             video_projects       video_project_persons             │
│  video_pipeline_steps                                                      │
│  persistent_tasks                                                          │
└────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────── Infrastruktur ────────────────────────────────┐
│  user_preferences   schema_version                                         │
└────────────────────────────────────────────────────────────────────────────┘
```

**Zentrale Migrations-Datei:** `supabase/supabase/migrations/20260411000000_init_vibemind.sql`
(502 Zeilen). Es existiert aktuell ein **zweiter, byte-identischer Ordner**
(`supabase/migrations/`) — dazu Abschnitt 11 (Drift & TODOs).

---

## 2. Ideas & Projects — Das Herzstück

### 2.1 Ideas-Tabelle

Jede rohe Idee (per Voice, Text oder Agent erzeugt) landet hier:

```sql
CREATE TABLE ideas (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    source TEXT DEFAULT 'voice',            -- voice | text | agent | import
    created_at TIMESTAMPTZ DEFAULT now(),
    score FLOAT DEFAULT 0.0,                -- Ranking/Prioritaet
    status TEXT DEFAULT 'raw',              -- raw | refined | promoted | archived
    promoted_to_project_id TEXT,            -- FK -> projects.id
    tags JSONB DEFAULT '[]',
    metadata JSONB DEFAULT '{}',
    agent_id TEXT,                          -- welcher Agent hat's erzeugt
    parent_id TEXT REFERENCES ideas(id),    -- Baum-Struktur (Unter-Ideen)
    embedding_vector vector(384),           -- pgvector, fuer Semantic Search
    embedding_hash TEXT                     -- Dedup: gleicher Text = gleicher Hash
);
```

**Wichtige Patterns:**

- **`status`-Lifecycle:** `raw` → `refined` → `promoted` → (optional) `archived`.
  Der Übergang `refined` → `promoted` erzeugt einen `projects`-Eintrag und
  setzt `promoted_to_project_id`.
- **`parent_id`-Baum:** Ideen können Kinder haben (z. B. Dekomposition großer
  Ideen in kleinere Teil-Ideen). `ON DELETE SET NULL` verhindert Waisen-Löschung.
- **`embedding_vector vector(384)`:** 384-dimensionale Float-Vektoren für
  Semantic Search. Die Dimension ist **hardgecoded** auf das Default-Embedding-
  Modell aus Portion 2 (`sentence-transformers/all-MiniLM-L6-v2`, 384 Dim). Das
  ist eine Kopplung, die in Drift-Abschnitt 11 als TODO markiert ist.
- **`embedding_hash`:** SHA-256 des normalisierten Textes. Verhindert, dass
  identische Texte doppelt embedded werden (Token-Kosten sparen).

**Indexe:**
```sql
idx_ideas_status      -- schnelle Filterung nach Lifecycle
idx_ideas_score DESC  -- Top-N-Ranking
idx_ideas_created     -- Zeitreihe
idx_ideas_parent      -- Baum-Traversal
idx_ideas_embedding   -- HNSW, siehe Abschnitt 9
```

### 2.2 Projects-Tabelle

Aus einer `promoted` Idee wird ein Projekt (ab hier beginnt Code-Generierung,
Tracking, Preview):

```sql
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT now(),
    from_idea_id TEXT REFERENCES ideas(id),
    progress FLOAT DEFAULT 0.0,             -- 0.0 - 1.0
    metadata JSONB DEFAULT '{}',
    project_path TEXT,                       -- Filesystem-Pfad
    generation_status TEXT DEFAULT 'pending',-- pending|running|complete|failed
    vnc_port INTEGER,                        -- fuer Remote-Preview
    job_id TEXT,                             -- externe Job-ID (Coding-Engine)
    requirements_json TEXT,
    convergence_progress FLOAT DEFAULT 0.0,  -- Society-of-Mind Konvergenz
    preview_url TEXT,
    tech_stack TEXT,                         -- "react+fastapi" etc.
    error_message TEXT
);
```

**Bezug zu Portion 5 (Coding Engine):** `job_id`, `convergence_progress` und
`generation_status` werden von der Coding-Engine (Event-Bus) geschrieben, wenn
ein Projekt in der 6-Phasen-Pipeline läuft.

### 2.3 Verbindung Ideas ↔ Projects

Beide Richtungen sind verknüpft:

```
ideas.promoted_to_project_id ──FK──► projects.id
projects.from_idea_id        ──FK──► ideas.id
```

Das ist bewusst redundant: man kann aus jeder Richtung springen, ohne zweiten
Query. `ON DELETE SET NULL` auf beiden Seiten verhindert Inkonsistenzen.

---

## 3. Canvas System — Visuelles Ideas-Layout

Der Ideas-Explorer (Portion 8 Frontends) rendert Ideen auf einem 2D-Canvas.
Positionen + Verbindungen werden persistiert, damit Layout über Reloads
erhalten bleibt.

### 3.1 canvas_nodes

```sql
CREATE TABLE canvas_nodes (
    id TEXT PRIMARY KEY,
    x FLOAT, y FLOAT,            -- 2D-Position
    width FLOAT, height FLOAT,
    node_type TEXT,              -- "idea" | "project" | "note" | ...
    reference_id TEXT,           -- FK auf ideas/projects (loose)
    label TEXT,
    color TEXT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now()
);
```

**Design-Entscheidung:** `reference_id` ist *keine* harte FK — ein Canvas-Knoten
kann auch "free-floating" sein (reine Notiz). Die Auflösung erfolgt zur
Laufzeit über `node_type + reference_id`.

### 3.2 canvas_edges

```sql
CREATE TABLE canvas_edges (
    id TEXT PRIMARY KEY,
    from_node_id TEXT NOT NULL,
    to_node_id TEXT NOT NULL,
    edge_type TEXT,              -- "depends" | "relates" | "blocks"
    label TEXT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now()
);
```

Kanten sind gerichtet (`from` → `to`). Zwei Indexe (`idx_canvas_edges_from`,
`idx_canvas_edges_to`) machen beide Traversal-Richtungen schnell.

---

## 4. Conversations — Voice & Chat Historie

Jede User-Session (via Voice-Channel oder Chat) bekommt eine eigene Zeile in
`conversation_sessions`, jede einzelne Nachricht landet in
`conversation_history`.

### 4.1 conversation_sessions

```sql
CREATE TABLE conversation_sessions (
    id TEXT PRIMARY KEY,
    started_at TIMESTAMPTZ DEFAULT now(),
    ended_at TIMESTAMPTZ,
    channel TEXT,                 -- "voice" | "chat" | "desktop" | ...
    user_id TEXT,
    metadata JSONB DEFAULT '{}',
    summary TEXT,                 -- LLM-generierte Zusammenfassung
    total_messages INTEGER DEFAULT 0
);
```

### 4.2 conversation_history

```sql
CREATE TABLE conversation_history (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES conversation_sessions(id) ON DELETE CASCADE,
    timestamp TIMESTAMPTZ DEFAULT now(),
    role TEXT,                    -- "user" | "assistant" | "system" | "tool"
    content TEXT,
    tool_calls JSONB,             -- wenn role='assistant' + Tool-Use
    metadata JSONB DEFAULT '{}'
);
```

**`ON DELETE CASCADE`:** Wird eine Session gelöscht, fliegt die History mit —
das vermeidet Waisen-Nachrichten, die man nie wieder findet.

**Realtime:** `conversation_history` ist auf `supabase_realtime` publiziert
(siehe Abschnitt 8). Das Electron-Frontend abonniert die Tabelle und rendert
neue Nachrichten live, ohne Polling.

---

## 5. Shuttles — Requirement Evaluation Pipeline

Ein "Shuttle" ist eine Transport-Einheit für eine Idee, die durch eine
Evaluations-Pipeline geschickt wird (Kostenschätzung, Machbarkeit,
Tech-Stack-Vorschlag, ...). Jede Stage erzeugt strukturierte Results.

```sql
CREATE TABLE shuttles (
    id TEXT PRIMARY KEY,
    bubble_id TEXT,                  -- Quell-Idee (Ideas-Explorer nennt sie "Bubble")
    project_id TEXT,                 -- optional: wenn bereits promoted
    status TEXT DEFAULT 'queued',    -- queued|running|complete|failed
    current_stage TEXT,              -- welche Pipeline-Stage gerade dran ist
    stages_completed JSONB DEFAULT '[]',
    results JSONB DEFAULT '{}',      -- {stage_name: {...}}
    requirements JSONB,
    cost_estimate FLOAT,
    complexity_score FLOAT,
    feasibility_score FLOAT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    error_message TEXT,
    metadata JSONB DEFAULT '{}'
);
```

**Indexe:** `bubble_id`, `status`, `created_at DESC`, `project_id`.
**Realtime:** ja (Frontend zeigt Live-Progress pro Stage).

**Bezug zu Portion 4 (Bridge/OpenFang):** Die Bridge kann einen Shuttle an
einen Space-Agent weiterreichen. Der Agent befüllt dann `results[stage_name]`
und setzt `current_stage` weiter.

---

## 6. Exploration & Discovery

Zwei zusammengehörige Tabellen für den "Ideas-Explorer" — ein Agent-System,
das von einer Wurzel-Idee ausgeht und verwandte Ideen generiert/bewertet.

### 6.1 exploration_sessions

```sql
CREATE TABLE exploration_sessions (
    id TEXT PRIMARY KEY,
    root_bubble_id TEXT,              -- Start-Idee
    status TEXT DEFAULT 'running',    -- running|paused|complete|failed
    strategy TEXT,                    -- "breadth" | "depth" | "beam"
    max_depth INTEGER DEFAULT 3,
    max_nodes INTEGER DEFAULT 50,
    nodes_generated INTEGER DEFAULT 0,
    nodes_accepted INTEGER DEFAULT 0,
    started_at TIMESTAMPTZ DEFAULT now(),
    completed_at TIMESTAMPTZ,
    config JSONB DEFAULT '{}',
    metadata JSONB DEFAULT '{}'
);
```

### 6.2 exploration_nodes

Jeder generierte Kandidat ist ein Knoten mit mehreren Score-Dimensionen:

```sql
CREATE TABLE exploration_nodes (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES exploration_sessions(id) ON DELETE CASCADE,
    parent_node_id TEXT,
    depth INTEGER,
    title TEXT,
    description TEXT,
    novelty_score FLOAT,            -- wie neu vs. existierende Ideen
    feasibility_score FLOAT,
    impact_score FLOAT,
    combined_score FLOAT,           -- gewichtete Summe
    is_accepted BOOLEAN DEFAULT false,
    accepted_as_idea_id TEXT,       -- FK auf ideas.id wenn akzeptiert
    embedding_vector vector(384),   -- gleiche Dimension wie ideas
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now()
);
```

**Indexe:** `session_id`, `combined_score DESC`, `is_accepted`.

### 6.3 discovered_edges

Kanten zwischen Ideen, die der Explorer *findet* (nicht manuell erzeugt):

```sql
CREATE TABLE discovered_edges (
    id TEXT PRIMARY KEY,
    from_idea_id TEXT,
    to_idea_id TEXT,
    edge_type TEXT,                 -- "semantic" | "causal" | "dependency"
    confidence FLOAT,
    similarity FLOAT,
    discovered_by TEXT,             -- agent_id
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now()
);
```

Unterschied zu `canvas_edges`: canvas_edges sind **User-gezeichnet**,
discovered_edges sind **Agent-gefunden**. Das Frontend kann beide Typen
overlay'en.

---

## 7. Mermaid Diagrams & Scheduled Tasks

### 7.1 mermaid_diagrams

Automatisch generierte Diagramme (Agent erzeugt Mermaid-Syntax aus Ideen):

```sql
CREATE TABLE mermaid_diagrams (
    id TEXT PRIMARY KEY,
    source_idea_id TEXT,
    diagram_type TEXT,              -- "flowchart" | "sequence" | "class" | ...
    mermaid_code TEXT NOT NULL,
    title TEXT,
    description TEXT,
    generated_by TEXT,              -- agent_id
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now()
);
```

### 7.2 scheduled_tasks

Zentrale Task-Scheduling-Tabelle für alle Spaces:

```sql
CREATE TABLE scheduled_tasks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    task_type TEXT,                 -- "cron" | "interval" | "once"
    schedule TEXT,                  -- Cron-Expression oder ISO-Zeit
    payload JSONB,                  -- was soll ausgefuehrt werden
    space TEXT,                     -- welcher Space owned den Task
    status TEXT DEFAULT 'pending',  -- pending|running|complete|failed|cancelled
    next_run_at TIMESTAMPTZ,
    last_run_at TIMESTAMPTZ,
    run_count INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    enabled BOOLEAN DEFAULT true,
    metadata JSONB DEFAULT '{}'
);
```

**Realtime:** ja. `idx_sched_next_run` erlaubt einem Scheduler-Worker, mit
einem einzigen Query (`WHERE next_run_at <= now() AND status='pending'`) die
nächsten fälligen Tasks zu holen.

---

## 8. Space-spezifische Tabellen

Diese Tabellen gehören zu jeweils einem konkreten Space (Portion 10). Sie
sind bewusst im globalen Schema — nicht per-Space-separiert — was im
Kontext der Space-MCP-Migration (Abschnitt 12) hinterfragt werden muss.

### 8.1 Flowzen (3 Tabellen)

Flowzen ist der Well-Being-Space (Check-Ins, Activity-Tracking, Tagebuch):

```sql
flowzen_checkins(id, mood, energy, focus, notes, created_at, metadata)
flowzen_activity(id, activity_type, duration_min, intensity, notes, created_at, metadata)
flowzen_diary(id, entry_date, mood_avg, title, content, tags, metadata, created_at)
```

Alle drei: Indexe auf `created_at DESC`, Realtime für `activity` und `diary`.

### 8.2 Videos (4 Tabellen)

Der Video-Space managt Video-Generierung mit mehreren Pipeline-Steps pro
Projekt und optional mehreren Personen pro Video:

```sql
videos(id, title, path, duration_sec, person, category, pipeline_stage, created_at, ...)
video_projects(id, name, status, target_persons JSONB, created_at, ...)
video_project_persons(id, project_id, person_name, role, ...)
video_pipeline_steps(id, project_id, person_name, step_name, status, output_path, ...)
```

**Design-Bemerkung:** `pipeline_stage` auf `videos` *und* `video_pipeline_steps`
für Projekte gibt es, weil es zwei Anwendungsfälle gibt: einzelne Videos
(Stage inline) vs. komplexe Projekte mit mehreren Personen (Stage pro
Person × Step).

### 8.3 persistent_tasks

Langlaufende Tasks, die Session-übergreifend überleben müssen (z. B. ein
laufender Code-Generator-Job):

```sql
CREATE TABLE persistent_tasks (
    id TEXT PRIMARY KEY,
    task_type TEXT,
    status TEXT DEFAULT 'pending',
    priority INTEGER DEFAULT 5,
    payload JSONB,
    result JSONB,
    error_message TEXT,
    progress FLOAT DEFAULT 0.0,
    worker_id TEXT,                 -- welcher Worker hat's gegrabbed
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    metadata JSONB DEFAULT '{}'
);
```

Unterschied zu `scheduled_tasks`: scheduled_tasks haben einen *Zeitplan*,
persistent_tasks laufen *sofort* und *möglicherweise lange*.

### 8.4 user_preferences & schema_version

```sql
CREATE TABLE user_preferences (
    user_id TEXT PRIMARY KEY,
    preferences JSONB DEFAULT '{}',
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ DEFAULT now(),
    description TEXT
);
```

`schema_version` trackt manuell gepflegte Migrations-Nummern (Legacy aus der
SQLite-Zeit — Supabase hat eigenes `schema_migrations`, siehe Drift in 11).

---

## 9. pgvector — Semantic Search

Die `ideas`-Tabelle hat einen `embedding_vector vector(384)` und einen
HNSW-Index für Approximate-Nearest-Neighbor-Suche:

```sql
CREATE INDEX idx_ideas_embedding ON ideas
USING hnsw (embedding_vector vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

**HNSW-Parameter-Bedeutung:**
- `m = 16` — max. Zahl der Verbindungen pro Knoten (höher = bessere Recall,
  mehr RAM).
- `ef_construction = 64` — Zahl der Kandidaten, die beim Einfügen gescannt
  werden (höher = bessere Qualität, langsamer Insert).
- `vector_cosine_ops` — Distanzmaß. Alternativen: `vector_l2_ops`,
  `vector_ip_ops`. Cosinus ist für normalisierte Sentence-Embeddings korrekt.

### 9.1 Search-Funktion

```sql
CREATE OR REPLACE FUNCTION search_ideas_by_embedding(
  query_embedding vector(384),
  match_threshold float DEFAULT 0.5,
  match_count int DEFAULT 10
)
RETURNS TABLE (id text, title text, description text, score float, similarity float)
LANGUAGE sql STABLE
AS $$
  SELECT ideas.id, ideas.title, ideas.description, ideas.score,
         1 - (ideas.embedding_vector <=> query_embedding) as similarity
  FROM ideas
  WHERE ideas.embedding_vector IS NOT NULL
    AND 1 - (ideas.embedding_vector <=> query_embedding) > match_threshold
  ORDER BY ideas.embedding_vector <=> query_embedding
  LIMIT match_count;
$$;
```

Der Operator `<=>` ist Cosinus-Distanz; `1 - distance` gibt Similarity in
`[0, 1]` zurück.

**Aufruf vom Client:**
```typescript
const { data } = await supabase.rpc('search_ideas_by_embedding', {
  query_embedding: myEmbedding,     // Float32Array[384]
  match_threshold: 0.7,
  match_count: 20
});
```

### 9.2 Kopplung an Portion 2

Die feste `384` in der Tabellen-Definition und in der Funktion ist **exakt die
Dimension des Default-Embedding-Modells** aus `llm_config.yml`:

```yaml
# aus llm_config.yml.example (Portion 2)
embeddings:
  default:
    provider: sentence-transformers
    model: all-MiniLM-L6-v2
    dimension: 384     # ← muss mit SQL-Schema uebereinstimmen
```

Wechselt man in Portion 2 das Default-Embedding auf `openai_large` (3072) oder
`ollama_local` (768), muss das Schema migriert werden (neuer Vektor-Column,
Re-Embed aller Ideen, Index neu bauen). Siehe TODO in Abschnitt 11.

---

## 10. RLS & Realtime

### 10.1 Row-Level Security

Die Migration aktiviert RLS auf **allen** Tabellen (außer `schema_version`
und `schema_migrations`) und erzeugt für jede Tabelle eine `allow_all`-Policy:

```sql
DO $$
DECLARE tbl TEXT;
BEGIN
    FOR tbl IN SELECT tablename FROM pg_tables WHERE schemaname = 'public'
               AND tablename NOT IN ('schema_version', 'schema_migrations')
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('CREATE POLICY "allow_all_%s" ON %I FOR ALL
                        USING (true) WITH CHECK (true)', tbl, tbl);
    END LOOP;
END $$;
```

**Das ist Dev-Modus.** Jeder Client mit dem Anon-Key kann alles lesen und
schreiben. Für Produktion muss das hart gemacht werden (pro-User, pro-Space).
TODO in Abschnitt 11.

### 10.2 Realtime-Publikationen

12 Tabellen sind für Realtime-Subscriptions freigegeben (Supabase
`supabase_realtime` Publication):

```
ideas, projects, canvas_nodes, canvas_edges,
conversation_history, scheduled_tasks, shuttles,
flowzen_activity, flowzen_diary,
videos, video_projects, persistent_tasks
```

Das Electron-Frontend (Portion 8) abonniert diese Tabellen, rendert
Änderungen live und braucht kein Polling.

**Was NICHT realtime-publiziert ist:** `conversation_sessions`,
`exploration_*`, `discovered_edges`, `mermaid_diagrams`, `flowzen_checkins`,
`video_project_persons`, `video_pipeline_steps`, `user_preferences`.
Grund: entweder selten-änderbar oder nur per expliziter Anfrage interessant.

---

## 11. Supabase-Stack & Konfiguration

Lokales Supabase läuft per Docker-Compose (von `supabase start` orchestriert).

### 11.1 Ports

| Port    | Service     | Zweck                                  |
|---------|-------------|----------------------------------------|
| `54321` | Kong + REST | HTTP-API (`/rest/v1/...`)              |
| `54322` | PostgreSQL  | Direct DB connection (psql, SQLAlchemy)|
| `54323` | Studio      | Web-UI (Browser-gestützter DB-Explorer)|
| `54324` | Inbucket    | SMTP-Catcher für Email-Testing         |

Konfigurationsdatei: `supabase/supabase/config.toml` (Standard-Supabase).

### 11.2 Container-Liste

```
supabase_db_supabase        Postgres + pgvector
supabase_kong_supabase      API-Gateway
supabase_rest_supabase      PostgREST
supabase_auth_supabase      GoTrue (auth)
supabase_realtime_supabase  Realtime-Server
supabase_storage_supabase   Object-Storage
supabase_studio_supabase    Web-UI
supabase_inbucket_supabase  SMTP-Tester
supabase_vector_supabase    Log-Aggregator (buggy unter Windows)
```

### 11.3 Zugriff aus dem Code

Zwei Wege:

**a) Direkt per psycopg2 / SQLAlchemy async** — schneller, kein REST-Overhead:
```python
DATABASE_URL = "postgresql://postgres:postgres@localhost:54322/postgres"
```
Verwendet z. B. von `spaces/desktop/Automation_ui/backend/app/database.py`.

**b) REST-API per `supabase-py` / HTTP** — für Realtime-Subscriptions und
einfache CRUD-Ops aus dem Frontend:
```python
from supabase import create_client
supabase = create_client("http://localhost:54321", SUPABASE_ANON_KEY)
```

---

## 12. SQLite → Supabase Migration

Historisch lief alles in SQLite (`voice/python/vibemind.db`). Das
Migrations-Script `supabase/migrate_sqlite_to_supabase.py` (191 Zeilen)
kopiert Daten in Abhängigkeits-Reihenfolge:

```python
TABLES = [
    "ideas", "projects",                     # Core
    "canvas_nodes", "canvas_edges",
    "conversation_sessions", "conversation_history",
    "shuttles",
    "exploration_sessions", "exploration_nodes", "discovered_edges",
    "mermaid_diagrams", "scheduled_tasks",
    "flowzen_checkins", "flowzen_activity", "flowzen_diary",
    "videos", "video_projects", "video_project_persons",
    "video_pipeline_steps",
    "persistent_tasks", "user_preferences",
]
```

**Reihenfolge ist kritisch:** `ideas` vor `projects` (FK
`projects.from_idea_id`), `conversation_sessions` vor `conversation_history`
(FK + ON DELETE CASCADE), `video_projects` vor den drei abhängigen Video-
Tabellen.

**JSON-Spalten:** Das Script erkennt Felder, die in SQLite als TEXT mit
JSON-Inhalt gespeichert sind, und parst sie vor dem POST an die Supabase-REST
(sonst landet der String als String, nicht als JSONB).

**Usage:**
```bash
python supabase/migrate_sqlite_to_supabase.py \
  --supabase-url http://localhost:54321 \
  --anon-key $SUPABASE_ANON_KEY
```

---

## 13. heal.sh — Self-Healing

`supabase/heal.sh` ist ein Bash-Script, das den Supabase-Stack wieder in Gang
bringt, wenn Container abgestürzt sind oder Kong seinen DNS-Cache verloren hat.

**Trigger:** wenn `curl http://localhost:54321/rest/v1/...` 503 zurückgibt.

**Was das Script tut:**
1. Findet alle exited Supabase-Container und startet sie neu
   (außer `supabase_vector` — bekanntes Windows-Docker-Socket-Problem).
2. Stoppt `supabase_vector`, falls er noch läuft (verursacht DNS-Issues).
3. `docker restart supabase_kong_supabase` — flusht den Kong-DNS-Cache.
4. Wartet 5 Sekunden und pollt `/rest/v1/ideas?limit=1` bis `200 OK` kommt.

**Bekanntes Problem:** Der Anon-Key steckt **hardgecoded** im Script. Siehe
Drift-Abschnitt 14.

---

## 14. Drift & architektonische TODOs

Bei der Dokumentation sind vier strukturelle Probleme aufgefallen, die als
`TODO(<tag>)` im Code markiert sind, damit man sie per `grep` wiederfindet.
Jedes TODO verweist auf genau diesen Abschnitt.

### 14.1 `TODO(rls-production-hardening)`

**Wo:** `supabase/supabase/migrations/20260411000000_init_vibemind.sql`,
Block ab Zeile 437.

**Was:** Alle Tabellen haben `CREATE POLICY "allow_all_<tbl>" FOR ALL USING
(true) WITH CHECK (true)`. Jeder Client mit Anon-Key darf alles.

**Warum das aktuell so ist:** Dev-Setup, ein einziger lokaler Benutzer,
kein Multi-Tenancy, kein Auth-Flow. Der Kommentar im SQL ist ehrlich:
"allow all for local dev (anon key)".

**Ziel-Zustand:**
- User-Auth (Supabase Auth / GoTrue) aktivieren
- Policies umschreiben auf `USING (auth.uid() = user_id)` wo sinnvoll
- Space-scoped Policies wo mehrere User denselben Space teilen
- `TODO(rls-production-hardening)` entfernen, wenn das in Prod läuft

### 14.2 `TODO(supabase-anon-key-envvar)`

**Wo:** `supabase/heal.sh`, Zeile 39.

**Was:**
```bash
-H "apikey: sb_publishable_ACJWlzQHlZjBrEguHvfOxg_3BJgxAaH"
```

**Warum das schlecht ist:** Selbst wenn es "nur" der Anon-Key ist (nicht
der Service-Key), ist ein hardgecoder Key im Git schlechtes Signal und
rotiert nicht mit dem restlichen Setup. Alle anderen Scripts und Apps
nutzen bereits `$SUPABASE_ANON_KEY` aus `.env`.

**Fix:**
```bash
source "$(dirname "$0")/../.env"
# ...
-H "apikey: $SUPABASE_ANON_KEY"
```

### 14.3 `TODO(duplicate-migrations)`

**Wo:**
- `supabase/migrations/20260411_init_vibemind.sql`
- `supabase/supabase/migrations/20260411000000_init_vibemind.sql`

**Was:** Beide Dateien sind **byte-identisch** (`diff` liefert leer).

**Warum das aktuell so ist:** Supabase-CLI erzeugt Migrations in
`supabase/migrations/`. Jemand hat den Ordner in ein weiteres
Unterverzeichnis kopiert (vermutlich, weil `supabase/` selbst schon
das Supabase-Projekt ist und dort noch ein `supabase/` Subfolder
aufgetaucht ist). Der CLI-Befehl `supabase db reset` schaut nur in den
"richtigen" Ordner, der andere wird ignoriert — und driftet irgendwann.

**Ziel-Zustand:** Einen der beiden Ordner löschen. Der **kanonische**
Pfad ist `supabase/supabase/migrations/` (wird vom CLI erwartet, wenn
`supabase/supabase/config.toml` das Projektroot ist). Der Legacy-Pfad
`supabase/migrations/` kann weg.

### 14.4 `TODO(embedding-dim-coupling)`

**Wo:**
- `supabase/supabase/migrations/20260411000000_init_vibemind.sql`
  Zeile 24 (`embedding_vector vector(384)`)
- Zeile 174 in `exploration_nodes` (gleiche Dimension)
- Zeile 478 (`search_ideas_by_embedding(query_embedding vector(384), ...)`)

**Was:** Die Zahl `384` ist dreimal im Schema hardcoded und muss mit dem
`embeddings.default.dimension`-Wert aus `llm_config.yml` (Portion 2)
übereinstimmen.

**Warum das eine Falle ist:** Wenn jemand in Portion 2 das Default-
Embedding auf `openai_large` (3072 Dim) oder `ollama_local` (768 Dim)
umstellt — und dabei nicht weiß, dass das Schema gekoppelt ist — dann
funktioniert die alte Search-Function nicht mehr, aber die App bootet
noch. Silent breakage.

**Optionen für den Fix (Design-Entscheidung nötig):**

1. **Schema-gen aus Config:** Ein Migration-Generator liest
   `llm_config.yml` und produziert die SQL mit der richtigen Dimension.
   → Migration ist dann allerdings nicht mehr pure SQL.
2. **Multi-Vector-Columns:** Eine Spalte pro Embedding-Modell
   (`embedding_mini`, `embedding_large`, ...). Teurer, aber flexibler.
3. **CI-Guard:** Pre-commit/CI-Check, der `llm_config.yml:embeddings.default
   .dimension` mit dem SQL-Wert abgleicht und bei Drift fehlschlägt.
   → Billigste Lösung, empfohlen als erster Schritt.

---

## 15. Bezug zur Space-MCP-Migration (Portion 1, Abschnitt 13.1)

Die laufende Migration von "N:M shared MCP tools" zu "1 MCP pro Space"
wirft eine offene **Daten-Layer-Frage** auf:

> Sollen Space-MCPs ihre Tabellen selbst besitzen (eigenes Schema), oder
> bleiben alle Tabellen im globalen `public` Schema?

**Pro "jeder Space sein eigenes Schema" (`flowzen.*`, `video.*`, ...):**
- Echte Isolation. Ein Space kann seine Tabellen umbenennen, ohne andere
  zu brechen.
- RLS-Policies werden automatisch Space-scoped.
- Space-MCPs können eigene Migrations haben, ohne globalen Lock-Step.

**Contra:**
- Cross-Space-Queries werden teurer (JOIN über Schemata).
- `ideas` und `projects` sind bewusst *global* geteilt — das würde
  zersplittern.
- Realtime-Publication pro Schema muss manuell gemanagt werden.

**Empfehlung (für die Roadmap):**
- `ideas`, `projects`, `canvas_*`, `conversation_*` bleiben global (Core-
  Daten, gemeinsam genutzt).
- Space-spezifische Tabellen (`flowzen_*`, `video*`, `persistent_tasks`,
  `scheduled_tasks`) **können** bei der Migration in ein Space-eigenes
  Schema wandern — das ist aber **optional** und nicht Voraussetzung
  für Phase 1 der Space-MCP-Migration.
- Entscheidung bis Space-MCP-Phase 2 aufschieben; erste Space-MCPs
  sprechen einfach das globale Schema.

Siehe `docs/migration-to-space-mcps.md` für den Migrations-Gesamtplan.

---

## 16. Zusammenfassung

| Thema                          | Status |
|--------------------------------|--------|
| 22-Tabellen-Schema             | produktiv, stabil |
| pgvector mit HNSW              | produktiv |
| Realtime für 12 Tabellen       | produktiv |
| SQLite → Supabase Migration    | einmaliger Sync, erledigt |
| heal.sh Self-Healing           | funktioniert, aber Key-Hardcoding (TODO) |
| RLS allow_all                  | **Dev-Only**, Prod-Hardening offen (TODO) |
| Duplizierte Migrations-Ordner  | Cleanup offen (TODO) |
| Embedding-Dimension-Kopplung   | CI-Guard offen (TODO) |
| Space-scoped Schemas           | Entscheidung offen (verweist auf Space-MCP-Migration) |

**Nächste Portion:** *Portion 4 — Brain (Tahlamus)* — das kognitive
Routing-System auf Port 5000 mit 268+ Core-Modulen.
