-- 005_nachrichten.sql: Versand der Nachrichten (system/nachrichten.py).
-- Wird nur als Admin angewendet (db/anwenden.py). Wiederholbar (if not exists /
-- drop policy if exists).
--
-- Zweites Tor neben der Fachschicht: zwei RESTRICTIVE-Policies auf event.nachricht, die
-- mit nachricht_mandant aus 003 UND-verknuepft gelten. Fuer event_app heisst das:
--   anlegen   Mail nur als Entwurf (nie freigegeben/versendet); Gruppe als Entwurf oder
--             mit dem Ergebnis des Sendens (versendet/fehler)
--   aendern   nur Entwuerfe; daraus wird ein Entwurf, verworfen oder - nur durch eine
--             Leitung der Firma oder einen Betreiber, als sie selbst - freigegeben
-- Freigegebene, versendete und verworfene Nachrichten aendert allein der Versand mit der
-- Admin-DSN (Eigentuemer, RLS greift ohne "force" nicht).

drop policy if exists nachricht_anlegen on event.nachricht;
create policy nachricht_anlegen on event.nachricht as restrictive for insert
    with check (
        freigegeben is null and freigegeben_von is null
        and (status = 'entwurf'
             or (kanal = 'gruppe' and status in ('versendet', 'fehler')))
    );

drop policy if exists nachricht_aendern on event.nachricht;
create policy nachricht_aendern on event.nachricht as restrictive for update
    using (status = 'entwurf')
    with check (
        (status in ('entwurf', 'verworfen') and freigegeben is null and freigegeben_von is null)
        or (status = 'freigegeben' and kanal = 'mail' and freigegeben is not null
            and freigegeben_von = event.person_id()
            and (event.ist_betreiber() or exists (
                select 1 from event.mitgliedschaft m
                where m.person_id = event.person_id() and m.firma_id = nachricht.firma_id
                  and m.rolle = 'leitung')))
    );

-- Empfaenger-Schnappschuss (Ruling T6-a) ----------------------------------------------
-- Die Freigabe friert die Empfaenger ein; versendet wird genau an diese Zeilen, je Zeile
-- hoechstens einmal: offen -> unterwegs (Commit vor dem Senden) -> versendet | fehler.
-- Nach einem Abbruch zeigt `unterwegs` "unklar", `offen` "nicht gesendet".

create table if not exists event.nachricht_empfaenger (
    id uuid primary key default gen_random_uuid(),
    nachricht_id uuid not null references event.nachricht (id) on delete cascade,
    mail text not null check (btrim(mail) <> '' and mail = btrim(mail)),
    status text not null default 'offen' check (
        status in ('offen', 'unterwegs', 'versendet', 'fehler')
    ),
    versucht timestamptz,
    fehler text
);
create unique index if not exists nachricht_empfaenger_mail
    on event.nachricht_empfaenger (nachricht_id, lower(mail));

alter table event.nachricht_empfaenger enable row level security;

-- Firma und Event-Bindung kommen ueber die Nachricht: die Unterabfrage laeuft selbst unter
-- der RLS von event.nachricht (nachricht_mandant), sieht also nur Nachrichten der eigenen
-- Firma bzw. des gewaehlten Events.
drop policy if exists nachricht_empfaenger_lesen on event.nachricht_empfaenger;
create policy nachricht_empfaenger_lesen on event.nachricht_empfaenger for select
    using (exists (
        select 1 from event.nachricht n where n.id = nachricht_empfaenger.nachricht_id));

-- Anlegen nur in der Transaktion der Freigabe: die Nachricht ist freigegeben, von der
-- Person dieses Kontexts, mit dem Zeitstempel dieser Transaktion (now()). Spaeter (auch
-- durch dieselbe Leitung) kommt kein Empfaenger mehr dazu.
drop policy if exists nachricht_empfaenger_anlegen on event.nachricht_empfaenger;
create policy nachricht_empfaenger_anlegen on event.nachricht_empfaenger for insert
    with check (
        status = 'offen' and versucht is null and fehler is null
        and exists (
            select 1 from event.nachricht n
            where n.id = nachricht_empfaenger.nachricht_id
              and n.kanal = 'mail' and n.status = 'freigegeben'
              and n.freigegeben = now() and n.freigegeben_von = event.person_id())
    );

revoke all on event.nachricht_empfaenger from public;
-- Kein update/delete fuer event_app: den Versandstand schreibt nur die Admin-DSN.
grant select, insert on event.nachricht_empfaenger to event_app;
