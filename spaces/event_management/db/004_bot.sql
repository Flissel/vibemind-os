-- 004_bot.sql: Bot-Tabellen (schluessel, marke, bestaetigung, verbindung, bereitstellung),
-- ihre security-definer-Funktionen und die eingeengten Personen-Spalten.
-- Wird nur als Admin angewendet (db/anwenden.py). Tabellen und Funktionen gehoeren dem
-- Admin; event_app ist nicht Eigentuemer, daher greift RLS ohne "force".
--
-- Zugriff fuer event_app (Ruling S4, verschaerft im Review):
--   schluessel, marke, bestaetigung  KEINE Grants; nur ueber die SD-Funktionen unten,
--                                    gebunden an den Schluessel-HASH (Besitznachweis).
--   verbindung                       select/insert/update mit RLS (Firma des Events,
--                                    eigene Person oder Betreiber); kein delete.
--   bereitstellung                   nur select/insert mit RLS; Auftraege aendert allein
--                                    der Arbeiter mit der Admin-DSN (Task 8).
-- Hashes sind immer sha256-Hex (64 Zeichen); ein Klartext passt nie in eine Hash-Spalte.

-- Schluessel ------------------------------------------------------------------------
-- art 'event' ist an genau ein Event (und dessen Firma) gebunden, 'leitung' an eine
-- Person; alle anderen Arten haben kein Event. Laufzeit hoechstens 730 Tage.

create table if not exists event.schluessel (
    id uuid primary key default gen_random_uuid(),
    hash text not null unique check (hash ~ '^[0-9a-f]{64}$'),
    art text not null check (art in ('event', 'leitung', 'betreiber', 'betreiber_lesen', 'melden')),
    event_id uuid,
    firma_id uuid,
    person_id uuid references event.person (id),
    bezeichnung text not null,
    angelegt timestamptz not null default now(),
    ablauf timestamptz not null,
    widerrufen timestamptz,
    check (
        (art = 'event' and event_id is not null and firma_id is not null)
        or (art <> 'event' and event_id is null and firma_id is null)
    ),
    check (art <> 'leitung' or person_id is not null),
    check (ablauf <= angelegt + interval '730 days'),
    -- Mit dem Event verschwindet auch sein Schluessel (pruefen lehnt ihn dann ab).
    foreign key (event_id, firma_id) references event.event (id, firma_id) on delete cascade
);

-- Marke: kurzlebige Event-Wahl eines Schluessels (Task 3).
create table if not exists event.marke (
    wert_hash text primary key check (wert_hash ~ '^[0-9a-f]{64}$'),
    schluessel_id uuid not null references event.schluessel (id) on delete cascade,
    event_id uuid not null references event.event (id) on delete cascade,
    ablauf timestamptz not null
);

-- Bestaetigung: Einmal-Code fuer riskante Aktionen, gebunden an Schluessel, Aktion, Ziel.
create table if not exists event.bestaetigung (
    code_hash text primary key check (code_hash ~ '^[0-9a-f]{64}$'),
    schluessel_id uuid not null references event.schluessel (id) on delete cascade,
    aktion text not null check (aktion ~ '^[a-z_]{1,64}$'),
    ziel jsonb not null check (jsonb_typeof(ziel) = 'object'),
    ablauf timestamptz not null,
    benutzt timestamptz
);

-- Verbindung: Einmal-Code fuer Telegram-Gruppe (Event) oder Direktchat (Person) (Task 7).
create table if not exists event.verbindung (
    id uuid primary key default gen_random_uuid(),
    art text not null check (art in ('gruppe', 'direkt')),
    code_hash text not null unique check (code_hash ~ '^[0-9a-f]{64}$'),
    event_id uuid,
    firma_id uuid,
    person_id uuid not null references event.person (id),
    ablauf timestamptz not null,
    chat_id bigint,
    chat_titel text,
    gemeldet timestamptz,
    bestaetigt timestamptz,
    status text not null default 'offen' check (
        status in ('offen', 'gemeldet', 'bestaetigt', 'verworfen')
    ),
    check (
        (art = 'gruppe' and event_id is not null and firma_id is not null)
        or (art = 'direkt' and event_id is null and firma_id is null)
    ),
    foreign key (event_id, firma_id) references event.event (id, firma_id) on delete cascade
);

-- Bereitstellung: Auftrag an den Arbeiter (Task 8, Admin-DSN). event_id ohne FK, damit ein
-- Abbau-Auftrag das Loeschen des Events ueberlebt. Agent-ID nach Ruling S5; sie landet in
-- Pfaden und Config-Schluesseln, daher streng geprueft.
create table if not exists event.bereitstellung (
    id uuid primary key default gen_random_uuid(),
    art text not null check (art in ('anlegen', 'abbauen')),
    ziel text not null check (ziel in ('gruppe', 'leitung', 'betreiber')),
    event_id uuid,
    person_id uuid references event.person (id),
    chat_id bigint not null,
    agent_id text not null check (agent_id ~ '^(ev|ld|bo)-[0-9a-f]{8}$'),
    status text not null default 'offen' check (status in ('offen', 'laeuft', 'erfolg', 'fehler')),
    schritt text,
    fehler text,
    angelegt timestamptz not null default now(),
    gestartet timestamptz,
    beendet timestamptz,
    check (
        (ziel = 'gruppe' and event_id is not null)
        or (ziel <> 'gruppe' and event_id is null and person_id is not null)
    ),
    check (left(agent_id, 2) = case ziel when 'gruppe' then 'ev' when 'leitung' then 'ld' else 'bo' end),
    -- Die 8 Hex gehoeren zum Ziel selbst (Event bei gruppe, sonst Person): ein Auftrag kann
    -- keinen Agenten eines fremden Events oder einer fremden Person anlegen oder abbauen.
    check (agent_id = left(agent_id, 3)
        || left(replace(coalesce(event_id, person_id)::text, '-', ''), 8))
);

-- RLS -------------------------------------------------------------------------------
-- schluessel, marke, bestaetigung: RLS an, keine Policy -> fuer event_app nichts (selbst
-- bei einem versehentlichen Grant). Die SD-Funktionen laufen als Eigentuemer.

alter table event.schluessel enable row level security;
alter table event.marke enable row level security;
alter table event.bestaetigung enable row level security;
alter table event.verbindung enable row level security;
alter table event.bereitstellung enable row level security;

drop policy if exists verbindung_zugriff on event.verbindung;
create policy verbindung_zugriff on event.verbindung for all
    using (
        event.ist_betreiber()
        or (firma_id is not null and event.mandant_ok(firma_id) and event.event_ok(event_id))
        or (firma_id is null and person_id = event.person_id())
    )
    with check (
        event.ist_betreiber()
        or (firma_id is not null and event.mandant_ok(firma_id) and event.event_ok(event_id))
        or (firma_id is null and person_id = event.person_id())
    );

-- bereitstellung hat keine firma_id: die Firma kommt aus dem Event.
-- Lesen: Firma des Events, eigene Person oder Betreiber.
drop policy if exists bereitstellung_lesen on event.bereitstellung;
create policy bereitstellung_lesen on event.bereitstellung for select
    using (
        event.ist_betreiber()
        or (event_id is not null and exists (
            select 1 from event.event e
            where e.id = bereitstellung.event_id
              and event.mandant_ok(e.firma_id) and event.event_ok(e.id)))
        or (event_id is null and person_id = event.person_id())
    );
-- Anlegen (ein Auftrag richtet einen Bot ein, daher enger als Lesen):
--   betreiber  nur im Betreiber-Kontext
--   gruppe     nur Leitung der Firma des Events (ein Mitglied reicht nicht); person_id ist
--              die bestaetigende Leitung selbst
--   leitung    nur fuer die eigene Person, und nur mit einer aktuellen Leitung
drop policy if exists bereitstellung_anlegen on event.bereitstellung;
create policy bereitstellung_anlegen on event.bereitstellung for insert
    with check (
        event.ist_betreiber()
        or (ziel = 'gruppe' and event_id is not null and person_id = event.person_id()
            and exists (
                select 1 from event.event e
                join event.mitgliedschaft m on m.firma_id = e.firma_id
                where e.id = bereitstellung.event_id
                  and event.mandant_ok(e.firma_id) and event.event_ok(e.id)
                  and m.person_id = event.person_id() and m.rolle = 'leitung'))
        or (ziel = 'leitung' and event_id is null and person_id = event.person_id()
            and exists (
                select 1 from event.mitgliedschaft m
                where m.person_id = event.person_id() and m.rolle = 'leitung'))
    );

-- Interne Helfer (nur fuer die SD-Funktionen, kein Grant an event_app) -------------------

-- Gueltig: nicht widerrufen, nicht abgelaufen, Person (falls vorhanden) aktiv und bei
-- Betreiber-Arten noch Betreiber.
create or replace function event.schluessel_aktiv(p_id uuid) returns boolean
    language sql stable security invoker
    set search_path = event, pg_temp
as $$
    select exists (
        select 1
        from event.schluessel s
        left join event.person p on p.id = s.person_id
        where s.id = p_id
          and s.widerrufen is null
          and s.ablauf > now()
          and (s.person_id is null or p.aktiv)
          and (s.art not in ('betreiber', 'betreiber_lesen') or s.person_id is null or p.betreiber)
    )
$$;

-- Darf der Schluessel dieses Event waehlen? event: nur sein Event; leitung: Events der
-- Firmen, in denen die Person JETZT Leitung ist; Betreiber-Arten: jedes; melden: keins.
create or replace function event.schluessel_darf_event(p_schluessel uuid, p_event uuid)
    returns boolean
    language sql stable security invoker
    set search_path = event, pg_temp
as $$
    select exists (
        select 1
        from event.schluessel s
        join event.event e on e.id = p_event
        where s.id = p_schluessel
          and event.schluessel_aktiv(s.id)
          and case s.art
              when 'event' then s.event_id = e.id
              when 'leitung' then exists (
                  select 1 from event.mitgliedschaft m
                  where m.person_id = s.person_id and m.firma_id = e.firma_id
                    and m.rolle = 'leitung')
              when 'betreiber' then true
              when 'betreiber_lesen' then true
              else false
          end
    )
$$;

-- SD-Funktionen fuer event_app: Schluessel, Marke, Bestaetigung --------------------------
-- Marke und Bestaetigung nehmen den HASH des Schluessels, nicht seine id: wer sie nutzt,
-- muss den Klartext besitzen (die id allein reicht nie).

-- Liefert den Schluessel nur, wenn er gueltig ist (sonst keine Zeile).
create or replace function event.schluessel_pruefen(p_hash text)
    returns table (id uuid, hash text, art text, event_id uuid, firma_id uuid, person_id uuid)
    language sql stable security definer
    set search_path = event, pg_temp
as $$
    select s.id, s.hash, s.art, s.event_id, s.firma_id, s.person_id
    from event.schluessel s
    where s.hash = p_hash and event.schluessel_aktiv(s.id)
$$;

-- Legt eine Marke an; false, wenn der Schluessel unbekannt/ungueltig ist oder das Event
-- nicht waehlen darf. Kaputte Eingaben (kein Hash, Laufzeit ausserhalb 1..60 min) sind
-- eine Ausnahme.
create or replace function event.marke_anlegen(
    p_hash text, p_schluessel_hash text, p_event uuid, p_minuten integer
) returns boolean
    language plpgsql volatile security definer
    set search_path = event, pg_temp
as $$
declare
    v_schluessel uuid;
begin
    if p_hash is null or p_hash !~ '^[0-9a-f]{64}$' then
        raise exception 'Hash ungueltig' using errcode = 'invalid_parameter_value';
    end if;
    if p_minuten is null or p_minuten < 1 or p_minuten > 60 then
        raise exception 'Laufzeit ungueltig' using errcode = 'invalid_parameter_value';
    end if;
    select s.id into v_schluessel from event.schluessel s where s.hash = p_schluessel_hash;
    if v_schluessel is null
            or not coalesce(event.schluessel_darf_event(v_schluessel, p_event), false) then
        return false;
    end if;
    insert into event.marke (wert_hash, schluessel_id, event_id, ablauf)
    values (p_hash, v_schluessel, p_event, now() + make_interval(mins => p_minuten))
    on conflict (wert_hash) do nothing;
    return found;
end
$$;

-- event_id der Marke oder null (unbekannt, fremder Schluessel, abgelaufen, Schluessel
-- ungueltig oder Event fuer ihn nicht mehr waehlbar).
create or replace function event.marke_pruefen(p_hash text, p_schluessel_hash text)
    returns uuid
    language sql stable security definer
    set search_path = event, pg_temp
as $$
    select m.event_id
    from event.marke m
    join event.schluessel s on s.id = m.schluessel_id
    where m.wert_hash = p_hash
      and s.hash = p_schluessel_hash
      and m.ablauf > now()
      and event.schluessel_darf_event(s.id, m.event_id)
$$;

create or replace function event.bestaetigung_anlegen(
    p_hash text, p_schluessel_hash text, p_aktion text, p_ziel jsonb, p_minuten integer
) returns boolean
    language plpgsql volatile security definer
    set search_path = event, pg_temp
as $$
declare
    v_schluessel uuid;
begin
    if p_hash is null or p_hash !~ '^[0-9a-f]{64}$' then
        raise exception 'Hash ungueltig' using errcode = 'invalid_parameter_value';
    end if;
    if p_aktion is null or p_aktion !~ '^[a-z_]{1,64}$' then
        raise exception 'Aktion ungueltig' using errcode = 'invalid_parameter_value';
    end if;
    if p_ziel is null or jsonb_typeof(p_ziel) <> 'object' then
        raise exception 'Ziel ungueltig' using errcode = 'invalid_parameter_value';
    end if;
    if p_minuten is null or p_minuten < 1 or p_minuten > 60 then
        raise exception 'Laufzeit ungueltig' using errcode = 'invalid_parameter_value';
    end if;
    select s.id into v_schluessel from event.schluessel s where s.hash = p_schluessel_hash;
    if v_schluessel is null or not coalesce(event.schluessel_aktiv(v_schluessel), false) then
        return false;
    end if;
    insert into event.bestaetigung (code_hash, schluessel_id, aktion, ziel, ablauf)
    values (p_hash, v_schluessel, p_aktion, p_ziel, now() + make_interval(mins => p_minuten))
    on conflict (code_hash) do nothing;
    return found;
end
$$;

-- Atomar einmalig: nur der erste passende Aufruf setzt benutzt und bekommt true.
create or replace function event.bestaetigung_einloesen(
    p_hash text, p_schluessel_hash text, p_aktion text, p_ziel jsonb
) returns boolean
    language sql volatile security definer
    set search_path = event, pg_temp
as $$
    with eingeloest as (
        update event.bestaetigung b
        set benutzt = now()
        from event.schluessel s
        where s.id = b.schluessel_id
          and s.hash = p_schluessel_hash
          and b.code_hash = p_hash
          and b.aktion = p_aktion
          and b.ziel = p_ziel
          and b.benutzt is null
          and b.ablauf > now()
          and event.schluessel_aktiv(s.id)
        returning 1
    )
    select count(*) = 1 from eingeloest
$$;

-- Personen-Spalten einengen (Ruling S2) ----------------------------------------------
-- passwort_hash ist fuer event_app nicht mehr lesbar (auch nicht in WHERE/Ausdruecken).
-- Schreiben per update bleibt (002), ebenso RLS person_aendern.

revoke select on event.person from event_app;
grant select (id, mail, name, betreiber, aktiv, sitzungs_version, angelegt)
    on event.person to event_app;

-- Login: einziger Weg, an den Hash zu kommen (Vergleich in Python, konstante Zeit).
create or replace function event.anmeldung_holen(p_mail text)
    returns table (id uuid, passwort_hash text, aktiv boolean)
    language sql stable security definer
    set search_path = event, pg_temp
as $$
    select p.id, p.passwort_hash, p.aktiv
    from event.person p
    where lower(p.mail) = lower(p_mail)
$$;

create or replace function event.hat_passwort(p_person uuid)
    returns boolean
    language sql stable security definer
    set search_path = event, pg_temp
as $$
    select exists (
        select 1 from event.person p where p.id = p_person and p.passwort_hash is not null
    )
$$;

-- Setzt Passwort (und optional Namen) nur, wenn noch keines gesetzt ist, die Person aktiv
-- ist und der Kontext die Person selbst oder Betreiber ist (wie RLS person_aendern).
create or replace function event.passwort_setzen_wenn_leer(
    p_person uuid, p_hash text, p_name text
) returns boolean
    language plpgsql volatile security definer
    set search_path = event, pg_temp
as $$
begin
    if p_hash is null or p_hash !~ '^scrypt\$' then
        raise exception 'Hash ungueltig' using errcode = 'invalid_parameter_value';
    end if;
    update event.person p
    set passwort_hash = p_hash, name = coalesce(nullif(btrim(p_name), ''), p.name)
    where p.id = p_person
      and p.passwort_hash is null
      and p.aktiv
      and (p.id = event.person_id() or event.ist_betreiber());
    return found;
end
$$;

-- Rechte ------------------------------------------------------------------------------

revoke all on
    event.schluessel, event.marke, event.bestaetigung, event.verbindung, event.bereitstellung
    from public;

grant select, insert, update on event.verbindung to event_app;
-- bereitstellung: kein update/delete fuer event_app (nur der Arbeiter, Admin-DSN).
grant select, insert on event.bereitstellung to event_app;

revoke all on function
    event.schluessel_aktiv(uuid), event.schluessel_darf_event(uuid, uuid),
    event.schluessel_pruefen(text), event.marke_anlegen(text, text, uuid, integer),
    event.marke_pruefen(text, text),
    event.bestaetigung_anlegen(text, text, text, jsonb, integer),
    event.bestaetigung_einloesen(text, text, text, jsonb),
    event.anmeldung_holen(text), event.hat_passwort(uuid),
    event.passwort_setzen_wenn_leer(uuid, text, text)
    from public;

grant execute on function
    event.schluessel_pruefen(text), event.marke_anlegen(text, text, uuid, integer),
    event.marke_pruefen(text, text),
    event.bestaetigung_anlegen(text, text, text, jsonb, integer),
    event.bestaetigung_einloesen(text, text, text, jsonb),
    event.anmeldung_holen(text), event.hat_passwort(uuid),
    event.passwort_setzen_wenn_leer(uuid, text, text)
    to event_app;
