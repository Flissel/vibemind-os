-- 008_pruefcode.sql: Rueckkanal-Pruefcode beim Verbinden (Ruling F-a, system/verbinden.py).
-- Wird nur als Admin angewendet (db/anwenden.py). Wiederholbar (if not exists /
-- drop ... if exists / create or replace).
--
-- Problem (Abschluss-Review F1): die chat_id beim Melden ist nur ein Argument des
-- Empfangs-LLM, getChat beweist nur, dass der geteilte Bot im Chat ist. Eine Leitung von
-- Firma B konnte so eine fremde Gruppe A melden und selbst bestaetigen.
-- Loesung: nach dem Melden schickt der Dienst einen zweiten Code (Pruefcode, 6 Zeichen)
-- per sendMessage in genau diesen Chat; bestaetigen verlangt ihn. Wer bestaetigt, beweist
-- damit Lesezugriff auf den Chat.
--
-- Der Pruefcode hat nur 30 Bit: sein sha256 waere offline sofort umkehrbar. Er liegt
-- deshalb in einer eigenen Tabelle ohne Grant an event_app (wie schluessel/marke) und wird
-- nur von SD-Funktionen gesetzt und verglichen:
--   verbindung_melden(code, chat, titel, pruefcode)   offen -> gemeldet samt Pruefcode-Hash
--   verbindung_pruefcode_zugestellt(code)            nach erfolgreichem sendMessage
--   verbindung_pruefcode_pruefen(id, pruefcode)      'ok' | 'falsch'; zaehlt Fehlversuche,
--                                                    der fuenfte verwirft die Verbindung
--   verbindung_geprueft(id)                          fuer die RLS-Policy unten
-- Das zweite Tor: verbindung_aendern laesst `bestaetigt` nur noch mit gepruefter
-- Verbindung zu (auch eine Leitung mit App-DSN kann den Pruefcode nicht umgehen).

create table if not exists event.verbindung_pruefung (
    verbindung_id uuid primary key references event.verbindung (id) on delete cascade,
    pruef_hash text not null check (pruef_hash ~ '^[0-9a-f]{64}$'),
    fehlversuche integer not null default 0 check (fehlversuche >= 0),
    geprueft timestamptz
);

-- RLS an, keine Policy, kein Grant: fuer event_app unsichtbar (auch fuer Betreiber).
alter table event.verbindung_pruefung enable row level security;
revoke all on event.verbindung_pruefung from public;

-- Sichtbar fuer die Oberflaeche: wurde der Pruefcode zugestellt? Setzt allein
-- verbindung_pruefcode_zugestellt (keine Spaltenrechte fuer event_app, 006).
alter table event.verbindung add column if not exists pruefcode_gesendet timestamptz;

-- Hash eines Pruefcode-Klartexts, oder null bei falschem Format (intern, kein Grant).
-- Normalisiert wie die Fachschicht: Rand weg, Grossbuchstaben, 6 Zeichen ohne 0/O/1/I.
create or replace function event.verbindung_pruefcode_hash(p_pruefcode text)
    returns text
    language sql immutable
    set search_path = pg_catalog, pg_temp
as $$
    select case when upper(btrim(p_pruefcode)) ~ '^[A-HJ-NP-Z2-9]{6}$'
                then encode(sha256(convert_to(upper(btrim(p_pruefcode)), 'UTF8')), 'hex') end
$$;

-- Melden setzt jetzt Chat, Titel UND Pruefcode-Hash in einer Anweisungsfolge (genau einmal,
-- offen -> gemeldet). Die alte Fassung ohne Pruefcode verschwindet.
drop function if exists event.verbindung_melden(text, bigint, text);

create or replace function event.verbindung_melden(
    p_code text, p_chat_id bigint, p_titel text, p_pruefcode text
) returns boolean
    language plpgsql volatile security definer
    set search_path = event, pg_temp
as $$
declare
    v_id uuid;
    v_pruef text := event.verbindung_pruefcode_hash(p_pruefcode);
begin
    if p_titel is not null and (char_length(p_titel) > 128 or p_titel ~ '[[:cntrl:]]') then
        raise exception 'Titel ungueltig' using errcode = 'invalid_parameter_value';
    end if;
    if v_pruef is null then
        raise exception 'Pruefcode ungueltig' using errcode = 'invalid_parameter_value';
    end if;
    update event.verbindung v
    set chat_id = p_chat_id, chat_titel = p_titel, status = 'gemeldet', gemeldet = now()
    where v.code_hash = event.verbindung_code_hash(p_code)
      and v.status = 'offen' and v.ablauf > now()
      and ((v.art = 'gruppe' and p_chat_id < 0) or (v.art = 'direkt' and p_chat_id > 0))
    returning v.id into v_id;
    if v_id is null then
        return false;
    end if;
    insert into event.verbindung_pruefung (verbindung_id, pruef_hash) values (v_id, v_pruef);
    return true;
end
$$;

-- Nach erfolgreichem sendMessage: Zustellung vermerken (nur einmal, nur gemeldet).
create or replace function event.verbindung_pruefcode_zugestellt(p_code text)
    returns boolean
    language plpgsql volatile security definer
    set search_path = event, pg_temp
as $$
begin
    update event.verbindung v set pruefcode_gesendet = now()
    where v.code_hash = event.verbindung_code_hash(p_code)
      and v.status = 'gemeldet' and v.pruefcode_gesendet is null;
    return found;
end
$$;

-- Pruefcode vergleichen. Berechtigt ist, wer die Verbindung bestaetigen duerfte (wie
-- verbindung_aendern: Gruppe -> Leitung der Firma oder Betreiber, im gebundenen Kontext nur
-- das eigene Event; Direktchat -> die Person selbst). Unbekannte id, fehlendes Recht, nicht
-- gemeldet oder Meldung aelter als 24 h: 'falsch' ohne Zaehlen (keine Auskunft, ob es die
-- Verbindung gibt). Sonst zaehlt jeder falsche Code; der fuenfte verwirft die Verbindung.
-- Richtig: 'ok' und `geprueft` gesetzt (in der Transaktion des Bestaetigens).
create or replace function event.verbindung_pruefcode_pruefen(p_id uuid, p_pruefcode text)
    returns text
    language plpgsql volatile security definer
    set search_path = event, pg_temp
as $$
declare
    v record;
    v_darf boolean;
    v_hash text;
    v_fehler integer;
begin
    select * into v from event.verbindung where id = p_id for update;
    if not found or v.status <> 'gemeldet' or v.gemeldet <= now() - interval '24 hours' then
        return 'falsch';
    end if;
    -- Eigene Anweisung: ein CASE ... THEN in der IF-Bedingung beendete sie zu frueh.
    v_darf := coalesce(case v.art
        when 'gruppe' then event.event_ok(v.event_id) and (event.ist_betreiber() or exists (
            select 1 from event.mitgliedschaft m
            where m.person_id = event.person_id() and m.firma_id = v.firma_id
              and m.rolle = 'leitung'))
        when 'direkt' then v.person_id = event.person_id()
        else false
    end, false);
    if not v_darf then
        return 'falsch';
    end if;
    select pruef_hash into v_hash from event.verbindung_pruefung
    where verbindung_id = p_id for update;
    if not found then
        return 'falsch';
    end if;
    if v_hash = event.verbindung_pruefcode_hash(p_pruefcode) then
        update event.verbindung_pruefung set geprueft = now() where verbindung_id = p_id;
        return 'ok';
    end if;
    update event.verbindung_pruefung set fehlversuche = fehlversuche + 1
    where verbindung_id = p_id returning fehlversuche into v_fehler;
    if v_fehler >= 5 then
        update event.verbindung set status = 'verworfen' where id = p_id;
    end if;
    return 'falsch';
end
$$;

-- Fuer die Policy: hat diese Verbindung den richtigen Pruefcode gesehen? Liefert nur ja/nein.
create or replace function event.verbindung_geprueft(p_id uuid)
    returns boolean
    language sql stable security definer
    set search_path = event, pg_temp
as $$
    select exists (select 1 from event.verbindung_pruefung
                   where verbindung_id = p_id and geprueft is not null)
$$;

-- verbindung_aendern aus 006, ergaenzt: `bestaetigt` nur mit gepruefter Verbindung.
drop policy if exists verbindung_aendern on event.verbindung;
create policy verbindung_aendern on event.verbindung as restrictive for update
    using (status <> 'verworfen')
    with check (
        (status = 'verworfen'
         or (status = 'bestaetigt' and chat_id is not null and gemeldet is not null
             and bestaetigt is not null and event.verbindung_geprueft(id)))
        and case art
            when 'gruppe' then event.ist_betreiber() or exists (
                select 1 from event.mitgliedschaft m
                where m.person_id = event.person_id() and m.firma_id = verbindung.firma_id
                  and m.rolle = 'leitung')
            when 'direkt' then person_id = event.person_id()
            else false
        end
    );

revoke all on function
    event.verbindung_pruefcode_hash(text),
    event.verbindung_melden(text, bigint, text, text),
    event.verbindung_pruefcode_zugestellt(text),
    event.verbindung_pruefcode_pruefen(uuid, text),
    event.verbindung_geprueft(uuid)
    from public;
grant execute on function
    event.verbindung_melden(text, bigint, text, text),
    event.verbindung_pruefcode_zugestellt(text),
    event.verbindung_pruefcode_pruefen(uuid, text),
    event.verbindung_geprueft(uuid)
    to event_app;
