-- verify_099: Werkbank-Tabellen und Szenen-Zustandsmaschine. LAEUFT NUR in der zurueckgerollten
-- migration_probe (098 + 099 + verify_099): der erste Block stellt fremde offene Auftraege beiseite und legt
-- Probezeilen an - nie gegen die echte Datenbank ausfuehren. now() steht in einer Transaktion still,
-- darum werden Leasezeiten per UPDATE in die Vergangenheit gelegt. Drei Auftraege insgesamt (die Tagesgrenze
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

  k2 := marketing.video_gliederung_setzen(a,
    '{"titel":"Probe","stufen":[{"name":"Anfang","szenen":[{"kurz":"s1"},{"kurz":"s2"}]},{"name":"Ende","szenen":[{"kurz":"s3"}]}]}',
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
  IF marketing.video_szene_text_setzen(s1, '{"sprechtext":"ueberschrieben"}') IS NOT NULL THEN
    RAISE EXCEPTION '099.5: text_setzen aendert den Sprechtext einer Clip-Rueckgabe'; END IF;
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
  IF marketing.video_fertig_setzen(a, '{"video":"v.mp4"}') THEN RAISE EXCEPTION '099.10: fertig_setzen mit offener Szene'; END IF;
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
  IF NOT marketing.video_fertig_setzen(a, '{"video":"v.mp4"}') THEN RAISE EXCEPTION '099.10: fertig_setzen'; END IF;
  IF (SELECT stand FROM auftrag.auftraege WHERE id = a) <> 'erzeugt' THEN RAISE EXCEPTION '099.10: Auftrag nicht erzeugt'; END IF;
  IF (SELECT (ergebnis->>'ki_hinweis') || (ergebnis->>'fassung') || (ergebnis->>'video') FROM auftrag.auftraege WHERE id = a)
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

-- 12: Zaehlung und Runden, 13: Hinweise je key einmal, Aufrufe (zweiter Auftrag)
DO $$
DECLARE a uuid := (SELECT v FROM t099 WHERE k = 'o2'); g text; ta text; tb text; tc text; sa uuid; sb uuid; sc uuid; e jsonb;
BEGIN
  IF marketing.video_werk_beginnen(a) IS NULL THEN RAISE EXCEPTION '099.12: werk_beginnen'; END IF;
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
              '_video_zaehlen', '_video_kennung', '_video_werk_json')
  LOOP
    n := n + 1;
    IF has_function_privilege('public', p.f, 'execute') OR has_function_privilege('sales_app', p.f, 'execute')
       OR has_function_privilege('anon', p.f, 'execute') OR has_function_privilege('authenticated', p.f, 'execute') THEN
      RAISE EXCEPTION '099.14: % ist fuer Fremde ausfuehrbar', p.f;
    END IF;
  END LOOP;
  IF n <> 22 THEN RAISE EXCEPTION '099.14: % statt 22 Funktionen', n; END IF;
  IF has_table_privilege('sales_app', 'marketing.video_werke', 'select')
     OR has_table_privilege('sales_app', 'marketing.video_szenen', 'select')
     OR has_table_privilege('sales_app', 'marketing.video_vorschlaege', 'select') THEN
    RAISE EXCEPTION '099.14: sales_app liest Werkbank-Tabellen'; END IF;
  RAISE NOTICE 'verify_099 ok';
END $$;
