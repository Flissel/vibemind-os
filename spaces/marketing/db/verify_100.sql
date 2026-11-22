-- verify_100: Datenbank-Huellen fuer das Gehirn. LAEUFT NUR in der zurueckgerollten migration_probe
-- (100 + verify_100, auf einer Datenbank mit 096 bis 099): die Bloecke legen Probezeilen an und seeden
-- Tageszaehler per INSERT - nie gegen die echte Datenbank ausfuehren. Jeder Block legt seine eigenen Zeilen
-- an und haengt nicht an Zeilen, die nur in Produktion stehen. now() steht in einer Transaktion still:
-- die Frist einer neuen Zeile ist daher genau now() + Frist, und alle Zeilen zaehlen zum selben Kalendertag.
-- Die Tagesgrenzen kommen zuletzt (Block 100.7), weil sie danach keine weitere Probe mehr zulassen.

-- 1: brain_anlegen legt eine Zeile des Gehirns richtig an; brain_arten ist genau wie vorgesehen gefuellt
DO $$
DECLARE a uuid; r record;
BEGIN
  IF (SELECT count(*) FROM auftrag.brain_arten) <> 1
     OR NOT EXISTS (SELECT 1 FROM auftrag.brain_arten WHERE an = 'brain' AND art = 'probe' AND version = 1) THEN
    RAISE EXCEPTION '100.1: brain_arten nicht genau (brain, probe, 1)'; END IF;
  a := auftrag.brain_anlegen('brain', 'probe', 1, 'p100', 'probe', '{}', 'plan_abc', 5);
  SELECT * INTO r FROM auftrag.auftraege WHERE id = a;
  IF r.id IS NULL THEN RAISE EXCEPTION '100.1: Zeile fehlt'; END IF;
  IF r.von <> 'brain' OR r.an <> 'brain' OR r.art <> 'probe' THEN RAISE EXCEPTION '100.1: von/an/art falsch'; END IF;
  IF r.stand <> 'offen' THEN RAISE EXCEPTION '100.1: Stand nicht offen'; END IF;
  IF r.art_version IS DISTINCT FROM 1 THEN RAISE EXCEPTION '100.1: art_version falsch'; END IF;
  IF r.plan_id IS DISTINCT FROM 'plan_abc' THEN RAISE EXCEPTION '100.1: plan_id falsch'; END IF;
  IF r.tiefe <> 0 OR r.wurzel_id <> a OR r.eltern_id IS NOT NULL THEN RAISE EXCEPTION '100.1: Wurzel falsch'; END IF;
  IF NOT (r.frist_bis > now() + interval '4 minutes' AND r.frist_bis < now() + interval '6 minutes') THEN
    RAISE EXCEPTION '100.1: Frist nicht etwa 5 Minuten'; END IF;
  IF array_length(r.verlauf, 1) <> 1 OR r.verlauf[1]->>'stand' <> 'offen' THEN
    RAISE EXCEPTION '100.1: Verlauf'; END IF;
  -- plan_id darf fehlen
  a := auftrag.brain_anlegen('brain', 'probe', 1, 'p100', 'probe', NULL, NULL, 120);
  IF (SELECT plan_id FROM auftrag.auftraege WHERE id = a) IS NOT NULL THEN RAISE EXCEPTION '100.1: plan_id NULL'; END IF;
  IF (SELECT eingabe FROM auftrag.auftraege WHERE id = a) <> '{}'::jsonb THEN RAISE EXCEPTION '100.1: eingabe NULL'; END IF;
END $$;

-- 2: die vier Eingabe-Ablehnungen, jede mit ihrem Text und ohne neue Zeile; fremde Art (sales) wird abgelehnt
DO $$
DECLARE n int; v_ok boolean;
BEGIN
  n := (SELECT count(*) FROM auftrag.auftraege);

  -- Art/Version/Empfaenger nicht in brain_arten
  v_ok := false;
  BEGIN
    PERFORM auftrag.brain_anlegen('brain', 'unbekannt', 1, 'p100', 'probe', '{}', NULL, 5);
    v_ok := true;
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Unbekannte Auftragsart' THEN RAISE EXCEPTION '100.2: falscher Text (Art): %', left(SQLERRM, 80); END IF;
  END;
  IF v_ok THEN RAISE EXCEPTION '100.2: unbekannte Art angenommen'; END IF;
  IF (SELECT count(*) FROM auftrag.auftraege) <> n THEN RAISE EXCEPTION '100.2: Zeilen nach Art-Ablehnung'; END IF;

  v_ok := false;
  BEGIN
    PERFORM auftrag.brain_anlegen('brain', 'probe', 2, 'p100', 'probe', '{}', NULL, 5);
    v_ok := true;
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Unbekannte Auftragsart' THEN RAISE EXCEPTION '100.2: falscher Text (Version): %', left(SQLERRM, 80); END IF;
  END;
  IF v_ok THEN RAISE EXCEPTION '100.2: unbekannte Version angenommen'; END IF;

  v_ok := false;
  BEGIN
    PERFORM auftrag.brain_anlegen('sales', 'post_pruefen', 1, 'p100', 'probe', '{}', NULL, 5);
    v_ok := true;
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Unbekannte Auftragsart' THEN RAISE EXCEPTION '100.2: falscher Text (sales): %', left(SQLERRM, 80); END IF;
  END;
  IF v_ok THEN RAISE EXCEPTION '100.2: Art von sales ueber brain_anlegen angenommen'; END IF;
  IF (SELECT count(*) FROM auftrag.auftraege) <> n THEN RAISE EXCEPTION '100.2: Zeilen nach Art/Version/sales'; END IF;

  -- Laden leer, Mandant leer (auch nur Leerzeichen)
  v_ok := false;
  BEGIN
    PERFORM auftrag.brain_anlegen('brain', 'probe', 1, '', 'probe', '{}', NULL, 5);
    v_ok := true;
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Auftrag braucht Laden und Mandant' THEN RAISE EXCEPTION '100.2: falscher Text (Laden): %', left(SQLERRM, 80); END IF;
  END;
  IF v_ok THEN RAISE EXCEPTION '100.2: leerer Laden angenommen'; END IF;

  v_ok := false;
  BEGIN
    PERFORM auftrag.brain_anlegen('brain', 'probe', 1, 'p100', '   ', '{}', NULL, 5);
    v_ok := true;
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Auftrag braucht Laden und Mandant' THEN RAISE EXCEPTION '100.2: falscher Text (Mandant): %', left(SQLERRM, 80); END IF;
  END;
  IF v_ok THEN RAISE EXCEPTION '100.2: leerer Mandant angenommen'; END IF;
  IF (SELECT count(*) FROM auftrag.auftraege) <> n THEN RAISE EXCEPTION '100.2: Zeilen nach Laden/Mandant'; END IF;

  -- Frist 0, 121 und NULL
  v_ok := false;
  BEGIN
    PERFORM auftrag.brain_anlegen('brain', 'probe', 1, 'p100', 'probe', '{}', NULL, 0);
    v_ok := true;
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Frist ausserhalb 1 bis 120 Minuten' THEN RAISE EXCEPTION '100.2: falscher Text (Frist 0): %', left(SQLERRM, 80); END IF;
  END;
  IF v_ok THEN RAISE EXCEPTION '100.2: Frist 0 angenommen'; END IF;

  v_ok := false;
  BEGIN
    PERFORM auftrag.brain_anlegen('brain', 'probe', 1, 'p100', 'probe', '{}', NULL, 121);
    v_ok := true;
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Frist ausserhalb 1 bis 120 Minuten' THEN RAISE EXCEPTION '100.2: falscher Text (Frist 121): %', left(SQLERRM, 80); END IF;
  END;
  IF v_ok THEN RAISE EXCEPTION '100.2: Frist 121 angenommen'; END IF;

  v_ok := false;
  BEGIN
    PERFORM auftrag.brain_anlegen('brain', 'probe', 1, 'p100', 'probe', '{}', NULL, NULL);
    v_ok := true;
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Frist ausserhalb 1 bis 120 Minuten' THEN RAISE EXCEPTION '100.2: falscher Text (Frist NULL): %', left(SQLERRM, 80); END IF;
  END;
  IF v_ok THEN RAISE EXCEPTION '100.2: Frist NULL angenommen'; END IF;
  IF (SELECT count(*) FROM auftrag.auftraege) <> n THEN RAISE EXCEPTION '100.2: Zeilen nach Frist'; END IF;

  -- Plan-Kennung: Sonderzeichen, zu lang (65), leer
  v_ok := false;
  BEGIN
    PERFORM auftrag.brain_anlegen('brain', 'probe', 1, 'p100', 'probe', '{}', 'plan abc!', 5);
    v_ok := true;
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Plan-Kennung ungueltig' THEN RAISE EXCEPTION '100.2: falscher Text (Plan Zeichen): %', left(SQLERRM, 80); END IF;
  END;
  IF v_ok THEN RAISE EXCEPTION '100.2: Plan-Kennung mit Sonderzeichen angenommen'; END IF;

  v_ok := false;
  BEGIN
    PERFORM auftrag.brain_anlegen('brain', 'probe', 1, 'p100', 'probe', '{}', repeat('a', 65), 5);
    v_ok := true;
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Plan-Kennung ungueltig' THEN RAISE EXCEPTION '100.2: falscher Text (Plan lang): %', left(SQLERRM, 80); END IF;
  END;
  IF v_ok THEN RAISE EXCEPTION '100.2: Plan-Kennung mit 65 Zeichen angenommen'; END IF;

  v_ok := false;
  BEGIN
    PERFORM auftrag.brain_anlegen('brain', 'probe', 1, 'p100', 'probe', '{}', '', 5);
    v_ok := true;
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Plan-Kennung ungueltig' THEN RAISE EXCEPTION '100.2: falscher Text (Plan leer): %', left(SQLERRM, 80); END IF;
  END;
  IF v_ok THEN RAISE EXCEPTION '100.2: leere Plan-Kennung angenommen'; END IF;
  IF (SELECT count(*) FROM auftrag.auftraege) <> n THEN RAISE EXCEPTION '100.2: Zeilen nach Plan-Kennung'; END IF;

  -- 64 Zeichen gehen (Grenze), danach hat sich genau eine Zeile angefuegt
  PERFORM auftrag.brain_anlegen('brain', 'probe', 1, 'p100', 'probe', '{}', repeat('a', 64), 5);
  IF (SELECT count(*) FROM auftrag.auftraege) <> n + 1 THEN RAISE EXCEPTION '100.2: Plan-Kennung mit 64 Zeichen'; END IF;
END $$;

-- 3: brain_probe_erledigen: erster Aufruf true (Stand fertig, ergebnis.probe), zweiter false, fremde Zeile false
DO $$
DECLARE a uuid; s uuid; n int;
BEGIN
  a := auftrag.brain_anlegen('brain', 'probe', 1, 'p100', 'probe', '{}', NULL, 5);
  n := array_length((SELECT verlauf FROM auftrag.auftraege WHERE id = a), 1);
  IF NOT auftrag.brain_probe_erledigen(a) THEN RAISE EXCEPTION '100.3: erster Aufruf nicht true'; END IF;
  IF (SELECT stand FROM auftrag.auftraege WHERE id = a) <> 'fertig' THEN RAISE EXCEPTION '100.3: Stand nicht fertig'; END IF;
  IF (SELECT ergebnis->>'probe' FROM auftrag.auftraege WHERE id = a) <> 'true' THEN
    RAISE EXCEPTION '100.3: ergebnis.probe'; END IF;
  IF array_length((SELECT verlauf FROM auftrag.auftraege WHERE id = a), 1) <> n + 1
     OR (SELECT verlauf[cardinality(verlauf)]->>'stand' FROM auftrag.auftraege WHERE id = a) <> 'fertig' THEN
    RAISE EXCEPTION '100.3: Verlauf'; END IF;
  IF auftrag.brain_probe_erledigen(a) THEN RAISE EXCEPTION '100.3: zweiter Aufruf nicht false'; END IF;
  -- unbekannte Kennung und fremde Zeile (von sales) bleiben unberuehrt
  IF auftrag.brain_probe_erledigen(gen_random_uuid()) THEN RAISE EXCEPTION '100.3: unbekannte Kennung'; END IF;
  s := auftrag.anlegen('sales', 'marketing', 'p100', 'probe', 'reel', '{}');
  IF auftrag.brain_probe_erledigen(s) THEN RAISE EXCEPTION '100.3: fremde Zeile erledigt'; END IF;
  IF (SELECT stand FROM auftrag.auftraege WHERE id = s) <> 'offen' THEN RAISE EXCEPTION '100.3: fremde Zeile veraendert'; END IF;
END $$;

-- 4: brain_lesen liefert die eigene Zeile als JSON, fuer fremde und unbekannte Zeilen NULL
DO $$
DECLARE a uuid; s uuid; j jsonb;
BEGIN
  a := auftrag.brain_anlegen('brain', 'probe', 1, 'p100', 'probe', '{}', 'plan_lesen', 5);
  j := auftrag.brain_lesen(a);
  IF j IS NULL OR j->>'id' <> a::text OR j->>'von' <> 'brain' OR j->>'plan_id' <> 'plan_lesen' THEN
    RAISE EXCEPTION '100.4: eigene Zeile nicht gelesen'; END IF;
  s := auftrag.anlegen('sales', 'marketing', 'p100', 'probe', 'reel', '{}');
  IF auftrag.brain_lesen(s) IS NOT NULL THEN RAISE EXCEPTION '100.4: Zeile von sales gelesen'; END IF;
  IF auftrag.brain_lesen(gen_random_uuid()) IS NOT NULL THEN RAISE EXCEPTION '100.4: unbekannte Kennung'; END IF;
  IF auftrag.brain_lesen(NULL) IS NOT NULL THEN RAISE EXCEPTION '100.4: NULL-Kennung'; END IF;
END $$;

-- 5: Altbestand unberuehrt: reel geht, post_pruefen bleibt fuer sales abgelehnt (Funktion und CHECK)
DO $$
DECLARE a uuid; v_ok boolean;
BEGIN
  a := auftrag.anlegen('sales', 'marketing', 'p100', 'probe', 'reel', '{}');
  IF (SELECT art || stand FROM auftrag.auftraege WHERE id = a) <> 'reeloffen' THEN
    RAISE EXCEPTION '100.5: Reel falsch angelegt'; END IF;
  IF (SELECT art_version IS NOT NULL OR plan_id IS NOT NULL FROM auftrag.auftraege WHERE id = a) THEN
    RAISE EXCEPTION '100.5: neue Spalten bei Altzeile nicht NULL'; END IF;

  v_ok := false;
  BEGIN
    PERFORM auftrag.anlegen('sales', 'marketing', 'p100', 'probe', 'post_pruefen', '{}');
    v_ok := true;
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Unbekannte Art des Auftrags' THEN RAISE EXCEPTION '100.5: falscher Text: %', left(SQLERRM, 80); END IF;
  END;
  IF v_ok THEN RAISE EXCEPTION '100.5: post_pruefen ueber anlegen angenommen'; END IF;

  v_ok := false;
  BEGIN
    INSERT INTO auftrag.auftraege (von, an, laden, mandant, art, wurzel_id, frist_bis)
    VALUES ('sales', 'marketing', 'p100', 'probe', 'post_pruefen', gen_random_uuid(), now() + interval '1 hour');
    v_ok := true;
  EXCEPTION WHEN check_violation THEN
    IF SQLERRM NOT LIKE '%auftraege_art_chk%' THEN RAISE EXCEPTION '100.5: falsche CHECK-Verletzung: %', left(SQLERRM, 120); END IF;
  END;
  IF v_ok THEN RAISE EXCEPTION '100.5: direkter INSERT von sales mit post_pruefen angenommen'; END IF;
END $$;

-- 6: Rechte der Rolle brain_app
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'brain_app') THEN
    RAISE EXCEPTION '100.6: Rolle brain_app fehlt'; END IF;
  IF (SELECT rolcanlogin FROM pg_roles WHERE rolname = 'brain_app') THEN
    RAISE EXCEPTION '100.6: brain_app darf sich anmelden'; END IF;
  IF NOT has_schema_privilege('brain_app', 'auftrag', 'USAGE') THEN
    RAISE EXCEPTION '100.6: brain_app ohne USAGE auf auftrag'; END IF;
  IF NOT has_function_privilege('brain_app', 'auftrag.brain_anlegen(text,text,int,text,text,jsonb,text,int)', 'EXECUTE') THEN
    RAISE EXCEPTION '100.6: brain_app darf brain_anlegen nicht'; END IF;
  IF NOT has_function_privilege('brain_app', 'auftrag.brain_lesen(uuid)', 'EXECUTE') THEN
    RAISE EXCEPTION '100.6: brain_app darf brain_lesen nicht'; END IF;
  IF NOT has_function_privilege('brain_app', 'auftrag.brain_probe_erledigen(uuid)', 'EXECUTE') THEN
    RAISE EXCEPTION '100.6: brain_app darf brain_probe_erledigen nicht'; END IF;
  IF has_function_privilege('brain_app', 'auftrag.anlegen(text,text,text,text,text,jsonb,uuid)', 'EXECUTE') THEN
    RAISE EXCEPTION '100.6: brain_app darf anlegen'; END IF;
  IF has_function_privilege('brain_app', 'auftrag.naechster(text,interval)', 'EXECUTE') THEN
    RAISE EXCEPTION '100.6: brain_app darf naechster'; END IF;
  IF has_function_privilege('brain_app', 'auftrag.stand_setzen(uuid,text,text,text)', 'EXECUTE') THEN
    RAISE EXCEPTION '100.6: brain_app darf stand_setzen'; END IF;
  IF has_table_privilege('brain_app', 'auftrag.auftraege', 'SELECT') THEN
    RAISE EXCEPTION '100.6: brain_app darf auftraege lesen'; END IF;
  IF has_table_privilege('brain_app', 'auftrag.brain_arten', 'SELECT') THEN
    RAISE EXCEPTION '100.6: brain_app darf brain_arten lesen'; END IF;
  IF has_function_privilege('public', 'auftrag.brain_anlegen(text,text,int,text,text,jsonb,text,int)', 'EXECUTE')
     OR has_function_privilege('public', 'auftrag.brain_lesen(uuid)', 'EXECUTE')
     OR has_function_privilege('public', 'auftrag.brain_probe_erledigen(uuid)', 'EXECUTE') THEN
    RAISE EXCEPTION '100.6: PUBLIC darf eine Gehirn-Funktion'; END IF;
  -- sales_app bekommt durch 100 nichts dazu
  IF has_function_privilege('sales_app', 'auftrag.brain_anlegen(text,text,int,text,text,jsonb,text,int)', 'EXECUTE') THEN
    RAISE EXCEPTION '100.6: sales_app darf brain_anlegen'; END IF;
  IF NOT has_function_privilege('sales_app', 'auftrag.sales_anlegen(text,text,text,text,jsonb,uuid)', 'EXECUTE') THEN
    RAISE EXCEPTION '100.6: sales_anlegen verloren'; END IF;
END $$;

-- 7: Tagesgrenzen. Die Zeilen bis kurz unter die Grenze werden direkt per INSERT gesetzt (als Probe-Ersatz fuer
-- viele echte Aufrufe), die Ablehnung selbst kommt aus brain_anlegen. Alles Seeden zaehlt zum heutigen Tag,
-- weil now() in der Transaktion still steht.
DO $$
DECLARE n int; c int; v_ok boolean;
BEGIN
  -- Proben: bis 49 auffuellen, die 50. geht, die 51. nicht
  c := (SELECT count(*) FROM auftrag.auftraege
         WHERE von = 'brain' AND art = 'probe' AND (angelegt AT TIME ZONE 'Europe/Berlin')::date = (now() AT TIME ZONE 'Europe/Berlin')::date);
  IF c < 49 THEN
    INSERT INTO auftrag.auftraege (id, von, an, laden, mandant, art, wurzel_id, frist_bis)
    SELECT i.id, 'brain', 'brain', 'p100', 'probe', 'probe', i.id, now() + interval '1 hour'
      FROM (SELECT gen_random_uuid() AS id FROM generate_series(1, 49 - c)) i;
  END IF;
  IF c < 50 THEN
    PERFORM auftrag.brain_anlegen('brain', 'probe', 1, 'p100', 'probe', '{}', NULL, 5);
  END IF;
  n := (SELECT count(*) FROM auftrag.auftraege);
  v_ok := false;
  BEGIN
    PERFORM auftrag.brain_anlegen('brain', 'probe', 1, 'p100', 'probe', '{}', NULL, 5);
    v_ok := true;
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Tagesgrenze erreicht: hoechstens 50 Proben pro Tag' THEN
      RAISE EXCEPTION '100.7: falscher Text (Proben): %', left(SQLERRM, 80); END IF;
  END;
  IF v_ok THEN RAISE EXCEPTION '100.7: 51. Probe angenommen'; END IF;
  IF (SELECT count(*) FROM auftrag.auftraege) <> n THEN RAISE EXCEPTION '100.7: Zeilen nach Probengrenze'; END IF;

  -- andere Arten des Gehirns: eigene Art nur fuer diesen Test in brain_arten (wird zurueckgerollt),
  -- bis 59 auffuellen, die 60. geht, die 61. nicht. Die volle Probengrenze beruehrt sie nicht.
  INSERT INTO auftrag.brain_arten (an, art, version) VALUES ('brain', 'p100_test', 1);
  c := (SELECT count(*) FROM auftrag.auftraege
         WHERE von = 'brain' AND art <> 'probe' AND (angelegt AT TIME ZONE 'Europe/Berlin')::date = (now() AT TIME ZONE 'Europe/Berlin')::date);
  IF c < 59 THEN
    INSERT INTO auftrag.auftraege (id, von, an, laden, mandant, art, wurzel_id, frist_bis)
    SELECT i.id, 'brain', 'brain', 'p100', 'probe', 'p100_test', i.id, now() + interval '1 hour'
      FROM (SELECT gen_random_uuid() AS id FROM generate_series(1, 59 - c)) i;
  END IF;
  IF c < 60 THEN
    PERFORM auftrag.brain_anlegen('brain', 'p100_test', 1, 'p100', 'probe', '{}', NULL, 5);
  END IF;
  n := (SELECT count(*) FROM auftrag.auftraege);
  v_ok := false;
  BEGIN
    PERFORM auftrag.brain_anlegen('brain', 'p100_test', 1, 'p100', 'probe', '{}', NULL, 5);
    v_ok := true;
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Tagesgrenze erreicht: hoechstens 60 Auftraege des Gehirns pro Tag' THEN
      RAISE EXCEPTION '100.7: falscher Text (Tag): %', left(SQLERRM, 80); END IF;
  END;
  IF v_ok THEN RAISE EXCEPTION '100.7: 61. Auftrag des Gehirns angenommen'; END IF;
  IF (SELECT count(*) FROM auftrag.auftraege) <> n THEN RAISE EXCEPTION '100.7: Zeilen nach Tagesgrenze'; END IF;

  -- die Grenze des Gehirns schliesst Sales nicht aus: sales hat sein eigenes Konto
  PERFORM auftrag.anlegen('sales', 'marketing', 'p100z', 'probe', 'reel', '{}');
  RAISE NOTICE 'verify_100 ok';
END $$;
