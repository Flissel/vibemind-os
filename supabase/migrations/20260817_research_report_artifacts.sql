-- Migration: Deep Research report artifacts (linked-artifact persistence)
-- Date: 2026-08-17
-- Task: task-research-0003-report-artifact-migration-v1
--
-- ⚠ NOT APPLIED. This file is written, reviewed, and merged as source only.
--   Applying it against any database is a separate, user-authorised live action
--   (see the runbook referenced below). Nothing in the authoring task executed
--   psql, PostgREST, `supabase db push`, or a docker exec against a database.
--
-- Why this table exists
-- ---------------------
-- Deep Research (service tier 2) produces a cited report of 5-10 pages at the
-- Hand's own `detailed` default. Open decision D1 asked where that text lives.
-- The answer taken here is "short idea plus linked artifact": the idea keeps a
-- summary, the full report lives as its own artifact row that the idea's
-- summary points at.
--
-- Two facts from the existing schema drive the shape below:
--
--   1. `ideas.description` is unbounded TEXT, so a 10-page body is legal today
--      with no migration — but every change to that row replays the whole body
--      through `public.ideas_sync_outbox` (20260610_ideas_sync_triggers.sql),
--      and `description` is a bidirectionally synced, hand-editable field under
--      last-write-wins. A report body there is a permanent payload cost and a
--      live conflict surface.
--   2. The already-merged contract family caps the idea-facing text at 4000
--      characters (`ResearchIdeaHandoffV1.idea_summary`) and keeps the report
--      itself behind `ResearchResultV1.artifact_refs`. The contracts already
--      model the idea side as a summary, not as the report.
--
-- Deliberate consequence — the foreign key sits on THIS table, never on
-- `public.ideas`. `public.ideas` gains no column, so `to_jsonb(NEW)` in
-- `emit_ideas_sync_event()` keeps its present shape and the ideas↔Rowboat sync
-- workers need no change. Writing a report artifact emits nothing into the
-- ideas outbox, because that trigger is bound to `public.ideas` alone. Whether
-- report artifacts should get a sync lane of their own is a carried decision
-- (D3), not something this migration settles.
--
-- Conventions follow the only comparable table in the schema,
-- `swe_design_artifacts` (20260521_swe_design.sql:60): TEXT surrogate primary
-- key defaulting to `gen_random_uuid()::text`, `artifact_type` / `name` /
-- `rel_path` / `format` / `content_text` / `content_json`, a per-parent
-- uniqueness constraint, `idx_<table>_<column>` index names, allow-all RLS, and
-- membership in the `supabase_realtime` publication. Three deliberate
-- deviations are marked `DEVIATION` below, each with its reason.
--
-- Companion documents (superproject `Flissel/vibemind_v1`):
--   docs/spaces/research/report-artifact-persistence-v1.md   — column provenance
--   docs/operations/2026-08-17-research-report-artifacts-migration-runbook-v1.md
--                                                            — apply / verify / roll back
--
-- Apply (PostgREST cannot run DDL — via docker-exec psql; MSYS_NO_PATHCONV=1 on
-- Git-Bash so the /tmp path is not mangled to a Windows path):
--   docker cp 20260817_research_report_artifacts.sql <supabase-db>:/tmp/
--   MSYS_NO_PATHCONV=1 docker exec <supabase-db> psql -U supabase_admin -d postgres -f /tmp/20260817_research_report_artifacts.sql

BEGIN;

-- ═══════════════════════════════════════════════════════════
-- research_report_artifacts — one row per Deep Research report
-- ═══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS public.research_report_artifacts (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,

    -- Contract identity. The surrogate `id` above keeps the schema's own
    -- convention; `artifact_ref` carries the value that appears in
    -- ResearchResultV1.artifact_refs, so a result document and a row can be
    -- joined. Without it nothing connects the contract family to the table.
    artifact_ref TEXT NOT NULL,

    -- Producing job / result. No foreign keys: neither ResearchJobV1 nor
    -- ResearchResultV1 has a table in this schema, and inventing one would
    -- pre-empt the parent/child job model that open decision D2 may require.
    job_id TEXT NOT NULL,
    result_id TEXT,

    -- Subject model from the merged service-tier declaration. `subject_ref` is
    -- the free text for a topic and the idea/bubble id otherwise.
    subject_type TEXT NOT NULL,
    subject_ref TEXT NOT NULL,

    -- Idea linkage. The FK lives here, not on public.ideas — see the header.
    -- idea_id   : the enriched idea, NULL for a topic subject.
    -- bubble_id : the owning cluster; both D2 shapes fit, because a single
    --             cluster report sets bubble_id alone while a per-member report
    --             sets both. Mirrors ideas_sync_outbox, which carries the same
    --             pair for the same reason.
    idea_id TEXT REFERENCES ideas(id) ON DELETE CASCADE,
    bubble_id TEXT REFERENCES ideas(id) ON DELETE SET NULL,

    -- artifact_type examples: research_report, research_log, source_manifest
    artifact_type TEXT NOT NULL DEFAULT 'research_report',
    name TEXT NOT NULL,                       -- file name the Hand wrote
    rel_path TEXT,                            -- path carried by research_complete

    -- Content: prefer content_json for structured (parsed) artifacts,
    -- content_text for the raw markdown report.
    format TEXT DEFAULT 'markdown',           -- markdown, text, json
    content_text TEXT,
    content_json JSONB,

    -- Run settings, bound to the Hand's own closed value sets. Nullable so a
    -- row can record an artifact whose settings were not captured; the CHECKs
    -- below reject any value outside the Hand's sets when one is present.
    depth TEXT,
    output_style TEXT,

    -- DEVIATION 1 from swe_design_artifacts.item_count (INTEGER DEFAULT 0):
    -- no default, and at least one. The research evidence policy is fail-closed
    -- — "a report without citations is a failed run" — so a zero-citation
    -- report must not be storable at all, and the writer must state the count
    -- rather than inherit a silent zero.
    citation_count INTEGER NOT NULL,

    -- E12: internal (Rowboat) context fails open, but never silently. A report
    -- written without internal context must say so; the CHECK below enforces
    -- that a disclosure text is present exactly when it was missing.
    internal_context_used BOOLEAN NOT NULL DEFAULT false,
    context_disclosure TEXT,

    -- No updated_at: a report artifact is write-once. A re-run of the same
    -- subject produces a new row, which is how "several reports over time on
    -- one idea" is expressed.
    created_at TIMESTAMPTZ DEFAULT now(),

    -- DEVIATION 2 from swe_design_artifacts' UNIQUE(run_id, rel_path): keyed on
    -- `name`, which is NOT NULL, instead of the nullable `rel_path`. In
    -- Postgres two NULLs are distinct, so a uniqueness constraint over a
    -- nullable column does not in fact deduplicate. Same intent, column that
    -- can carry it.
    CONSTRAINT research_report_artifacts_job_name_key UNIQUE (job_id, name),

    CONSTRAINT research_report_artifacts_artifact_ref_key UNIQUE (artifact_ref),

    -- DEVIATION 3: the neighbour validates none of its enumerated text columns.
    -- These CHECKs exist because each value set is closed upstream — the ID
    -- grammar by the contract family, depth/output_style by the Hand — and a
    -- typo that reaches storage is unrecoverable evidence damage.
    CONSTRAINT research_report_artifacts_artifact_ref_check
        CHECK (artifact_ref ~ '^artifact_v1_[0-9A-HJKMNPQRSTVWXYZ]{26}$'),
    CONSTRAINT research_report_artifacts_job_id_check
        CHECK (job_id ~ '^job_v1_[0-9A-HJKMNPQRSTVWXYZ]{26}$'),
    CONSTRAINT research_report_artifacts_result_id_check
        CHECK (result_id IS NULL OR result_id ~ '^result_v1_[0-9A-HJKMNPQRSTVWXYZ]{26}$'),
    CONSTRAINT research_report_artifacts_subject_type_check
        CHECK (subject_type IN ('topic', 'idea', 'bubble')),
    CONSTRAINT research_report_artifacts_format_check
        CHECK (format IN ('markdown', 'text', 'json')),
    CONSTRAINT research_report_artifacts_depth_check
        CHECK (depth IS NULL OR depth IN ('quick', 'thorough', 'exhaustive')),
    CONSTRAINT research_report_artifacts_output_style_check
        CHECK (output_style IS NULL OR output_style IN ('brief', 'detailed', 'academic', 'executive')),
    CONSTRAINT research_report_artifacts_citation_count_check
        CHECK (citation_count >= 1),

    -- E12 disclosure: no internal context ⇒ the omission is stated.
    CONSTRAINT research_report_artifacts_context_disclosure_check
        CHECK (internal_context_used OR context_disclosure IS NOT NULL),

    -- Subject linkage. Written to admit both open D2 shapes: a bubble subject
    -- needs its cluster, and may or may not also name a member idea.
    CONSTRAINT research_report_artifacts_subject_link_check CHECK (
        (subject_type = 'topic'  AND idea_id IS NULL AND bubble_id IS NULL)
        OR (subject_type = 'idea'   AND idea_id IS NOT NULL)
        OR (subject_type = 'bubble' AND bubble_id IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_research_report_artifacts_idea
    ON public.research_report_artifacts(idea_id);
CREATE INDEX IF NOT EXISTS idx_research_report_artifacts_bubble
    ON public.research_report_artifacts(bubble_id);
CREATE INDEX IF NOT EXISTS idx_research_report_artifacts_job
    ON public.research_report_artifacts(job_id);
CREATE INDEX IF NOT EXISTS idx_research_report_artifacts_type
    ON public.research_report_artifacts(artifact_type);
CREATE INDEX IF NOT EXISTS idx_research_report_artifacts_created
    ON public.research_report_artifacts(created_at DESC);

COMMENT ON TABLE public.research_report_artifacts IS
    'One row per Deep Research report artifact. The idea keeps a summary; the '
    'full report lives here and is referenced from it. The foreign key is on '
    'this side, so public.ideas and its Rowboat sync payload are unchanged. '
    'Not attached to any sync outbox — see open decision D3.';
COMMENT ON COLUMN public.research_report_artifacts.artifact_ref IS
    'Contract-side artifact id (artifact_v1_<ULID>) as it appears in '
    'ResearchResultV1.artifact_refs. Join key between result documents and rows.';
COMMENT ON COLUMN public.research_report_artifacts.bubble_id IS
    'Owning cluster (an idea with children). Set alone for a single cluster '
    'report, set alongside idea_id for a per-member report. Both shapes of the '
    'open bubble fan-out decision (D2) are storable without a further migration.';
COMMENT ON COLUMN public.research_report_artifacts.citation_count IS
    'Citations carried by the report. CHECK >= 1 because the research evidence '
    'policy is fail-closed: an uncited report is a failed run, not an artifact.';
COMMENT ON COLUMN public.research_report_artifacts.context_disclosure IS
    'Required when internal_context_used is false: the sentence the report '
    'carries stating it ran without internal context (E12 forbids silent '
    'degradation).';

-- ═══════════════════════════════════════════════════════════
-- RLS — allow-all for local dev (anon key), matching every other table
-- ═══════════════════════════════════════════════════════════
ALTER TABLE public.research_report_artifacts ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "allow_all_research_report_artifacts" ON public.research_report_artifacts;
CREATE POLICY "allow_all_research_report_artifacts" ON public.research_report_artifacts
    FOR ALL USING (true) WITH CHECK (true);

-- ═══════════════════════════════════════════════════════════
-- Realtime — Electron / dashboard can subscribe to report arrival
-- ═══════════════════════════════════════════════════════════
-- Guarded, unlike the bare `ALTER PUBLICATION ... ADD TABLE` in
-- 20260521_swe_design.sql and 20260411_init_vibemind.sql: re-running those
-- aborts with 42710 duplicate_object once the table is already a member. The
-- guard is what makes this file re-runnable end to end. It also tolerates a
-- plain Postgres without the Supabase publication.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime')
       AND NOT EXISTS (
           SELECT 1 FROM pg_publication_tables
           WHERE pubname = 'supabase_realtime'
             AND schemaname = 'public'
             AND tablename = 'research_report_artifacts'
       )
    THEN
        EXECUTE 'ALTER PUBLICATION supabase_realtime ADD TABLE public.research_report_artifacts';
    END IF;
END $$;

COMMIT;

-- PostgREST schema-cache reload (run separately if applying this file alone):
NOTIFY pgrst, 'reload schema';

-- ────────────────────────────────────────────────────────────────────────
-- Rollback (manual; see the runbook for when each variant applies)
--
--   A. Nothing written yet — full reversal, no data loss:
--        BEGIN;
--        DO $$
--        BEGIN
--            IF EXISTS (
--                SELECT 1 FROM pg_publication_tables
--                WHERE pubname = 'supabase_realtime'
--                  AND schemaname = 'public'
--                  AND tablename = 'research_report_artifacts'
--            ) THEN
--                EXECUTE 'ALTER PUBLICATION supabase_realtime DROP TABLE public.research_report_artifacts';
--            END IF;
--        END $$;
--        DROP TABLE IF EXISTS public.research_report_artifacts;
--        COMMIT;
--        NOTIFY pgrst, 'reload schema';
--
--   B. Artifacts already written — preserve them first:
--        \copy (SELECT * FROM public.research_report_artifacts)
--              TO 'research_report_artifacts_backup.csv' CSV HEADER
--      then run variant A. Dropping the table does NOT touch public.ideas:
--      the foreign keys point from here to there, never the other way, and no
--      trigger, outbox row, or column on public.ideas is created by this file.
--      Idea summaries survive a rollback and become pointers to a report that
--      no longer resolves — the dangling-reference cost of the linked-artifact
--      shape, and the reason the backup step comes first.
--
-- Idempotency
--   Re-running the whole file is safe: CREATE TABLE IF NOT EXISTS,
--   CREATE INDEX IF NOT EXISTS, DROP POLICY IF EXISTS before CREATE POLICY,
--   and the guarded publication block above. ENABLE ROW LEVEL SECURITY is
--   idempotent by definition. COMMENT ON overwrites.
--   Caveat, and it is the one that matters: IF NOT EXISTS makes a re-run
--   *harmless*, not *converging*. Against a database that already holds an
--   older `research_report_artifacts`, this file changes nothing and reports
--   success. Any later change to the shape needs its own ALTER migration.
-- ────────────────────────────────────────────────────────────────────────
