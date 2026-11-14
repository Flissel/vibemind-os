-- 009_schluessel_name.sql: Name des Aufrufers am Schluessel (Etappe 4, Task 4).
-- Mit Name wird der actor eines Bot-Aufrufs `bot:<art>:<name>` (z. B. die Betreiber-
-- Schluessel des Brain mit Name `VibeMind`), ohne Name bleibt er `bot:<art>`.
--
-- Rechte unveraendert wie in 004: event_app hat auf event.schluessel KEINE Grants
-- (auch nicht auf die neue Spalte). Den Namen setzt nur das Ausstellen mit der
-- Admin-DSN; event_app liest ihn ausschliesslich ueber event.schluessel_pruefen.
-- Wiederholbar: add column if not exists, Funktion wird neu angelegt.

alter table event.schluessel
    add column if not exists name text check (name ~ '^[A-Za-z0-9_]{1,40}$');

-- Rueckgabetyp aendert sich (name kommt dazu): create or replace reicht dafuer nicht.
drop function if exists event.schluessel_pruefen(text);

-- Liefert den Schluessel nur, wenn er gueltig ist (sonst keine Zeile).
create function event.schluessel_pruefen(p_hash text)
    returns table (id uuid, hash text, art text, event_id uuid, firma_id uuid, person_id uuid,
                   name text)
    language sql stable security definer
    set search_path = event, pg_temp
as $$
    select s.id, s.hash, s.art, s.event_id, s.firma_id, s.person_id, s.name
    from event.schluessel s
    where s.hash = p_hash and event.schluessel_aktiv(s.id)
$$;

revoke all on function event.schluessel_pruefen(text) from public;
grant execute on function event.schluessel_pruefen(text) to event_app;
