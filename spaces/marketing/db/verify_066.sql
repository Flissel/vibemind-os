-- Nachweise fuer 066, nur ueber migration_probe (eine Transaktion + ROLLBACK).
-- Wartende und (wie nach verify_065) abgelaufen laufende Auftraege echter Firmen (z. B. ein Wissens-Lauf bei ausgeschaltetem PC) in DIESER Transaktion
-- beiseite, damit pult_marke_naechster vorhersagbar bleibt; der ROLLBACK stellt sie wieder her.
UPDATE marketing.marken_auftraege SET status = 'fehler' WHERE status IN ('offen','in_arbeit');
CREATE TEMP TABLE _p066 ON COMMIT DROP AS SELECT NULL::text AS k, NULL::uuid AS id LIMIT 0;

DO $$ BEGIN
  INSERT INTO marketing.mandanten (id, name, aktiv) VALUES
    ('probe_m66', 'Probe M66', true), ('probe_m66_c', 'Probe M66 C', true);
END $$;

-- 0) Struktur
DO $$ BEGIN
  ASSERT to_regclass('marketing.marken_auftraege_ein_laufender') IS NOT NULL, '0: Index ein laufender';
  ASSERT to_regclass('marketing.marken_auftraege_ein_wissen') IS NOT NULL, '0: Index ein wartender Wissens-Lauf';
  ASSERT to_regprocedure('marketing.pult_marke_bearbeitung_anlegen(text, jsonb)') IS NOT NULL, '0: bearbeitung_anlegen';
  ASSERT to_regprocedure('marketing._marke_wissen_wartet(text)') IS NOT NULL, '0: wissen_wartet';
  ASSERT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'marken_wissen_nach_uebernahme' AND NOT tgisinternal),
         '0: Trigger';
  ASSERT (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
           WHERE n.nspname = 'marketing' AND p.proname = 'pult_gestalt_fehler') = 1, '0: genau eine pult_gestalt_fehler';
  ASSERT to_regprocedure('marketing.pult_marke_naechster(interval, text[])') IS NOT NULL, '0: naechster mit Arten';
  ASSERT to_regprocedure('marketing.pult_marke_naechster(interval)') IS NOT NULL, '0: naechster ohne Arten (Huelle)';
  ASSERT (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
           WHERE n.nspname = 'marketing' AND p.proname = 'pult_marke_naechster' AND p.pronargdefaults > 0) = 0,
         '0: kein Vorgabewert (ein Argument waere sonst mehrdeutig)';
  ASSERT to_regprocedure('marketing.pult_marke_profil_melden(text, jsonb)') IS NOT NULL, '0: profil_melden';
  ASSERT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'marketing'
                  AND table_name = 'marken_spiegel' AND column_name = 'profil' AND data_type = 'jsonb'), '0: Spalte profil';
END $$;

-- 1) Gestalt-Pruefung: logo_dunkel wie logo
DO $$ DECLARE g jsonb; v text; BEGIN
  SELECT gestalt - 'logo' INTO g FROM marketing.layout_vorlagen WHERE name = 'dunkel';
  ASSERT marketing.pult_gestalt_fehler(g || '{"logo_dunkel":"data:image/png;base64,iVBORw0KGgo="}') IS NULL, '1a: gueltig';
  v := marketing.pult_gestalt_fehler(g || '{"logo_dunkel":"https://x.example/logo.png"}');
  ASSERT v = 'logo_dunkel muss ein PNG/JPEG unter 150 KB sein', format('1b: keine data-URL: %s', v);
  v := marketing.pult_gestalt_fehler(g || jsonb_build_object('logo_dunkel', 'data:image/png;base64,' || repeat('A', 204800)));
  ASSERT v = 'logo_dunkel muss ein PNG/JPEG unter 150 KB sein', format('1c: zu gross: %s', v);
END $$;

-- 2) Spiegel kennt logo_dunkel; null entfernt es
DO $$ DECLARE n int; g jsonb; v_fehler text; BEGIN
  n := marketing.pult_marke_spiegeln('probe_m66',
         '{"akzent":"#336699","logo":"data:image/png;base64,iVBORw0KGgo=","logo_dunkel":"data:image/png;base64,iVBORw0KGgp="}',
         '2026-10-09 12:00 von probe');
  ASSERT n = 1, format('2a: Fassung 1, ist %s', n);
  SELECT gestalt INTO g FROM marketing.layout_vorlagen WHERE mandant = 'probe_m66' AND standard;
  ASSERT g->>'logo_dunkel' = 'data:image/png;base64,iVBORw0KGgp=' AND g->>'logo' = 'data:image/png;base64,iVBORw0KGgo=',
         format('2b: %s', g);
  n := marketing.pult_marke_spiegeln('probe_m66', '{"logo_dunkel":null}', '2026-10-09 12:05 von probe');
  SELECT gestalt INTO g FROM marketing.layout_vorlagen WHERE mandant = 'probe_m66' AND standard;
  ASSERT NOT (g ? 'logo_dunkel') AND g ? 'logo', format('2c: null entfernt nur logo_dunkel: %s', g);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_spiegeln('probe_m66', '{"grund":"#000000"}', 'x');
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Spiegel kennt nur akzent, flaeche, logo, logo_dunkel, schriften', format('2d: %s', v_fehler);
END $$;

-- 3) Arten und Bearbeitung (Formular -> Agent)
DO $$ DECLARE a uuid; v uuid; j jsonb; r record; v_fehler text; BEGIN
  v_fehler := NULL;
  BEGIN INSERT INTO marketing.marken_auftraege (mandant, art, nachricht) VALUES ('probe_m66_c', 'unsinn', 'x');
  EXCEPTION WHEN check_violation THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler IS NOT NULL, '3a: unbekannte Art wird abgelehnt';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_bearbeitung_anlegen('probe_m66', '[]'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Formular muss ein Objekt sein', format('3b: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_bearbeitung_anlegen('probe_m66', jsonb_build_object('x', repeat('a', 70000)));
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Formular zu groß', format('3c: %s', v_fehler);
  a := marketing.pult_marke_bearbeitung_anlegen('probe_m66', '{"akzent":"#112233","abschnitte":{"Ton":"Ruhig."}}');
  SELECT * INTO r FROM marketing.marken_auftraege WHERE id = a;
  ASSERT r.art = 'bearbeitung' AND r.status = 'offen' AND r.nachricht = 'Profil bearbeitet (Formular)'
     AND r.kontext->'formular'->>'akzent' = '#112233' AND (r.kontext->>'woertlich')::boolean,
         format('3d: angelegt: %s', row_to_json(r));
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_anlegen('probe_m66', 'Noch was', '{}'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Der Assistent arbeitet gerade', format('3e: %s', v_fehler);
  j := marketing.pult_marke_naechster(interval '5 minutes');
  ASSERT j->>'id' = a::text AND j->>'art' = 'bearbeitung' AND j->'kontext'->'formular'->>'akzent' = '#112233',
         format('3f: naechster: %s', j);
  v := marketing.pult_marke_vorschlag(a, '{"akzent":"#112233"}', 'Wie im Formular', '[]');
  ASSERT (SELECT status FROM marketing.marken_vorschlaege WHERE id = v) = 'offen', '3g: Vorschlag aus Bearbeitung';
  INSERT INTO _p066 VALUES ('v_bearb', v);
  a := marketing.pult_marke_anlegen('probe_m66', 'Und jetzt?', '{}');
  j := marketing.pult_marke_naechster(interval '5 minutes');
  ASSERT jsonb_array_length(j->'verlauf') = 1 AND j->'verlauf'->0->>'nachricht' = 'Profil bearbeitet (Formular)'
     AND j->'vorschlag'->>'id' = v::text, format('3h: Verlauf und offener Vorschlag: %s', j);
  PERFORM marketing.pult_marke_fertig(a, 'ok', '[]');
  -- ein nicht abgeholter Bearbeitungs-Auftrag stirbt nach 2 min wie ein Chat
  a := marketing.pult_marke_bearbeitung_anlegen('probe_m66_c', '{}');
  UPDATE marketing.marken_auftraege SET erstellt_am = now() - interval '3 minutes' WHERE id = a;
  PERFORM marketing._marke_aufraeumen('probe_m66_c');
  ASSERT (SELECT status FROM marketing.marken_auftraege WHERE id = a) = 'fehler', '3i: Bearbeitung stirbt nach 2 min';
END $$;

-- 4) Wissens-Lauf nach der Uebernahme; ein neuer ersetzt einen wartenden
DO $$ DECLARE v uuid; u uuid; w uuid; a uuid; j jsonb; r record; v_seit double precision; BEGIN
  v := (SELECT id FROM _p066 WHERE k = 'v_bearb');
  u := marketing.pult_marke_uebernehmen(v, 'probe', 'probe_m66');
  UPDATE marketing.marken_auftraege SET erstellt_am = now() - interval '10 minutes' WHERE id = u;
  v_seit := extract(epoch FROM now() - interval '10 minutes');
  j := marketing.pult_marke_naechster(interval '5 minutes');
  ASSERT j->>'id' = u::text, format('4a: %s', j);
  PERFORM marketing.pult_marke_fertig(u, 'Übernommen', '[]');
  SELECT * INTO r FROM marketing.marken_auftraege WHERE mandant = 'probe_m66' AND art = 'wissen' AND status = 'offen';
  ASSERT FOUND AND r.kontext->>'uebernahme' = u::text AND (r.kontext->>'seit')::double precision = v_seit
     AND r.nachricht = 'Wissen nach der Übernahme aktualisieren', format('4b: Wissens-Lauf: %s', row_to_json(r));
  w := r.id;
  -- ein wartender Wissens-Lauf haelt keinen Chat auf; naechster nimmt den Chat zuerst
  a := marketing.pult_marke_anlegen('probe_m66', 'Ton kuerzer', '{}');
  j := marketing.pult_marke_naechster(interval '5 minutes');
  ASSERT j->>'id' = a::text, format('4c: Chat vor Wissen: %s', j);
  v := marketing.pult_marke_vorschlag(a, '{"akzent":"#223344"}', 'x', '[]');
  u := marketing.pult_marke_uebernehmen(v, 'probe', 'probe_m66');
  j := marketing.pult_marke_naechster(interval '5 minutes');
  ASSERT j->>'id' = u::text, format('4d: Uebernehmen vor Wissen: %s', j);
  PERFORM marketing.pult_marke_fertig(u, 'Übernommen', '[]');
  SELECT * INTO r FROM marketing.marken_auftraege WHERE id = w;
  ASSERT r.status = 'fertig' AND r.antwort = 'Ersetzt durch einen neueren Wissens-Lauf.', format('4e: ersetzt: %s', row_to_json(r));
  SELECT * INTO r FROM marketing.marken_auftraege WHERE mandant = 'probe_m66' AND art = 'wissen' AND status = 'offen';
  ASSERT FOUND AND r.kontext->>'uebernahme' = u::text AND (r.kontext->>'seit')::double precision = v_seit,
         format('4f: neuer Lauf behaelt den fruehesten Beginn: %s', row_to_json(r));
  INSERT INTO _p066 VALUES ('w_offen', r.id);
  -- PC aus: ein wartender Wissens-Lauf stirbt nicht
  UPDATE marketing.marken_auftraege SET erstellt_am = now() - interval '30 minutes' WHERE id = r.id;
  PERFORM marketing._marke_aufraeumen('probe_m66');
  ASSERT (SELECT status FROM marketing.marken_auftraege WHERE id = r.id) = 'offen', '4g: Wissens-Lauf wartet';
  -- R7: der Marken-Faden (ohne Arten) holt nie einen Wissens-Lauf, auch nicht ueber die Huelle
  ASSERT marketing.pult_marke_naechster(interval '5 minutes') IS NULL, '4g2: Marken-Faden bekommt kein wissen';
  ASSERT marketing.pult_marke_naechster(interval '5 minutes', NULL) IS NULL, '4g3: NULL = alles ausser wissen';
  j := marketing.pult_marke_naechster(interval '5 minutes', ARRAY['wissen']);
  ASSERT j->>'id' = r.id::text AND j->>'art' = 'wissen' AND j->'vorschlag' = 'null'::jsonb AND (j->'kontext' ? 'seit'),
         format('4h: naechster gibt den Wissens-Lauf aus: %s', j);
END $$;

-- 5) Review Focus 5: Uebernahme waehrend ein Wissens-Lauf arbeitet
DO $$ DECLARE w uuid := (SELECT id FROM _p066 WHERE k = 'w_offen'); a uuid; v uuid; u uuid; j jsonb; r record; BEGIN
  ASSERT (SELECT status FROM marketing.marken_auftraege WHERE id = w) = 'in_arbeit', '5a: Lauf arbeitet';
  -- R7: waehrend der Wissens-Lauf arbeitet, wird ein Chat angelegt UND vergeben (der Marken-Faden ist frei)
  a := marketing.pult_marke_anlegen('probe_m66', 'Heller', '{}');
  j := marketing.pult_marke_naechster(interval '5 minutes');
  ASSERT j->>'id' = a::text AND j->>'art' = 'chat', format('5a2: Chat vergeben trotz laufendem Wissen: %s', j);
  v := marketing.pult_marke_vorschlag(a, '{"akzent":"#334455"}', 'x', '[]');
  u := marketing.pult_marke_uebernehmen(v, 'probe', 'probe_m66');
  j := marketing.pult_marke_naechster(interval '5 minutes');
  ASSERT j->>'id' = u::text, format('5a3: Uebernehmen vergeben trotz laufendem Wissen: %s', j);
  PERFORM marketing.pult_marke_fertig(u, 'Übernommen', '[]');
  ASSERT (SELECT status FROM marketing.marken_auftraege WHERE id = w) = 'in_arbeit', '5b: der laufende bleibt';
  SELECT * INTO r FROM marketing.marken_auftraege WHERE mandant = 'probe_m66' AND art = 'wissen' AND status = 'offen';
  ASSERT FOUND AND r.kontext->>'uebernahme' = u::text, format('5c: neuer wartender: %s', row_to_json(r));
  ASSERT marketing.pult_marke_naechster(interval '5 minutes', ARRAY['wissen']) IS NULL, '5d: kein zweiter Lauf derselben Firma';
  ASSERT marketing.pult_marke_naechster(interval '5 minutes') IS NULL, '5d2: Marken-Faden nimmt den wartenden nicht';
  ASSERT marketing._marke_wissen_wartet('probe_m66'), '5e: wartet';
  UPDATE marketing.marken_auftraege SET vergeben_bis = now() - interval '1 second' WHERE id = w;
  PERFORM marketing._marke_aufraeumen('probe_m66');           -- kein unique_violation
  ASSERT (SELECT status || '|' || antwort FROM marketing.marken_auftraege WHERE id = w)
         = 'fertig|Ersetzt durch einen neueren Wissens-Lauf.', '5f: abgelaufener mit Nachfolger endet ersetzt';
  j := marketing.pult_marke_naechster(interval '5 minutes', ARRAY['wissen']);
  ASSERT j->>'id' = r.id::text, format('5g: jetzt der wartende: %s', j);
  ASSERT marketing.pult_marke_fertig(r.id, 'Wissen aktualisiert: 0 Dateien', '[]') = 0, '5h: fertig ohne Markierung';
END $$;

-- 6) R7: Art-Filter von naechster (Marken-Faden ohne wissen, Wissens-Faden nur wissen)
DO $$ DECLARE c uuid; w uuid; j jsonb; v_fehler text; BEGIN
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_naechster(interval '5 minutes', ARRAY['unsinn']);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Unbekannte Auftragsart', format('6a: unbekannte Art: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_naechster(interval '5 minutes', ARRAY[]::text[]);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Unbekannte Auftragsart', format('6b: leere Liste: %s', v_fehler);
  INSERT INTO marketing.marken_auftraege (mandant, art, nachricht, kontext)
  VALUES ('probe_m66_c', 'wissen', 'Wissen nach der Übernahme aktualisieren', '{"seit": 0}') RETURNING id INTO w;
  c := marketing.pult_marke_anlegen('probe_m66_c', 'Ton?', '{}');
  UPDATE marketing.marken_auftraege SET erstellt_am = now() - interval '1 minute' WHERE id = w;   -- wissen ist aelter
  j := marketing.pult_marke_naechster(interval '5 minutes', ARRAY['wissen']);
  ASSERT j->>'id' = w::text AND j->>'art' = 'wissen', format('6c: Wissens-Faden nur wissen: %s', j);
  ASSERT marketing.pult_marke_naechster(interval '5 minutes', ARRAY['wissen']) IS NULL, '6d: Wissens-Faden nimmt keinen Chat';
  j := marketing.pult_marke_naechster(interval '5 minutes');
  ASSERT j->>'id' = c::text AND j->>'art' = 'chat', format('6e: Marken-Faden nimmt den Chat: %s', j);
  PERFORM marketing.pult_marke_fertig(c, 'ok', '[]');
  PERFORM marketing.pult_marke_fertig(w, 'Wissen aktualisiert: 0 Dateien', '[]');
END $$;

-- 7) R8: echtes Profil der Marke.md im Spiegel
DO $$ DECLARE p jsonb; v_fehler text; BEGIN
  p := '{"werte":{"akzent":"#112233","webseite":"https://probe.example"},"abschnitte":{"Ton":"Ruhig.","Zielgruppe":"Alle"}}';
  ASSERT marketing.pult_marke_profil_melden('probe_m66', p), '7a: gemeldet';
  ASSERT (SELECT profil FROM marketing.marken_spiegel WHERE mandant = 'probe_m66') = p, '7b: gespeichert';
  PERFORM marketing.pult_marke_profil_hinweise('probe_m66', '["Marke.md: text ungültig"]');
  PERFORM marketing.pult_marke_spiegeln('probe_m66', '{"akzent":"#445566"}', 'stand probe');
  ASSERT (SELECT profil FROM marketing.marken_spiegel WHERE mandant = 'probe_m66') = p, '7c: Hinweise/Spiegel lassen profil stehen';
  ASSERT marketing.pult_marke_profil_melden('probe_m66', '{"werte":{},"abschnitte":{"Ton":"Neu."}}'), '7d: ersetzt';
  ASSERT (SELECT profil->'abschnitte'->>'Ton' FROM marketing.marken_spiegel WHERE mandant = 'probe_m66') = 'Neu.', '7e: neu';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_profil_melden('probe_m66', '{"werte":{}}'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Profil braucht genau werte und abschnitte als Objekte', format('7f: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_profil_melden('probe_m66', '{"werte":{"akzent":1},"abschnitte":{}}');
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Profilwerte und Abschnitte muessen Text sein', format('7g: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_profil_melden('probe_m66',
          jsonb_build_object('werte', '{}'::jsonb, 'abschnitte', jsonb_build_object('Ton', repeat('a', 70000))));
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Profil zu groß', format('7h: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_profil_melden('gibt_es_nicht_m66', '{"werte":{},"abschnitte":{}}');
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Unbekannte Firma', format('7i: %s', v_fehler);
END $$;

-- 8) T1 M1: _marke_aufraeumen sperrt die Firmen vor den Auftraegen; ein zurueckgesetzter abgelaufener
-- Wissens-Lauf und der Trigger einer Uebernahme vertragen sich (kein unique_violation). Das echte Rennen
-- braucht zwei Verbindungen; hier: Reihenfolge im Quelltext + der sequentielle Fall in beide Richtungen (5f).
DO $$ DECLARE src text; a uuid; v uuid; u uuid; w uuid; j jsonb; BEGIN
  SELECT prosrc INTO src FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname = 'marketing' AND p.proname = '_marke_aufraeumen';
  ASSERT position('FROM marketing.mandanten' IN src) > 0
     AND position('FOR NO KEY UPDATE' IN src) > 0
     AND position('FOR NO KEY UPDATE' IN src) < position('UPDATE marketing.marken_auftraege' IN src),
         '8a: erst Firmen sperren, dann Auftraege aendern';
  a := marketing.pult_marke_anlegen('probe_m66', 'Noch dunkler', '{}');
  j := marketing.pult_marke_naechster(interval '5 minutes');
  v := marketing.pult_marke_vorschlag(a, '{"akzent":"#101010"}', 'x', '[]');
  INSERT INTO marketing.marken_auftraege (mandant, art, nachricht, kontext)
  VALUES ('probe_m66', 'wissen', 'Wissen nach der Übernahme aktualisieren', '{"seit": 0}') RETURNING id INTO w;
  j := marketing.pult_marke_naechster(interval '5 minutes', ARRAY['wissen']);
  ASSERT j->>'id' = w::text, format('8b: Wissens-Lauf vergeben: %s', j);
  UPDATE marketing.marken_auftraege SET vergeben_bis = now() - interval '1 second' WHERE id = w;
  u := marketing.pult_marke_uebernehmen(v, 'probe', 'probe_m66');     -- raeumt auf: w wird wieder offen
  ASSERT (SELECT status FROM marketing.marken_auftraege WHERE id = w) = 'offen', '8c: abgelaufener Lauf wartet wieder';
  j := marketing.pult_marke_naechster(interval '5 minutes');
  ASSERT j->>'id' = u::text, format('8d: %s', j);
  PERFORM marketing.pult_marke_fertig(u, 'Übernommen', '[]');         -- Trigger: kein unique_violation
  ASSERT (SELECT status || '|' || antwort FROM marketing.marken_auftraege WHERE id = w)
         = 'fertig|Ersetzt durch einen neueren Wissens-Lauf.', '8e: zurueckgesetzter Lauf ersetzt';
  ASSERT (SELECT count(*) FROM marketing.marken_auftraege
           WHERE mandant = 'probe_m66' AND art = 'wissen' AND status = 'offen') = 1, '8f: genau ein wartender';
END $$;

SELECT 'verify_066 ok' AS ergebnis;
