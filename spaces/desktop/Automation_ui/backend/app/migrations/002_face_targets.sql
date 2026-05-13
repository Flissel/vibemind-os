-- face_targets: Supabase-backed face-swap target presets.
-- Replaces the filesystem-based vibevideo_deepfake/faceswap/targets/*.jpg
-- with a queryable, taggable, multi-image-per-person store.
--
-- Source image bytes live in Supabase Storage bucket 'face_targets'.
-- This table holds the metadata + embedding for duplicate-detection
-- and similarity-search across the collection.

create extension if not exists "uuid-ossp";
create extension if not exists vector;

create table if not exists public.face_targets (
    -- Stable slug (URL-safe, used by /api/video/faceswap target=...)
    id              text primary key,
    display_name    text not null,

    -- Storage: bucket "face_targets", path = id || '/' || image_id || '.jpg'
    -- Each target may have multiple source images (different angles / lighting).
    -- The first-uploaded one is the "primary" for backwards-compat.
    primary_image_path text not null,

    -- Identity embedding (InsightFace buffalo_l, 512-dim float32).
    -- Used for: duplicate detection, "find similar face", clustering.
    embedding       vector(512),

    -- Free-text tags: ["male", "white", "30s", "celebrity:eminem"]
    tags            text[] default '{}'::text[],

    -- Provenance / consent
    source_url      text,
    license_note    text,
    consent_status  text default 'private',  -- private / consented / public-domain

    -- Lifecycle
    enabled         boolean default true,
    created_at      timestamptz default now(),
    updated_at      timestamptz default now()
);

-- One row per source image; primary_image_path on face_targets references one of these.
create table if not exists public.face_target_images (
    id              uuid primary key default uuid_generate_v4(),
    target_id       text not null references public.face_targets(id) on delete cascade,
    storage_path    text not null,    -- bucket: face_targets, path: target_id/uuid.jpg
    width           int,
    height          int,
    embedding       vector(512),      -- per-image embedding for multi-source blending
    quality_score   real,             -- 0-1, e.g. blur score / face confidence
    created_at      timestamptz default now()
);

create index if not exists face_targets_enabled_idx
    on public.face_targets (enabled, display_name);
create index if not exists face_target_images_target_idx
    on public.face_target_images (target_id);

-- Touch trigger so updated_at stays fresh
create or replace function public.face_targets_touch_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end$$;

drop trigger if exists trg_face_targets_touch on public.face_targets;
create trigger trg_face_targets_touch
    before update on public.face_targets
    for each row execute function public.face_targets_touch_updated_at();
