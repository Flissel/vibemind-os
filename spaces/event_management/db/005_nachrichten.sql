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

-- Zahl der Empfaenger beim Versand (vorher zaehlt die Fachschicht live).
alter table event.nachricht
    add column if not exists empfaenger_anzahl integer check (empfaenger_anzahl >= 0);

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
