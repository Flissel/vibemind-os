-- Pruefung zu rowboat_leser.sql. Laeuft als supabase_admin in EINER Transaktion und endet mit
-- ROLLBACK, aendert also nichts. Gibt nur Zaehlungen und Schema-Namen aus, nie Zeilendaten.
--   docker exec -i debian-supabase-db-1 psql -U supabase_admin -d postgres -f - < verify_rowboat_leser.sql
\set ON_ERROR_STOP on
begin;

-- Als Admin: Rolle und Sperr-Regel.
do $$
declare s text;
declare n bigint;
declare laeden int := 0;
begin
  if not exists (select 1 from pg_authid
                  where rolname = 'rowboat_leser' and rolcanlogin and not rolsuper and not rolinherit
                    and not rolcreaterole and not rolcreatedb and not rolreplication and not rolbypassrls
                    and rolpassword is null) then
    raise exception 'rowboat_leser fehlt oder hat falsche Attribute';
  end if;
  if not exists (select 1 from pg_roles where rolname = 'rowboat_leser'
                    and 'default_transaction_read_only=on' = any(rolconfig)
                    and 'statement_timeout=60s' = any(rolconfig)) then
    raise exception 'rowboat_leser: default_transaction_read_only oder statement_timeout nicht gesetzt';
  end if;
  if exists (select 1 from pg_auth_members m join pg_roles r on r.oid = m.member
              where r.rolname = 'rowboat_leser') then
    raise exception 'rowboat_leser ist Mitglied einer anderen Rolle';
  end if;

  for s in select nspname from pg_namespace
            where nspname ~ '^sales(_[a-z][a-z0-9_]{0,30})?$' and nspname <> 'sales_test'
            order by nspname loop
    laeden := laeden + 1;
    -- Jeder Kontakt, den sales-claw als privat (server._privat_sql) oder mit Loeschantrag fuehrt,
    -- ist in der Sicht gesperrt.
    execute format($q$
      select count(*) from %1$I.leads l join %1$I.rowboat_kontakte k on k.id = l.id
       where not k.gesperrt
         and ((coalesce(jsonb_typeof(l.enrichment->'_privat'), '') = 'object'
               and coalesce(l.enrichment->'_privat'->>'privat', '') <> 'false')
              or l.enrichment ? '_loeschantrag')$q$, s) into n;
    if n > 0 then
      raise exception 'Schema %: % private Kontakte sind in rowboat_kontakte nicht gesperrt', s, n;
    end if;
  end loop;
  if exists (select 1 from pg_namespace where nspname = 'sales_test')
     and to_regclass('sales_test.rowboat_kontakte') is not null then
    raise exception 'sales_test hat eine rowboat_kontakte-Sicht';
  end if;
  raise notice 'verify (admin): % Laden-Schemata geprueft', laeden;
end $$;

-- Ab hier als rowboat_leser in einer read-only Transaktion (wie die Login-Sitzung des Jobs).
set local role rowboat_leser;
set local transaction_read_only = on;

do $$
declare s text;
declare n bigint;
declare laeden int := 0;
begin
  for s in select nspname from pg_namespace
            where nspname ~ '^sales(_[a-z][a-z0-9_]{0,30})?$' and nspname <> 'sales_test'
            order by nspname loop
    laeden := laeden + 1;
    if to_regclass(format('%I.rowboat_kontakte', s)) is null then
      raise exception 'Schema %: Sicht rowboat_kontakte fehlt (rowboat_leser.sql erneut einspielen)', s;
    end if;
    execute format('select count(*) from %I.rowboat_kontakte', s) into n;
    execute format('select count(*) from %I.rowboat_ereignisse', s) into n;
    execute format('select count(*) from %I.rowboat_newsletter', s) into n;

    -- Gesperrte Kontakte tragen keine Daten.
    execute format($q$
      select count(*) from %I.rowboat_kontakte
       where gesperrt and (name is not null or company is not null or title is not null
             or status is not null or consent_status is not null or source is not null
             or email_klein is not null or art is not null or bereiche is not null
             or updated_at is not null)$q$, s) into n;
    if n > 0 then
      raise exception 'Schema %: % gesperrte Kontakte mit Daten', s, n;
    end if;

    -- Laden-Tabellen sind nicht lesbar.
    begin
      execute format('select 1 from %I.leads limit 1', s);
      raise exception 'Schema %: leads ist fuer rowboat_leser lesbar', s;
    exception when insufficient_privilege then null;
    end;
    begin
      execute format('select body from %I.drafts limit 1', s);
      raise exception 'Schema %: drafts.body ist fuer rowboat_leser lesbar', s;
    exception when insufficient_privilege then null;
    end;
    begin
      execute format('select payload from %I.activities limit 1', s);
      raise exception 'Schema %: activities.payload ist fuer rowboat_leser lesbar', s;
    exception when insufficient_privilege then null;
    end;
  end loop;
  if laeden = 0 then
    raise exception 'kein Laden-Schema gefunden';
  end if;

  -- Schreiben scheitert an der read-only Transaktion.
  begin
    insert into sales.leads (name) values ('rowboat_leser-verify');
    raise exception 'insert als rowboat_leser moeglich';
  exception when read_only_sql_transaction then null;
  end;

  -- Marketing: erlaubte Spalten lesbar, alles andere nicht.
  perform count(*) from (select id, name from marketing.mandanten) x;
  perform count(*) from (select id, mandant, art, titel, status, freigegebene_fassung from marketing.inhalte) x;
  perform count(*) from (select inhalt, fassung, format, felder, bloecke, seite from marketing.inhalt_fassungen) x;
  perform count(*) from (select id, mandant, art, titel, status, dauer_s, geaendert_am, archiviert
                           from marketing.video_auftraege) x;
  perform count(*) from (select id, mandant, titel, status, dauer_s, geaendert_am from marketing.ton_auftraege) x;
  begin
    perform pflichtteil from marketing.mandanten limit 1;
    raise exception 'marketing.mandanten.pflichtteil ist lesbar';
  exception when insufficient_privilege then null;
  end;
  begin
    perform skript from marketing.ton_auftraege limit 1;
    raise exception 'marketing.ton_auftraege.skript ist lesbar';
  exception when insufficient_privilege then null;
  end;
  begin
    perform briefing from marketing.video_auftraege limit 1;
    raise exception 'marketing.video_auftraege.briefing ist lesbar';
  exception when insufficient_privilege then null;
  end;
  raise notice 'verify (rowboat_leser): % Laden-Schemata ok', laeden;
end $$;

rollback;
