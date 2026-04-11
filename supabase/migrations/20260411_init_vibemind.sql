-- VibeMind Database Schema for Supabase
-- Migrated from SQLite (vibemind.db)

-- ═══════════════════════════════════════════════════════════
-- Ideas / Bubbles
-- ═══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS ideas (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    source TEXT DEFAULT 'voice',
    created_at TIMESTAMPTZ DEFAULT now(),
    score FLOAT DEFAULT 0.0,
    status TEXT DEFAULT 'raw' CHECK (status IN ('raw', 'scored', 'promoted', 'archived', 'active')),
    promoted_to_project_id TEXT,
    tags JSONB DEFAULT '[]',
    metadata JSONB DEFAULT '{}',
    parent_id TEXT REFERENCES ideas(id) ON DELETE SET NULL,
    agent_id TEXT,
    embedding_vector FLOAT8[],
    embedding_hash TEXT
);

CREATE INDEX IF NOT EXISTS idx_ideas_status ON ideas(status);
CREATE INDEX IF NOT EXISTS idx_ideas_parent ON ideas(parent_id);
CREATE INDEX IF NOT EXISTS idx_ideas_score ON ideas(score DESC);

-- ═══════════════════════════════════════════════════════════
-- Projects
-- ═══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'paused', 'completed', 'archived')),
    created_at TIMESTAMPTZ DEFAULT now(),
    from_idea_id TEXT REFERENCES ideas(id) ON DELETE SET NULL,
    progress FLOAT DEFAULT 0.0,
    metadata JSONB DEFAULT '{}',
    project_path TEXT,
    generation_status TEXT DEFAULT 'pending',
    vnc_port INTEGER,
    job_id TEXT,
    requirements_json TEXT,
    convergence_progress FLOAT DEFAULT 0.0,
    preview_url TEXT,
    tech_stack TEXT,
    error_message TEXT
);

-- ═══════════════════════════════════════════════════════════
-- Tasks (persistent)
-- ═══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS persistent_tasks (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    event_type TEXT NOT NULL,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'completed', 'failed', 'cancelled')),
    input_text TEXT,
    result_text TEXT,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON persistent_tasks(status);

-- ═══════════════════════════════════════════════════════════
-- Scheduled Tasks
-- ═══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    cron_expression TEXT,
    next_run TIMESTAMPTZ,
    last_run TIMESTAMPTZ,
    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'paused', 'completed', 'cancelled')),
    event_type TEXT,
    payload JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now()
);

-- ═══════════════════════════════════════════════════════════
-- Conversations
-- ═══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS conversation_sessions (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    user_id TEXT DEFAULT 'default',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    metadata JSONB DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS conversation_history (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    session_id TEXT REFERENCES conversation_sessions(id) ON DELETE CASCADE,
    speaker TEXT NOT NULL CHECK (speaker IN ('user', 'agent')),
    text TEXT NOT NULL,
    timestamp TIMESTAMPTZ DEFAULT now(),
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_conv_session ON conversation_history(session_id);

-- ═══════════════════════════════════════════════════════════
-- Flowzen Activity Tracking
-- ═══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS flowzen_activity (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    event_type TEXT NOT NULL,
    description TEXT DEFAULT '',
    timestamp TIMESTAMPTZ DEFAULT now(),
    duration_minutes FLOAT,
    space TEXT,
    metadata JSONB DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS flowzen_checkins (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    mood TEXT,
    energy INTEGER CHECK (energy BETWEEN 1 AND 10),
    notes TEXT DEFAULT '',
    timestamp TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS flowzen_diary (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    entry TEXT NOT NULL,
    tags JSONB DEFAULT '[]',
    timestamp TIMESTAMPTZ DEFAULT now()
);

-- ═══════════════════════════════════════════════════════════
-- Canvas (visual board)
-- ═══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS canvas_nodes (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    node_type TEXT NOT NULL DEFAULT 'note',
    title TEXT DEFAULT '',
    content TEXT DEFAULT '',
    x FLOAT DEFAULT 0.0,
    y FLOAT DEFAULT 0.0,
    linked_idea_id TEXT REFERENCES ideas(id) ON DELETE SET NULL,
    linked_project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
    summary TEXT,
    metadata JSONB DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS canvas_edges (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    from_node_id TEXT REFERENCES canvas_nodes(id) ON DELETE CASCADE,
    to_node_id TEXT REFERENCES canvas_nodes(id) ON DELETE CASCADE,
    edge_type TEXT DEFAULT 'default'
);

-- ═══════════════════════════════════════════════════════════
-- Video Projects
-- ═══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS video_projects (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    name TEXT NOT NULL,
    status TEXT DEFAULT 'draft',
    created_at TIMESTAMPTZ DEFAULT now(),
    metadata JSONB DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS videos (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    project_id TEXT REFERENCES video_projects(id) ON DELETE CASCADE,
    title TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    output_path TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    metadata JSONB DEFAULT '{}'
);

-- ═══════════════════════════════════════════════════════════
-- User Preferences
-- ═══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id TEXT PRIMARY KEY DEFAULT 'default',
    preferences JSONB DEFAULT '{}',
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- ═══════════════════════════════════════════════════════════
-- Enable Row Level Security (RLS) on all tables
-- ═══════════════════════════════════════════════════════════
ALTER TABLE ideas ENABLE ROW LEVEL SECURITY;
ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE persistent_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE flowzen_activity ENABLE ROW LEVEL SECURITY;
ALTER TABLE canvas_nodes ENABLE ROW LEVEL SECURITY;

-- For now, allow all access (anon key) — tighten for production
CREATE POLICY "Allow all for anon" ON ideas FOR ALL USING (true);
CREATE POLICY "Allow all for anon" ON projects FOR ALL USING (true);
CREATE POLICY "Allow all for anon" ON persistent_tasks FOR ALL USING (true);
CREATE POLICY "Allow all for anon" ON conversation_history FOR ALL USING (true);
CREATE POLICY "Allow all for anon" ON flowzen_activity FOR ALL USING (true);
CREATE POLICY "Allow all for anon" ON canvas_nodes FOR ALL USING (true);
