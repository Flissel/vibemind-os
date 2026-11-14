-- 003_zusatz_bausteine.sql: Tabellen der Zusatz-Bausteine (Registry in system/bausteine.py)
-- und Tabelle nachricht. Wird nur als Admin angewendet (db/anwenden.py).
--
-- Muster wie die Kern-Bausteine in 001/002:
--   (event_id, firma_id)  -> event (id, firma_id)    nur Event derselben Firma
--   (<kontakt>, firma_id) -> kontakt (id, firma_id)  nur Kontakt derselben Firma
--   (<ref>, event_id)     -> <ziel> (id, event_id)   nur Zeile desselben Events
-- RLS: Firma und (falls gesetzt) gewaehltes Event. Die Datei ist wiederholbar
-- (if not exists / drop policy if exists), anwenden.py fuehrt sie trotzdem nur einmal aus.

-- Hackathon -------------------------------------------------------------------

create table if not exists event.wettbewerb_team (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null,
    firma_id uuid not null,
    name text not null,
    thema text,
    ansprech_id uuid,
    angelegt timestamptz not null default now(),
    foreign key (event_id, firma_id) references event.event (id, firma_id) on delete cascade,
    foreign key (ansprech_id, firma_id) references event.kontakt (id, firma_id),
    unique (id, event_id)
);

-- Ein Team mit Abgaben laesst sich nicht loeschen (kein stiller Datenverlust); beim
-- Loeschen des Events raeumt die Kaskade beides gemeinsam ab.
create table if not exists event.abgabe (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null,
    firma_id uuid not null,
    team_id uuid not null,
    titel text not null,
    link text check (link ~ '^https?://'),
    eingereicht timestamptz,
    angelegt timestamptz not null default now(),
    foreign key (event_id, firma_id) references event.event (id, firma_id) on delete cascade,
    foreign key (team_id, event_id) references event.wettbewerb_team (id, event_id)
);

create table if not exists event.jury (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null,
    firma_id uuid not null,
    kontakt_id uuid not null,
    rolle text,
    angelegt timestamptz not null default now(),
    foreign key (event_id, firma_id) references event.event (id, firma_id) on delete cascade,
    foreign key (kontakt_id, firma_id) references event.kontakt (id, firma_id)
);

-- Messe -----------------------------------------------------------------------

create table if not exists event.aussteller (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null,
    firma_id uuid not null,
    name text not null,
    ansprech_id uuid,
    status text not null default 'angefragt' check (
        status in ('angefragt', 'zugesagt', 'abgesagt')
    ),
    angelegt timestamptz not null default now(),
    foreign key (event_id, firma_id) references event.event (id, firma_id) on delete cascade,
    foreign key (ansprech_id, firma_id) references event.kontakt (id, firma_id),
    unique (id, event_id)
);

create table if not exists event.stand (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null,
    firma_id uuid not null,
    bezeichnung text not null,
    aussteller_id uuid,
    groesse text,
    angelegt timestamptz not null default now(),
    foreign key (event_id, firma_id) references event.event (id, firma_id) on delete cascade,
    -- Aussteller nur aus demselben Event; beim Loeschen wird nur aussteller_id geleert.
    foreign key (aussteller_id, event_id) references event.aussteller (id, event_id)
        on delete set null (aussteller_id)
);

-- Online-Event ----------------------------------------------------------------

create table if not exists event.sprecher (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null,
    firma_id uuid not null,
    kontakt_id uuid not null,
    thema text,
    status text not null default 'angefragt' check (
        status in ('angefragt', 'zugesagt', 'abgesagt')
    ),
    angelegt timestamptz not null default now(),
    foreign key (event_id, firma_id) references event.event (id, firma_id) on delete cascade,
    foreign key (kontakt_id, firma_id) references event.kontakt (id, firma_id)
);

create table if not exists event.online_raum (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null,
    firma_id uuid not null,
    titel text not null,
    link text not null check (link ~ '^https?://'),
    zugang text,
    angelegt timestamptz not null default now(),
    foreign key (event_id, firma_id) references event.event (id, firma_id) on delete cascade
);

create table if not exists event.aufzeichnung (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null,
    firma_id uuid not null,
    titel text not null,
    link text check (link ~ '^https?://'),
    status text not null default 'geplant' check (
        status in ('geplant', 'aufgenommen', 'veroeffentlicht')
    ),
    angelegt timestamptz not null default now(),
    foreign key (event_id, firma_id) references event.event (id, firma_id) on delete cascade
);

-- Team-Meeting ----------------------------------------------------------------

create table if not exists event.protokoll_punkt (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null,
    firma_id uuid not null,
    punkt text not null,
    notiz text,
    verantwortlich_id uuid,
    angelegt timestamptz not null default now(),
    foreign key (event_id, firma_id) references event.event (id, firma_id) on delete cascade,
    foreign key (verantwortlich_id, firma_id) references event.kontakt (id, firma_id)
);

create table if not exists event.beschluss (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null,
    firma_id uuid not null,
    text text not null,
    gefasst timestamptz,
    verantwortlich_id uuid,
    angelegt timestamptz not null default now(),
    foreign key (event_id, firma_id) references event.event (id, firma_id) on delete cascade,
    foreign key (verantwortlich_id, firma_id) references event.kontakt (id, firma_id)
);

-- Veranstaltung, Fest, Festival -----------------------------------------------

create table if not exists event.verpflegung (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null,
    firma_id uuid not null,
    titel text not null,
    menge int check (menge >= 0),
    anbieter text,
    betrag_cent bigint check (betrag_cent >= 0),
    angelegt timestamptz not null default now(),
    foreign key (event_id, firma_id) references event.event (id, firma_id) on delete cascade
);

create table if not exists event.programm_punkt (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null,
    firma_id uuid not null,
    titel text not null,
    beginn timestamptz,
    ende timestamptz,
    beschreibung text,
    angelegt timestamptz not null default now(),
    foreign key (event_id, firma_id) references event.event (id, firma_id) on delete cascade,
    check (beginn is null or ende is null or ende >= beginn)
);

create table if not exists event.helferschicht (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null,
    firma_id uuid not null,
    titel text not null,
    beginn timestamptz not null,
    ende timestamptz not null,
    kontakt_id uuid,
    bedarf int check (bedarf >= 0),
    angelegt timestamptz not null default now(),
    foreign key (event_id, firma_id) references event.event (id, firma_id) on delete cascade,
    foreign key (kontakt_id, firma_id) references event.kontakt (id, firma_id),
    check (ende >= beginn)
);

create table if not exists event.buehne (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null,
    firma_id uuid not null,
    name text not null,
    ort text,
    angelegt timestamptz not null default now(),
    foreign key (event_id, firma_id) references event.event (id, firma_id) on delete cascade
);

-- Nachrichten (kein Registry-Eintrag; eigenes Modul) ----------------------------
-- empfaenger_filter z. B. {"zusage": ["zugesagt"]}. Kein Loeschen fuer event_app:
-- verworfene Entwuerfe bleiben mit status 'verworfen' nachvollziehbar.

create table if not exists event.nachricht (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null,
    firma_id uuid not null,
    kanal text not null check (kanal in ('gruppe', 'mail')),
    betreff text,
    text text not null,
    empfaenger_filter jsonb not null default '{}',
    status text not null default 'entwurf' check (
        status in ('entwurf', 'freigegeben', 'versendet', 'fehler', 'verworfen')
    ),
    angelegt timestamptz not null default now(),
    erstellt_von text,
    freigegeben_von uuid references event.person (id),
    freigegeben timestamptz,
    versendet timestamptz,
    fehler text,
    foreign key (event_id, firma_id) references event.event (id, firma_id) on delete cascade
);

-- RLS -------------------------------------------------------------------------

alter table event.wettbewerb_team enable row level security;
alter table event.abgabe enable row level security;
alter table event.jury enable row level security;
alter table event.aussteller enable row level security;
alter table event.stand enable row level security;
alter table event.sprecher enable row level security;
alter table event.online_raum enable row level security;
alter table event.aufzeichnung enable row level security;
alter table event.protokoll_punkt enable row level security;
alter table event.beschluss enable row level security;
alter table event.verpflegung enable row level security;
alter table event.programm_punkt enable row level security;
alter table event.helferschicht enable row level security;
alter table event.buehne enable row level security;
alter table event.nachricht enable row level security;

-- Firma und (falls gesetzt) gewaehltes Event, wie die Kern-Bausteine.
do $$
declare
    t text;
begin
    foreach t in array array[
        'wettbewerb_team', 'abgabe', 'jury', 'aussteller', 'stand', 'sprecher',
        'online_raum', 'aufzeichnung', 'protokoll_punkt', 'beschluss', 'verpflegung',
        'programm_punkt', 'helferschicht', 'buehne', 'nachricht'
    ] loop
        execute format('drop policy if exists %I on event.%I', t || '_mandant', t);
        execute format(
            'create policy %I on event.%I for all'
            ' using (event.mandant_ok(firma_id) and event.event_ok(event_id))'
            ' with check (event.mandant_ok(firma_id) and event.event_ok(event_id))',
            t || '_mandant', t);
    end loop;
end
$$;

-- Rechte ----------------------------------------------------------------------

revoke all on
    event.wettbewerb_team, event.abgabe, event.jury, event.aussteller, event.stand,
    event.sprecher, event.online_raum, event.aufzeichnung, event.protokoll_punkt,
    event.beschluss, event.verpflegung, event.programm_punkt, event.helferschicht,
    event.buehne, event.nachricht
    from public;

grant select, insert, update, delete on
    event.wettbewerb_team, event.abgabe, event.jury, event.aussteller, event.stand,
    event.sprecher, event.online_raum, event.aufzeichnung, event.protokoll_punkt,
    event.beschluss, event.verpflegung, event.programm_punkt, event.helferschicht,
    event.buehne
    to event_app;

-- nachricht: kein delete.
grant select, insert, update on event.nachricht to event_app;
