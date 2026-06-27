-- 20260627 — coding-engine design artifacts + verification signals → vibemind supabase
--
-- Producer:  coding-engine (epic_orchestrator) writes design docs at the start of a
--            generation and a build/test verdict at the end.
-- Consumer:  brain truth: validators — ground-truth signals "design persisted" /
--            "build passed", queried the same way as the bubble caps (postgrest).
--
-- Matches the 20260411_init_vibemind convention: TEXT ids + allow_all RLS policy so
-- postgrest anon read/write works (writes also fine via service_role).

CREATE TABLE IF NOT EXISTS design_artifacts (
    id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    project_ref   TEXT NOT NULL,                                  -- coding-engine natural key (job_id / output_dir name)
    project_id    TEXT REFERENCES projects(id) ON DELETE SET NULL, -- supabase project row, when known
    epic_id       TEXT,
    artifact_type TEXT NOT NULL,                                  -- requirements | architecture | contracts | api_doc | data_dict | epic
    title         TEXT NOT NULL,
    content       TEXT NOT NULL,
    format        TEXT NOT NULL DEFAULT 'markdown',               -- markdown | json | yaml
    source_file   TEXT,
    metadata      JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_design_artifacts_ref  ON design_artifacts(project_ref);
CREATE INDEX IF NOT EXISTS idx_design_artifacts_type ON design_artifacts(artifact_type);

CREATE TABLE IF NOT EXISTS verification_results (
    id                TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    project_ref       TEXT NOT NULL,
    project_id        TEXT REFERENCES projects(id) ON DELETE SET NULL,
    task_id           TEXT,
    epic_id           TEXT,
    verification_type TEXT NOT NULL,                              -- build | typecheck | lint | unit | integration | e2e
    passed            BOOLEAN NOT NULL,
    exit_code         INTEGER,
    command           TEXT,
    stdout_tail       TEXT,                                       -- last N chars (avoid bloat)
    stderr_tail       TEXT,
    duration_ms       INTEGER,
    executed_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_verification_ref    ON verification_results(project_ref);
CREATE INDEX IF NOT EXISTS idx_verification_type   ON verification_results(verification_type);
CREATE INDEX IF NOT EXISTS idx_verification_passed ON verification_results(passed);

-- RLS — match init_vibemind allow_all convention (postgrest anon read/write).
DO $$
DECLARE tbl TEXT;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['design_artifacts','verification_results'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('DROP POLICY IF EXISTS "allow_all_%s" ON %I', tbl, tbl);
        EXECUTE format('CREATE POLICY "allow_all_%s" ON %I FOR ALL USING (true) WITH CHECK (true)', tbl, tbl);
    END LOOP;
END $$;
