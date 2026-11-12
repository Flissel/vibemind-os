-- 001_schema.sql: Laufzeitrolle event_app, Schema event, Tabellen.
-- Wird nur als Admin angewendet (db/anwenden.py). Alle Tabellen gehoeren dem Admin.
-- Rechte und RLS fuer event_app folgen in 002_rls.sql.

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'event_app') then
        create role event_app nologin;
    end if;
end
$$;

create schema if not exists event;
create extension if not exists pgcrypto;

-- Mandanten und Personen ------------------------------------------------------

create table if not exists event.firma (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    kurz text not null unique check (kurz ~ '^[a-z0-9-]{2,32}$'),
    angelegt timestamptz not null default now()
);

create table if not exists event.person (
    id uuid primary key default gen_random_uuid(),
    mail text not null,
    name text not null,
    passwort_hash text,
    betreiber boolean not null default false,
    aktiv boolean not null default true,
    sitzungs_version int not null default 1,
    angelegt timestamptz not null default now()
);
create unique index if not exists person_mail_klein on event.person (lower(mail));

create table if not exists event.mitgliedschaft (
    person_id uuid not null references event.person (id),
    firma_id uuid not null references event.firma (id),
    rolle text not null check (rolle in ('leitung', 'mitglied')),
    primary key (person_id, firma_id)
);

-- Events und Kontakte ---------------------------------------------------------

create table if not exists event.event (
    id uuid primary key default gen_random_uuid(),
    firma_id uuid not null references event.firma (id),
    kategorie text not null,
    name text not null,
    beginn timestamptz not null,
    ende timestamptz not null,
    ort_adresse text,
    ort_link text,
    beschreibung text,
    status text not null default 'geplant' check (
        status in ('entwurf', 'geplant', 'laeuft', 'abgeschlossen', 'abgesagt', 'archiviert')
    ),
    quelle text not null default 'neu' check (quelle in ('neu', 'legacy')),
    legacy_url text,
    metadaten jsonb not null default '{}',
    angelegt timestamptz not null default now(),
    geaendert timestamptz not null default now(),
    check (ende >= beginn),
    unique (id, firma_id)
);

create table if not exists event.kontakt (
    id uuid primary key default gen_random_uuid(),
    firma_id uuid not null references event.firma (id),
    name text not null,
    mail text,
    telegram text,
    person_id uuid references event.person (id),
    angelegt timestamptz not null default now(),
    unique (id, firma_id)
);

-- Bausteine -------------------------------------------------------------------
-- (event_id, firma_id) und (kontakt_id, firma_id) sind zusammengesetzte
-- Fremdschluessel: eine Zeile kann nur auf Event und Kontakt derselben Firma zeigen.

create table if not exists event.orga_team (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null,
    firma_id uuid not null,
    kontakt_id uuid not null,
    rolle text,
    angelegt timestamptz not null default now(),
    foreign key (event_id, firma_id) references event.event (id, firma_id) on delete cascade,
    foreign key (kontakt_id, firma_id) references event.kontakt (id, firma_id)
);

create table if not exists event.teilnehmende (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null,
    firma_id uuid not null,
    kontakt_id uuid not null,
    zusage text not null default 'offen' check (
        zusage in ('offen', 'zugesagt', 'vielleicht', 'abgesagt')
    ),
    angelegt timestamptz not null default now(),
    foreign key (event_id, firma_id) references event.event (id, firma_id) on delete cascade,
    foreign key (kontakt_id, firma_id) references event.kontakt (id, firma_id),
    unique (event_id, kontakt_id)
);

create table if not exists event.aufgabe_kategorie (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null,
    firma_id uuid not null,
    name text not null,
    angelegt timestamptz not null default now(),
    foreign key (event_id, firma_id) references event.event (id, firma_id) on delete cascade,
    unique (event_id, name)
);

create table if not exists event.aufgabe (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null,
    firma_id uuid not null,
    titel text not null,
    beschreibung text,
    status text not null default 'offen' check (status in ('offen', 'in_arbeit', 'erledigt')),
    kategorie_id uuid references event.aufgabe_kategorie (id) on delete set null,
    verantwortlich_id uuid,
    faellig timestamptz,
    angelegt timestamptz not null default now(),
    foreign key (event_id, firma_id) references event.event (id, firma_id) on delete cascade,
    foreign key (verantwortlich_id, firma_id) references event.kontakt (id, firma_id)
);

create table if not exists event.ablauf (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null,
    firma_id uuid not null,
    beginn timestamptz not null,
    ende timestamptz,
    titel text not null,
    ort text,
    verantwortlich_id uuid,
    angelegt timestamptz not null default now(),
    foreign key (event_id, firma_id) references event.event (id, firma_id) on delete cascade,
    foreign key (verantwortlich_id, firma_id) references event.kontakt (id, firma_id),
    check (ende is null or ende >= beginn)
);

create table if not exists event.budget_posten (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null,
    firma_id uuid not null,
    titel text not null,
    art text not null check (art in ('kosten', 'einnahme')),
    betrag_cent bigint not null check (betrag_cent >= 0),
    status text not null default 'geplant' check (status in ('geplant', 'bestaetigt', 'bezahlt')),
    angelegt timestamptz not null default now(),
    foreign key (event_id, firma_id) references event.event (id, firma_id) on delete cascade
);

create table if not exists event.verlauf (
    id bigserial primary key,
    event_id uuid not null,
    firma_id uuid not null,
    zeitpunkt timestamptz not null default now(),
    actor text not null,
    quelle text not null,
    baustein text not null,
    aktion text not null,
    ziel_id uuid,
    foreign key (event_id, firma_id) references event.event (id, firma_id) on delete cascade
);

-- Einladungen -----------------------------------------------------------------
-- Gespeichert wird nur der sha256-Hash des Tokens.

create table if not exists event.einladung (
    id uuid primary key default gen_random_uuid(),
    person_id uuid not null references event.person (id),
    firma_id uuid not null references event.firma (id),
    rolle text not null check (rolle in ('leitung', 'mitglied')),
    token_hash text not null unique,
    gueltig_bis timestamptz not null,
    eingeloest timestamptz
);
