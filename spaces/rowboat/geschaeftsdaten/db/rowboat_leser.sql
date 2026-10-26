-- Rowboat-Zentrale Teil B: Lese-Rolle `rowboat_leser` fuer den naechtlichen Geschaeftsdaten-Lauf.
-- Plan: docs/superpowers/plans/2026-10-26-rowboat-zentrale-teil-b-geschaeftsdaten.md (Task 1).
--
-- Anwenden als supabase_admin auf debian-supabase-db-1 (offload-vm):
--   docker exec -i debian-supabase-db-1 psql -U supabase_admin -d postgres -f - < rowboat_leser.sql
-- Danach immer verify_rowboat_leser.sql (Transaktion mit ROLLBACK).
--
-- Bewusst OHNE Migrationsnummer (Betreiber-Entscheid 3): idempotent und wiederholbar. Ein neuer
-- Laden (Schema sales_<kennung>) braucht einen erneuten Lauf dieser Datei; fehlt einem Laden die
-- Sicht, ueberspringt der Job ihn und meldet es.
--
-- Die Rolle hat kein Passwort und ist nur ueber die trust-Regel fuer 127.0.0.1 IM Container
-- erreichbar. Sie liest ausschliesslich die drei Sichten je Laden und einzelne Spalten von fuenf
-- Marketing-Tabellen. Keine Rechte auf Laden-Tabellen selbst, keine Nachrichtentexte, keine
-- Freitext-Felder, keine Termin-Details ausser Datum und Uhrzeit.
--
-- Sperr-Regel (fail-closed in SCHUTZrichtung, strenger als server._privat): gesperrt ist jeder
-- Kontakt mit dem Schluessel _privat, solange der Vermerk nicht ausdruecklich ein Objekt mit
-- privat=false ist, und jeder Kontakt mit dem Schluessel _loeschantrag. Unklar zaehlt als gesperrt.
-- Fuer gesperrte Kontakte liefert die Sicht nur id, Sperrflag, Archivflag und created_at.
--
-- Alles laeuft in EINER Transaktion (begin ... commit): bricht ein Schritt ab, bleibt nichts halb
-- angelegt. Achtung bei Proben: Diese Datei nicht in ein eigenes begin/rollback einbetten, ihr
-- commit wuerde die aeussere Transaktion festschreiben.
\set ON_ERROR_STOP on
begin;

do $$
begin
  if current_user <> 'supabase_admin' then
    raise exception 'rowboat_leser.sql als supabase_admin einspielen (Eigentuemer der Sichten), nicht als %',
      current_user;
  end if;
end $$;

-- Rolle: login ohne Passwort, keine Sonderrechte, erbt nichts.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'rowboat_leser') then
    create role rowboat_leser login nosuperuser nocreatedb nocreaterole noinherit noreplication;
  end if;
end $$;
alter role rowboat_leser password null;
alter role rowboat_leser set default_transaction_read_only = on;
alter role rowboat_leser set statement_timeout = '60s';

-- Je Laden-Schema drei Sichten. Schleife wie sales-claw db/provision.sql (gueltige Laden-Schemata
-- nach server.SCHEMA_MUSTER), aber NIE sales_test.
do $$
declare s text;
declare laeden text[];
begin
  select coalesce(array_agg(schema_name order by schema_name), array[]::text[])
    into laeden
    from information_schema.schemata
    where schema_name ~ '^sales(_[a-z][a-z0-9_]{0,30})?$'
      and schema_name <> 'sales_test';
  foreach s in array laeden loop
    execute format('grant usage on schema %I to rowboat_leser', s);

    execute format($sicht$
      create or replace view %1$I.rowboat_kontakte with (security_barrier) as
      select l.id,
             p.gesperrt,
             p.archiviert,
             case when p.gesperrt then null else l.name end as name,
             case when p.gesperrt then null else l.company end as company,
             case when p.gesperrt then null else l.title end as title,
             case when p.gesperrt then null else l.status end as status,
             case when p.gesperrt then null else l.consent_status end as consent_status,
             case when p.gesperrt then null else l.source end as source,
             case when p.gesperrt then null else nullif(lower(btrim(l.email)), '') end as email_klein,
             case when p.gesperrt then null else
               (case when l.enrichment->>'art' in ('person', 'firma') then l.enrichment->>'art'
                     when coalesce(btrim(l.company), '') <> '' then 'firma'
                     else 'person' end) end as art,
             case when p.gesperrt then null else
               (case when jsonb_typeof(l.enrichment->'bereiche') = 'array' then l.enrichment->'bereiche'
                     else '[]'::jsonb end) end as bereiche,
             l.created_at,
             case when p.gesperrt then null else l.updated_at end as updated_at
        from %1$I.leads l
        cross join lateral (select
          coalesce(
            ((l.enrichment ? '_privat') and not (jsonb_typeof(l.enrichment->'_privat') = 'object'
                 and coalesce(l.enrichment->'_privat'->>'privat', '') = 'false'))
            or (l.enrichment ? '_loeschantrag'),
            true) as gesperrt,
          coalesce(jsonb_typeof(l.enrichment->'archiviert'->'archiviert') = 'boolean'
               and (l.enrichment->'archiviert'->>'archiviert') = 'true', false) as archiviert) p
    $sicht$, s);

    -- Ereignisse nur fuer nicht gesperrte Kontakte, nur Typ, Zeit und einzelne Payload-Schluessel.
    -- Ein gesperrter oder unklarer Empfehlungsgeber wird weder mit Name noch mit id genannt.
    execute format($sicht$
      create or replace view %1$I.rowboat_ereignisse with (security_barrier) as
      select a.lead_id,
             a.type as typ,
             a.created_at as zeit,
             case when a.type in ('termin', 'termin_abgesagt') then a.payload->>'uid' end as termin_uid,
             case when a.type = 'termin' then a.payload->>'datum' end as termin_datum,
             case when a.type = 'termin' then a.payload->>'uhrzeit' end as termin_uhrzeit,
             -- fail-closed: nur mit gefundener Empfehlungsliste; ist ein Geber-Kontakt eingetragen,
             -- muss er in der Sicht vorhanden und nicht gesperrt sein (ek.gesperrt = false, NULL zaehlt nicht).
             case when a.type = 'empfehlung'
                       and e.id is not null and (e.empfohlen_von_lead is null or ek.gesperrt = false)
                  then a.payload->>'empfohlen_von_name' end as empfohlen_von_name,
             case when a.type = 'empfehlung' and e.id is not null and ek.gesperrt = false
                  then e.empfohlen_von_lead end as empfohlen_von_lead
        from %1$I.activities a
        join %1$I.rowboat_kontakte k on k.id = a.lead_id and not k.gesperrt
        left join %1$I.empfehlungslisten e
               on a.type = 'empfehlung' and e.id::text = a.payload->>'liste_id'
        left join %1$I.rowboat_kontakte ek on ek.id = e.empfohlen_von_lead
       where a.type in ('nachricht_ausgehend', 'kundenantwort', 'nachricht', 'versand', 'termin',
                        'termin_abgesagt', 'stufenwechsel', 'empfehlung')
    $sicht$, s);

    -- Versendete Newsletter: nur Kennung, erster Versand und Empfaengerzahl.
    execute format($sicht$
      create or replace view %1$I.rowboat_newsletter with (security_barrier) as
      select d.newsletter,
             coalesce(min(d.sent_at), min(d.created_at)) as erster_versand,
             count(distinct d.lead_id)::int as empfaenger
        from %1$I.drafts d
       where d.status = 'sent' and d.newsletter is not null
       group by d.newsletter
    $sicht$, s);

    execute format('grant select on %1$I.rowboat_kontakte, %1$I.rowboat_ereignisse, %1$I.rowboat_newsletter '
                   'to rowboat_leser', s);
  end loop;
end $$;

-- Marketing: nur einzelne Spalten, keine ganzen Tabellen.
grant usage on schema marketing to rowboat_leser;
grant select (id, name) on marketing.mandanten to rowboat_leser;
grant select (id, mandant, art, titel, status, freigegebene_fassung) on marketing.inhalte to rowboat_leser;
grant select (inhalt, fassung, format, felder, bloecke, seite) on marketing.inhalt_fassungen to rowboat_leser;
grant select (id, mandant, art, titel, status, dauer_s, geaendert_am, archiviert)
  on marketing.video_auftraege to rowboat_leser;
grant select (id, mandant, titel, status, dauer_s, geaendert_am) on marketing.ton_auftraege to rowboat_leser;

commit;
