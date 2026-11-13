-- 006_verbinden.sql: Telegram-Gruppe und Direktchat per Einmal-Code (system/verbinden.py).
-- Wird nur als Admin angewendet (db/anwenden.py). Wiederholbar (if not exists /
-- drop policy if exists / create or replace).
--
-- Vertrauenswurzel (Task-6-Carry): `nachrichten.an_gruppe` sendet an die chat_id einer
-- bestaetigten Gruppen-Verbindung, und der Bereitstellungs-Arbeiter bindet einen Agenten an
-- die chat_id eines Auftrags. Beide chat_ids muessen deshalb aus dem Melden mit gueltigem
-- Code stammen (SD-Funktion unten) und von einer Leitung bzw. der Person selbst bestaetigt
-- sein. Fuer event_app heisst das:
--   verbindung      anlegen nur offen, ohne Chat, als man selbst und nur als Leitung (gruppe:
--                   der Firma des Events; direkt: irgendeiner Firma, oder Betreiber), Code
--                   hoechstens 15 Minuten gueltig; chat_id/chat_titel/gemeldet setzt allein
--                   `verbindung_melden`; aendern nur status/bestaetigt, nur zu verworfen
--                   oder (mit gemeldetem Chat) bestaetigt, nur durch eine Leitung der Firma
--                   bzw. einen Betreiber (gruppe) oder die Person selbst (direkt).
--   bereitstellung  ein Auftrag nennt nur eine chat_id, die zu einer bestaetigten (anlegen)
--                   bzw. einmal bestaetigten (abbauen) Verbindung desselben Ziels gehoert.
-- verbindung.person_id ist bei art='gruppe' nur "wer hat den Code erzeugt" und zaehlt fuer
-- keine Berechtigung. Dass der Bot nie bestaetigt (quelle 'bot'), prueft die Fachschicht;
-- RLS kennt die Quelle nicht.

-- Eindeutigkeit ----------------------------------------------------------------------
-- Ein Telegram-Chat ist hoechstens einmal bestaetigt verbunden (eine Gruppe nicht mit zwei
-- Events), ein Event hat hoechstens eine bestaetigte Gruppe, eine Person hoechstens einen
-- bestaetigten Direktchat. "Neue Gruppe ersetzt alte" verwirft die alte vor dem Bestaetigen
-- der neuen (zwei Anweisungen in einer Transaktion).

create unique index if not exists verbindung_chat_bestaetigt
    on event.verbindung (chat_id) where status = 'bestaetigt';
create unique index if not exists verbindung_gruppe_bestaetigt
    on event.verbindung (event_id) where art = 'gruppe' and status = 'bestaetigt';
create unique index if not exists verbindung_direkt_bestaetigt
    on event.verbindung (person_id) where art = 'direkt' and status = 'bestaetigt';

-- Spaltenrechte -----------------------------------------------------------------------
-- 004 gab select/insert/update auf die ganze Tabelle. Jetzt: anlegen nur die Spalten eines
-- neuen Codes (status/chat_* kommen aus den Vorgaben), aendern nur status und bestaetigt.

revoke insert, update on event.verbindung from event_app;
grant insert (art, code_hash, event_id, firma_id, person_id, ablauf)
    on event.verbindung to event_app;
grant update (status, bestaetigt) on event.verbindung to event_app;

-- RESTRICTIVE-Policies (UND-verknuepft mit verbindung_zugriff aus 004) ------------------

drop policy if exists verbindung_anlegen on event.verbindung;
create policy verbindung_anlegen on event.verbindung as restrictive for insert
    with check (
        status = 'offen' and chat_id is null and chat_titel is null
        and gemeldet is null and bestaetigt is null
        and ablauf <= now() + interval '15 minutes'
        and person_id = event.person_id()
        and case art
            when 'gruppe' then event.ist_betreiber() or exists (
                select 1 from event.mitgliedschaft m
                where m.person_id = event.person_id() and m.firma_id = verbindung.firma_id
                  and m.rolle = 'leitung')
            when 'direkt' then event.ist_betreiber() or exists (
                select 1 from event.mitgliedschaft m
                where m.person_id = event.person_id() and m.rolle = 'leitung')
            else false
        end
    );

drop policy if exists verbindung_aendern on event.verbindung;
create policy verbindung_aendern on event.verbindung as restrictive for update
    using (status <> 'verworfen')
    with check (
        (status = 'verworfen'
         or (status = 'bestaetigt' and chat_id is not null and gemeldet is not null
             and bestaetigt is not null))
        and case art
            when 'gruppe' then event.ist_betreiber() or exists (
                select 1 from event.mitgliedschaft m
                where m.person_id = event.person_id() and m.firma_id = verbindung.firma_id
                  and m.rolle = 'leitung')
            when 'direkt' then person_id = event.person_id()
            else false
        end
    );

-- Auftraege nur fuer bestaetigte Chats (UND-verknuepft mit bereitstellung_anlegen aus 004).
-- Die Unterabfrage laeuft unter der RLS von event.verbindung, sieht also nur Verbindungen,
-- die der Kontext ohnehin sehen darf.
drop policy if exists bereitstellung_chat on event.bereitstellung;
create policy bereitstellung_chat on event.bereitstellung as restrictive for insert
    with check (exists (
        select 1 from event.verbindung v
        where v.chat_id = bereitstellung.chat_id
          and v.bestaetigt is not null
          and (v.status = 'bestaetigt'
               or (bereitstellung.art = 'abbauen' and v.status = 'verworfen'))
          and case when bereitstellung.ziel = 'gruppe'
                   then v.art = 'gruppe' and v.event_id = bereitstellung.event_id
                   else v.art = 'direkt' and v.person_id = bereitstellung.person_id
              end
    ));

-- SD-Funktionen fuer das Melden (Ruling S3) ---------------------------------------------
-- event-mcp hat nur die App-DSN und keinen Mandanten-Kontext. Beide Funktionen nehmen den
-- HASH des Codes: wer melden will, muss den Code besitzen (die id allein reicht nie).

-- Offener, nicht abgelaufener Code: id und art; sonst keine Zeile.
create or replace function event.verbindung_finden(p_code_hash text)
    returns table (id uuid, art text)
    language sql stable security definer
    set search_path = event, pg_temp
as $$
    select v.id, v.art
    from event.verbindung v
    where v.code_hash = p_code_hash and v.status = 'offen' and v.ablauf > now()
$$;

-- Setzt Chat und Titel genau einmal (offen -> gemeldet); Vorzeichen passend zur Art
-- (Gruppen negativ, Direktchats positiv). false, wenn nichts passt.
create or replace function event.verbindung_melden(
    p_code_hash text, p_chat_id bigint, p_titel text
) returns boolean
    language plpgsql volatile security definer
    set search_path = event, pg_temp
as $$
begin
    if p_titel is not null and (char_length(p_titel) > 128 or p_titel ~ '[[:cntrl:]]') then
        raise exception 'Titel ungueltig' using errcode = 'invalid_parameter_value';
    end if;
    update event.verbindung v
    set chat_id = p_chat_id, chat_titel = p_titel, status = 'gemeldet', gemeldet = now()
    where v.code_hash = p_code_hash and v.status = 'offen' and v.ablauf > now()
      and ((v.art = 'gruppe' and p_chat_id < 0) or (v.art = 'direkt' and p_chat_id > 0));
    return found;
end
$$;

-- Agent-Name schon fuer ein anderes Ziel vergeben (Ruling S5: gleiche 8 Hex bei einem
-- anderen Event bzw. einer anderen Person mit bestaetigter Verbindung)? Sieht ueber alle
-- Mandanten, liefert aber nur ja/nein.
create or replace function event.agent_belegt(p_agent text, p_ziel uuid)
    returns boolean
    language plpgsql stable security definer
    set search_path = event, pg_temp
as $$
begin
    if p_agent is null or p_agent !~ '^(ev|ld|bo)-[0-9a-f]{8}$' or p_ziel is null then
        raise exception 'Agent ungueltig' using errcode = 'invalid_parameter_value';
    end if;
    return exists (
        select 1 from event.verbindung v
        where v.status = 'bestaetigt'
          and v.art = case left(p_agent, 2) when 'ev' then 'gruppe' else 'direkt' end
          and coalesce(v.event_id, v.person_id) <> p_ziel
          and left(replace(coalesce(v.event_id, v.person_id)::text, '-', ''), 8)
              = right(p_agent, 8)
    );
end
$$;

revoke all on function
    event.verbindung_finden(text), event.verbindung_melden(text, bigint, text),
    event.agent_belegt(text, uuid)
    from public;
grant execute on function
    event.verbindung_finden(text), event.verbindung_melden(text, bigint, text),
    event.agent_belegt(text, uuid)
    to event_app;
