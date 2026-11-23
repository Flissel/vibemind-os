-- verify_101: Empfang fuer Auftraege des Gehirns (Kern, Huellen Sales und Rowboat, _vergeben, brain_lesen).
-- LAEUFT NUR in der zurueckgerollten migration_probe (096, 098, 100, 101 + verify_101): die Bloecke legen Probezeilen
-- direkt per INSERT an (die Art kommt nicht aus der Tabelle der Gehirn-Arten, die hier nicht angefasst wird) - nie gegen die
-- echte Datenbank ausfuehren. Jeder Block legt seine eigenen Zeilen an. now() steht in einer Transaktion still:
-- "vergangen" heisst daher eine Zeit vor now() beim Anlegen der Zeile.

-- Hilfsfunktion (nur in dieser Sitzung): eine Zeile mit gewuenschtem Stand und Fristen anlegen.
CREATE FUNCTION pg_temp.s101(p_von text, p_an text, p_laden text, p_art text, p_stand text,
                             p_frist interval, p_vergeben interval, p_ergebnis jsonb DEFAULT NULL) RETURNS uuid
LANGUAGE plpgsql AS $f$
DECLARE v_id uuid := gen_random_uuid();
BEGIN
  INSERT INTO auftrag.auftraege (id, von, an, laden, mandant, art, wurzel_id, stand, versuche, frist_bis,
                                 vergeben_bis, ergebnis)
  VALUES (v_id, p_von, p_an, p_laden, 'probe', p_art, v_id, p_stand,
          CASE WHEN p_stand = 'in_arbeit' THEN 1 ELSE 0 END,
          now() + p_frist,
          CASE WHEN p_stand = 'in_arbeit' THEN now() + p_vergeben END, coalesce(p_ergebnis, '{}'::jsonb));
  RETURN v_id;
END $f$;

-- 1: Rolle rowboat_empfang, brain/probe/1 vorhanden (nicht einzige Zeile), Rechte der beiden Empfaenger
DO $$
DECLARE v_sig text; v_ids oid[]; v_namen text;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rowboat_empfang') THEN
    RAISE EXCEPTION '101.1: Rolle rowboat_empfang fehlt'; END IF;
  IF (SELECT rolcanlogin FROM pg_roles WHERE rolname = 'rowboat_empfang') THEN
    RAISE EXCEPTION '101.1: rowboat_empfang darf sich anmelden'; END IF;
  IF NOT EXISTS (SELECT 1 FROM auftrag.brain_arten WHERE an = 'brain' AND art = 'probe' AND version = 1) THEN
    RAISE EXCEPTION '101.1: brain/probe/1 fehlt in brain_arten'; END IF;
  IF NOT has_schema_privilege('rowboat_empfang', 'auftrag', 'USAGE') THEN
    RAISE EXCEPTION '101.1: rowboat_empfang ohne USAGE auf auftrag'; END IF;

  -- Huellen: nur der eigene Empfaenger
  FOREACH v_sig IN ARRAY ARRAY['auftrag.sales_empfang_naechster(text,interval)',
                               'auftrag.sales_empfang_abschliessen(uuid,jsonb,text)',
                               'auftrag.sales_empfang_fehler(uuid,text,text)'] LOOP
    IF NOT has_function_privilege('sales_app', v_sig, 'EXECUTE') THEN
      RAISE EXCEPTION '101.1: sales_app darf % nicht', v_sig; END IF;
    IF has_function_privilege('rowboat_empfang', v_sig, 'EXECUTE') THEN
      RAISE EXCEPTION '101.1: rowboat_empfang darf %', v_sig; END IF;
    IF has_function_privilege('brain_app', v_sig, 'EXECUTE') OR has_function_privilege('public', v_sig, 'EXECUTE') THEN
      RAISE EXCEPTION '101.1: brain_app oder PUBLIC darf %', v_sig; END IF;
  END LOOP;
  FOREACH v_sig IN ARRAY ARRAY['auftrag.rowboat_empfang_naechster(text,interval)',
                               'auftrag.rowboat_empfang_abschliessen(uuid,jsonb,text)',
                               'auftrag.rowboat_empfang_fehler(uuid,text,text)',
                               'auftrag.rowboat_empfang_bekannt(text,text[])'] LOOP
    IF NOT has_function_privilege('rowboat_empfang', v_sig, 'EXECUTE') THEN
      RAISE EXCEPTION '101.1: rowboat_empfang darf % nicht', v_sig; END IF;
    IF has_function_privilege('sales_app', v_sig, 'EXECUTE') THEN
      RAISE EXCEPTION '101.1: sales_app darf %', v_sig; END IF;
    IF has_function_privilege('brain_app', v_sig, 'EXECUTE') OR has_function_privilege('public', v_sig, 'EXECUTE') THEN
      RAISE EXCEPTION '101.1: brain_app oder PUBLIC darf %', v_sig; END IF;
  END LOOP;
  -- Kern: niemand
  FOREACH v_sig IN ARRAY ARRAY['auftrag._empfang_naechster(text,text,interval)',
                               'auftrag._empfang_abschliessen(text,uuid,jsonb,text)',
                               'auftrag._empfang_fehler(text,uuid,text,text)'] LOOP
    IF has_function_privilege('sales_app', v_sig, 'EXECUTE') OR has_function_privilege('rowboat_empfang', v_sig, 'EXECUTE')
       OR has_function_privilege('brain_app', v_sig, 'EXECUTE') OR has_function_privilege('public', v_sig, 'EXECUTE') THEN
      RAISE EXCEPTION '101.1: Kern % ist ausfuehrbar', v_sig; END IF;
  END LOOP;
  -- brain_lesen bleibt fuer brain_app, nicht fuer PUBLIC
  IF NOT has_function_privilege('brain_app', 'auftrag.brain_lesen(uuid)', 'EXECUTE')
     OR has_function_privilege('public', 'auftrag.brain_lesen(uuid)', 'EXECUTE') THEN
    RAISE EXCEPTION '101.1: Rechte an brain_lesen verschoben'; END IF;

  -- rowboat_empfang darf im Schema auftrag genau die vier Rowboat-Funktionen
  v_ids := ARRAY(SELECT p.oid FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                  WHERE n.nspname = 'auftrag' AND has_function_privilege('rowboat_empfang', p.oid, 'EXECUTE')
                  ORDER BY p.oid);
  IF v_ids IS DISTINCT FROM ARRAY(SELECT o FROM unnest(ARRAY[
        'auftrag.rowboat_empfang_naechster(text,interval)'::regprocedure::oid,
        'auftrag.rowboat_empfang_abschliessen(uuid,jsonb,text)'::regprocedure::oid,
        'auftrag.rowboat_empfang_fehler(uuid,text,text)'::regprocedure::oid,
        'auftrag.rowboat_empfang_bekannt(text,text[])'::regprocedure::oid]) AS o ORDER BY o) THEN
    SELECT string_agg(p.proname, ', ' ORDER BY p.proname) INTO v_namen FROM pg_proc p WHERE p.oid = ANY (v_ids);
    RAISE EXCEPTION '101.1: rowboat_empfang darf nicht genau die vier Funktionen (%)', coalesce(v_namen, '-'); END IF;
  IF has_table_privilege('rowboat_empfang', 'auftrag.auftraege', 'SELECT')
     OR has_table_privilege('rowboat_empfang', 'auftrag.brain_arten', 'SELECT') THEN
    RAISE EXCEPTION '101.1: rowboat_empfang liest Tabellen'; END IF;
END $$;

-- 2: Lebenslauf je Empfaenger: uebernehmen, zweites Uebernehmen nichts, abschliessen, zweites false
DO $$
DECLARE v_an text; v_laden text; a uuid; b uuid; j jsonb; r record; v_ok boolean;
BEGIN
  FOREACH v_an IN ARRAY ARRAY['sales', 'rowboat'] LOOP
    v_laden := 'p101_' || v_an;
    a := pg_temp.s101('brain', v_an, v_laden, 'probe101', 'offen', interval '1 hour', interval '0');
    EXECUTE format('SELECT auftrag.%I_empfang_naechster($1, $2)', v_an) INTO j USING v_laden, interval '5 minutes';
    IF j IS NULL OR j->>'id' <> a::text OR j->>'art' <> 'probe101' THEN RAISE EXCEPTION '101.2: % uebernimmt nicht', v_an; END IF;
    IF NOT (j ? 'frist_bis' AND j ? 'eingabe' AND j ? 'plan_id' AND j ? 'art_version')
       OR j ? 'mandant' OR j ? 'verlauf' OR j ? 'ergebnis' THEN RAISE EXCEPTION '101.2: % Uebergabe falsch', v_an; END IF;
    SELECT * INTO r FROM auftrag.auftraege WHERE id = a;
    IF r.stand <> 'in_arbeit' OR r.versuche <> 1 OR r.vergeben_bis IS NULL THEN RAISE EXCEPTION '101.2: % Stand nach Uebernahme', v_an; END IF;
    EXECUTE format('SELECT auftrag.%I_empfang_naechster($1, $2)', v_an) INTO j USING v_laden, interval '5 minutes';
    IF j IS NOT NULL THEN RAISE EXCEPTION '101.2: % zweites Uebernehmen liefert etwas', v_an; END IF;

    -- falscher Laden und Nicht-Objekt schliessen nichts ab
    EXECUTE format('SELECT auftrag.%I_empfang_abschliessen($1, $2, $3)', v_an) INTO v_ok USING a, '{"x":1}'::jsonb, 'fremd';
    IF v_ok THEN RAISE EXCEPTION '101.2: % Abschluss im fremden Laden', v_an; END IF;
    EXECUTE format('SELECT auftrag.%I_empfang_abschliessen($1, $2, $3)', v_an) INTO v_ok USING a, '[1]'::jsonb, v_laden;
    IF v_ok THEN RAISE EXCEPTION '101.2: % Abschluss mit Liste', v_an; END IF;
    EXECUTE format('SELECT auftrag.%I_empfang_abschliessen($1, $2, $3)', v_an) INTO v_ok
      USING a, jsonb_build_object('x', repeat('a', 70000)), v_laden;
    IF v_ok THEN RAISE EXCEPTION '101.2: % Abschluss mit zu grossem Ergebnis', v_an; END IF;
    IF (SELECT stand FROM auftrag.auftraege WHERE id = a) <> 'in_arbeit' THEN RAISE EXCEPTION '101.2: % Stand nach Fehlversuchen', v_an; END IF;

    EXECUTE format('SELECT auftrag.%I_empfang_abschliessen($1, $2, $3)', v_an) INTO v_ok USING a, '{"ok":true}'::jsonb, v_laden;
    IF NOT v_ok THEN RAISE EXCEPTION '101.2: % Abschluss false', v_an; END IF;
    SELECT * INTO r FROM auftrag.auftraege WHERE id = a;
    IF r.stand <> 'fertig' OR r.ergebnis->>'ok' <> 'true' OR r.vergeben_bis IS NOT NULL
       OR r.verlauf[cardinality(r.verlauf)]->>'stand' <> 'fertig' THEN RAISE EXCEPTION '101.2: % Stand nach Abschluss', v_an; END IF;
    EXECUTE format('SELECT auftrag.%I_empfang_abschliessen($1, $2, $3)', v_an) INTO v_ok USING a, '{"ok":true}'::jsonb, v_laden;
    IF v_ok THEN RAISE EXCEPTION '101.2: % zweites Abschliessen true', v_an; END IF;
    EXECUTE format('SELECT auftrag.%I_empfang_fehler($1, $2, $3)', v_an) INTO v_ok USING a, 'zu spaet', v_laden;
    IF v_ok THEN RAISE EXCEPTION '101.2: % Fehler nach Abschluss true', v_an; END IF;

    -- Fehlerweg: Grund auf 300 Zeichen, leerer Grund bekommt den festen Text
    b := pg_temp.s101('brain', v_an, v_laden, 'probe101', 'in_arbeit', interval '1 hour', interval '5 minutes');
    EXECUTE format('SELECT auftrag.%I_empfang_fehler($1, $2, $3)', v_an) INTO v_ok USING b, 'x', 'fremd';
    IF v_ok THEN RAISE EXCEPTION '101.2: % Fehler im fremden Laden', v_an; END IF;
    EXECUTE format('SELECT auftrag.%I_empfang_fehler($1, $2, $3)', v_an) INTO v_ok USING b, repeat('y', 400), v_laden;
    IF NOT v_ok THEN RAISE EXCEPTION '101.2: % Fehler false', v_an; END IF;
    SELECT * INTO r FROM auftrag.auftraege WHERE id = b;
    IF r.stand <> 'fehler' OR length(r.fehler) <> 300 OR r.vergeben_bis IS NOT NULL THEN RAISE EXCEPTION '101.2: % Fehlertext/Stand', v_an; END IF;
    b := pg_temp.s101('brain', v_an, v_laden, 'probe101', 'in_arbeit', interval '1 hour', interval '5 minutes');
    EXECUTE format('SELECT auftrag.%I_empfang_fehler($1, $2, $3)', v_an) INTO v_ok USING b, '   ', v_laden;
    IF (SELECT fehler FROM auftrag.auftraege WHERE id = b) <> 'Fehler ohne Angabe' THEN RAISE EXCEPTION '101.2: % leerer Grund', v_an; END IF;
    -- ein offener Auftrag laesst sich nicht als Fehler abschliessen (erst uebernehmen)
    b := pg_temp.s101('brain', v_an, v_laden, 'probe101', 'offen', interval '1 hour', interval '0');
    EXECUTE format('SELECT auftrag.%I_empfang_fehler($1, $2, $3)', v_an) INTO v_ok USING b, 'x', v_laden;
    IF v_ok THEN RAISE EXCEPTION '101.2: % Fehler auf offenem Auftrag', v_an; END IF;
    UPDATE auftrag.auftraege SET stand = 'abgelaufen' WHERE id = b;
  END LOOP;
END $$;

-- 3: Abschluss nach Leihdauer und nach Frist, Liegengebliebene werden fehler (kein zweiter Versuch), Fristen, Grenzen
DO $$
DECLARE v_an text; v_laden text; a uuid; b uuid; c uuid; j jsonb; r record; v_ok boolean; v_text text;
BEGIN
  FOREACH v_an IN ARRAY ARRAY['sales', 'rowboat'] LOOP
    v_laden := 'p101c_' || v_an;
    -- Leihdauer vorbei: Abschluss false, Stand unveraendert
    a := pg_temp.s101('brain', v_an, v_laden, 'probe101', 'in_arbeit', interval '1 hour', interval '-1 minute');
    EXECUTE format('SELECT auftrag.%I_empfang_abschliessen($1, $2, $3)', v_an) INTO v_ok USING a, '{"ok":true}'::jsonb, v_laden;
    IF v_ok THEN RAISE EXCEPTION '101.3: % Abschluss nach Leihdauer', v_an; END IF;
    -- Frist vorbei (Leihdauer noch offen): Abschluss false
    b := pg_temp.s101('brain', v_an, v_laden, 'probe101', 'in_arbeit', interval '-1 minute', interval '5 minutes');
    EXECUTE format('SELECT auftrag.%I_empfang_abschliessen($1, $2, $3)', v_an) INTO v_ok USING b, '{"ok":true}'::jsonb, v_laden;
    IF v_ok THEN RAISE EXCEPTION '101.3: % Abschluss nach Frist', v_an; END IF;
    -- offen, aber Frist vorbei: wird nicht vergeben
    c := pg_temp.s101('brain', v_an, v_laden, 'probe101', 'offen', interval '-1 minute', interval '0');
    -- naechster: a (liegengeblieben) wird fehler mit festem Text; b ebenfalls; c bleibt offen und wird nicht vergeben
    EXECUTE format('SELECT auftrag.%I_empfang_naechster($1, $2)', v_an) INTO j USING v_laden, interval '5 minutes';
    IF j IS NOT NULL THEN RAISE EXCEPTION '101.3: % vergibt Liegengebliebenes oder Abgelaufenes', v_an; END IF;
    v_text := CASE v_an WHEN 'sales' THEN 'Bearbeitung bei Sales abgebrochen' ELSE 'Bearbeitung bei Rowboat abgebrochen' END;
    SELECT * INTO r FROM auftrag.auftraege WHERE id = a;
    IF r.stand <> 'fehler' OR r.fehler <> v_text OR r.versuche <> 1 OR r.vergeben_bis IS NOT NULL
       OR r.verlauf[cardinality(r.verlauf)]->>'stand' <> 'fehler' THEN
      RAISE EXCEPTION '101.3: % Liegengebliebener nicht fehler (%, %)', v_an, r.stand, r.fehler; END IF;
    IF (SELECT stand FROM auftrag.auftraege WHERE id = c) <> 'offen' THEN RAISE EXCEPTION '101.3: % Frist-vorbei-Zeile angefasst', v_an; END IF;
    -- der Auftrag, dessen Leihdauer noch laeuft (b), bleibt in Arbeit
    IF (SELECT stand FROM auftrag.auftraege WHERE id = b) <> 'in_arbeit' THEN RAISE EXCEPTION '101.3: % laufender Auftrag angefasst', v_an; END IF;

    -- Leihdauer: 0, negativ, ueber 15 Minuten, NULL; leerer Laden; Text der Ablehnung
    FOREACH v_text IN ARRAY ARRAY['0 seconds', '-1 minute', '16 minutes'] LOOP
      v_ok := false;
      BEGIN
        EXECUTE format('SELECT auftrag.%I_empfang_naechster($1, $2::interval)', v_an) INTO j USING v_laden, v_text;
        v_ok := true;
      EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'Leihdauer ausserhalb 0 bis 15 Minuten' THEN RAISE EXCEPTION '101.3: falscher Text (Leihdauer %): %', v_text, left(SQLERRM, 80); END IF;
      END;
      IF v_ok THEN RAISE EXCEPTION '101.3: % Leihdauer % angenommen', v_an, v_text; END IF;
    END LOOP;
    v_ok := false;
    BEGIN
      EXECUTE format('SELECT auftrag.%I_empfang_naechster($1, $2)', v_an) INTO j USING v_laden, NULL::interval;
      v_ok := true;
    EXCEPTION WHEN raise_exception THEN
      IF SQLERRM <> 'Leihdauer ausserhalb 0 bis 15 Minuten' THEN RAISE EXCEPTION '101.3: falscher Text (Leihdauer NULL)'; END IF;
    END;
    IF v_ok THEN RAISE EXCEPTION '101.3: % Leihdauer NULL angenommen', v_an; END IF;
    v_ok := false;
    BEGIN
      EXECUTE format('SELECT auftrag.%I_empfang_naechster($1, $2)', v_an) INTO j USING '  ', interval '5 minutes';
      v_ok := true;
    EXCEPTION WHEN raise_exception THEN
      IF SQLERRM <> 'Der Laden fehlt' THEN RAISE EXCEPTION '101.3: falscher Text (Laden)'; END IF;
    END;
    IF v_ok THEN RAISE EXCEPTION '101.3: % leerer Laden angenommen', v_an; END IF;
    UPDATE auftrag.auftraege SET stand = 'abgelaufen' WHERE id IN (b, c);
  END LOOP;
END $$;

-- 4: Trennung der Empfaenger, des Absenders und der alten Vergabe (_vergeben)
DO $$
DECLARE a uuid; b uuid; s uuid; m uuid; v uuid; j jsonb; v_ok boolean;
BEGIN
  -- Auftrag an Rowboat: Sales sieht ihn nicht, auch nicht im selben Laden; Rowboat-Huelle fasst keine Sales-Zeile an
  a := pg_temp.s101('brain', 'rowboat', 'p101d', 'probe101', 'offen', interval '1 hour', interval '0');
  j := auftrag.sales_empfang_naechster('p101d', interval '5 minutes');
  IF j IS NOT NULL THEN RAISE EXCEPTION '101.4: Sales uebernimmt Auftrag an Rowboat'; END IF;
  b := pg_temp.s101('brain', 'sales', 'p101d', 'probe101', 'in_arbeit', interval '1 hour', interval '5 minutes');
  IF auftrag.rowboat_empfang_fehler(b, 'x', 'p101d') THEN RAISE EXCEPTION '101.4: Rowboat beendet Auftrag an Sales'; END IF;
  IF auftrag.rowboat_empfang_abschliessen(b, '{}'::jsonb, 'p101d') THEN RAISE EXCEPTION '101.4: Rowboat schliesst Auftrag an Sales ab'; END IF;
  IF auftrag.sales_empfang_abschliessen(a, '{}'::jsonb, 'p101d') THEN RAISE EXCEPTION '101.4: Sales schliesst Auftrag an Rowboat ab'; END IF;

  -- Zeile, die Sales selbst gibt (von sales, an sales): kein Empfang
  s := pg_temp.s101('sales', 'sales', 'p101e', 'reel', 'offen', interval '1 hour', interval '0');
  IF auftrag.sales_empfang_naechster('p101e', interval '5 minutes') IS NOT NULL THEN
    RAISE EXCEPTION '101.4: Empfang uebernimmt Auftrag von sales'; END IF;
  IF (SELECT stand FROM auftrag.auftraege WHERE id = s) <> 'offen' THEN RAISE EXCEPTION '101.4: Zeile von sales veraendert'; END IF;
  s := pg_temp.s101('sales', 'sales', 'p101e', 'reel', 'in_arbeit', interval '1 hour', interval '5 minutes');
  IF auftrag.sales_empfang_fehler(s, 'x', 'p101e') OR auftrag.sales_empfang_abschliessen(s, '{}'::jsonb, 'p101e') THEN
    RAISE EXCEPTION '101.4: Empfang beendet Auftrag von sales'; END IF;

  -- die alten Wege vergeben nie einen Auftrag des Gehirns
  m := pg_temp.s101('brain', 'rowboat', 'p101f', 'probe101', 'offen', interval '1 hour', interval '0');
  IF auftrag.naechster('rowboat', interval '5 minutes') IS NOT NULL THEN
    RAISE EXCEPTION '101.4: naechster(rowboat) liefert etwas'; END IF;
  IF (SELECT stand || versuche FROM auftrag.auftraege WHERE id = m) <> 'offen0' THEN RAISE EXCEPTION '101.4: naechster(rowboat) fasst die Zeile an'; END IF;
  m := pg_temp.s101('brain', 'marketing', 'p101f', 'probe101', 'offen', interval '1 hour', interval '0');
  IF auftrag.naechster('marketing', interval '5 minutes') IS NOT NULL THEN
    RAISE EXCEPTION '101.4: naechster(marketing) vergibt Auftrag des Gehirns'; END IF;
  -- video_naechster: erst mit art = 'video' wird die Ausschlussklausel in _vergeben wirklich geprueft
  v := pg_temp.s101('brain', 'marketing', 'p101f', 'video', 'offen', interval '1 hour', interval '0');
  IF auftrag.video_naechster(interval '5 minutes') IS NOT NULL THEN
    RAISE EXCEPTION '101.4: video_naechster vergibt Auftrag des Gehirns'; END IF;
  IF (SELECT stand || versuche FROM auftrag.auftraege WHERE id = v) <> 'offen0' THEN
    RAISE EXCEPTION '101.4: video_naechster fasst die Video-Zeile des Gehirns an'; END IF;
  IF (SELECT stand || versuche FROM auftrag.auftraege WHERE id = m) <> 'offen0' THEN RAISE EXCEPTION '101.4: naechster(marketing) fasst die Zeile an'; END IF;
  -- und ein normaler Auftrag an Marketing wird weiter vergeben (Altbestand heil)
  m := auftrag.anlegen('sales', 'marketing', 'p101g', 'probe', 'reel', '{}');
  j := auftrag.naechster('marketing', interval '5 minutes');
  IF j IS NULL OR j->>'id' <> m::text THEN RAISE EXCEPTION '101.4: normaler Auftrag an marketing nicht vergeben'; END IF;
  UPDATE auftrag.auftraege SET stand = 'abgelaufen' WHERE laden IN ('p101d', 'p101e', 'p101f') AND stand IN ('offen', 'in_arbeit');
END $$;

-- 5: brain_lesen setzt Ueberfaelliges auf abgelaufen und liest sonst wie zuvor
DO $$
DECLARE a uuid; b uuid; c uuid; d uuid; s uuid; j jsonb; n int;
BEGIN
  a := pg_temp.s101('brain', 'rowboat', 'p101h', 'probe101', 'offen', interval '-1 minute', interval '0');
  b := pg_temp.s101('brain', 'sales', 'p101h', 'probe101', 'in_arbeit', interval '-1 minute', interval '5 minutes');
  c := pg_temp.s101('brain', 'sales', 'p101h', 'probe101', 'offen', interval '1 hour', interval '0');
  d := pg_temp.s101('brain', 'sales', 'p101h', 'probe101', 'fertig', interval '-1 minute', interval '0', '{"ok":true}');
  s := pg_temp.s101('sales', 'marketing', 'p101h', 'reel', 'offen', interval '-1 minute', interval '0');

  n := cardinality((SELECT verlauf FROM auftrag.auftraege WHERE id = a));
  j := auftrag.brain_lesen(a);
  IF j->>'stand' <> 'abgelaufen' OR j->>'id' <> a::text THEN RAISE EXCEPTION '101.5: offene ueberfaellige Zeile nicht abgelaufen'; END IF;
  IF (SELECT stand FROM auftrag.auftraege WHERE id = a) <> 'abgelaufen'
     OR cardinality((SELECT verlauf FROM auftrag.auftraege WHERE id = a)) <> n + 1
     OR (SELECT verlauf[cardinality(verlauf)]->>'stand' FROM auftrag.auftraege WHERE id = a) <> 'abgelaufen' THEN
    RAISE EXCEPTION '101.5: Zeile oder Verlauf nach Ablauf'; END IF;
  -- zweiter Lesevorgang haengt nichts mehr an
  PERFORM auftrag.brain_lesen(a);
  IF cardinality((SELECT verlauf FROM auftrag.auftraege WHERE id = a)) <> n + 1 THEN RAISE EXCEPTION '101.5: Verlauf doppelt'; END IF;

  j := auftrag.brain_lesen(b);
  IF j->>'stand' <> 'abgelaufen' OR j->>'vergeben_bis' IS NOT NULL THEN RAISE EXCEPTION '101.5: laufende ueberfaellige Zeile nicht abgelaufen'; END IF;
  IF auftrag.brain_lesen(c)->>'stand' <> 'offen' THEN RAISE EXCEPTION '101.5: Zeile in Frist veraendert'; END IF;
  j := auftrag.brain_lesen(d);
  IF j->>'stand' <> 'fertig' OR j->'ergebnis'->>'ok' <> 'true' THEN RAISE EXCEPTION '101.5: fertige Zeile veraendert'; END IF;
  -- Zeile von sales: NULL und unveraendert; unbekannt und NULL: NULL
  IF auftrag.brain_lesen(s) IS NOT NULL THEN RAISE EXCEPTION '101.5: Zeile von sales gelesen'; END IF;
  IF (SELECT stand FROM auftrag.auftraege WHERE id = s) <> 'offen' THEN RAISE EXCEPTION '101.5: Zeile von sales abgelaufen'; END IF;
  IF auftrag.brain_lesen(gen_random_uuid()) IS NOT NULL OR auftrag.brain_lesen(NULL) IS NOT NULL THEN
    RAISE EXCEPTION '101.5: unbekannte Kennung'; END IF;
  UPDATE auftrag.auftraege SET stand = 'abgelaufen' WHERE id = c;
END $$;

-- 6: rowboat_empfang_bekannt liest nur fertige Ergebnisse von rowboat/vorschlaege_anlegen im eigenen Laden
DO $$
DECLARE r text[];
BEGIN
  PERFORM pg_temp.s101('brain', 'rowboat', 'p101i', 'vorschlaege_anlegen', 'fertig', interval '1 hour', interval '0',
          '{"punkte":[{"punkt_id":"aaaaaaaaaaaaaaaa","stand":"neu"},{"punkt_id":"bbbbbbbbbbbbbbbb","stand":"schon_vorhanden"},"x",7]}');
  PERFORM pg_temp.s101('brain', 'rowboat', 'p101i', 'vorschlaege_anlegen', 'fehler', interval '1 hour', interval '0',
          '{"punkte":[{"punkt_id":"cccccccccccccccc"}]}');
  PERFORM pg_temp.s101('brain', 'rowboat', 'p101j', 'vorschlaege_anlegen', 'fertig', interval '1 hour', interval '0',
          '{"punkte":[{"punkt_id":"dddddddddddddddd"}]}');
  PERFORM pg_temp.s101('brain', 'rowboat', 'p101i', 'andere_art', 'fertig', interval '1 hour', interval '0',
          '{"punkte":[{"punkt_id":"eeeeeeeeeeeeeeee"}]}');
  PERFORM pg_temp.s101('brain', 'sales', 'p101i', 'vorschlaege_anlegen', 'fertig', interval '1 hour', interval '0',
          '{"punkte":[{"punkt_id":"ffffffffffffffff"}]}');
  PERFORM pg_temp.s101('brain', 'rowboat', 'p101i', 'vorschlaege_anlegen', 'fertig', interval '1 hour', interval '0',
          '{"punkte":"kein array"}');
  PERFORM pg_temp.s101('brain', 'rowboat', 'p101i', 'vorschlaege_anlegen', 'fertig', interval '1 hour', interval '0', '[]'::jsonb);

  r := auftrag.rowboat_empfang_bekannt('p101i', ARRAY['aaaaaaaaaaaaaaaa', 'bbbbbbbbbbbbbbbb', 'cccccccccccccccc',
        'dddddddddddddddd', 'eeeeeeeeeeeeeeee', 'ffffffffffffffff', '0000000000000000', 'aaaaaaaaaaaaaaaa']);
  IF r IS DISTINCT FROM ARRAY['aaaaaaaaaaaaaaaa', 'bbbbbbbbbbbbbbbb'] THEN
    RAISE EXCEPTION '101.6: bekannt liefert %', r; END IF;
  IF auftrag.rowboat_empfang_bekannt('p101i', ARRAY[]::text[]) IS DISTINCT FROM ARRAY[]::text[] THEN RAISE EXCEPTION '101.6: leere Liste'; END IF;
  IF auftrag.rowboat_empfang_bekannt('p101i', NULL) IS DISTINCT FROM ARRAY[]::text[] THEN RAISE EXCEPTION '101.6: NULL-Liste'; END IF;
  IF auftrag.rowboat_empfang_bekannt('  ', ARRAY['aaaaaaaaaaaaaaaa']) IS DISTINCT FROM ARRAY[]::text[] THEN RAISE EXCEPTION '101.6: leerer Laden'; END IF;
  IF auftrag.rowboat_empfang_bekannt('p101x', ARRAY['aaaaaaaaaaaaaaaa']) IS DISTINCT FROM ARRAY[]::text[] THEN RAISE EXCEPTION '101.6: fremder Laden'; END IF;
  RAISE NOTICE 'verify_101 ok';
END $$;
