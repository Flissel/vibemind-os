-- Schema-Auszug fuer die Tests von "Zurueck" (Stufe A).
-- Quelle: pg_dump --schema-only der zwoelf ueberwachten Tabellen aus der laufenden
-- Supabase (debian-supabase-db-1, 20.11.2026), maschinell gekuerzt:
--   * `embedding_vector` ist hier `text` statt `vector(384)` (kein pgvector im Test-Postgres),
--     der hnsw-Index dazu fehlt.
--   * Fremdschluessel auf Marketing-Tabellen ausserhalb der zwoelf fehlen.
--   * Von den vorhandenen Ausloesern bleiben nur die beiden `updated_at`-Ausloeser;
--     die Abgleich- und Marketing-Ausloeser brauchen Tabellen ausserhalb des Auszugs.
-- Nicht von Hand pflegen: bei Schema-Aenderungen neu ziehen.

CREATE SCHEMA IF NOT EXISTS marketing;

CREATE FUNCTION public.ideas_touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $f$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$f$;

CREATE FUNCTION public.canvas_touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $f$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$f$;

CREATE TABLE marketing.broadcast_proposals (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    channel text NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    draft_subject text,
    draft_body_text text NOT NULL,
    draft_body_html text,
    draft_media_url text,
    draft_template_id uuid,
    draft_channel_params jsonb DEFAULT '{}'::jsonb,
    created_by text NOT NULL,
    edited_by text,
    edited_at timestamp with time zone,
    approval_channel text,
    approval_requested_at timestamp with time zone,
    approval_token_hash text,
    approved_at timestamp with time zone,
    approved_by text,
    rejected_at timestamp with time zone,
    rejected_by text,
    rejection_reason text,
    sent_at timestamp with time zone,
    sent_external_id text,
    sent_via_campaign_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    openfang_approval_id uuid,
    bubble_id text,
    CONSTRAINT broadcast_proposals_approved_has_actor CHECK (((approved_at IS NULL) OR (approved_by IS NOT NULL))),
    CONSTRAINT broadcast_proposals_body_nonempty CHECK ((length(draft_body_text) > 0)),
    CONSTRAINT broadcast_proposals_rejected_has_actor CHECK (((rejected_at IS NULL) OR (rejected_by IS NOT NULL))),
    CONSTRAINT broadcast_proposals_status_known CHECK ((status = ANY (ARRAY['draft'::text, 'pending_approval'::text, 'approved'::text, 'rejected'::text, 'sent'::text, 'failed'::text]))),
    CONSTRAINT broadcast_proposals_token_when_requested CHECK (((status <> 'pending_approval'::text) OR (approval_token_hash IS NOT NULL)))
);

CREATE TABLE marketing.crowdfunding_batches (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    bubble_id text,
    channel text NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    amount text DEFAULT '1.00'::text NOT NULL,
    currency text DEFAULT 'EUR'::text NOT NULL,
    recipients_audience_id uuid,
    message_template text,
    approval_channel text,
    approval_requested_at timestamp with time zone,
    approval_token_hash text,
    approval_token_raw text,
    openfang_approval_id uuid,
    approved_at timestamp with time zone,
    approved_by text,
    rejected_at timestamp with time zone,
    rejected_by text,
    rejection_reason text,
    sent_at timestamp with time zone,
    sent_batch_size integer,
    created_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT crowdfunding_batches_amount_sane CHECK ((amount ~ '^[0-9]+\.[0-9]{2}$'::text)),
    CONSTRAINT crowdfunding_batches_approved_has_actor CHECK (((approved_at IS NULL) OR (approved_by IS NOT NULL))),
    CONSTRAINT crowdfunding_batches_rejected_has_actor CHECK (((rejected_at IS NULL) OR (rejected_by IS NOT NULL))),
    CONSTRAINT crowdfunding_batches_status_known CHECK ((status = ANY (ARRAY['draft'::text, 'pending_approval'::text, 'approved'::text, 'rejected'::text, 'sent'::text, 'failed'::text])))
);

CREATE TABLE public.ideas (
    id text DEFAULT (gen_random_uuid())::text NOT NULL,
    title text NOT NULL,
    description text DEFAULT ''::text,
    source text DEFAULT 'voice'::text,
    created_at timestamp with time zone DEFAULT now(),
    score double precision DEFAULT 0.0,
    status text DEFAULT 'raw'::text,
    promoted_to_project_id text,
    tags jsonb DEFAULT '[]'::jsonb,
    metadata jsonb DEFAULT '{}'::jsonb,
    agent_id text,
    parent_id text,
    embedding_hash text,
    embedding_vector text,
    category text,
    color text,
    channels jsonb DEFAULT '[]'::jsonb NOT NULL,
    auto_classified boolean DEFAULT false NOT NULL,
    classification_confidence numeric,
    classified_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now(),
    last_synced_at timestamp with time zone,
    kind text DEFAULT 'idea'::text NOT NULL,
    target_channel text,
    mirofish_report_id text,
    mirofish_score integer,
    mirofish_last_run_at timestamp with time zone,
    broadcast_proposal_id uuid,
    sent_external_id text
);

CREATE TABLE public.canvas_edges (
    id text DEFAULT (gen_random_uuid())::text NOT NULL,
    from_node_id text NOT NULL,
    to_node_id text NOT NULL,
    edge_type text DEFAULT 'default'::text
);

CREATE TABLE public.canvas_nodes (
    id text DEFAULT (gen_random_uuid())::text NOT NULL,
    node_type text DEFAULT 'note'::text NOT NULL,
    title text DEFAULT ''::text,
    content text DEFAULT ''::text,
    x double precision DEFAULT 0.0,
    y double precision DEFAULT 0.0,
    linked_idea_id text,
    linked_project_id text,
    summary text,
    metadata jsonb DEFAULT '{}'::jsonb,
    format_schema jsonb,
    content_json jsonb,
    previous_content_json jsonb,
    last_formatted timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now(),
    last_synced_at timestamp with time zone,
    reformat_pending boolean DEFAULT false
);

CREATE TABLE public.discovered_edges (
    id text DEFAULT (gen_random_uuid())::text NOT NULL,
    from_idea_id text NOT NULL,
    to_idea_id text NOT NULL,
    edge_type text DEFAULT 'discovered'::text,
    edge_label text,
    reasoning text,
    confidence double precision DEFAULT 0.0,
    connection_type text,
    exploration_session_id text,
    exploration_node_id text,
    created_at timestamp with time zone DEFAULT now(),
    metadata jsonb DEFAULT '{}'::jsonb
);

CREATE TABLE public.exploration_sessions (
    id text DEFAULT (gen_random_uuid())::text NOT NULL,
    root_bubble_id text NOT NULL,
    root_bubble_title text,
    exploration_query text,
    status text DEFAULT 'running'::text,
    current_stage integer DEFAULT 1,
    created_at timestamp with time zone DEFAULT now(),
    completed_at timestamp with time zone,
    total_nodes_explored integer DEFAULT 0,
    best_score double precision DEFAULT 0.0,
    metadata jsonb DEFAULT '{}'::jsonb
);

CREATE TABLE public.mermaid_diagrams (
    id text DEFAULT (gen_random_uuid())::text NOT NULL,
    title text NOT NULL,
    diagram_type text DEFAULT 'flowchart'::text NOT NULL,
    content text NOT NULL,
    source_idea_id text,
    source_shuttle_id text,
    source_requirement_ids jsonb DEFAULT '[]'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone,
    version integer DEFAULT 1,
    metadata jsonb DEFAULT '{}'::jsonb
);

CREATE TABLE public.projects (
    id text DEFAULT (gen_random_uuid())::text NOT NULL,
    name text NOT NULL,
    description text DEFAULT ''::text,
    status text DEFAULT 'active'::text,
    created_at timestamp with time zone DEFAULT now(),
    from_idea_id text,
    progress double precision DEFAULT 0.0,
    metadata jsonb DEFAULT '{}'::jsonb,
    project_path text,
    generation_status text DEFAULT 'pending'::text,
    vnc_port integer,
    job_id text,
    requirements_json text,
    convergence_progress double precision DEFAULT 0.0,
    preview_url text,
    tech_stack text,
    error_message text
);

CREATE TABLE public.research_report_artifacts (
    id text DEFAULT (gen_random_uuid())::text NOT NULL,
    artifact_ref text NOT NULL,
    job_id text NOT NULL,
    result_id text,
    subject_type text NOT NULL,
    subject_ref text NOT NULL,
    idea_id text,
    bubble_id text,
    artifact_type text DEFAULT 'research_report'::text NOT NULL,
    name text NOT NULL,
    rel_path text,
    format text DEFAULT 'markdown'::text,
    content_text text,
    content_json jsonb,
    depth text,
    output_style text,
    citation_count integer NOT NULL,
    internal_context_used boolean DEFAULT false NOT NULL,
    context_disclosure text,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT research_report_artifacts_artifact_ref_check CHECK ((artifact_ref ~ '^artifact_v1_[0-9A-HJKMNPQRSTVWXYZ]{26}$'::text)),
    CONSTRAINT research_report_artifacts_citation_count_check CHECK ((citation_count >= 1)),
    CONSTRAINT research_report_artifacts_context_disclosure_check CHECK ((internal_context_used OR (context_disclosure IS NOT NULL))),
    CONSTRAINT research_report_artifacts_depth_check CHECK (((depth IS NULL) OR (depth = ANY (ARRAY['quick'::text, 'thorough'::text, 'exhaustive'::text])))),
    CONSTRAINT research_report_artifacts_format_check CHECK ((format = ANY (ARRAY['markdown'::text, 'text'::text, 'json'::text]))),
    CONSTRAINT research_report_artifacts_job_id_check CHECK ((job_id ~ '^job_v1_[0-9A-HJKMNPQRSTVWXYZ]{26}$'::text)),
    CONSTRAINT research_report_artifacts_output_style_check CHECK (((output_style IS NULL) OR (output_style = ANY (ARRAY['brief'::text, 'detailed'::text, 'academic'::text, 'executive'::text])))),
    CONSTRAINT research_report_artifacts_result_id_check CHECK (((result_id IS NULL) OR (result_id ~ '^result_v1_[0-9A-HJKMNPQRSTVWXYZ]{26}$'::text))),
    CONSTRAINT research_report_artifacts_subject_link_check CHECK ((((subject_type = 'topic'::text) AND (idea_id IS NULL) AND (bubble_id IS NULL)) OR ((subject_type = 'idea'::text) AND (idea_id IS NOT NULL)) OR ((subject_type = 'bubble'::text) AND (bubble_id IS NOT NULL)))),
    CONSTRAINT research_report_artifacts_subject_type_check CHECK ((subject_type = ANY (ARRAY['topic'::text, 'idea'::text, 'bubble'::text])))
);

CREATE TABLE public.shuttles (
    id text DEFAULT (gen_random_uuid())::text NOT NULL,
    shuttle_id text NOT NULL,
    bubble_id text NOT NULL,
    bubble_name text NOT NULL,
    score double precision DEFAULT 0.0,
    passed_count integer DEFAULT 0,
    failed_count integer DEFAULT 0,
    total_count integer DEFAULT 0,
    status text DEFAULT 'launching'::text,
    current_stage text DEFAULT 'mining'::text,
    project_id text,
    stage_type text DEFAULT 'full'::text,
    stage_data jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    completed_at timestamp with time zone,
    requirement_results jsonb DEFAULT '{}'::jsonb,
    metadata jsonb DEFAULT '{}'::jsonb
);

CREATE TABLE public.swe_design_runs (
    id text DEFAULT (gen_random_uuid())::text NOT NULL,
    pipeline_id text,
    project_name text NOT NULL,
    slug text NOT NULL,
    status text DEFAULT 'in_progress'::text,
    domain text DEFAULT 'custom'::text,
    total_stages integer DEFAULT 0,
    completed_stages integer DEFAULT 0,
    total_cost_usd double precision DEFAULT 0.0,
    total_tokens integer DEFAULT 0,
    total_llm_calls integer DEFAULT 0,
    project_id text,
    shuttle_id text,
    output_dir text,
    gitea_repo text,
    gitea_commit_sha text,
    manifest jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    completed_at timestamp with time zone
);

ALTER TABLE ONLY marketing.broadcast_proposals
    ADD CONSTRAINT broadcast_proposals_pkey PRIMARY KEY (id);

ALTER TABLE ONLY marketing.crowdfunding_batches
    ADD CONSTRAINT crowdfunding_batches_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.canvas_edges
    ADD CONSTRAINT canvas_edges_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.canvas_nodes
    ADD CONSTRAINT canvas_nodes_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.discovered_edges
    ADD CONSTRAINT discovered_edges_from_idea_id_to_idea_id_key UNIQUE (from_idea_id, to_idea_id);

ALTER TABLE ONLY public.discovered_edges
    ADD CONSTRAINT discovered_edges_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.exploration_sessions
    ADD CONSTRAINT exploration_sessions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.ideas
    ADD CONSTRAINT ideas_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.mermaid_diagrams
    ADD CONSTRAINT mermaid_diagrams_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT projects_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.research_report_artifacts
    ADD CONSTRAINT research_report_artifacts_artifact_ref_key UNIQUE (artifact_ref);

ALTER TABLE ONLY public.research_report_artifacts
    ADD CONSTRAINT research_report_artifacts_job_name_key UNIQUE (job_id, name);

ALTER TABLE ONLY public.research_report_artifacts
    ADD CONSTRAINT research_report_artifacts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.shuttles
    ADD CONSTRAINT shuttles_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.shuttles
    ADD CONSTRAINT shuttles_shuttle_id_key UNIQUE (shuttle_id);

ALTER TABLE ONLY public.swe_design_runs
    ADD CONSTRAINT swe_design_runs_pkey PRIMARY KEY (id);

CREATE INDEX idx_broadcast_proposals_bubble ON marketing.broadcast_proposals USING btree (bubble_id) WHERE (bubble_id IS NOT NULL);

CREATE INDEX idx_broadcast_proposals_channel ON marketing.broadcast_proposals USING btree (channel, created_at DESC);

CREATE INDEX idx_broadcast_proposals_openfang_pending ON marketing.broadcast_proposals USING btree (openfang_approval_id) WHERE ((status = 'pending_approval'::text) AND (openfang_approval_id IS NOT NULL));

CREATE INDEX idx_broadcast_proposals_pending_approval ON marketing.broadcast_proposals USING btree (approval_requested_at DESC) WHERE (status = 'pending_approval'::text);

CREATE INDEX idx_broadcast_proposals_status ON marketing.broadcast_proposals USING btree (status, created_at DESC);

CREATE INDEX idx_crowdfunding_batches_bubble ON marketing.crowdfunding_batches USING btree (bubble_id) WHERE (bubble_id IS NOT NULL);

CREATE INDEX idx_crowdfunding_batches_status ON marketing.crowdfunding_batches USING btree (status, created_at DESC);

CREATE INDEX idx_canvas_edges_from ON public.canvas_edges USING btree (from_node_id);

CREATE INDEX idx_canvas_edges_to ON public.canvas_edges USING btree (to_node_id);

CREATE INDEX idx_canvas_nodes_type ON public.canvas_nodes USING btree (node_type);

CREATE INDEX idx_disc_edges_from ON public.discovered_edges USING btree (from_idea_id);

CREATE INDEX idx_disc_edges_to ON public.discovered_edges USING btree (to_idea_id);

CREATE INDEX idx_exploration_root ON public.exploration_sessions USING btree (root_bubble_id);

CREATE INDEX idx_exploration_status ON public.exploration_sessions USING btree (status);

CREATE INDEX idx_ideas_created ON public.ideas USING btree (created_at DESC);

CREATE INDEX idx_ideas_parent ON public.ideas USING btree (parent_id);

CREATE INDEX idx_ideas_score ON public.ideas USING btree (score DESC);

CREATE INDEX idx_ideas_status ON public.ideas USING btree (status);

CREATE INDEX idx_mermaid_idea ON public.mermaid_diagrams USING btree (source_idea_id);

CREATE INDEX idx_mermaid_type ON public.mermaid_diagrams USING btree (diagram_type);

CREATE INDEX idx_projects_generation ON public.projects USING btree (generation_status);

CREATE INDEX idx_projects_job_id ON public.projects USING btree (job_id);

CREATE INDEX idx_projects_status ON public.projects USING btree (status);

CREATE INDEX idx_research_report_artifacts_bubble ON public.research_report_artifacts USING btree (bubble_id);

CREATE INDEX idx_research_report_artifacts_created ON public.research_report_artifacts USING btree (created_at DESC);

CREATE INDEX idx_research_report_artifacts_idea ON public.research_report_artifacts USING btree (idea_id);

CREATE INDEX idx_research_report_artifacts_job ON public.research_report_artifacts USING btree (job_id);

CREATE INDEX idx_research_report_artifacts_type ON public.research_report_artifacts USING btree (artifact_type);

CREATE INDEX idx_shuttles_bubble ON public.shuttles USING btree (bubble_id);

CREATE INDEX idx_shuttles_created ON public.shuttles USING btree (created_at DESC);

CREATE INDEX idx_shuttles_project ON public.shuttles USING btree (project_id);

CREATE INDEX idx_shuttles_status ON public.shuttles USING btree (status);

CREATE INDEX idx_swe_design_runs_created ON public.swe_design_runs USING btree (created_at DESC);

CREATE INDEX idx_swe_design_runs_project ON public.swe_design_runs USING btree (project_id);

CREATE INDEX idx_swe_design_runs_slug ON public.swe_design_runs USING btree (slug);

CREATE INDEX idx_swe_design_runs_status ON public.swe_design_runs USING btree (status);

CREATE TRIGGER trg_canvas_touch_updated_at BEFORE UPDATE ON public.canvas_nodes FOR EACH ROW EXECUTE FUNCTION public.canvas_touch_updated_at();

CREATE TRIGGER trg_ideas_touch_updated_at BEFORE UPDATE ON public.ideas FOR EACH ROW EXECUTE FUNCTION public.ideas_touch_updated_at();

ALTER TABLE ONLY marketing.broadcast_proposals
    ADD CONSTRAINT broadcast_proposals_bubble_id_fkey FOREIGN KEY (bubble_id) REFERENCES public.ideas(id) ON DELETE SET NULL;

ALTER TABLE ONLY marketing.crowdfunding_batches
    ADD CONSTRAINT crowdfunding_batches_bubble_id_fkey FOREIGN KEY (bubble_id) REFERENCES public.ideas(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.canvas_edges
    ADD CONSTRAINT canvas_edges_from_node_id_fkey FOREIGN KEY (from_node_id) REFERENCES public.canvas_nodes(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.canvas_edges
    ADD CONSTRAINT canvas_edges_to_node_id_fkey FOREIGN KEY (to_node_id) REFERENCES public.canvas_nodes(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.canvas_nodes
    ADD CONSTRAINT canvas_nodes_linked_idea_id_fkey FOREIGN KEY (linked_idea_id) REFERENCES public.ideas(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.canvas_nodes
    ADD CONSTRAINT canvas_nodes_linked_project_id_fkey FOREIGN KEY (linked_project_id) REFERENCES public.projects(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.discovered_edges
    ADD CONSTRAINT discovered_edges_exploration_session_id_fkey FOREIGN KEY (exploration_session_id) REFERENCES public.exploration_sessions(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.discovered_edges
    ADD CONSTRAINT discovered_edges_from_idea_id_fkey FOREIGN KEY (from_idea_id) REFERENCES public.ideas(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.discovered_edges
    ADD CONSTRAINT discovered_edges_to_idea_id_fkey FOREIGN KEY (to_idea_id) REFERENCES public.ideas(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.exploration_sessions
    ADD CONSTRAINT exploration_sessions_root_bubble_id_fkey FOREIGN KEY (root_bubble_id) REFERENCES public.ideas(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.ideas
    ADD CONSTRAINT fk_ideas_project FOREIGN KEY (promoted_to_project_id) REFERENCES public.projects(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.ideas
    ADD CONSTRAINT ideas_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.ideas(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.mermaid_diagrams
    ADD CONSTRAINT mermaid_diagrams_source_idea_id_fkey FOREIGN KEY (source_idea_id) REFERENCES public.ideas(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.mermaid_diagrams
    ADD CONSTRAINT mermaid_diagrams_source_shuttle_id_fkey FOREIGN KEY (source_shuttle_id) REFERENCES public.shuttles(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT projects_from_idea_id_fkey FOREIGN KEY (from_idea_id) REFERENCES public.ideas(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.research_report_artifacts
    ADD CONSTRAINT research_report_artifacts_bubble_id_fkey FOREIGN KEY (bubble_id) REFERENCES public.ideas(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.research_report_artifacts
    ADD CONSTRAINT research_report_artifacts_idea_id_fkey FOREIGN KEY (idea_id) REFERENCES public.ideas(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.shuttles
    ADD CONSTRAINT shuttles_bubble_id_fkey FOREIGN KEY (bubble_id) REFERENCES public.ideas(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.shuttles
    ADD CONSTRAINT shuttles_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.swe_design_runs
    ADD CONSTRAINT swe_design_runs_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.swe_design_runs
    ADD CONSTRAINT swe_design_runs_shuttle_id_fkey FOREIGN KEY (shuttle_id) REFERENCES public.shuttles(id) ON DELETE SET NULL;

CREATE POLICY allow_all_canvas_edges ON public.canvas_edges USING (true) WITH CHECK (true);

CREATE POLICY allow_all_canvas_nodes ON public.canvas_nodes USING (true) WITH CHECK (true);

CREATE POLICY allow_all_discovered_edges ON public.discovered_edges USING (true) WITH CHECK (true);

CREATE POLICY allow_all_exploration_sessions ON public.exploration_sessions USING (true) WITH CHECK (true);

CREATE POLICY allow_all_ideas ON public.ideas USING (true) WITH CHECK (true);

CREATE POLICY allow_all_mermaid_diagrams ON public.mermaid_diagrams USING (true) WITH CHECK (true);

CREATE POLICY allow_all_projects ON public.projects USING (true) WITH CHECK (true);

CREATE POLICY allow_all_research_report_artifacts ON public.research_report_artifacts USING (true) WITH CHECK (true);

CREATE POLICY allow_all_shuttles ON public.shuttles USING (true) WITH CHECK (true);

CREATE POLICY allow_all_swe_design_runs ON public.swe_design_runs USING (true) WITH CHECK (true);

ALTER TABLE public.canvas_edges ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.canvas_nodes ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.discovered_edges ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.exploration_sessions ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.ideas ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.mermaid_diagrams ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.projects ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.research_report_artifacts ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.shuttles ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.swe_design_runs ENABLE ROW LEVEL SECURITY;
