-- verify_098: Auftragsbuch nimmt art video mit Stand werkbank. LAEUFT NUR in der zurueckgerollten
-- migration_probe (098 + verify_098): die Bloecke stellen fremde offene Auftraege beiseite und legen
-- Probezeilen an - nie gegen die echte Datenbank ausfuehren. now() steht in einer Transaktion still,
-- darum werden Lease und Frist per UPDATE in die Vergangenheit gelegt.

-- 1: anlegen mit art video, Tagesgrenze 2 je von+laden, Reel bleibt davon unberuehrt
DO $$
DECLARE a uuid; b uuid;
BEGIN
  a := auftrag.anlegen('sales', 'marketing', 'p098', 'probe', 'video', '{"thema":"x"}');
  IF (SELECT art || stand FROM auftrag.auftraege WHERE id = a) <> 'videooffen' THEN
    RAISE EXCEPTION '098.1: Video-Auftrag falsch angelegt'; END IF;
  b := auftrag.anlegen('sales', 'marketing', 'p098', 'probe', 'video', '{}');
  BEGIN
    PERFORM auftrag.anlegen('sales', 'marketing', 'p098', 'probe', 'video', '{}');
    RAISE EXCEPTION '098.1: dritter Video-Auftrag wurde angenommen';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM NOT LIKE '%2 Video-Auftraege%' THEN RAISE; END IF;
  END;
  -- anderer Laden zaehlt eigen, Reel desselben Ladens geht noch
  PERFORM auftrag.anlegen('sales', 'marketing', 'p098b', 'probe', 'video', '{}');
  PERFORM auftrag.anlegen('sales', 'marketing', 'p098', 'probe', 'reel', '{}');
  BEGIN
    PERFORM auftrag.anlegen('sales', 'marketing', 'p098', 'probe', 'podcast', '{}');
    RAISE EXCEPTION '098.1: unbekannte art angenommen';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM LIKE '098.1:%' THEN RAISE; END IF;
  END;
END $$;

-- 2 + 3: naechster ueberspringt Video, video_naechster vergibt mit Lease und zwei Versuchen
DO $$
DECLARE a uuid; r jsonb;
BEGIN
  UPDATE auftrag.auftraege SET stand = 'abgebrochen' WHERE stand IN ('offen', 'in_arbeit');
  a := auftrag.anlegen('brain', 'marketing', 'p098c', 'probe', 'video', '{"thema":"v"}');
  IF auftrag.naechster('marketing', interval '10 min') IS NOT NULL THEN
    RAISE EXCEPTION '098.2: naechster hat einen Video-Auftrag vergeben'; END IF;
  IF (SELECT stand || versuche FROM auftrag.auftraege WHERE id = a) <> 'offen0' THEN
    RAISE EXCEPTION '098.2: naechster hat den Video-Auftrag angefasst'; END IF;
  r := auftrag.video_naechster(interval '10 min');
  IF r IS NULL OR (r->>'id')::uuid <> a OR (r->>'versuch')::int <> 1 OR r->>'art' <> 'video' THEN
    RAISE EXCEPTION '098.2: Uebernahme'; END IF;
  IF (SELECT stand || versuche FROM auftrag.auftraege WHERE id = a) <> 'in_arbeit1' THEN
    RAISE EXCEPTION '098.2: Stand/Versuche'; END IF;
  IF auftrag.video_naechster(interval '10 min') IS NOT NULL THEN RAISE EXCEPTION '098.2: Doppelvergabe'; END IF;

  -- 3: Lease abgelaufen -> zweiter Versuch, danach fehler
  UPDATE auftrag.auftraege SET vergeben_bis = now() - interval '1 min' WHERE id = a;
  r := auftrag.video_naechster(interval '10 min');
  IF r IS NULL OR (r->>'id')::uuid <> a OR (r->>'versuch')::int <> 2 THEN RAISE EXCEPTION '098.3: zweiter Versuch'; END IF;
  UPDATE auftrag.auftraege SET vergeben_bis = now() - interval '1 min' WHERE id = a;
  IF auftrag.video_naechster(interval '10 min') IS NOT NULL THEN RAISE EXCEPTION '098.3: dritter Versuch'; END IF;
  IF (SELECT stand || '/' || fehler FROM auftrag.auftraege WHERE id = a) <> 'fehler/Zwei Versuche ohne Ergebnis' THEN
    RAISE EXCEPTION '098.3: kein fehler nach zwei Versuchen'; END IF;
  BEGIN
    PERFORM auftrag.video_naechster(NULL);
    RAISE EXCEPTION '098.3: NULL-Lease angenommen';
  EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '098.3:%' THEN RAISE; END IF; END;
  -- ein Reel vergibt video_naechster nie
  PERFORM auftrag.anlegen('brain', 'marketing', 'p098c', 'probe', 'reel', '{}');
  IF auftrag.video_naechster(interval '10 min') IS NOT NULL THEN RAISE EXCEPTION '098.3: Reel von video_naechster vergeben'; END IF;
END $$;

-- 4: Stand werkbank nur fuer Video
DO $$
DECLARE v uuid; r uuid;
BEGIN
  UPDATE auftrag.auftraege SET stand = 'abgebrochen' WHERE stand IN ('offen', 'in_arbeit');
  v := auftrag.anlegen('brain', 'marketing', 'p098d', 'probe', 'video', '{}');
  r := auftrag.anlegen('brain', 'marketing', 'p098d', 'probe', 'reel', '{}');
  UPDATE auftrag.auftraege SET stand = 'in_arbeit' WHERE id IN (v, r);
  IF NOT auftrag.stand_setzen(v, 'in_arbeit', 'werkbank') THEN RAISE EXCEPTION '098.4: Video nach werkbank verweigert'; END IF;
  IF (SELECT stand || coalesce(vergeben_bis::text, '-') FROM auftrag.auftraege WHERE id = v) <> 'werkbank-' THEN
    RAISE EXCEPTION '098.4: Stand werkbank / Lease'; END IF;
  IF (SELECT verlauf[cardinality(verlauf)]->>'stand' FROM auftrag.auftraege WHERE id = v) <> 'werkbank' THEN
    RAISE EXCEPTION '098.4: Verlauf'; END IF;
  IF auftrag.stand_setzen(r, 'in_arbeit', 'werkbank') THEN RAISE EXCEPTION '098.4: Reel nach werkbank erlaubt'; END IF;
  IF (SELECT stand FROM auftrag.auftraege WHERE id = r) <> 'in_arbeit' THEN RAISE EXCEPTION '098.4: Reel veraendert'; END IF;
  -- Reel in werkbank (nur per UPDATE denkbar): kein Uebergang heraus
  UPDATE auftrag.auftraege SET stand = 'werkbank' WHERE id = r;
  IF auftrag.stand_setzen(r, 'werkbank', 'erzeugt') OR auftrag.stand_setzen(r, 'werkbank', 'fehler')
     OR auftrag.stand_setzen(r, 'werkbank', 'abgebrochen') THEN
    RAISE EXCEPTION '098.4: Reel aus werkbank heraus erlaubt'; END IF;
  -- Video: offen -> werkbank bleibt verboten, Ausgaenge gehen
  IF auftrag.stand_setzen(v, 'werkbank', 'wartet_freigabe') THEN RAISE EXCEPTION '098.4: werkbank->wartet_freigabe'; END IF;
  IF NOT auftrag.stand_setzen(v, 'werkbank', 'abgebrochen') THEN RAISE EXCEPTION '098.4: werkbank->abgebrochen'; END IF;
  IF auftrag.stand_setzen(v, 'offen', 'werkbank') THEN RAISE EXCEPTION '098.4: offen->werkbank'; END IF;
END $$;

-- 5 + 6: werkbank laeuft nie ab, ergebnis_mischen, Rundlauf werkbank -> erzeugt -> wartet_freigabe -> werkbank
DO $$
DECLARE v uuid; n int;
BEGIN
  UPDATE auftrag.auftraege SET stand = 'abgebrochen' WHERE stand IN ('offen', 'in_arbeit');
  v := auftrag.anlegen('brain', 'marketing', 'p098e', 'probe', 'video', '{}');
  UPDATE auftrag.auftraege SET stand = 'werkbank', frist_bis = now() - interval '1 day' WHERE id = v;
  PERFORM auftrag.ablaufen();
  IF (SELECT stand FROM auftrag.auftraege WHERE id = v) <> 'werkbank' THEN RAISE EXCEPTION '098.5: werkbank ist abgelaufen'; END IF;
  IF (SELECT count(*) FROM auftrag.laufende('marketing') x WHERE (x->>'id')::uuid = v) <> 0 THEN
    RAISE EXCEPTION '098.5: laufende zeigt werkbank'; END IF;
  IF auftrag.video_naechster(interval '10 min') IS NOT NULL OR auftrag.naechster('marketing', interval '10 min') IS NOT NULL THEN
    RAISE EXCEPTION '098.5: werkbank wurde vergeben'; END IF;

  IF NOT auftrag.ergebnis_mischen(v, '{"gate":"gliederung"}') THEN RAISE EXCEPTION '098.6: mischen in werkbank'; END IF;
  IF (SELECT ergebnis->>'gate' FROM auftrag.auftraege WHERE id = v) <> 'gliederung' THEN RAISE EXCEPTION '098.6: ergebnis'; END IF;
  IF NOT auftrag.stand_setzen(v, 'werkbank', 'erzeugt') THEN RAISE EXCEPTION '098.6: werkbank->erzeugt'; END IF;
  IF NOT auftrag.stand_setzen(v, 'erzeugt', 'wartet_freigabe') THEN RAISE EXCEPTION '098.6: erzeugt->wartet_freigabe'; END IF;
  IF NOT auftrag.stand_setzen(v, 'wartet_freigabe', 'werkbank') THEN RAISE EXCEPTION '098.6: wartet_freigabe->werkbank'; END IF;
  IF NOT auftrag.stand_setzen(v, 'werkbank', 'erzeugt') THEN RAISE EXCEPTION '098.6: zweites werkbank->erzeugt'; END IF;
  IF NOT auftrag.stand_setzen(v, 'erzeugt', 'werkbank') THEN RAISE EXCEPTION '098.6: erzeugt->werkbank'; END IF;
  IF NOT auftrag.stand_setzen(v, 'werkbank', 'fehler', 'x') THEN RAISE EXCEPTION '098.6: werkbank->fehler'; END IF;
  -- die sales_*-Huelle erreicht die neuen Uebergaenge nur fuer eigene Zeilen
  v := auftrag.sales_anlegen('marketing', 'p098f', 'probe', 'video', '{}');
  UPDATE auftrag.auftraege SET stand = 'in_arbeit' WHERE id = v;
  IF NOT auftrag.sales_stand_setzen(v, 'in_arbeit', 'werkbank') THEN RAISE EXCEPTION '098.6: sales_stand_setzen werkbank'; END IF;
  v := auftrag.anlegen('brain', 'sales', 'p098f', 'probe', 'video', '{}');
  UPDATE auftrag.auftraege SET stand = 'in_arbeit' WHERE id = v;
  IF auftrag.sales_stand_setzen(v, 'in_arbeit', 'werkbank') THEN RAISE EXCEPTION '098.6: sales_stand_setzen fremd'; END IF;
END $$;

-- 7: Rechte
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                  WHERE n.nspname = 'auftrag' AND p.proname = 'video_naechster') THEN
    RAISE EXCEPTION '098.7: video_naechster fehlt'; END IF;
  IF has_function_privilege('sales_app', 'auftrag.video_naechster(interval)', 'execute') THEN
    RAISE EXCEPTION '098.7: sales_app darf video_naechster'; END IF;
  IF has_function_privilege('public', 'auftrag.video_naechster(interval)', 'execute') THEN
    RAISE EXCEPTION '098.7: PUBLIC darf video_naechster'; END IF;
  IF has_function_privilege('sales_app', 'auftrag.anlegen(text,text,text,text,text,jsonb,uuid)', 'execute')
     OR has_function_privilege('sales_app', 'auftrag.naechster(text,interval)', 'execute') THEN
    RAISE EXCEPTION '098.7: sales_app darf allgemeine Funktionen'; END IF;
  IF NOT has_function_privilege('sales_app', 'auftrag.sales_anlegen(text,text,text,text,jsonb,uuid)', 'execute') THEN
    RAISE EXCEPTION '098.7: sales_anlegen verloren'; END IF;
  -- SECURITY DEFINER und search_path der neu geschriebenen Funktionen
  IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
       WHERE n.nspname = 'auftrag'
         AND p.proname IN ('anlegen', 'naechster', 'video_naechster', 'ergebnis_mischen', 'stand_setzen')
         AND p.prosecdef AND p.proconfig @> ARRAY['search_path=auftrag, pg_temp']) <> 5 THEN
    RAISE EXCEPTION '098.7: SECURITY DEFINER / search_path'; END IF;
END $$;

-- 8: Reel unveraendert (Kopie der Pruefung 4 aus verify_096 gegen die neuen Funktionskoerper)
DO $$
DECLARE a uuid; r jsonb;
BEGIN
  UPDATE auftrag.auftraege SET stand = 'abgebrochen' WHERE stand IN ('offen', 'in_arbeit');
  a := auftrag.anlegen('sales', 'marketing', 'p098r', 'probe', 'reel', '{"thema":"y"}');
  r := auftrag.naechster('marketing', interval '10 min');
  IF r IS NULL OR (r->>'id')::uuid <> a OR (r->>'versuch')::int <> 1 THEN RAISE EXCEPTION '098.8: Uebernahme'; END IF;
  IF (SELECT stand || versuche FROM auftrag.auftraege WHERE id = a) <> 'in_arbeit1' THEN
    RAISE EXCEPTION '098.8: Stand/Versuche'; END IF;
  IF auftrag.naechster('marketing', interval '10 min') IS NOT NULL THEN RAISE EXCEPTION '098.8: Doppelvergabe'; END IF;

  IF NOT auftrag.verlaengern(a, interval '20 min') THEN RAISE EXCEPTION '098.8: verlaengern'; END IF;
  IF NOT auftrag.ergebnis_mischen(a, '{"video":"abc"}') THEN RAISE EXCEPTION '098.8: mischen'; END IF;
  UPDATE auftrag.auftraege SET vergeben_bis = now() - interval '1 min' WHERE id = a;
  IF auftrag.naechster('marketing', interval '10 min') IS NOT NULL THEN
    RAISE EXCEPTION '098.8: Auftrag mit Video neu uebernommen'; END IF;
  IF (SELECT count(*) FROM auftrag.laufende('marketing') x WHERE (x->>'id')::uuid = a) <> 1 THEN
    RAISE EXCEPTION '098.8: laufende fehlt'; END IF;
  IF (SELECT versuche FROM auftrag.auftraege WHERE id = a) <> 1 THEN RAISE EXCEPTION '098.8: versuche geaendert'; END IF;

  UPDATE auftrag.auftraege SET ergebnis = '{}', versuche = 2 WHERE id = a;
  IF auftrag.naechster('marketing', interval '10 min') IS NOT NULL THEN RAISE EXCEPTION '098.8: dritter Versuch'; END IF;
  IF (SELECT stand || '/' || fehler FROM auftrag.auftraege WHERE id = a) <> 'fehler/Zwei Versuche ohne Ergebnis' THEN
    RAISE EXCEPTION '098.8: kein fehler nach zwei Versuchen'; END IF;
  RAISE NOTICE 'verify_098 ok';
END $$;
