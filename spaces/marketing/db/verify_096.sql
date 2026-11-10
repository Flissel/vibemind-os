-- verify_096: Auftragsbuch. LAEUFT NUR in der zurueckgerollten migration_probe: die Bloecke stellen
-- fremde offene Auftraege beiseite und legen Probezeilen an - nie gegen die echte Datenbank ausfuehren.
-- Jeder Block legt seine eigenen Zeilen an; fremde offene Zeilen werden vorab beiseite gestellt
-- (das Zurueckrollen macht es ungeschehen). now() steht in einer Transaktion still, darum
-- werden Lease und Frist per UPDATE in die Vergangenheit gelegt.

-- 1 + 3 + 5: anlegen, Tiefe, Standwechsel
DO $$
DECLARE a uuid; b uuid; c uuid; r record; n int;
BEGIN
  UPDATE auftrag.auftraege SET stand = 'abgebrochen' WHERE stand IN ('offen', 'in_arbeit');
  a := auftrag.anlegen('sales', 'marketing', 'p096a', 'probe', 'reel', '{"thema":"x"}');
  SELECT * INTO r FROM auftrag.auftraege WHERE id = a;
  IF r.stand <> 'offen' OR r.tiefe <> 0 OR r.wurzel_id <> a THEN
    RAISE EXCEPTION '096.1: Wurzel falsch angelegt'; END IF;
  IF r.frist_bis < now() + interval '119 minutes' THEN RAISE EXCEPTION '096.1: Frist'; END IF;
  IF array_length(r.verlauf, 1) <> 1 OR r.verlauf[1]->>'stand' <> 'offen' THEN
    RAISE EXCEPTION '096.1: Verlauf'; END IF;

  b := auftrag.anlegen('sales', 'marketing', 'p096a', 'probe', 'reel', '{}', a);
  c := auftrag.anlegen('sales', 'marketing', 'p096a', 'probe', 'reel', '{}', b);
  SELECT * INTO r FROM auftrag.auftraege WHERE id = c;
  IF r.tiefe <> 2 OR r.wurzel_id <> a OR r.eltern_id <> b THEN RAISE EXCEPTION '096.3: Kind falsch'; END IF;
  BEGIN
    PERFORM auftrag.anlegen('sales', 'marketing', 'p096a', 'probe', 'reel', '{}', c);
    RAISE EXCEPTION '096.3: Tiefe 3 wurde angenommen';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM NOT LIKE '%Tiefe ueberschritten%' THEN RAISE; END IF;
  END;
  BEGIN
    PERFORM auftrag.anlegen('sales', 'fremd', 'p096a', 'probe', 'reel', '{}');
    RAISE EXCEPTION '096: unbekanntes an angenommen';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM LIKE '096:%' THEN RAISE; END IF;
  END;
  BEGIN
    PERFORM auftrag.anlegen('sales', 'marketing', 'p096a', 'probe', 'podcast', '{}');
    RAISE EXCEPTION '096: unbekannte art angenommen';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM LIKE '096:%' THEN RAISE; END IF;
  END;

  -- 5: Standwechsel
  UPDATE auftrag.auftraege SET stand = 'in_arbeit' WHERE id = a;
  IF auftrag.stand_setzen(a, 'offen', 'erzeugt') THEN RAISE EXCEPTION '096.5: falscher Stand wurde gewechselt'; END IF;
  n := array_length((SELECT verlauf FROM auftrag.auftraege WHERE id = a), 1);
  IF NOT auftrag.stand_setzen(a, 'in_arbeit', 'erzeugt') THEN RAISE EXCEPTION '096.5: Wechsel verweigert'; END IF;
  IF array_length((SELECT verlauf FROM auftrag.auftraege WHERE id = a), 1) <> n + 1 THEN
    RAISE EXCEPTION '096.5: Verlauf nicht um einen Eintrag laenger'; END IF;
  IF auftrag.stand_setzen(a, 'erzeugt', 'fertig') THEN RAISE EXCEPTION '096.5: verbotener Uebergang'; END IF;
  IF NOT auftrag.stand_setzen(a, 'erzeugt', 'fehler', repeat('x', 500)) THEN RAISE EXCEPTION '096.5: fehler'; END IF;
  IF length((SELECT fehler FROM auftrag.auftraege WHERE id = a)) <> 300 THEN
    RAISE EXCEPTION '096.5: Fehlertext nicht gekuerzt'; END IF;
END $$;

-- 2: Tagesgrenze
DO $$
DECLARE i int;
BEGIN
  FOR i IN 1..5 LOOP
    PERFORM auftrag.anlegen('sales', 'marketing', 'p096b', 'probe', 'reel', '{}');
  END LOOP;
  BEGIN
    PERFORM auftrag.anlegen('sales', 'marketing', 'p096b', 'probe', 'reel', '{}');
    RAISE EXCEPTION '096.2: sechster Auftrag wurde angenommen';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM NOT LIKE '%Tagesgrenze%' THEN RAISE; END IF;
  END;
  -- anderer Laden zaehlt eigen
  PERFORM auftrag.anlegen('sales', 'marketing', 'p096c', 'probe', 'reel', '{}');
END $$;

-- 4: naechster, Lease, Video, laufende, zwei Versuche
DO $$
DECLARE a uuid; r jsonb;
BEGIN
  UPDATE auftrag.auftraege SET stand = 'abgebrochen' WHERE stand IN ('offen', 'in_arbeit');
  a := auftrag.anlegen('sales', 'marketing', 'p096d', 'probe', 'reel', '{"thema":"y"}');
  r := auftrag.naechster('marketing', interval '10 min');
  IF r IS NULL OR (r->>'id')::uuid <> a OR (r->>'versuch')::int <> 1 THEN RAISE EXCEPTION '096.4: Uebernahme'; END IF;
  IF (SELECT stand || versuche FROM auftrag.auftraege WHERE id = a) <> 'in_arbeit1' THEN
    RAISE EXCEPTION '096.4: Stand/Versuche'; END IF;
  IF auftrag.naechster('marketing', interval '10 min') IS NOT NULL THEN RAISE EXCEPTION '096.4: Doppelvergabe'; END IF;

  IF NOT auftrag.verlaengern(a, interval '20 min') THEN RAISE EXCEPTION '096.4: verlaengern'; END IF;
  IF NOT auftrag.ergebnis_mischen(a, '{"video":"abc"}') THEN RAISE EXCEPTION '096.4: mischen'; END IF;
  UPDATE auftrag.auftraege SET vergeben_bis = now() - interval '1 min' WHERE id = a;
  IF auftrag.naechster('marketing', interval '10 min') IS NOT NULL THEN
    RAISE EXCEPTION '096.4: Auftrag mit Video neu uebernommen'; END IF;
  IF (SELECT count(*) FROM auftrag.laufende('marketing') x WHERE (x->>'id')::uuid = a) <> 1 THEN
    RAISE EXCEPTION '096.4: laufende fehlt'; END IF;
  IF (SELECT versuche FROM auftrag.auftraege WHERE id = a) <> 1 THEN RAISE EXCEPTION '096.4: versuche geaendert'; END IF;

  -- zwei Versuche ohne Ergebnis: dritte Uebernahme -> fehler
  UPDATE auftrag.auftraege SET ergebnis = '{}', versuche = 2 WHERE id = a;
  IF auftrag.naechster('marketing', interval '10 min') IS NOT NULL THEN RAISE EXCEPTION '096.4: dritter Versuch'; END IF;
  IF (SELECT stand || '/' || fehler FROM auftrag.auftraege WHERE id = a) <> 'fehler/Zwei Versuche ohne Ergebnis' THEN
    RAISE EXCEPTION '096.4: kein fehler nach zwei Versuchen'; END IF;
END $$;

-- 6: ablaufen, eigene, gemeldet, erinnert
DO $$
DECLARE a uuid; n int;
BEGIN
  a := auftrag.anlegen('sales', 'marketing', 'p096e', 'probe', 'reel', '{}');
  UPDATE auftrag.auftraege SET frist_bis = now() - interval '1 min' WHERE id = a;
  n := auftrag.ablaufen();
  IF n < 1 THEN RAISE EXCEPTION '096.6: ablaufen zaehlt nicht'; END IF;
  IF (SELECT stand FROM auftrag.auftraege WHERE id = a) <> 'abgelaufen' THEN RAISE EXCEPTION '096.6: Stand'; END IF;
  IF (SELECT verlauf[array_length(verlauf, 1)]->>'stand' FROM auftrag.auftraege WHERE id = a) <> 'abgelaufen' THEN
    RAISE EXCEPTION '096.6: Verlauf'; END IF;
  IF (SELECT count(*) FROM auftrag.eigene('sales', 'p096e', ARRAY['abgelaufen'])) <> 1 THEN RAISE EXCEPTION '096: eigene'; END IF;
  IF (SELECT count(*) FROM auftrag.eigene('rowboat', 'p096e', ARRAY['abgelaufen'])) <> 0 THEN RAISE EXCEPTION '096: eigene fremd'; END IF;
  IF NOT auftrag.gemeldet(a, 'abgelaufen') OR NOT auftrag.erinnert(a) THEN RAISE EXCEPTION '096: melden'; END IF;
  IF (SELECT gemeldet_stand FROM auftrag.auftraege WHERE id = a) <> 'abgelaufen' THEN RAISE EXCEPTION '096: gemeldet_stand'; END IF;
END $$;

-- 8: an, Eltern, Gesamtgrenze, sales_*-Huelle, Randfaelle
DO $$
DECLARE a uuid; b uuid; x uuid; i int; gesperrt boolean := false;
BEGIN
  UPDATE auftrag.auftraege SET stand = 'abgebrochen' WHERE stand IN ('offen', 'in_arbeit');
  -- gueltiges an ausser marketing
  PERFORM auftrag.anlegen('sales', 'rowboat', 'p096f', 'probe', 'reel', '{}');

  -- Eltern muessen zu demselben von und laden gehoeren
  a := auftrag.anlegen('sales', 'marketing', 'p096f', 'probe', 'reel', '{}');
  BEGIN
    PERFORM auftrag.anlegen('brain', 'marketing', 'p096f', 'probe', 'reel', '{}', a);
    RAISE EXCEPTION '096.8: fremder Eltern-Auftrag (von) angenommen';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM NOT LIKE '%Eltern-Auftrag gehoert einem anderen%' THEN RAISE; END IF; END;
  BEGIN
    PERFORM auftrag.anlegen('sales', 'marketing', 'p096g', 'probe', 'reel', '{}', a);
    RAISE EXCEPTION '096.8: fremder Eltern-Auftrag (laden) angenommen';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM NOT LIKE '%Eltern-Auftrag gehoert einem anderen%' THEN RAISE; END IF; END;

  -- Gesamtgrenze 20 je von und Tag ueber alle Laeden
  FOR i IN 1..40 LOOP
    BEGIN
      PERFORM auftrag.anlegen('rowboat', 'marketing', 'p096h' || (i / 5), 'probe', 'reel', '{}');
    EXCEPTION WHEN raise_exception THEN
      IF SQLERRM NOT LIKE '%Tagesgrenze%' THEN RAISE; END IF;
      IF SQLERRM LIKE '%und Laden%' THEN CONTINUE; END IF;
      gesperrt := true; EXIT;
    END;
  END LOOP;
  IF NOT gesperrt THEN RAISE EXCEPTION '096.8: Gesamtgrenze greift nicht'; END IF;
  IF (SELECT count(*) FROM auftrag.auftraege
       WHERE von = 'rowboat'
         AND (angelegt AT TIME ZONE 'Europe/Berlin')::date = (now() AT TIME ZONE 'Europe/Berlin')::date) <> 20 THEN
    RAISE EXCEPTION '096.8: Gesamtgrenze nicht bei 20'; END IF;

  -- sales_*: nur eigene Zeilen (von = sales)
  b := auftrag.sales_anlegen('marketing', 'p096i', 'probe', 'reel', '{}');
  IF (SELECT von FROM auftrag.auftraege WHERE id = b) <> 'sales' THEN RAISE EXCEPTION '096.8: sales_anlegen von'; END IF;
  IF (SELECT count(*) FROM auftrag.sales_eigene('p096i', NULL)) <> 1 THEN RAISE EXCEPTION '096.8: sales_eigene'; END IF;
  x := auftrag.anlegen('marketing', 'sales', 'p096i', 'probe', 'reel', '{}');
  UPDATE auftrag.auftraege SET stand = 'in_arbeit' WHERE id = x;
  IF auftrag.sales_stand_setzen(x, 'in_arbeit', 'erzeugt') THEN
    RAISE EXCEPTION '096.8: sales_stand_setzen auf fremder Zeile'; END IF;
  IF auftrag.sales_ergebnis_mischen(x, '{"a":1}') OR auftrag.sales_gemeldet(x, 'erzeugt') OR auftrag.sales_erinnert(x) THEN
    RAISE EXCEPTION '096.8: sales_* auf fremder Zeile'; END IF;
  IF (SELECT stand FROM auftrag.auftraege WHERE id = x) <> 'in_arbeit' THEN
    RAISE EXCEPTION '096.8: fremde Zeile veraendert'; END IF;
  UPDATE auftrag.auftraege SET stand = 'in_arbeit' WHERE id = b;
  IF NOT auftrag.sales_stand_setzen(b, 'in_arbeit', 'erzeugt') THEN RAISE EXCEPTION '096.8: sales_stand_setzen eigen'; END IF;
  IF NOT auftrag.sales_ergebnis_mischen(b, '{"a":1}') OR NOT auftrag.sales_gemeldet(b, 'erzeugt')
     OR NOT auftrag.sales_erinnert(b) THEN
    RAISE EXCEPTION '096.8: sales_* eigen'; END IF;

  -- Randfaelle: NULL-Stand, kein Objekt, NULL-Lease
  IF auftrag.stand_setzen(b, 'erzeugt', NULL) THEN RAISE EXCEPTION '096.8: NULL nach_stand'; END IF;
  IF auftrag.ergebnis_mischen(b, '[1]') OR auftrag.ergebnis_mischen(b, '"x"') THEN
    RAISE EXCEPTION '096.8: kein Objekt gemischt'; END IF;
  BEGIN
    PERFORM auftrag.naechster('marketing', NULL);
    RAISE EXCEPTION '096.8: NULL-Lease angenommen';
  EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '096.8:%' THEN RAISE; END IF; END;
END $$;

-- 9: naechster sucht weiter; Frist
DO $$
DECLARE x uuid; y uuid; z uuid; r jsonb;
BEGIN
  UPDATE auftrag.auftraege SET stand = 'abgebrochen' WHERE stand IN ('offen', 'in_arbeit');
  x := auftrag.anlegen('brain', 'marketing', 'p096j', 'probe', 'reel', '{}');
  y := auftrag.anlegen('brain', 'marketing', 'p096j', 'probe', 'reel', '{}');
  UPDATE auftrag.auftraege SET versuche = 2, angelegt = now() - interval '2 min' WHERE id = x;
  UPDATE auftrag.auftraege SET angelegt = now() - interval '1 min' WHERE id = y;
  r := auftrag.naechster('marketing', interval '10 min');
  IF r IS NULL OR (r->>'id')::uuid <> y THEN RAISE EXCEPTION '096.9: naechster sucht nicht weiter'; END IF;
  IF (SELECT stand || '/' || fehler FROM auftrag.auftraege WHERE id = x) <> 'fehler/Zwei Versuche ohne Ergebnis' THEN
    RAISE EXCEPTION '096.9: Altauftrag nicht auf fehler'; END IF;
  -- frist_bis <= now(): nicht vergeben
  z := auftrag.anlegen('brain', 'marketing', 'p096j', 'probe', 'reel', '{}');
  UPDATE auftrag.auftraege SET frist_bis = now() WHERE id = z;
  IF auftrag.naechster('marketing', interval '10 min') IS NOT NULL THEN
    RAISE EXCEPTION '096.9: Auftrag nach Fristende vergeben'; END IF;
END $$;

-- 7: Rechte
DO $$
DECLARE f record;
  erlaubt text[] := ARRAY['sales_anlegen', 'sales_eigene', 'sales_ergebnis_mischen',
                          'sales_stand_setzen', 'sales_gemeldet', 'sales_erinnert', 'ablaufen'];
BEGIN
  FOR f IN SELECT p.oid, p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'auftrag' LOOP
    IF has_function_privilege('sales_app', f.oid, 'execute') <> (f.proname = ANY (erlaubt)) THEN
      RAISE EXCEPTION '096.7: falsches Recht fuer sales_app auf auftrag.%', f.proname; END IF;
  END LOOP;
  IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
       WHERE n.nspname = 'auftrag' AND p.proname = ANY (erlaubt)) <> 7 THEN
    RAISE EXCEPTION '096.7: nicht alle sieben Funktionen vorhanden'; END IF;
  IF has_table_privilege('sales_app', 'auftrag.auftraege', 'select') THEN
    RAISE EXCEPTION '096.7: sales_app darf die Tabelle lesen'; END IF;
  IF has_function_privilege('sales_app', 'auftrag.anlegen(text,text,text,text,text,jsonb,uuid)', 'execute') THEN
    RAISE EXCEPTION '096.7: sales_app darf das allgemeine anlegen'; END IF;
  IF has_function_privilege('sales_app', 'auftrag.naechster(text,interval)', 'execute') THEN
    RAISE EXCEPTION '096.7: sales_app darf naechster'; END IF;
  RAISE NOTICE 'verify_096 ok';
END $$;
