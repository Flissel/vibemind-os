-- verify_099: Werkbank-Tabellen und Szenen-Zustandsmaschine. LAEUFT NUR in der zurueckgerollten
-- migration_probe (098 + 099 + verify_099): der erste Block stellt fremde offene Auftraege beiseite und legt
-- Probezeilen an - nie gegen die echte Datenbank ausfuehren. now() steht in einer Transaktion still,
-- darum werden Leasezeiten per UPDATE in die Vergangenheit gelegt. Zwei Auftraege insgesamt (die Tagesgrenze
-- von 20 je Absender zaehlt die echten Auftraege von heute mit): o1 = Zustandsmaschine, o2 = Zaehlung.

CREATE TEMP TABLE t099 (k text PRIMARY KEY, v uuid);
CREATE TEMP TABLE t099k (k text PRIMARY KEY, kennung text);

-- 0: zwei Video-Auftraege anlegen und uebernehmen (verschiedene Laeden wegen der Tagesgrenze 2 je Laden)
DO $$
DECLARE a uuid; b uuid; r jsonb;
BEGIN
  UPDATE auftrag.auftraege SET stand = 'abgebrochen' WHERE stand IN ('offen', 'in_arbeit');
  a := auftrag.anlegen('sales', 'marketing', 'p099a', 'probe', 'video', '{"thema":"Probe eins"}');
  r := auftrag.video_naechster(interval '10 min');
  IF (r->>'id')::uuid <> a THEN RAISE EXCEPTION '099.0: o1 nicht uebernommen'; END IF;
  b := auftrag.anlegen('sales', 'marketing', 'p099b', 'probe', 'video', '{"thema":"Probe zwei"}');
  r := auftrag.video_naechster(interval '10 min');
  IF (r->>'id')::uuid <> b THEN RAISE EXCEPTION '099.0: o2 nicht uebernommen'; END IF;
  INSERT INTO t099 VALUES ('o1', a), ('o2', b);
END $$;

-- Tabellen: Kennungsmuster und hoechstens ein lebender Vorschlag je (Auftrag, Szene, Tor)
DO $$
DECLARE a uuid := (SELECT v FROM t099 WHERE k = 'o1');
BEGIN
  INSERT INTO marketing.video_werke (auftrag, mandant) VALUES (a, 'probe');
  BEGIN
    INSERT INTO marketing.video_vorschlaege (kennung, auftrag, tor, fassung, stand)
    VALUES ('wb-ABCDEFGHIJ', a, 'gliederung', 1, 'wartend');
    RAISE EXCEPTION '099.T: falsche Kennung angenommen';
  EXCEPTION WHEN check_violation THEN NULL; END;
  INSERT INTO marketing.video_vorschlaege (kennung, auftrag, tor, fassung, stand)
  VALUES ('wb-aaaaaaaaaa', a, 'gliederung', 1, 'wartend');
  BEGIN
    INSERT INTO marketing.video_vorschlaege (kennung, auftrag, tor, fassung, stand)
    VALUES ('wb-bbbbbbbbbb', a, 'gliederung', 2, 'offen');
    RAISE EXCEPTION '099.T: zweiter lebender Vorschlag angenommen';
  EXCEPTION WHEN unique_violation THEN NULL; END;
  INSERT INTO marketing.video_vorschlaege (kennung, auftrag, tor, fassung, stand)
  VALUES ('wb-bbbbbbbbbb', a, 'gliederung', 2, 'erledigt');
  DELETE FROM marketing.video_vorschlaege WHERE auftrag = a;
  DELETE FROM marketing.video_werke WHERE auftrag = a;
END $$;

-- 1: werk_beginnen idempotent, Gliederung setzen
DO $$
DECLARE a uuid := (SELECT v FROM t099 WHERE k = 'o1'); k text;
BEGIN
  IF marketing.video_werk_beginnen(gen_random_uuid()) IS NOT NULL THEN
    RAISE EXCEPTION '099.1: unbekannter Auftrag angenommen'; END IF;
  IF marketing.video_werk_beginnen(a) IS NULL OR marketing.video_werk_beginnen(a) IS NULL THEN
    RAISE EXCEPTION '099.1: werk_beginnen liefert nichts'; END IF;
  IF (SELECT count(*) FROM marketing.video_werke WHERE auftrag = a) <> 1 THEN
    RAISE EXCEPTION '099.1: werk_beginnen legt mehr als eine Zeile an'; END IF;
  IF (SELECT mandant FROM marketing.video_werke WHERE auftrag = a) <> 'probe' THEN
    RAISE EXCEPTION '099.1: Mandant'; END IF;
  IF (marketing.video_werk_beginnen(a))->>'gliederung_stand' <> 'geplant' THEN
    RAISE EXCEPTION '099.1: Form von werk_beginnen'; END IF;

  k := marketing.video_gliederung_setzen(a,
    '{"titel":"Probe","stufen":[{"name":"Anfang","szenen":[{"kurz":"s1"},{"kurz":"s2"}]},{"name":"Ende","szenen":[{"kurz":"s3"}]}]}',
    '["a.md"]');
  IF k !~ '^wb-[a-z2-7]{10}$' THEN RAISE EXCEPTION '099.1: Kennung %', k; END IF;
  IF (SELECT stand FROM auftrag.auftraege WHERE id = a) <> 'werkbank' THEN
    RAISE EXCEPTION '099.1: Auftrag nicht auf werkbank'; END IF;
  IF (SELECT stand || fassung FROM marketing.video_vorschlaege WHERE kennung = k) <> 'wartend1' THEN
    RAISE EXCEPTION '099.1: Vorschlag nicht wartend'; END IF;
  IF (SELECT gliederung_stand || gliederung_fassung FROM marketing.video_werke WHERE auftrag = a) <> 'text_wartet1' THEN
    RAISE EXCEPTION '099.1: Gliederungsstand'; END IF;
  IF marketing.video_gliederung_setzen(a, '{"titel":"zweit"}', '[]') IS NOT NULL THEN
    RAISE EXCEPTION '099.1: zweite Gliederung aus text_wartet angenommen'; END IF;
  INSERT INTO t099k VALUES ('g1', k);
END $$;

-- 2: Gliederung nochmal -> geplant, neue Fassung 2, passt -> text_ok mit Szenen in Reihenfolge
DO $$
DECLARE a uuid := (SELECT v FROM t099 WHERE k = 'o1'); k1 text := (SELECT kennung FROM t099k WHERE k = 'g1'); k2 text; r jsonb;
BEGIN
  IF NOT marketing.video_wunsch_setzen(k1, 'kuerzer bitte') THEN RAISE EXCEPTION '099.2: Wunsch'; END IF;
  IF (SELECT gliederung_wunsch FROM marketing.video_werke WHERE auftrag = a) <> 'kuerzer bitte' THEN
    RAISE EXCEPTION '099.2: Wunsch nicht gespeichert'; END IF;
  r := marketing.video_entscheiden(k1, 'nochmal', 'rowboat');
  IF NOT (r->>'ok')::boolean OR r->>'stand' <> 'geplant' THEN RAISE EXCEPTION '099.2: nochmal %', r; END IF;
  IF (SELECT gliederung_stand FROM marketing.video_werke WHERE auftrag = a) <> 'geplant' THEN
    RAISE EXCEPTION '099.2: Gliederung nicht geplant'; END IF;
  IF (SELECT stand FROM marketing.video_vorschlaege WHERE kennung = k1) <> 'verworfen' THEN
    RAISE EXCEPTION '099.2: Vorschlag nicht verworfen'; END IF;
  IF marketing.video_wunsch_setzen(k1, 'zu spaet') THEN RAISE EXCEPTION '099.2: Wunsch an erledigtem Vorschlag'; END IF;
  IF (marketing.video_entscheiden(k1, 'passt', 'rowboat'))->>'ok' <> 'false' THEN
    RAISE EXCEPTION '099.2: entschiedener Vorschlag nochmal entschieden'; END IF;

  -- eine Gliederung ohne Szene ist eine Sackgasse: abgelehnt, nichts aendert sich
  IF marketing.video_gliederung_setzen(a, '{"titel":"leer"}', '[]') IS NOT NULL
     OR marketing.video_gliederung_setzen(a, '{"stufen":[{"name":"x","szenen":[]}]}', '[]') IS NOT NULL
     OR marketing.video_gliederung_setzen(a, '{"stufen":"x"}', '[]') IS NOT NULL THEN
    RAISE EXCEPTION '099.2: Gliederung ohne Szene angenommen'; END IF;
  IF (SELECT gliederung_stand || gliederung_fassung FROM marketing.video_werke WHERE auftrag = a) <> 'geplant1'
     OR (SELECT count(*) FROM marketing.video_vorschlaege WHERE auftrag = a AND stand IN ('wartend', 'offen')) <> 0 THEN
    RAISE EXCEPTION '099.2: abgelehnte Gliederung hat etwas veraendert'; END IF;
  k2 := marketing.video_gliederung_setzen(a,
    '{"titel":"Probe","stufen":[{"name":"Anfang","szenen":[{"kurz":"s1","bildart":"avatar_slide","quellen":["a.md"]},{"kurz":"s2","bildart":"gibtsnicht"}]},{"name":"Ende","szenen":[{"kurz":"s3"}]}]}',
    '["a.md","b.md"]');
  IF (SELECT gliederung_fassung FROM marketing.video_werke WHERE auftrag = a) <> 2 THEN
    RAISE EXCEPTION '099.2: Fassung nicht 2'; END IF;
  IF (SELECT fassung FROM marketing.video_vorschlaege WHERE kennung = k2) <> 2 THEN
    RAISE EXCEPTION '099.2: Vorschlag-Fassung'; END IF;
  IF (SELECT gliederung_wunsch FROM marketing.video_werke WHERE auftrag = a) IS NOT NULL
     OR (SELECT gliederung_wuensche[1]->>'text' FROM marketing.video_werke WHERE auftrag = a) <> 'kuerzer bitte' THEN
    RAISE EXCEPTION '099.2: Wunsch nicht in der Historie'; END IF;
  IF (SELECT count(*) FROM marketing.video_szenen WHERE auftrag = a) <> 0 THEN
    RAISE EXCEPTION '099.2: Szenen vor der Freigabe'; END IF;

  r := marketing.video_entscheiden(k2, 'passt', 'rowboat');
  IF NOT (r->>'ok')::boolean OR r->>'stand' <> 'text_ok' THEN RAISE EXCEPTION '099.2: passt %', r; END IF;
  IF (SELECT gliederung_stand FROM marketing.video_werke WHERE auftrag = a) <> 'text_ok' THEN
    RAISE EXCEPTION '099.2: Gliederung nicht text_ok'; END IF;
  IF (SELECT string_agg(nr || ':' || kurz || ':' || stand, ',' ORDER BY nr) FROM marketing.video_szenen WHERE auftrag = a)
     <> '1:s1:geplant,2:s2:geplant,3:s3:geplant' THEN
    RAISE EXCEPTION '099.2: Szenen falsch angelegt'; END IF;
  IF (SELECT stufe FROM marketing.video_szenen WHERE auftrag = a AND nr = 3) <> 'Ende' THEN
    RAISE EXCEPTION '099.2: Stufe'; END IF;
  IF (SELECT bildart || quellen::text FROM marketing.video_szenen WHERE auftrag = a AND nr = 1) <> 'avatar_slide["a.md"]'
     OR (SELECT bildart FROM marketing.video_szenen WHERE auftrag = a AND nr = 2) IS NOT NULL
     OR (SELECT quellen::text FROM marketing.video_szenen WHERE auftrag = a AND nr = 3) <> '[]' THEN
    RAISE EXCEPTION '099.2: bildart/quellen aus der Gliederung'; END IF;
  IF marketing.video_gliederung_setzen(a, '{"titel":"drittens"}', '[]') IS NOT NULL THEN
    RAISE EXCEPTION '099.2: Gliederung aus text_ok angenommen'; END IF;
END $$;

-- 3: Text setzen und freigeben, andere Szene bleibt unberuehrt
DO $$
DECLARE a uuid := (SELECT v FROM t099 WHERE k = 'o1'); s1 uuid; s2 uuid; t1 text; t2 text; v_geaendert timestamptz;
BEGIN
  SELECT id INTO s1 FROM marketing.video_szenen WHERE auftrag = a AND nr = 1;
  SELECT id INTO s2 FROM marketing.video_szenen WHERE auftrag = a AND nr = 2;
  INSERT INTO t099 VALUES ('s1', s1), ('s2', s2), ('s3', (SELECT id FROM marketing.video_szenen WHERE auftrag = a AND nr = 3));
  UPDATE marketing.video_szenen SET geaendert = timestamptz '2000-01-01 00:00+00' WHERE id = s2;
  v_geaendert := (SELECT geaendert FROM marketing.video_szenen WHERE id = s2);

  IF marketing.video_szene_text_setzen((SELECT v FROM t099 WHERE k = 's3'), '{"sprechtext":"x","bildart":"foo"}') IS NOT NULL
     OR marketing.video_szene_text_setzen((SELECT v FROM t099 WHERE k = 's3'), '{"sprechtext":"x","uebergang":"zoom"}') IS NOT NULL
     OR marketing.video_szene_text_setzen((SELECT v FROM t099 WHERE k = 's3'), '{"bildart":"slide"}') IS NOT NULL THEN
    RAISE EXCEPTION '099.3: ungueltiger Text angenommen'; END IF;
  IF (SELECT stand || fassung FROM marketing.video_szenen WHERE id = (SELECT v FROM t099 WHERE k = 's3')) <> 'geplant0' THEN
    RAISE EXCEPTION '099.3: abgelehnter Text hat die Szene veraendert'; END IF;
  t1 := marketing.video_szene_text_setzen(s1,
    '{"sprechtext":"Text eins","bildart":"slide","bild":{"titel":"x"},"uebergang":"blende","quellen":["a.md"]}');
  IF t1 !~ '^wb-[a-z2-7]{10}$' THEN RAISE EXCEPTION '099.3: Kennung %', t1; END IF;
  IF (SELECT stand || fassung || coalesce(rueckgabe_von, '-') || bildart || uebergang FROM marketing.video_szenen WHERE id = s1)
     <> 'text_wartet1-slideblende' THEN RAISE EXCEPTION '099.3: Szenenfelder'; END IF;
  IF (SELECT tor || stand || fassung FROM marketing.video_vorschlaege WHERE kennung = t1) <> 'textwartend1' THEN
    RAISE EXCEPTION '099.3: Textvorschlag'; END IF;
  IF marketing.video_szene_text_setzen(s1, '{"sprechtext":"nochmal"}') IS NOT NULL THEN
    RAISE EXCEPTION '099.3: Text aus text_wartet angenommen'; END IF;

  -- zweite Szene: Text setzen, damit gleich ein Vorschlag fuer Block 4 da ist
  t2 := marketing.video_szene_text_setzen(s2, '{"sprechtext":"Text zwei","bildart":"avatar_slide","bild":{}}');
  INSERT INTO t099k VALUES ('t2', t2);
  UPDATE marketing.video_szenen SET geaendert = timestamptz '2000-01-01 00:00+00' WHERE id = s2;
  v_geaendert := timestamptz '2000-01-01 00:00+00';

  IF (marketing.video_entscheiden(t1, 'passt', 'rowboat'))->>'ok' <> 'true' THEN
    RAISE EXCEPTION '099.3: passt'; END IF;
  IF (SELECT stand FROM marketing.video_szenen WHERE id = s1) <> 'text_ok' THEN RAISE EXCEPTION '099.3: Szene nicht text_ok'; END IF;
  IF (SELECT stand FROM marketing.video_vorschlaege WHERE kennung = t1) <> 'angenommen' THEN
    RAISE EXCEPTION '099.3: Vorschlag nicht angenommen'; END IF;
  IF (SELECT stand || geaendert::text FROM marketing.video_szenen WHERE id = s2) <> 'text_wartet' || v_geaendert::text THEN
    RAISE EXCEPTION '099.3: zweite Szene veraendert'; END IF;
  IF (SELECT stand FROM marketing.video_szenen WHERE id = (SELECT v FROM t099 WHERE k = 's3')) <> 'geplant' THEN
    RAISE EXCEPTION '099.3: dritte Szene veraendert'; END IF;
END $$;

-- 4: Text nochmal -> geplant, rueckgabe_von text, Wunsch im Wortlaut in der Historie
DO $$
DECLARE a uuid := (SELECT v FROM t099 WHERE k = 'o1'); s2 uuid := (SELECT v FROM t099 WHERE k = 's2');
        t2 text := (SELECT kennung FROM t099k WHERE k = 't2'); t2b text; r jsonb;
BEGIN
  IF NOT marketing.video_wunsch_setzen(t2, 'mehr Zahlen') THEN RAISE EXCEPTION '099.4: Wunsch'; END IF;
  -- auf 2000 Zeichen gekuerzt
  IF NOT marketing.video_wunsch_setzen(t2, repeat('x', 2500)) THEN RAISE EXCEPTION '099.4: langer Wunsch'; END IF;
  IF (SELECT length(wunsch) FROM marketing.video_szenen WHERE id = s2) <> 2000 THEN
    RAISE EXCEPTION '099.4: Wunsch nicht gekuerzt'; END IF;
  IF NOT marketing.video_wunsch_setzen(t2, 'mehr Zahlen') THEN RAISE EXCEPTION '099.4: Wunsch neu'; END IF;
  r := marketing.video_entscheiden(t2, 'nochmal', 'rowboat');
  IF NOT (r->>'ok')::boolean OR r->>'stand' <> 'geplant' THEN RAISE EXCEPTION '099.4: nochmal %', r; END IF;
  IF (SELECT stand || rueckgabe_von FROM marketing.video_szenen WHERE id = s2) <> 'geplanttext' THEN
    RAISE EXCEPTION '099.4: Stand/rueckgabe_von'; END IF;
  IF (SELECT wunsch FROM marketing.video_szenen WHERE id = s2) <> 'mehr Zahlen' THEN
    RAISE EXCEPTION '099.4: Wunsch geht verloren, bevor der Text neu entsteht'; END IF;
  t2b := marketing.video_szene_text_setzen(s2, '{"sprechtext":"Text zwei neu","bildart":"slide","bild":{}}');
  IF t2b IS NULL THEN RAISE EXCEPTION '099.4: neuer Text abgelehnt'; END IF;
  IF (SELECT stand || fassung || coalesce(rueckgabe_von, '-') || coalesce(wunsch, '-') FROM marketing.video_szenen WHERE id = s2)
     <> 'text_wartet2--' THEN RAISE EXCEPTION '099.4: Zustand nach neuem Text'; END IF;
  IF (SELECT wuensche[1]->>'text' || '/' || (wuensche[1]->>'tor') || '/' || cardinality(wuensche)
        FROM marketing.video_szenen WHERE id = s2) <> 'mehr Zahlen/text/1' THEN
    RAISE EXCEPTION '099.4: Historie'; END IF;
  IF (SELECT wuensche[1]->>'zeit' FROM marketing.video_szenen WHERE id = s2) IS NULL THEN
    RAISE EXCEPTION '099.4: Historie ohne Zeit'; END IF;

  -- 6: veraltete Kennung wirkt nicht mehr
  IF (marketing.video_entscheiden(t2, 'passt', 'rowboat'))->>'ok' <> 'false' THEN
    RAISE EXCEPTION '099.6: alte Kennung wirkt'; END IF;
  IF (SELECT stand FROM marketing.video_szenen WHERE id = s2) <> 'text_wartet' THEN
    RAISE EXCEPTION '099.6: Szenenstand geaendert'; END IF;

  IF (marketing.video_entscheiden(t2b, 'passt', 'rowboat'))->>'ok' <> 'true' THEN RAISE EXCEPTION '099.4: passt'; END IF;
  -- Szene 3: Text und freigeben, damit alle drei text_ok sind
  t2b := marketing.video_szene_text_setzen((SELECT v FROM t099 WHERE k = 's3'),
         '{"sprechtext":"Text drei","bildart":"clip","bild":{"clip":"c.mp4"},"uebergang":"schnitt"}');
  IF (marketing.video_entscheiden(t2b, 'passt', 'seite'))->>'ok' <> 'true' THEN RAISE EXCEPTION '099.4: passt drei'; END IF;
  IF (SELECT string_agg(stand, ',' ORDER BY nr) FROM marketing.video_szenen WHERE auftrag = a) <> 'text_ok,text_ok,text_ok' THEN
    RAISE EXCEPTION '099.4: nicht alle text_ok'; END IF;
END $$;

-- 8: nehmen, Lease, Fehler, nochmal
DO $$
DECLARE a uuid := (SELECT v FROM t099 WHERE k = 'o1'); s2 uuid := (SELECT v FROM t099 WHERE k = 's2'); r jsonb;
BEGIN
  r := marketing.video_szene_nehmen(a, interval '10 min');
  IF (r->>'nr')::int <> 1 OR r->>'stand' <> 'wird_erzeugt' OR r->>'sprechtext' <> 'Text eins' THEN
    RAISE EXCEPTION '099.8: erste Szene %', r; END IF;
  r := marketing.video_szene_nehmen(a, interval '10 min');
  IF (r->>'nr')::int <> 2 THEN RAISE EXCEPTION '099.8: zweiter Aufruf nimmt nicht die naechste Szene'; END IF;
  UPDATE marketing.video_szenen SET vergeben_bis = now() - interval '1 min' WHERE id = s2;
  r := marketing.video_szene_nehmen(a, interval '10 min');
  IF (r->>'nr')::int <> 2 THEN RAISE EXCEPTION '099.8: abgelaufene Szene nicht wieder genommen'; END IF;

  IF NOT marketing.video_szene_fehler(s2, 'kaputt') THEN RAISE EXCEPTION '099.8: fehler'; END IF;
  IF (SELECT stand || fehler || fehler_nr FROM marketing.video_szenen WHERE id = s2) <> 'text_okkaputt1' THEN
    RAISE EXCEPTION '099.8: Fehlerzustand'; END IF;
  IF (SELECT count(*) FROM marketing.video_werke w, jsonb_array_elements(w.hinweise) h
       WHERE w.auftrag = a AND h->>'key' = 's2:1' AND length(h->>'satz') > 0) <> 1 THEN
    RAISE EXCEPTION '099.8: Hinweis zum Fehler'; END IF;
  IF marketing.video_szene_fehler(s2, 'nochmal') THEN RAISE EXCEPTION '099.8: fehler ausserhalb von wird_erzeugt'; END IF;
  r := marketing.video_szene_nehmen(a, interval '10 min');
  IF (r->>'nr')::int <> 3 THEN RAISE EXCEPTION '099.8: nehmen uebersprang die fehlerhafte Szene nicht'; END IF;
  IF marketing.video_szene_nehmen(a, interval '10 min') IS NOT NULL THEN
    RAISE EXCEPTION '099.8: fehlerhafte Szene wurde genommen'; END IF;
  IF NOT marketing.video_szene_nochmal(s2) THEN RAISE EXCEPTION '099.8: nochmal'; END IF;
  IF (SELECT fehler FROM marketing.video_szenen WHERE id = s2) IS NOT NULL THEN RAISE EXCEPTION '099.8: fehler nicht geloescht'; END IF;
  r := marketing.video_szene_nehmen(a, interval '10 min');
  IF (r->>'nr')::int <> 2 THEN RAISE EXCEPTION '099.8: nehmen nach nochmal'; END IF;
  IF (SELECT string_agg(stand, ',' ORDER BY nr) FROM marketing.video_szenen WHERE auftrag = a)
     <> 'wird_erzeugt,wird_erzeugt,wird_erzeugt' THEN RAISE EXCEPTION '099.8: Staende'; END IF;
END $$;

-- 9 + 5 + 7: Clips setzen, Clip-Tor
DO $$
DECLARE a uuid := (SELECT v FROM t099 WHERE k = 'o1');
        s1 uuid := (SELECT v FROM t099 WHERE k = 's1'); s2 uuid := (SELECT v FROM t099 WHERE k = 's2');
        s3 uuid := (SELECT v FROM t099 WHERE k = 's3'); c1 text; c1b text; c1c text; c2 text; c3 text; r jsonb;
BEGIN
  -- 9: alte Variante aendert nichts
  IF marketing.video_szene_clip_setzen(s1, 5, 'x.mp4', 4.2, 'v', 'e') IS NOT NULL THEN
    RAISE EXCEPTION '099.9: falsche Variante angenommen'; END IF;
  IF (SELECT stand || coalesce(clip_datei, '-') FROM marketing.video_szenen WHERE id = s1) <> 'wird_erzeugt-' THEN
    RAISE EXCEPTION '099.9: Szene geaendert'; END IF;
  c1 := marketing.video_szene_clip_setzen(s1, 0, 's1_v0.mp4', 4.2, 'stimme-a', 'hyperframes');
  IF c1 !~ '^wb-[a-z2-7]{10}$' THEN RAISE EXCEPTION '099.9: Clip-Kennung'; END IF;
  IF (SELECT stand || clip_datei || dauer_s || stimme || engine FROM marketing.video_szenen WHERE id = s1)
     <> 'clip_wartets1_v0.mp44.2stimme-ahyperframes' THEN RAISE EXCEPTION '099.9: Clipfelder'; END IF;
  IF (SELECT tor || stand || fassung FROM marketing.video_vorschlaege WHERE kennung = c1) <> 'clipwartend1' THEN
    RAISE EXCEPTION '099.9: Clip-Vorschlag'; END IF;
  IF marketing.video_szene_clip_setzen(s1, 0, 's1_v0b.mp4', 4.2, 'v', 'e') IS NOT NULL THEN
    RAISE EXCEPTION '099.9: Clip aus clip_wartet angenommen'; END IF;

  -- 5: Clip nochmal ohne Wunsch -> text_ok, variante +1; Szene wird neu genommen
  r := marketing.video_entscheiden(c1, 'nochmal', 'rowboat');
  IF r->>'stand' <> 'text_ok' THEN RAISE EXCEPTION '099.5: nochmal ohne Wunsch %', r; END IF;
  IF (SELECT stand || variante || coalesce(rueckgabe_von, '-') FROM marketing.video_szenen WHERE id = s1) <> 'text_ok1-' THEN
    RAISE EXCEPTION '099.5: Zustand nach nochmal ohne Wunsch'; END IF;
  r := marketing.video_szene_nehmen(a, interval '10 min');
  IF (r->>'nr')::int <> 1 OR (r->>'variante')::int <> 1 THEN RAISE EXCEPTION '099.5: nehmen mit neuer Variante'; END IF;
  c1b := marketing.video_szene_clip_setzen(s1, 1, 's1_v1.mp4', 4.0, 'stimme-a', 'hyperframes');
  IF (SELECT fassung FROM marketing.video_vorschlaege WHERE kennung = c1b) <> 2 THEN
    RAISE EXCEPTION '099.5: zweiter Clip-Vorschlag ist keine Fassung 2'; END IF;
  -- mit Wunsch -> geplant, rueckgabe_von clip
  IF NOT marketing.video_wunsch_setzen(c1b, 'andere Farbe') THEN RAISE EXCEPTION '099.5: Wunsch'; END IF;
  r := marketing.video_entscheiden(c1b, 'nochmal', 'rowboat');
  IF r->>'stand' <> 'geplant' THEN RAISE EXCEPTION '099.5: nochmal mit Wunsch %', r; END IF;
  IF (SELECT stand || rueckgabe_von FROM marketing.video_szenen WHERE id = s1) <> 'geplantclip' THEN
    RAISE EXCEPTION '099.5: rueckgabe_von clip'; END IF;
  IF marketing.video_szene_bild_setzen(s2, 'slide', '{}') THEN RAISE EXCEPTION '099.5: bild_setzen ohne rueckgabe_von clip'; END IF;
  IF marketing.video_szene_bild_setzen(s1, 'unbekannt', '{}') THEN RAISE EXCEPTION '099.5: unbekannte Bildart'; END IF;
  IF NOT marketing.video_szene_bild_setzen(s1, 'avatar_gross', '{"x":1}') THEN RAISE EXCEPTION '099.5: bild_setzen'; END IF;
  IF (SELECT stand || variante || coalesce(rueckgabe_von, '-') || sprechtext || bildart || coalesce(wunsch, '-')
        FROM marketing.video_szenen WHERE id = s1) <> 'text_ok2-Text einsavatar_gross-' THEN
    RAISE EXCEPTION '099.5: Zustand nach bild_setzen'; END IF;
  IF (SELECT wuensche[1]->>'text' || '/' || (wuensche[1]->>'tor') FROM marketing.video_szenen WHERE id = s1) <> 'andere Farbe/clip' THEN
    RAISE EXCEPTION '099.5: Clip-Wunsch nicht in der Historie'; END IF;
  IF marketing.video_szene_bild_setzen(s1, 'slide', '{}') THEN RAISE EXCEPTION '099.5: zweites bild_setzen'; END IF;

  -- 7: Entscheidung auf der Seite: offen -> zurueckziehen, wartend -> erledigt
  c2 := marketing.video_szene_clip_setzen(s2, 0, 's2_v0.mp4', 3.0, 'stimme-a', 'hyperframes');
  IF NOT marketing.video_vorschlag_gepostet(c2, 'Szene 2 ist da') THEN RAISE EXCEPTION '099.7: gepostet'; END IF;
  IF (SELECT stand || text FROM marketing.video_vorschlaege WHERE kennung = c2) <> 'offenSzene 2 ist da' THEN
    RAISE EXCEPTION '099.7: Vorschlag nicht offen'; END IF;
  IF marketing.video_vorschlag_gepostet(c2, 'nochmal') THEN RAISE EXCEPTION '099.7: zweites gepostet'; END IF;
  IF (marketing.video_entscheiden(c2, 'passt', 'seite'))->>'ok' <> 'true' THEN RAISE EXCEPTION '099.7: Seite'; END IF;
  IF (SELECT stand FROM marketing.video_vorschlaege WHERE kennung = c2) <> 'zurueckziehen' THEN
    RAISE EXCEPTION '099.7: Vorschlag nicht zurueckziehen'; END IF;
  IF (SELECT stand FROM marketing.video_szenen WHERE id = s2) <> 'fertig' THEN RAISE EXCEPTION '099.7: Szene nicht fertig'; END IF;
  IF (SELECT count(*) FROM marketing.video_vorschlaege_lebend() x WHERE x->>'kennung' = c2 AND x->>'stand' = 'zurueckziehen'
        AND (x->>'nr')::int = 2 AND x->>'titel' = 'Probe' AND x->>'kurz' = 's2' AND x->>'tor' = 'clip') <> 1 THEN
    RAISE EXCEPTION '099.7: lebend zeigt zurueckziehen nicht'; END IF;
  IF NOT marketing.video_vorschlag_erledigt(c2) THEN RAISE EXCEPTION '099.7: erledigt'; END IF;
  IF (SELECT stand FROM marketing.video_vorschlaege WHERE kennung = c2) <> 'erledigt' THEN RAISE EXCEPTION '099.7: Stand erledigt'; END IF;
  IF marketing.video_vorschlag_erledigt(c2) THEN RAISE EXCEPTION '099.7: zweites erledigt'; END IF;
  c3 := marketing.video_szene_clip_setzen(s3, 0, 's3_v0.mp4', 5.0, 'stimme-a', 'hyperframes');
  IF (marketing.video_entscheiden(c3, 'passt', 'seite'))->>'ok' <> 'true' THEN RAISE EXCEPTION '099.7: Seite wartend'; END IF;
  IF (SELECT stand FROM marketing.video_vorschlaege WHERE kennung = c3) <> 'erledigt' THEN
    RAISE EXCEPTION '099.7: wartend + Seite ist nicht erledigt'; END IF;

  -- 10: fertig_setzen erst, wenn alle fertig sind
  IF marketing.video_fertig_setzen(a, '{"datei":"v.mp4"}') THEN RAISE EXCEPTION '099.10: fertig_setzen mit offener Szene'; END IF;
  IF (SELECT stand FROM auftrag.auftraege WHERE id = a) <> 'werkbank' THEN RAISE EXCEPTION '099.10: Auftrag veraendert'; END IF;
  r := marketing.video_szene_nehmen(a, interval '10 min');
  c1c := marketing.video_szene_clip_setzen(s1, 2, 's1_v2.mp4', 4.1, 'stimme-a', 'hyperframes');
  IF (marketing.video_entscheiden(c1c, 'passt', 'rowboat'))->>'ok' <> 'true' THEN RAISE EXCEPTION '099.10: Clip passt'; END IF;
  IF (SELECT count(*) FROM marketing.video_vorschlaege_lebend() x WHERE x->>'kennung' = c1c AND x->>'stand' = 'angenommen') <> 1 THEN
    RAISE EXCEPTION '099.10: lebend zeigt angenommen nicht'; END IF;
  IF NOT marketing.video_vorschlag_abgehakt(c1c) THEN RAISE EXCEPTION '099.10: abgehakt'; END IF;
  IF (SELECT count(*) FROM marketing.video_vorschlaege_lebend() x WHERE x->>'kennung' = c1c) <> 0 THEN
    RAISE EXCEPTION '099.10: lebend zeigt abgehakte Vorschlaege'; END IF;

  IF (SELECT alle_fertig FROM marketing.video_arbeit() x, LATERAL jsonb_to_record(x) AS t(auftrag uuid, alle_fertig boolean)
       WHERE t.auftrag = a) IS NOT TRUE THEN RAISE EXCEPTION '099.10: alle_fertig'; END IF;
  IF marketing.video_fertig_setzen(a, '{}') OR marketing.video_fertig_setzen(a, '{"datei":""}')
     OR marketing.video_fertig_setzen(a, '{"video":"v.mp4"}') THEN
    RAISE EXCEPTION '099.10: fertig_setzen ohne Dateinamen angenommen'; END IF;
  IF (SELECT stand FROM auftrag.auftraege WHERE id = a) <> 'werkbank'
     OR (SELECT video_fassung || coalesce(video_datei, '-') FROM marketing.video_werke WHERE auftrag = a) <> '0-' THEN
    RAISE EXCEPTION '099.10: fertig_setzen ohne Dateinamen hat etwas veraendert'; END IF;
  IF NOT marketing.video_fertig_setzen(a, '{"datei":"v.mp4"}') THEN RAISE EXCEPTION '099.10: fertig_setzen'; END IF;
  IF (SELECT stand FROM auftrag.auftraege WHERE id = a) <> 'erzeugt' THEN RAISE EXCEPTION '099.10: Auftrag nicht erzeugt'; END IF;
  IF (SELECT (ergebnis->>'ki_hinweis') || (ergebnis->>'fassung') || (ergebnis->>'datei') FROM auftrag.auftraege WHERE id = a)
     <> 'true1v.mp4' THEN RAISE EXCEPTION '099.10: Ergebnis'; END IF;
  IF (SELECT video_fassung || video_datei || video_veraltet FROM marketing.video_werke WHERE auftrag = a) <> '1v.mp4false' THEN
    RAISE EXCEPTION '099.10: Werk nach fertig_setzen'; END IF;
  IF marketing.video_fertig_setzen(a, '{}') THEN RAISE EXCEPTION '099.10: zweites fertig_setzen'; END IF;
  IF (SELECT ergebnis->'werkbank'->>'szenen' FROM auftrag.auftraege WHERE id = a) <> '3' THEN
    RAISE EXCEPTION '099.10: Zaehlung szenen'; END IF;
  IF (SELECT ergebnis->'werkbank'->'je_stand'->>'fertig' FROM auftrag.auftraege WHERE id = a) <> '3' THEN
    RAISE EXCEPTION '099.10: Zaehlung je_stand'; END IF;
END $$;

-- 11: fertige Szene oeffnen bei Auftrag wartet_freigabe
DO $$
DECLARE a uuid := (SELECT v FROM t099 WHERE k = 'o1');
        s2 uuid := (SELECT v FROM t099 WHERE k = 's2'); s3 uuid := (SELECT v FROM t099 WHERE k = 's3');
BEGIN
  IF NOT auftrag.stand_setzen(a, 'erzeugt', 'wartet_freigabe') THEN RAISE EXCEPTION '099.11: Vorbereitung'; END IF;
  -- Auftrag fertig oder abgebrochen: false, Szene und Werk bleiben
  IF NOT auftrag.stand_setzen(a, 'wartet_freigabe', 'fertig') THEN RAISE EXCEPTION '099.11: Vorbereitung fertig'; END IF;
  IF marketing.video_szene_oeffnen(s2, 'zu spaet') OR marketing.video_szene_oeffnen(s2, NULL) THEN
    RAISE EXCEPTION '099.11: oeffnen bei fertigem Auftrag'; END IF;
  UPDATE auftrag.auftraege SET stand = 'abgebrochen' WHERE id = a;
  IF marketing.video_szene_oeffnen(s2, 'zu spaet') THEN RAISE EXCEPTION '099.11: oeffnen bei abgebrochenem Auftrag'; END IF;
  IF (SELECT stand FROM marketing.video_szenen WHERE id = s2) <> 'fertig'
     OR (SELECT video_veraltet FROM marketing.video_werke WHERE auftrag = a)
     OR (SELECT stand FROM auftrag.auftraege WHERE id = a) <> 'abgebrochen' THEN
    RAISE EXCEPTION '099.11: abgewiesenes oeffnen hat etwas veraendert'; END IF;
  UPDATE auftrag.auftraege SET stand = 'wartet_freigabe' WHERE id = a;
  IF NOT marketing.video_szene_oeffnen(s2, 'neue Idee') THEN RAISE EXCEPTION '099.11: oeffnen'; END IF;
  IF (SELECT stand FROM auftrag.auftraege WHERE id = a) <> 'werkbank' THEN RAISE EXCEPTION '099.11: Auftrag nicht werkbank'; END IF;
  IF NOT (SELECT video_veraltet FROM marketing.video_werke WHERE auftrag = a) THEN RAISE EXCEPTION '099.11: video_veraltet'; END IF;
  IF (SELECT stand || rueckgabe_von || wunsch FROM marketing.video_szenen WHERE id = s2) <> 'geplantclipneue Idee' THEN
    RAISE EXCEPTION '099.11: Szene nach oeffnen mit Wunsch'; END IF;
  -- ohne Wunsch: wie Clip nochmal ohne Wunsch
  IF NOT marketing.video_szene_oeffnen(s3, NULL) THEN RAISE EXCEPTION '099.11: oeffnen ohne Wunsch'; END IF;
  IF (SELECT stand || variante || coalesce(rueckgabe_von, '-') FROM marketing.video_szenen WHERE id = s3) <> 'text_ok1-' THEN
    RAISE EXCEPTION '099.11: Szene nach oeffnen ohne Wunsch'; END IF;
  IF marketing.video_szene_oeffnen(s3, 'x') THEN RAISE EXCEPTION '099.11: oeffnen einer nicht fertigen Szene'; END IF;
  IF (SELECT count(*) FROM marketing.video_arbeit() x WHERE (x->>'auftrag')::uuid = a
        AND jsonb_array_length(x->'szenen') = 3 AND (x->>'alle_fertig')::boolean = false
        AND (x->>'video_veraltet')::boolean AND (x->>'video_fassung')::int = 1) <> 1 THEN
    RAISE EXCEPTION '099.11: video_arbeit'; END IF;
END $$;

-- 11b: Rueckgabe vom Clip-Tor mit geaendertem Text: text_setzen laeuft ueber das Text-Tor (Plan 7, Spec 5.1)
DO $$
DECLARE a uuid := (SELECT v FROM t099 WHERE k = 'o1'); s2 uuid := (SELECT v FROM t099 WHERE k = 's2'); k text;
BEGIN
  IF (SELECT stand || rueckgabe_von FROM marketing.video_szenen WHERE id = s2) <> 'geplantclip' THEN
    RAISE EXCEPTION '099.11b: Vorbedingung'; END IF;
  k := marketing.video_szene_text_setzen(s2, '{"sprechtext":"Text zwei v3"}');
  IF k !~ '^wb-[a-z2-7]{10}$' THEN RAISE EXCEPTION '099.11b: Kennung'; END IF;
  IF (SELECT stand || coalesce(rueckgabe_von, '-') || sprechtext || bildart || variante || coalesce(clip_datei, '-')
        FROM marketing.video_szenen WHERE id = s2) <> 'text_wartet-Text zwei v3slide1-' THEN
    RAISE EXCEPTION '099.11b: Szene nach text_setzen aus der Clip-Rueckgabe'; END IF;
  IF (SELECT fassung FROM marketing.video_szenen WHERE id = s2) <> 3 THEN RAISE EXCEPTION '099.11b: Fassung'; END IF;
  IF (SELECT wuensche[cardinality(wuensche)]->>'text' || '/' || (wuensche[cardinality(wuensche)]->>'tor')
        FROM marketing.video_szenen WHERE id = s2) <> 'neue Idee/clip' OR (SELECT wunsch FROM marketing.video_szenen WHERE id = s2) IS NOT NULL THEN
    RAISE EXCEPTION '099.11b: Clip-Wunsch in der Historie'; END IF;
  IF (SELECT tor || stand || fassung FROM marketing.video_vorschlaege WHERE kennung = k) <> 'textwartend3' THEN
    RAISE EXCEPTION '099.11b: Textvorschlag'; END IF;
  IF (marketing.video_entscheiden(k, 'passt', 'rowboat'))->>'stand' <> 'text_ok' THEN RAISE EXCEPTION '099.11b: passt'; END IF;
END $$;

-- 12: Zaehlung und Runden, 13: Hinweise je key einmal, Aufrufe (zweiter Auftrag)
DO $$
DECLARE a uuid := (SELECT v FROM t099 WHERE k = 'o2'); g text; ta text; tb text; tc text; sa uuid; sb uuid; sc uuid; e jsonb;
BEGIN
  IF marketing.video_werk_beginnen(a) IS NULL THEN RAISE EXCEPTION '099.12: werk_beginnen'; END IF;
  -- Auftrag nicht mehr in in_arbeit/werkbank: keine Gliederung, nichts entsteht
  UPDATE auftrag.auftraege SET stand = 'abgebrochen' WHERE id = a;
  IF marketing.video_gliederung_setzen(a, '{"stufen":[{"szenen":[{"kurz":"a"}]}]}', '[]') IS NOT NULL THEN
    RAISE EXCEPTION '099.12: Gliederung bei abgebrochenem Auftrag'; END IF;
  IF (SELECT gliederung_stand FROM marketing.video_werke WHERE auftrag = a) <> 'geplant'
     OR (SELECT count(*) FROM marketing.video_vorschlaege WHERE auftrag = a) <> 0 THEN
    RAISE EXCEPTION '099.12: abgewiesene Gliederung hat etwas veraendert'; END IF;
  UPDATE auftrag.auftraege SET stand = 'in_arbeit' WHERE id = a;
  g := marketing.video_gliederung_setzen(a, '{"stufen":[{"szenen":[{"kurz":"a"},{"kurz":"b"},{"kurz":"c"}]}]}', '[]');
  IF (marketing.video_entscheiden(g, 'passt', 'rowboat'))->>'ok' <> 'true' THEN RAISE EXCEPTION '099.12: Gliederung'; END IF;
  SELECT id INTO sa FROM marketing.video_szenen WHERE auftrag = a AND nr = 1;
  SELECT id INTO sb FROM marketing.video_szenen WHERE auftrag = a AND nr = 2;
  SELECT id INTO sc FROM marketing.video_szenen WHERE auftrag = a AND nr = 3;
  ta := marketing.video_szene_text_setzen(sa, '{"sprechtext":"A","bildart":"slide"}');
  tb := marketing.video_szene_text_setzen(sb, '{"sprechtext":"B","bildart":"slide"}');
  e := (SELECT ergebnis->'werkbank' FROM auftrag.auftraege WHERE id = a);
  IF (e->>'wartet')::int <> 0 OR (e->>'runde')::int <> 0 OR e->>'wartet_seit' IS NOT NULL THEN
    RAISE EXCEPTION '099.12: Zaehlung vor dem Posten %', e; END IF;
  IF (e->'je_stand'->>'text_wartet')::int <> 2 OR (e->'je_stand'->>'geplant')::int <> 1 THEN
    RAISE EXCEPTION '099.12: je_stand %', e; END IF;
  IF NOT marketing.video_vorschlag_gepostet(ta, 'Szene A') OR NOT marketing.video_vorschlag_gepostet(tb, 'Szene B') THEN
    RAISE EXCEPTION '099.12: gepostet'; END IF;
  e := (SELECT ergebnis->'werkbank' FROM auftrag.auftraege WHERE id = a);
  IF (e->>'wartet')::int <> 2 OR (e->>'runde')::int <> 1 OR e->>'wartet_seit' IS NULL OR e->>'wartet_tor' <> 'text' THEN
    RAISE EXCEPTION '099.12: Zaehlung nach dem Posten %', e; END IF;
  IF (SELECT string_agg(x->>'kennung', ',' ORDER BY ordinality) FROM marketing.video_vorschlaege_lebend() WITH ORDINALITY x
       WHERE x->>'auftrag' = a::text) <> g || ',' || ta || ',' || tb THEN
    RAISE EXCEPTION '099.12: Reihenfolge von lebend (Gliederung zuerst, dann nach nr)'; END IF;
  PERFORM marketing.video_entscheiden(ta, 'passt', 'rowboat');
  e := (SELECT ergebnis->'werkbank' FROM auftrag.auftraege WHERE id = a);
  IF (e->>'wartet')::int <> 1 OR (e->>'runde')::int <> 1 THEN RAISE EXCEPTION '099.12: eine Entscheidung %', e; END IF;
  PERFORM marketing.video_entscheiden(tb, 'passt', 'rowboat');
  e := (SELECT ergebnis->'werkbank' FROM auftrag.auftraege WHERE id = a);
  IF (e->>'wartet')::int <> 0 OR e->>'wartet_seit' IS NOT NULL THEN RAISE EXCEPTION '099.12: beide entschieden %', e; END IF;
  tc := marketing.video_szene_text_setzen(sc, '{"sprechtext":"C","bildart":"slide"}');
  IF NOT marketing.video_vorschlag_gepostet(tc, 'Szene C') THEN RAISE EXCEPTION '099.12: gepostet C'; END IF;
  e := (SELECT ergebnis->'werkbank' FROM auftrag.auftraege WHERE id = a);
  IF (e->>'runde')::int <> 2 OR (e->>'wartet')::int <> 1 THEN RAISE EXCEPTION '099.12: zweite Runde %', e; END IF;

  -- 6 (Fassung): ein lebender Vorschlag, dessen Szene schon eine andere Fassung hat. Ueber die Funktionen ist
  -- das nicht erreichbar (ein neuer Text braucht geplant, also einen entschiedenen Vorschlag); darum per UPDATE.
  UPDATE marketing.video_szenen SET fassung = fassung + 1 WHERE id = sc;
  IF (marketing.video_entscheiden(tc, 'passt', 'rowboat'))->>'ok' <> 'false' THEN
    RAISE EXCEPTION '099.6: Vorschlag mit veralteter Fassung wirkt'; END IF;
  IF (SELECT stand FROM marketing.video_szenen WHERE id = sc) <> 'text_wartet'
     OR (SELECT stand FROM marketing.video_vorschlaege WHERE kennung = tc) <> 'offen' THEN
    RAISE EXCEPTION '099.6: veraltete Fassung hat etwas veraendert'; END IF;
  UPDATE marketing.video_szenen SET fassung = fassung - 1 WHERE id = sc;

  -- 13: Hinweis je key einmal, Aufrufe zaehlen
  IF NOT marketing.video_hinweis(a, 'k1', 'Ein Satz') THEN RAISE EXCEPTION '099.13: erster Hinweis'; END IF;
  IF marketing.video_hinweis(a, 'k1', 'Ein Satz') THEN RAISE EXCEPTION '099.13: zweiter Hinweis mit gleichem key'; END IF;
  IF (SELECT jsonb_array_length(hinweise) FROM marketing.video_werke WHERE auftrag = a) <> 1 THEN
    RAISE EXCEPTION '099.13: mehr als ein Eintrag'; END IF;
  IF (SELECT jsonb_array_length(ergebnis->'werkbank'->'hinweise') FROM auftrag.auftraege WHERE id = a) <> 1 THEN
    RAISE EXCEPTION '099.13: Hinweis nicht im Ergebnis'; END IF;
  IF marketing.video_aufruf_zaehlen(a) <> 1 OR marketing.video_aufruf_zaehlen(a) <> 2 THEN
    RAISE EXCEPTION '099.13: aufruf_zaehlen'; END IF;
  IF (SELECT (ergebnis->'werkbank'->>'aufrufe')::int FROM auftrag.auftraege WHERE id = a) <> 2 THEN
    RAISE EXCEPTION '099.13: aufrufe im Ergebnis'; END IF;
  IF marketing.video_hinweis(gen_random_uuid(), 'k', 's') THEN RAISE EXCEPTION '099.13: Hinweis ohne Werk'; END IF;
END $$;

-- 15 (C1, I3): aufgegebene Szene -> Grund sichtbar -> neuer Wunsch -> neuer Versuch; Belege am Text (o2)
DO $$
DECLARE a uuid := (SELECT v FROM t099 WHERE k = 'o2'); tc text; sc uuid; sa uuid; k text; n int;
BEGIN
  SELECT id INTO sc FROM marketing.video_szenen WHERE auftrag = a AND nr = 3;
  SELECT id INTO sa FROM marketing.video_szenen WHERE auftrag = a AND nr = 1;
  tc := (SELECT kennung FROM marketing.video_vorschlaege WHERE szene = sc AND stand = 'offen');
  IF NOT marketing.video_wunsch_setzen(tc, 'erster Wunsch') THEN RAISE EXCEPTION '099.15: Wunsch'; END IF;
  IF (marketing.video_entscheiden(tc, 'nochmal', 'rowboat'))->>'stand' <> 'geplant' THEN RAISE EXCEPTION '099.15: nochmal'; END IF;
  -- Aufgabe gilt nur fuer den Wunsch, mit dem der Arbeiter es versucht hat, und nur in geplant
  IF marketing.video_szene_aufgeben(sc, 'Grund', 'anderer Wunsch') OR marketing.video_szene_aufgeben(sa, 'Grund', NULL) THEN
    RAISE EXCEPTION '099.15: aufgeben trotz anderem Wunsch oder Stand'; END IF;
  IF NOT marketing.video_szene_aufgeben(sc, 'Szene 3: Quelle fehlt – bitte Material ergänzen.', 'erster Wunsch') THEN
    RAISE EXCEPTION '099.15: aufgeben'; END IF;
  IF (SELECT x->>'aufgegeben' FROM marketing.video_arbeit() w, jsonb_array_elements(w->'szenen') x
       WHERE (w->>'auftrag')::uuid = a AND (x->>'nr')::int = 3) <> 'Szene 3: Quelle fehlt – bitte Material ergänzen.' THEN
    RAISE EXCEPTION '099.15: Grund nicht sichtbar'; END IF;
  -- neuer Wunsch: nur geplant, nicht leer
  IF marketing.video_szene_wunsch(sa, 'x') OR marketing.video_szene_wunsch(sc, '   ') OR marketing.video_szene_wunsch(sc, NULL) THEN
    RAISE EXCEPTION '099.15: Wunsch an falscher Szene oder leer'; END IF;
  IF NOT marketing.video_szene_wunsch(sc, ' neuer Wunsch ') THEN RAISE EXCEPTION '099.15: szene_wunsch'; END IF;
  IF (SELECT coalesce(aufgegeben, '-') || '/' || wunsch FROM marketing.video_szenen WHERE id = sc) <> '-/neuer Wunsch' THEN
    RAISE EXCEPTION '099.15: Zustand nach szene_wunsch'; END IF;
  IF (SELECT string_agg((w->>'text') || ':' || (w->>'tor'), ',') FROM marketing.video_szenen s, unnest(s.wuensche) w
       WHERE s.id = sc) <> 'erster Wunsch:text,neuer Wunsch:text' THEN
    RAISE EXCEPTION '099.15: Historie nach szene_wunsch'; END IF;
  IF NOT marketing.video_szene_wunsch(sc, repeat('y', 2500)) THEN RAISE EXCEPTION '099.15: langer Wunsch'; END IF;
  SELECT cardinality(wuensche) INTO n FROM marketing.video_szenen WHERE id = sc;
  IF (SELECT length(wunsch) FROM marketing.video_szenen WHERE id = sc) <> 2000 OR n <> 3 THEN
    RAISE EXCEPTION '099.15: Wunsch gekuerzt / Historie %', n; END IF;
  -- beendeter Auftrag: kein Wunsch
  UPDATE auftrag.auftraege SET stand = 'abgebrochen' WHERE id = a;
  IF marketing.video_szene_wunsch(sc, 'zu spaet') THEN RAISE EXCEPTION '099.15: Wunsch bei beendetem Auftrag'; END IF;
  UPDATE auftrag.auftraege SET stand = 'werkbank' WHERE id = a;
  -- der neue Text verbraucht den Wunsch (nicht doppelt in der Historie), traegt seine Belege
  k := marketing.video_szene_text_setzen(sc,
         '{"sprechtext":"C neu","bildart":"slide","belege":[{"aussage":"12 Teilnehmer","quelle":"auftrag"}]}');
  IF k IS NULL THEN RAISE EXCEPTION '099.15: Text nach neuem Wunsch'; END IF;
  IF (SELECT stand || '/' || cardinality(wuensche) || '/' || coalesce(wunsch, '-') || '/' || coalesce(aufgegeben, '-')
        FROM marketing.video_szenen WHERE id = sc) <> 'text_wartet/3/-/-' THEN
    RAISE EXCEPTION '099.15: Zustand nach Text'; END IF;
  IF (SELECT belege->0->>'aussage' || '|' || (belege->0->>'quelle') FROM marketing.video_szenen WHERE id = sc)
     <> '12 Teilnehmer|auftrag' THEN RAISE EXCEPTION '099.15: Belege nicht gespeichert'; END IF;
  IF (SELECT x->'belege'->0->>'quelle' FROM marketing.video_arbeit() w, jsonb_array_elements(w->'szenen') x
       WHERE (w->>'auftrag')::uuid = a AND (x->>'nr')::int = 3) <> 'auftrag' THEN
    RAISE EXCEPTION '099.15: Belege nicht in der Werk-Form'; END IF;
  -- ein Text ohne Belege hat leere Belege (die alten gehoeren zum alten Text)
  PERFORM marketing.video_wunsch_setzen(k, 'nochmal');
  PERFORM marketing.video_entscheiden(k, 'nochmal', 'seite');
  k := marketing.video_szene_text_setzen(sc, '{"sprechtext":"C ohne"}');
  IF (SELECT belege::text FROM marketing.video_szenen WHERE id = sc) <> '[]' THEN RAISE EXCEPTION '099.15: alte Belege'; END IF;
  INSERT INTO t099k VALUES ('tc2', k);
END $$;

-- 16 (C1): aufgegebene Gliederung -> Grund sichtbar -> neuer Wunsch; Gliederung setzen loescht den Grund (o1)
DO $$
DECLARE a uuid := (SELECT v FROM t099 WHERE k = 'o1'); k text; n int;
BEGIN
  IF marketing.video_gliederung_wunsch(a, 'x') THEN RAISE EXCEPTION '099.16: Wunsch an freigegebener Gliederung'; END IF;
  UPDATE marketing.video_werke SET gliederung_stand = 'geplant' WHERE auftrag = a;   -- nur in der Probe: geparkte Ueberarbeitung
  SELECT cardinality(gliederung_wuensche) INTO n FROM marketing.video_werke WHERE auftrag = a;
  IF marketing.video_gliederung_aufgeben(a, 'G', 'anderer') THEN RAISE EXCEPTION '099.16: aufgeben trotz anderem Wunsch'; END IF;
  IF NOT marketing.video_gliederung_aufgeben(a, 'Die Gliederung ließ sich nicht schreiben – bitte Wunsch ergänzen.', NULL) THEN
    RAISE EXCEPTION '099.16: aufgeben'; END IF;
  IF (marketing._video_werk_json(a))->>'gliederung_aufgegeben' <> 'Die Gliederung ließ sich nicht schreiben – bitte Wunsch ergänzen.' THEN
    RAISE EXCEPTION '099.16: Grund nicht sichtbar'; END IF;
  IF marketing.video_gliederung_wunsch(a, '') THEN RAISE EXCEPTION '099.16: leerer Wunsch'; END IF;
  IF NOT marketing.video_gliederung_wunsch(a, 'kuerzer') THEN RAISE EXCEPTION '099.16: gliederung_wunsch'; END IF;
  IF (SELECT coalesce(gliederung_aufgegeben, '-') || '/' || gliederung_wunsch || '/' || cardinality(gliederung_wuensche)
        || '/' || (gliederung_wuensche[cardinality(gliederung_wuensche)]->>'tor') FROM marketing.video_werke WHERE auftrag = a)
     <> '-/kuerzer/' || (n + 1) || '/gliederung' THEN RAISE EXCEPTION '099.16: Zustand nach Wunsch'; END IF;
  IF jsonb_array_length((marketing._video_werk_json(a))->'gliederung_wuensche') <> n + 1 THEN
    RAISE EXCEPTION '099.16: Historie nicht in der Werk-Form'; END IF;
  UPDATE auftrag.auftraege SET stand = 'fehler' WHERE id = a;
  IF marketing.video_gliederung_wunsch(a, 'zu spaet') THEN RAISE EXCEPTION '099.16: Wunsch bei beendetem Auftrag'; END IF;
  UPDATE auftrag.auftraege SET stand = 'werkbank' WHERE id = a;
  PERFORM marketing.video_gliederung_aufgeben(a, 'G2', 'kuerzer');
  k := marketing.video_gliederung_setzen(a, '{"titel":"Probe","stufen":[{"name":"A","szenen":[{"kurz":"s1"}]}]}', '[]');
  IF k IS NULL THEN RAISE EXCEPTION '099.16: Gliederung nach Wunsch'; END IF;
  IF (SELECT coalesce(gliederung_aufgegeben, '-') || '/' || coalesce(gliederung_wunsch, '-') || '/' || cardinality(gliederung_wuensche)
        FROM marketing.video_werke WHERE auftrag = a) <> '-/-/' || (n + 1) THEN
    RAISE EXCEPTION '099.16: Gliederung setzen loescht den Grund nicht / Historie doppelt'; END IF;
  IF (marketing.video_entscheiden(k, 'passt', 'seite'))->>'stand' <> 'text_ok' THEN RAISE EXCEPTION '099.16: zurueck auf text_ok'; END IF;
  IF (SELECT count(*) FROM marketing.video_szenen WHERE auftrag = a) <> 3 THEN RAISE EXCEPTION '099.16: Szenen veraendert'; END IF;
END $$;

-- 17 (I1, M10): Video neu zusammenfuegen, gescheitertes Zusammenfuegen, Bild mit Quellen und Belegen (o1)
DO $$
DECLARE a uuid := (SELECT v FROM t099 WHERE k = 'o1'); s1 uuid := (SELECT v FROM t099 WHERE k = 's1');
BEGIN
  IF marketing.video_neu_bauen(a) THEN RAISE EXCEPTION '099.17: neu bauen mit offener Szene'; END IF;
  UPDATE marketing.video_szenen SET stand = 'fertig' WHERE auftrag = a;          -- nur in der Probe
  IF marketing.video_bau_gescheitert(a, 1, 'x') THEN RAISE EXCEPTION '099.17: Fehler fuer fremden Versuch'; END IF;
  IF NOT marketing.video_bau_gescheitert(a, 0, 'Auf dem PC ist nicht genug Platz.') THEN RAISE EXCEPTION '099.17: gescheitert'; END IF;
  IF (marketing._video_werk_json(a))->>'video_fehler' <> 'Auf dem PC ist nicht genug Platz.' THEN
    RAISE EXCEPTION '099.17: video_fehler nicht sichtbar'; END IF;
  IF NOT marketing.video_neu_bauen(a) THEN RAISE EXCEPTION '099.17: neu bauen'; END IF;
  IF (SELECT video_versuch || coalesce(video_fehler, '-') FROM marketing.video_werke WHERE auftrag = a) <> '1-' THEN
    RAISE EXCEPTION '099.17: Versuch/Fehler nach neu bauen'; END IF;
  IF ((marketing._video_werk_json(a))->>'video_versuch')::int <> 1 THEN RAISE EXCEPTION '099.17: video_versuch in der Werk-Form'; END IF;
  IF marketing.video_bau_gescheitert(a, 0, 'alt') THEN RAISE EXCEPTION '099.17: alter Versuch setzt Fehler'; END IF;
  IF NOT marketing.video_bau_gescheitert(a, 1, 'wieder') THEN RAISE EXCEPTION '099.17: zweites gescheitert'; END IF;
  IF NOT marketing.video_fertig_setzen(a, '{"datei":"v2.mp4"}') THEN RAISE EXCEPTION '099.17: fertig_setzen'; END IF;
  IF (SELECT video_fehler FROM marketing.video_werke WHERE auftrag = a) IS NOT NULL THEN
    RAISE EXCEPTION '099.17: Erfolg loescht video_fehler nicht'; END IF;
  IF marketing.video_neu_bauen(a) OR marketing.video_bau_gescheitert(a, 1, 'x') THEN
    RAISE EXCEPTION '099.17: neu bauen / Fehler ausserhalb von werkbank'; END IF;
  UPDATE marketing.video_werke SET video_fehler = 'x' WHERE auftrag = a;
  IF NOT marketing.video_szene_oeffnen(s1, 'Bild anders') THEN RAISE EXCEPTION '099.17: oeffnen'; END IF;
  IF (SELECT video_fehler FROM marketing.video_werke WHERE auftrag = a) IS NOT NULL THEN
    RAISE EXCEPTION '099.17: oeffnen loescht video_fehler nicht'; END IF;
  -- M10: Bild mit Quellen und Belegen; ohne Listen bleibt der alte Wert
  IF NOT marketing.video_szene_bild_setzen(s1, 'slide', '{}', '["q.md"]', '[{"aussage":"b 3","quelle":"q.md"}]') THEN
    RAISE EXCEPTION '099.17: bild_setzen mit Listen'; END IF;
  IF (SELECT quellen::text || (belege->0->>'aussage') FROM marketing.video_szenen WHERE id = s1) <> '["q.md"]b 3' THEN
    RAISE EXCEPTION '099.17: Quellen/Belege nicht gespeichert'; END IF;
  UPDATE marketing.video_szenen SET stand = 'geplant', rueckgabe_von = 'clip' WHERE id = s1;
  IF NOT marketing.video_szene_bild_setzen(s1, 'slide', '{}') THEN RAISE EXCEPTION '099.17: bild_setzen ohne Listen'; END IF;
  IF (SELECT quellen::text || (belege->0->>'aussage') FROM marketing.video_szenen WHERE id = s1) <> '["q.md"]b 3' THEN
    RAISE EXCEPTION '099.17: Quellen/Belege ohne Listen geaendert'; END IF;
END $$;

-- 18 (I5, M4): Vorschlaege beendeter Auftraege: entscheiden lehnt ab, lebend schliesst sie (o2)
DO $$
DECLARE a uuid := (SELECT v FROM t099 WHERE k = 'o2'); tc text := (SELECT kennung FROM t099k WHERE k = 'tc2'); g text;
        o1 uuid := (SELECT v FROM t099 WHERE k = 'o1'); t1 text; s2 uuid := (SELECT v FROM t099 WHERE k = 's2');
BEGIN
  IF NOT marketing.video_vorschlag_gepostet(tc, 'Szene C') THEN RAISE EXCEPTION '099.18: gepostet'; END IF;
  UPDATE marketing.video_werke SET gliederung_stand = 'geplant' WHERE auftrag = a;   -- nur in der Probe
  g := marketing.video_gliederung_setzen(a, '{"stufen":[{"szenen":[{"kurz":"a"},{"kurz":"b"},{"kurz":"c"}]}]}', '[]');
  -- lebender Vorschlag eines laufenden Auftrags (o1) bleibt unberuehrt
  t1 := (SELECT kennung FROM marketing.video_vorschlaege WHERE auftrag = o1 AND stand IN ('wartend', 'offen') LIMIT 1);
  IF t1 IS NULL THEN
    UPDATE marketing.video_szenen SET stand = 'geplant', rueckgabe_von = NULL WHERE id = s2;
    t1 := marketing.video_szene_text_setzen(s2, '{"sprechtext":"lebt"}');
  END IF;
  UPDATE auftrag.auftraege SET stand = 'abgebrochen' WHERE id = a;
  IF (marketing.video_entscheiden(g, 'passt', 'seite'))->>'ok' <> 'false'
     OR (marketing.video_entscheiden(tc, 'nochmal', 'rowboat'))->>'ok' <> 'false' THEN
    RAISE EXCEPTION '099.18: Entscheidung bei beendetem Auftrag'; END IF;
  IF (SELECT stand FROM marketing.video_vorschlaege WHERE kennung = g) <> 'wartend'
     OR (SELECT stand FROM marketing.video_vorschlaege WHERE kennung = tc) <> 'offen'
     OR (SELECT stand FROM marketing.video_szenen WHERE auftrag = a AND nr = 3) <> 'text_wartet' THEN
    RAISE EXCEPTION '099.18: abgelehnte Entscheidung hat etwas veraendert'; END IF;
  IF (SELECT count(*) FROM marketing.video_vorschlaege_lebend() x WHERE x->>'kennung' = tc AND x->>'stand' = 'zurueckziehen') <> 1
     OR (SELECT count(*) FROM marketing.video_vorschlaege_lebend() x WHERE x->>'kennung' = g) <> 0 THEN
    RAISE EXCEPTION '099.18: lebend schliesst nicht'; END IF;
  IF (SELECT stand FROM marketing.video_vorschlaege WHERE kennung = g) <> 'erledigt'
     OR (SELECT stand FROM marketing.video_vorschlaege WHERE kennung = tc) <> 'zurueckziehen' THEN
    RAISE EXCEPTION '099.18: Staende nach lebend'; END IF;
  IF (SELECT count(*) FROM marketing.video_vorschlaege WHERE auftrag = a AND stand IN ('wartend', 'offen')) <> 0 THEN
    RAISE EXCEPTION '099.18: beendeter Auftrag haelt Plaetze'; END IF;
  IF (SELECT stand FROM marketing.video_vorschlaege WHERE kennung = t1) NOT IN ('wartend', 'offen') THEN
    RAISE EXCEPTION '099.18: Vorschlag eines laufenden Auftrags geschlossen'; END IF;
  IF NOT marketing.video_vorschlag_erledigt(tc) THEN RAISE EXCEPTION '099.18: erledigt nach Rueckzug'; END IF;
END $$;

-- 14: Rechte und Schreibweise: nichts fuer PUBLIC, sales_app, anon, authenticated
DO $$
DECLARE p record; n int := 0;
BEGIN
  FOR p IN SELECT pr.oid::regprocedure AS f FROM pg_proc pr JOIN pg_namespace ns ON ns.oid = pr.pronamespace
            WHERE ns.nspname = 'marketing' AND pr.proname IN (
              'video_werk_beginnen', 'video_gliederung_setzen', 'video_wunsch_setzen', 'video_entscheiden',
              'video_szene_text_setzen', 'video_szene_bild_setzen', 'video_szene_nehmen', 'video_szene_clip_setzen',
              'video_szene_fehler', 'video_szene_nochmal', 'video_szene_oeffnen', 'video_hinweis',
              'video_aufruf_zaehlen', 'video_arbeit', 'video_fertig_setzen', 'video_vorschlaege_lebend',
              'video_vorschlag_gepostet', 'video_vorschlag_erledigt', 'video_vorschlag_abgehakt',
              '_video_zaehlen', '_video_kennung', '_video_werk_json', '_video_gliederung_szenen',
              'video_szene_wunsch', 'video_gliederung_wunsch', 'video_szene_aufgeben', 'video_gliederung_aufgeben',
              'video_bau_gescheitert', 'video_neu_bauen', '_video_wunsch_anhaengen')
  LOOP
    n := n + 1;
    IF has_function_privilege('public', p.f, 'execute') OR has_function_privilege('sales_app', p.f, 'execute')
       OR has_function_privilege('anon', p.f, 'execute') OR has_function_privilege('authenticated', p.f, 'execute') THEN
      RAISE EXCEPTION '099.14: % ist fuer Fremde ausfuehrbar', p.f;
    END IF;
  END LOOP;
  IF n <> 30 THEN RAISE EXCEPTION '099.14: % statt 30 Funktionen', n; END IF;
  FOR p IN SELECT r.rolle, t.tabelle, pr.recht
             FROM (VALUES ('public'), ('sales_app'), ('anon'), ('authenticated')) r(rolle),
                  (VALUES ('marketing.video_werke'), ('marketing.video_szenen'), ('marketing.video_vorschlaege')) t(tabelle),
                  (VALUES ('select'), ('insert'), ('update'), ('delete')) pr(recht)
  LOOP
    IF has_table_privilege(p.rolle, p.tabelle, p.recht) THEN
      RAISE EXCEPTION '099.14: % hat % auf %', p.rolle, p.recht, p.tabelle; END IF;
  END LOOP;
  RAISE NOTICE 'verify_099 ok';
END $$;
