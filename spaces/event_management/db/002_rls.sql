-- 002_rls.sql: Row Level Security als zweites Mandanten-Tor, Rechte fuer event_app.
-- Wird nur als Admin angewendet (db/anwenden.py). Tabellen und Funktionen gehoeren
-- dem Admin; event_app ist nicht Eigentuemer, daher greift RLS ohne "force".
--
-- Kontext (Task 4 setzt ihn per set_config(..., true) je Transaktion):
--   event.firmen    kommagetrennte Firmen-UUIDs
--   event.betreiber 'an' fuer Betreiber
--   event.person    UUID der angemeldeten Person
--   event.event     UUID des gewaehlten Events
-- Fehlt eine Einstellung oder ist sie '', gilt sie als nicht gesetzt (fail-closed).

-- Kontext-Funktionen ------------------------------------------------------------

create or replace function event.firmen() returns uuid[]
    language sql stable security invoker
as $$
    select coalesce(
        string_to_array(nullif(current_setting('event.firmen', true), ''), ',')::uuid[],
        '{}'::uuid[]
    )
$$;

create or replace function event.ist_betreiber() returns boolean
    language sql stable security invoker
as $$
    select coalesce(current_setting('event.betreiber', true) = 'an', false)
$$;

create or replace function event.person_id() returns uuid
    language sql stable security invoker
as $$
    select nullif(current_setting('event.person', true), '')::uuid
$$;

create or replace function event.aktuelles_event() returns uuid
    language sql stable security invoker
as $$
    select nullif(current_setting('event.event', true), '')::uuid
$$;

create or replace function event.mandant_ok(f uuid) returns boolean
    language sql stable security invoker
as $$
    select event.ist_betreiber() or coalesce(f = any(event.firmen()), false)
$$;

create or replace function event.event_ok(e uuid) returns boolean
    language sql stable security invoker
as $$
    select event.aktuelles_event() is null or coalesce(e = event.aktuelles_event(), false)
$$;

-- Einzige Ausnahme fuer den Weg ohne Login: Einladung per Token-Hash lesen.
create or replace function event.einladung_holen(p_hash text)
    returns table (
        id uuid, person_id uuid, firma_id uuid, firma_name text,
        rolle text, gueltig_bis timestamptz, eingeloest timestamptz
    )
    language sql stable security definer
    set search_path = event, pg_temp
as $$
    select e.id, e.person_id, e.firma_id, f.name, e.rolle, e.gueltig_bis, e.eingeloest
    from event.einladung e
    join event.firma f on f.id = e.firma_id
    where e.token_hash = p_hash
$$;

-- RLS einschalten (alle Tabellen ausser migration und person) ------------------

alter table event.firma enable row level security;
alter table event.mitgliedschaft enable row level security;
alter table event.event enable row level security;
alter table event.kontakt enable row level security;
alter table event.einladung enable row level security;
alter table event.orga_team enable row level security;
alter table event.teilnehmende enable row level security;
alter table event.aufgabe_kategorie enable row level security;
alter table event.aufgabe enable row level security;
alter table event.ablauf enable row level security;
alter table event.budget_posten enable row level security;
alter table event.verlauf enable row level security;

-- Policies --------------------------------------------------------------------
-- Jede schreibende Policy hat ein "with check": eine Zeile laesst sich weder in
-- eine fremde Firma anlegen noch per update dorthin verschieben.

-- firma: lesen/aendern/loeschen nur eigene, anlegen nur Betreiber.
create policy firma_lesen on event.firma for select
    using (event.mandant_ok(id));
create policy firma_anlegen on event.firma for insert
    with check (event.ist_betreiber());
create policy firma_aendern on event.firma for update
    using (event.mandant_ok(id)) with check (event.mandant_ok(id));
create policy firma_loeschen on event.firma for delete
    using (event.mandant_ok(id));

-- mitgliedschaft: die eigene Person sieht ihre Mitgliedschaften, schreiben nur mandant_ok.
create policy mitgliedschaft_lesen on event.mitgliedschaft for select
    using (event.mandant_ok(firma_id) or person_id = event.person_id());
create policy mitgliedschaft_anlegen on event.mitgliedschaft for insert
    with check (event.mandant_ok(firma_id));
create policy mitgliedschaft_aendern on event.mitgliedschaft for update
    using (event.mandant_ok(firma_id)) with check (event.mandant_ok(firma_id));
create policy mitgliedschaft_loeschen on event.mitgliedschaft for delete
    using (event.mandant_ok(firma_id));

-- event: Firma und (falls gesetzt) gewaehltes Event.
create policy event_mandant on event.event for all
    using (event.mandant_ok(firma_id) and event.event_ok(id))
    with check (event.mandant_ok(firma_id) and event.event_ok(id));

-- kontakt und einladung: nur die Firma.
create policy kontakt_mandant on event.kontakt for all
    using (event.mandant_ok(firma_id))
    with check (event.mandant_ok(firma_id));
create policy einladung_mandant on event.einladung for all
    using (event.mandant_ok(firma_id))
    with check (event.mandant_ok(firma_id));

-- Bausteine und verlauf: Firma und (falls gesetzt) gewaehltes Event.
create policy orga_team_mandant on event.orga_team for all
    using (event.mandant_ok(firma_id) and event.event_ok(event_id))
    with check (event.mandant_ok(firma_id) and event.event_ok(event_id));
create policy teilnehmende_mandant on event.teilnehmende for all
    using (event.mandant_ok(firma_id) and event.event_ok(event_id))
    with check (event.mandant_ok(firma_id) and event.event_ok(event_id));
create policy aufgabe_kategorie_mandant on event.aufgabe_kategorie for all
    using (event.mandant_ok(firma_id) and event.event_ok(event_id))
    with check (event.mandant_ok(firma_id) and event.event_ok(event_id));
create policy aufgabe_mandant on event.aufgabe for all
    using (event.mandant_ok(firma_id) and event.event_ok(event_id))
    with check (event.mandant_ok(firma_id) and event.event_ok(event_id));
create policy ablauf_mandant on event.ablauf for all
    using (event.mandant_ok(firma_id) and event.event_ok(event_id))
    with check (event.mandant_ok(firma_id) and event.event_ok(event_id));
create policy budget_posten_mandant on event.budget_posten for all
    using (event.mandant_ok(firma_id) and event.event_ok(event_id))
    with check (event.mandant_ok(firma_id) and event.event_ok(event_id));
-- verlauf: update/delete scheitern schon am fehlenden Recht (append-only).
create policy verlauf_mandant on event.verlauf for all
    using (event.mandant_ok(firma_id) and event.event_ok(event_id))
    with check (event.mandant_ok(firma_id) and event.event_ok(event_id));

-- Rechte ----------------------------------------------------------------------

revoke all on schema event from public;
grant usage on schema event to event_app;

revoke all on all tables in schema event from public;
revoke all on all sequences in schema event from public;

grant select, insert, update, delete on
    event.firma, event.mitgliedschaft, event.event, event.kontakt, event.einladung,
    event.orga_team, event.teilnehmende, event.aufgabe_kategorie, event.aufgabe,
    event.ablauf, event.budget_posten
    to event_app;

-- verlauf ist append-only.
grant select, insert on event.verlauf to event_app;

-- person: ohne RLS; das Betreiber-Flag ist nicht aenderbar, loeschen nicht erlaubt.
grant select, insert on event.person to event_app;
grant update (name, passwort_hash, sitzungs_version, aktiv) on event.person to event_app;

grant usage on all sequences in schema event to event_app;

-- Funktionen: kein Ausfuehren fuer PUBLIC, nur fuer event_app.
revoke all on function
    event.firmen(), event.ist_betreiber(), event.person_id(), event.aktuelles_event(),
    event.mandant_ok(uuid), event.event_ok(uuid), event.einladung_holen(text)
    from public;
grant execute on function
    event.firmen(), event.ist_betreiber(), event.person_id(), event.aktuelles_event(),
    event.mandant_ok(uuid), event.event_ok(uuid), event.einladung_holen(text)
    to event_app;
