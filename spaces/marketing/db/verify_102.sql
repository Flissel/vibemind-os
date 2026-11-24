-- verify_102: Higgsfield-Uebergaenge der Werkbank. LAEUFT NUR in der zurueckgerollten migration_probe
-- (102 + verify_102): der erste Block stellt fremde offene Auftraege beiseite und legt Probezeilen an - nie gegen
-- die echte Datenbank ausfuehren. now() steht in einer Transaktion still, darum werden Leihzeiten per UPDATE in die
-- Vergangenheit gelegt. Zwei Auftraege: o1 = Zustandsmaschine und Geldregeln, o2 = Grenze von 6 Stellen.

CREATE TEMP TABLE t102 (k text PRIMARY KEY, v uuid);

-- Eine Szene von geplant bis fertig bringen (Text, Freigabe, Erzeugen, Clip, Freigabe).
CREATE FUNCTION pg_temp.szene_fertig(p_szene uuid, p_uebergang text) RETURNS void LANGUAGE plpgsql AS $$
DECLARE k text; r jsonb; s jsonb; v_a uuid;
BEGIN
  SELECT auftrag INTO v_a FROM marketing.video_szenen WHERE id = p_szene;
  IF (SELECT stand FROM marketing.video_szenen WHERE id = p_szene) = 'geplant' THEN
    k := marketing.video_szene_text_setzen(p_szene,
           jsonb_build_object('sprechtext', 'Probetext', 'bildart', 'slide', 'uebergang', p_uebergang));
    IF k IS NULL THEN RAISE EXCEPTION '102.H: Text nicht angenommen'; END IF;
    r := marketing.video_entscheiden(k, 'passt', 'seite');
    IF r->>'ok' <> 'true' THEN RAISE EXCEPTION '102.H: Text nicht freigegeben %', r; END IF;
  END IF;
  s := marketing.video_szene_nehmen(v_a, interval '20 min');
  IF s IS NULL OR (s->>'id')::uuid <> p_szene THEN RAISE EXCEPTION '102.H: falsche Szene genommen %', s; END IF;
  k := marketing.video_szene_clip_setzen(p_szene, (s->>'variante')::int, 'probe.mp4', 5, 'standard', 'chatterbox');
  IF k IS NULL THEN RAISE EXCEPTION '102.H: Clip nicht angenommen'; END IF;
  r := marketing.video_entscheiden(k, 'passt', 'seite');
  IF r->>'stand' <> 'fertig' THEN RAISE EXCEPTION '102.H: Clip nicht freigegeben %', r; END IF;
END $$;

CREATE FUNCTION pg_temp.teil(p_von int, p_nach int, p_prompt text, p_preis numeric) RETURNS jsonb LANGUAGE sql AS $$
  SELECT jsonb_build_object('start_bild', 'a.jpg', 'end_bild', 'b.jpg', 'prompt', p_prompt, 'dauer_s', 3,
                            'preis', p_preis, 'modell', 'kling3_0', 'von_variante', p_von, 'nach_variante', p_nach);
$$;

-- 0: zwei Video-Auftraege mit Gliederung und Szenen
DO $$
DECLARE a uuid; b uuid; r jsonb; k text;
BEGIN
  UPDATE auftrag.auftraege SET stand = 'abgebrochen' WHERE stand IN ('offen', 'in_arbeit');
  a := auftrag.anlegen('sales', 'marketing', 'p102a', 'probe', 'video', '{"thema":"Probe eins"}');
  r := auftrag.video_naechster(interval '10 min');
  IF (r->>'id')::uuid <> a THEN RAISE EXCEPTION '102.0: o1 nicht uebernommen'; END IF;
  b := auftrag.anlegen('sales', 'marketing', 'p102b', 'probe', 'video', '{"thema":"Probe zwei"}');
  r := auftrag.video_naechster(interval '10 min');
  IF (r->>'id')::uuid <> b THEN RAISE EXCEPTION '102.0: o2 nicht uebernommen'; END IF;
  INSERT INTO t102 VALUES ('o1', a), ('o2', b);
  PERFORM marketing.video_werk_beginnen(a);
  PERFORM marketing.video_werk_beginnen(b);
  k := marketing.video_gliederung_setzen(a,
    '{"titel":"Probe","stufen":[{"name":"Anfang","szenen":[{"kurz":"s1"},{"kurz":"s2"}]},{"name":"Ende","szenen":[{"kurz":"s3"},{"kurz":"s4"}]}]}', '[]');
  r := marketing.video_entscheiden(k, 'passt', 'seite');
  IF r->>'ok' <> 'true' THEN RAISE EXCEPTION '102.0: Gliederung o1 %', r; END IF;
  k := marketing.video_gliederung_setzen(b,
    '{"titel":"Viele","stufen":[{"name":"A","szenen":["1","2","3","4","5","6","7","8","9"]}]}', '[]');
  r := marketing.video_entscheiden(k, 'passt', 'seite');
  IF r->>'ok' <> 'true' THEN RAISE EXCEPTION '102.0: Gliederung o2 %', r; END IF;
  INSERT INTO t102 SELECT 's' || nr, id FROM marketing.video_szenen WHERE auftrag = a;
END $$;

-- 1: der dritte Uebergangswert; erste Szene und siebte Stelle werden still zum Schnitt
DO $$
DECLARE a uuid := (SELECT v FROM t102 WHERE k = 'o1'); b uuid := (SELECT v FROM t102 WHERE k = 'o2'); s record;
BEGIN
  PERFORM pg_temp.szene_fertig((SELECT v FROM t102 WHERE k = 's1'), 'higgsfield');
  IF (SELECT uebergang FROM marketing.video_szenen WHERE auftrag = a AND nr = 1) <> 'schnitt' THEN
    RAISE EXCEPTION '102.1: erste Szene behielt higgsfield'; END IF;
  IF (marketing._video_werk_json(a))->>'hf_blockiert' <> 'false' THEN RAISE EXCEPTION '102.1: blockiert ohne Stelle'; END IF;
  FOR s IN SELECT id FROM marketing.video_szenen WHERE auftrag = b ORDER BY nr LOOP
    IF marketing.video_szene_text_setzen(s.id, '{"sprechtext":"x","uebergang":"higgsfield"}') IS NULL THEN
      RAISE EXCEPTION '102.1: Text o2 abgelehnt'; END IF;
  END LOOP;
  IF (SELECT count(*) FROM marketing.video_szenen WHERE auftrag = b AND uebergang = 'higgsfield') <> 6
     OR (SELECT string_agg(nr::text, ',' ORDER BY nr) FROM marketing.video_szenen
          WHERE auftrag = b AND uebergang = 'schnitt') <> '1,8,9' THEN
    RAISE EXCEPTION '102.1: Grenze von 6 Stellen'; END IF;
  BEGIN
    UPDATE marketing.video_szenen SET uebergang = 'zoom' WHERE auftrag = b AND nr = 9;
    RAISE EXCEPTION '102.1: unbekannter Uebergang angenommen';
  EXCEPTION WHEN check_violation THEN NULL; END;
END $$;

-- 2: Vorschlag erst, wenn beide Szenen fertig sind; Formpruefung; kein zweiter Vorschlag
DO $$
DECLARE a uuid := (SELECT v FROM t102 WHERE k = 'o1'); s2 uuid := (SELECT v FROM t102 WHERE k = 's2'); h uuid; f jsonb;
BEGIN
  IF marketing.video_hf_vorschlagen(a, s2, pg_temp.teil(0, 0, 'Papier wird geschnitten', 5.25)) IS NOT NULL THEN
    RAISE EXCEPTION '102.2: Vorschlag vor fertiger Szene'; END IF;
  PERFORM pg_temp.szene_fertig(s2, 'higgsfield');
  f := marketing.video_hf_abgleichen(a);
  IF jsonb_array_length(f->'fehlend') <> 1 OR (f->'fehlend'->0->>'nach_nr')::int <> 2
     OR (f->'fehlend'->0->>'von_nr')::int <> 1 OR f->'fehlend'->0->>'von_datei' <> 'probe.mp4' THEN
    RAISE EXCEPTION '102.2: fehlend %', f; END IF;
  IF NOT marketing._video_hf_blockiert(a) THEN RAISE EXCEPTION '102.2: fehlender Vorschlag blockiert nicht'; END IF;
  IF marketing.video_hf_vorschlagen(a, s2, pg_temp.teil(0, 1, 'x', 5.25)) IS NOT NULL
     OR marketing.video_hf_vorschlagen(a, s2, pg_temp.teil(1, 0, 'x', 5.25)) IS NOT NULL THEN
    RAISE EXCEPTION '102.2: falsche Variante angenommen'; END IF;
  IF marketing.video_hf_vorschlagen(a, s2, pg_temp.teil(0, 0, E'zwei\nZeilen', 5.25)) IS NOT NULL
     OR marketing.video_hf_vorschlagen(a, s2, pg_temp.teil(0, 0, repeat('x', 1001), 5.25)) IS NOT NULL
     OR marketing.video_hf_vorschlagen(a, s2, pg_temp.teil(0, 0, '   ', 5.25)) IS NOT NULL
     OR marketing.video_hf_vorschlagen(a, s2, pg_temp.teil(0, 0, 'x', 5.25) - 'start_bild') IS NOT NULL
     OR marketing.video_hf_vorschlagen(a, (SELECT v FROM t102 WHERE k = 's3'), pg_temp.teil(0, 0, 'x', 5.25)) IS NOT NULL THEN
    RAISE EXCEPTION '102.2: ungueltiger Vorschlag angenommen'; END IF;
  h := marketing.video_hf_vorschlagen(a, s2, pg_temp.teil(0, 0, 'Papier wird geschnitten', 5.25));
  IF h IS NULL THEN RAISE EXCEPTION '102.2: Vorschlag abgelehnt'; END IF;
  INSERT INTO t102 VALUES ('h1', h);
  IF (SELECT stand || ':' || versuch || ':' || preis FROM marketing.video_hf_clips WHERE id = h) <> 'vorgeschlagen:1:5.25' THEN
    RAISE EXCEPTION '102.2: Zeile'; END IF;
  IF marketing.video_hf_vorschlagen(a, s2, pg_temp.teil(0, 0, 'nochmal', 5.25)) IS NOT NULL THEN
    RAISE EXCEPTION '102.2: zweiter Vorschlag an derselben Stelle'; END IF;
  IF (SELECT ergebnis->'werkbank'->>'hf_wartet' FROM auftrag.auftraege WHERE id = a) <> '1' THEN
    RAISE EXCEPTION '102.2: Zaehler hf_wartet'; END IF;
  IF marketing.video_hf_nehmen(interval '10 min') IS NOT NULL THEN RAISE EXCEPTION '102.2: Vorschlag ohne Freigabe vergeben'; END IF;
END $$;

-- 3: Freigabe nur ueber die Seite, mit gesehenem Preis und gueltigem Text; der zweite Klick wird abgelehnt
DO $$
DECLARE a uuid := (SELECT v FROM t102 WHERE k = 'o1'); h uuid := (SELECT v FROM t102 WHERE k = 'h1'); r jsonb;
BEGIN
  r := marketing.video_hf_entscheiden(h, 'erzeugen', 'Text', 5.25, 'felix', 'rowboat');
  IF r->>'ok' <> 'false' OR r->>'grund' <> 'weg' THEN RAISE EXCEPTION '102.3: Weg rowboat %', r; END IF;
  r := marketing.video_hf_entscheiden(h, 'erzeugen', 'Text', 5.25, 'felix', NULL);
  IF r->>'grund' <> 'weg' THEN RAISE EXCEPTION '102.3: Weg fehlt %', r; END IF;
  r := marketing.video_hf_entscheiden(h, 'erzeugen', 'Text', 5.00, 'felix', 'seite');
  IF r->>'grund' <> 'preis' OR (r->>'preis')::numeric <> 5.25 THEN RAISE EXCEPTION '102.3: Preis %', r; END IF;
  r := marketing.video_hf_entscheiden(h, 'erzeugen', 'Text', NULL, 'felix', 'seite');
  IF r->>'grund' <> 'preis' THEN RAISE EXCEPTION '102.3: Preis fehlt %', r; END IF;
  r := marketing.video_hf_entscheiden(h, 'erzeugen', E'boese\tZeichen', 5.25, 'felix', 'seite');
  IF r->>'grund' <> 'prompt' THEN RAISE EXCEPTION '102.3: Text %', r; END IF;
  r := marketing.video_hf_entscheiden(h, 'passt', 'Text', 5.25, 'felix', 'seite');
  IF r->>'ok' <> 'false' THEN RAISE EXCEPTION '102.3: unbekanntes Urteil %', r; END IF;
  IF (SELECT stand FROM marketing.video_hf_clips WHERE id = h) <> 'vorgeschlagen' THEN
    RAISE EXCEPTION '102.3: abgelehnte Freigabe hat etwas veraendert'; END IF;
  r := marketing.video_hf_entscheiden(h, 'erzeugen', '  Geaenderter Text  ', 5.25, 'felix', 'seite');
  IF r->>'ok' <> 'true' OR r->>'stand' <> 'freigegeben' THEN RAISE EXCEPTION '102.3: Freigabe %', r; END IF;
  IF (SELECT prompt || '|' || entschieden_von FROM marketing.video_hf_clips WHERE id = h) <> 'Geaenderter Text|felix' THEN
    RAISE EXCEPTION '102.3: Text oder Name nicht gespeichert'; END IF;
  r := marketing.video_hf_entscheiden(h, 'erzeugen', 'Geaenderter Text', 5.25, 'felix', 'seite');
  IF r->>'ok' <> 'false' OR r->>'grund' <> 'stand' THEN RAISE EXCEPTION '102.3: zweiter Klick %', r; END IF;
  r := marketing.video_hf_entscheiden(h, 'verwerfen', NULL, NULL, 'felix', 'seite');
  IF r->>'ok' <> 'false' THEN RAISE EXCEPTION '102.3: freigegebener Clip verworfen %', r; END IF;
  IF NOT marketing._video_hf_blockiert(a) THEN RAISE EXCEPTION '102.3: freigegeben blockiert nicht'; END IF;
END $$;

-- 4: Vergeben, Preis vor dem Bestellen, Fehler, Nochmal
DO $$
DECLARE a uuid := (SELECT v FROM t102 WHERE k = 'o1'); h uuid := (SELECT v FROM t102 WHERE k = 'h1'); j jsonb; r jsonb; h2 uuid;
BEGIN
  j := marketing.video_hf_nehmen(interval '10 min');
  IF (j->>'id')::uuid <> h OR j->>'stand' <> 'bestellt' OR (j->>'nach_nr')::int <> 2 OR (j->>'von_nr')::int <> 1
     OR j->>'mandant' <> 'probe' OR j->>'prompt' <> 'Geaenderter Text' OR j->>'hf_auftrag' IS NOT NULL THEN
    RAISE EXCEPTION '102.4: nehmen %', j; END IF;
  IF marketing.video_hf_nehmen(interval '10 min') IS NOT NULL THEN RAISE EXCEPTION '102.4: doppelt vergeben'; END IF;
  IF marketing.video_hf_fertig(h, 'x.mp4', 3) THEN RAISE EXCEPTION '102.4: fertig ohne Nummer'; END IF;
  -- Preis hat sich geaendert: zurueck auf vorgeschlagen, neuer Preis, neu klicken
  IF marketing.video_hf_preis_pruefen(h, 8.75) THEN RAISE EXCEPTION '102.4: anderer Preis angenommen'; END IF;
  IF (SELECT stand || ':' || preis || ':' || (hinweis IS NOT NULL) || ':' || (entschieden_am IS NULL)
        FROM marketing.video_hf_clips WHERE id = h) <> 'vorgeschlagen:8.75:true:true' THEN
    RAISE EXCEPTION '102.4: Preisabweichung'; END IF;
  IF marketing.video_hf_auftrag_merken(h, 'job-12345678', 8.75) THEN RAISE EXCEPTION '102.4: Nummer ohne Bestellung'; END IF;
  r := marketing.video_hf_entscheiden(h, 'erzeugen', 'Geaenderter Text', 5.25, 'felix', 'seite');
  IF r->>'grund' <> 'preis' THEN RAISE EXCEPTION '102.4: alter Preis %', r; END IF;
  r := marketing.video_hf_entscheiden(h, 'erzeugen', 'Geaenderter Text', 8.75, 'felix', 'seite');
  IF r->>'ok' <> 'true' THEN RAISE EXCEPTION '102.4: neue Freigabe %', r; END IF;
  j := marketing.video_hf_nehmen(interval '10 min');
  IF (j->>'id')::uuid <> h THEN RAISE EXCEPTION '102.4: zweites Nehmen'; END IF;
  IF NOT marketing.video_hf_preis_pruefen(h, 8.75) THEN RAISE EXCEPTION '102.4: gleicher Preis abgelehnt'; END IF;
  -- Bestellen scheitert: fehler, wartet auf den Menschen
  IF NOT marketing.video_hf_fehler(h, 'Die Anmeldung bei Higgsfield ist abgelaufen.') THEN RAISE EXCEPTION '102.4: Fehler melden'; END IF;
  IF marketing.video_hf_fehler(h, 'nochmal') THEN RAISE EXCEPTION '102.4: Fehler doppelt'; END IF;
  IF (SELECT stand FROM marketing.video_hf_clips WHERE id = h) <> 'fehler'
     OR (SELECT ergebnis->'werkbank'->>'hf_fehler' FROM auftrag.auftraege WHERE id = a) <> '1'
     OR NOT EXISTS (SELECT 1 FROM marketing.video_werke w, jsonb_array_elements(w.hinweise) x
                     WHERE w.auftrag = a AND x->>'key' LIKE 'hf\_%') THEN
    RAISE EXCEPTION '102.4: Fehlerstand oder Hinweis'; END IF;
  IF marketing.video_hf_nehmen(interval '10 min') IS NOT NULL THEN RAISE EXCEPTION '102.4: Fehler neu vergeben'; END IF;
  r := marketing.video_hf_entscheiden(h, 'erzeugen', 'Text', 8.75, 'felix', 'seite');
  IF r->>'grund' <> 'stand' THEN RAISE EXCEPTION '102.4: Fehler direkt neu bestellt %', r; END IF;
  IF marketing.video_hf_nochmal(h, 'Neuer Text', 'felix', 'rowboat') IS NOT NULL
     OR marketing.video_hf_nochmal(h, E'x\ny', 'felix', 'seite') IS NOT NULL THEN
    RAISE EXCEPTION '102.4: Nochmal ueber falschen Weg oder mit falschem Text'; END IF;
  h2 := marketing.video_hf_nochmal(h, 'Neuer Text', 'felix', 'seite');
  IF h2 IS NULL OR (SELECT stand FROM marketing.video_hf_clips WHERE id = h) <> 'verworfen'
     OR (SELECT stand || ':' || versuch || ':' || prompt FROM marketing.video_hf_clips WHERE id = h2) <> 'vorgeschlagen:2:Neuer Text' THEN
    RAISE EXCEPTION '102.4: Nochmal'; END IF;
  IF marketing.video_hf_nehmen(interval '10 min') IS NOT NULL THEN RAISE EXCEPTION '102.4: Nochmal ohne Klick vergeben'; END IF;
  INSERT INTO t102 VALUES ('h2', h2);
END $$;

-- 5: unterbrochene Bestellung ohne Nummer wird NIE wiederholt; mit Nummer wird weiter gewartet
DO $$
DECLARE a uuid := (SELECT v FROM t102 WHERE k = 'o1'); h2 uuid := (SELECT v FROM t102 WHERE k = 'h2'); h3 uuid; j jsonb; r jsonb;
BEGIN
  r := marketing.video_hf_entscheiden(h2, 'erzeugen', 'Neuer Text', 8.75, 'felix', 'seite');
  j := marketing.video_hf_nehmen(interval '10 min');
  IF (j->>'id')::uuid <> h2 THEN RAISE EXCEPTION '102.5: nehmen h2'; END IF;
  UPDATE marketing.video_hf_clips SET vergeben_bis = now() - interval '1 min' WHERE id = h2;
  IF marketing.video_hf_nehmen(interval '10 min') IS NOT NULL THEN RAISE EXCEPTION '102.5: Bestellung ohne Nummer neu vergeben'; END IF;
  IF (SELECT stand FROM marketing.video_hf_clips WHERE id = h2) <> 'fehler'
     OR (SELECT fehler FROM marketing.video_hf_clips WHERE id = h2) NOT LIKE '%unterbrochen%' THEN
    RAISE EXCEPTION '102.5: unterbrochene Bestellung nicht auf fehler'; END IF;
  h3 := marketing.video_hf_nochmal(h2, 'Dritter Text', 'felix', 'seite');
  r := marketing.video_hf_entscheiden(h3, 'erzeugen', 'Dritter Text', 8.75, 'felix', 'seite');
  IF r->>'ok' <> 'true' THEN RAISE EXCEPTION '102.5: Freigabe h3 %', r; END IF;
  j := marketing.video_hf_nehmen(interval '10 min');
  IF (j->>'id')::uuid <> h3 OR NOT marketing.video_hf_preis_pruefen(h3, 8.75) THEN RAISE EXCEPTION '102.5: h3'; END IF;
  IF marketing.video_hf_auftrag_merken(h3, 'zu kurz', 8.75) OR marketing.video_hf_auftrag_merken(h3, NULL, 8.75)
     OR marketing.video_hf_auftrag_merken(h3, '--help', 8.75) THEN
    RAISE EXCEPTION '102.5: ungueltige Nummer angenommen'; END IF;
  IF NOT marketing.video_hf_auftrag_merken(h3, '8323a0b9-2ef6-4665-89a2-2d20ee770dfb', 8.75) THEN
    RAISE EXCEPTION '102.5: Nummer nicht vermerkt'; END IF;
  IF marketing.video_hf_auftrag_merken(h3, 'andere-nummer-1', 8.75) THEN RAISE EXCEPTION '102.5: Nummer ueberschrieben'; END IF;
  IF marketing.video_hf_preis_pruefen(h3, 8.75) THEN RAISE EXCEPTION '102.5: Preispruefung nach der Bestellung'; END IF;
  IF (SELECT preis_bestellt FROM marketing.video_hf_clips WHERE id = h3) <> 8.75
     OR (SELECT ergebnis->'werkbank'->>'hf_credits' FROM auftrag.auftraege WHERE id = a) <> '8.75' THEN
    RAISE EXCEPTION '102.5: bestellter Preis'; END IF;
  IF NOT marketing.video_hf_weiter(h3, interval '10 min') THEN RAISE EXCEPTION '102.5: Leihe verlaengern'; END IF;
  UPDATE marketing.video_hf_clips SET vergeben_bis = now() - interval '1 min' WHERE id = h3;
  j := marketing.video_hf_nehmen(interval '10 min');
  IF (j->>'id')::uuid <> h3 OR j->>'hf_auftrag' <> '8323a0b9-2ef6-4665-89a2-2d20ee770dfb' OR j->>'stand' <> 'bestellt' THEN
    RAISE EXCEPTION '102.5: bestellter Clip mit Nummer nicht weitergegeben %', j; END IF;
  IF marketing.video_hf_fertig(h3, '', 3) OR marketing.video_hf_fertig(h3, 'x.mp4', 0) THEN
    RAISE EXCEPTION '102.5: ungueltige Fertigmeldung'; END IF;
  IF NOT marketing.video_hf_fertig(h3, 'wb-00000000-h02-f3.mp4', 2.0) THEN RAISE EXCEPTION '102.5: fertig'; END IF;
  IF marketing.video_hf_fertig(h3, 'wb-00000000-h02-f3.mp4', 2.0) OR marketing.video_hf_weiter(h3, interval '1 min') THEN
    RAISE EXCEPTION '102.5: fertig doppelt'; END IF;
  IF marketing._video_hf_blockiert(a) THEN RAISE EXCEPTION '102.5: fertiger Clip blockiert'; END IF;
  IF (marketing._video_werk_json(a))->'hf_uebergaenge' <> '[{"nach_nr": 2, "datei": "wb-00000000-h02-f3.mp4", "dauer_s": 2.0}]'::jsonb THEN
    RAISE EXCEPTION '102.5: hf_uebergaenge %', (marketing._video_werk_json(a))->'hf_uebergaenge'; END IF;
  INSERT INTO t102 VALUES ('h3', h3);
END $$;

-- 6: fehlender Vorschlag haelt das Video auf; "ohne Uebergang weiter"
DO $$
DECLARE a uuid := (SELECT v FROM t102 WHERE k = 'o1'); l jsonb;
BEGIN
  PERFORM pg_temp.szene_fertig((SELECT v FROM t102 WHERE k = 's3'), 'higgsfield');
  PERFORM pg_temp.szene_fertig((SELECT v FROM t102 WHERE k = 's4'), 'schnitt');
  IF NOT marketing._video_hf_blockiert(a) THEN RAISE EXCEPTION '102.6: Stelle 3 blockiert nicht'; END IF;
  IF marketing.video_fertig_setzen(a, '{"datei":"v.mp4"}') THEN RAISE EXCEPTION '102.6: Video trotz offener Stelle'; END IF;
  l := marketing.video_hf_liste(a);
  IF l->'vorbereitung' <> '[3]'::jsonb OR jsonb_array_length(l->'clips') <> 1 OR l->'clips'->0->>'stand' <> 'fertig'
     OR (l->>'credits')::numeric <> 8.75 OR (l->>'bestellt')::int <> 1 OR l->'clips'->0->>'aktuell' <> 'true' THEN
    RAISE EXCEPTION '102.6: Liste %', l; END IF;
  IF marketing.video_hf_ohne(a, 3, 'rowboat') OR marketing.video_hf_ohne(a, 4, 'seite') OR marketing.video_hf_ohne(a, 2, 'seite') THEN
    RAISE EXCEPTION '102.6: ohne Uebergang falsch angenommen'; END IF;
  IF NOT marketing.video_hf_ohne(a, 3, 'seite') THEN RAISE EXCEPTION '102.6: ohne Uebergang'; END IF;
  IF marketing._video_hf_blockiert(a) OR (SELECT uebergang FROM marketing.video_szenen WHERE auftrag = a AND nr = 3) <> 'schnitt' THEN
    RAISE EXCEPTION '102.6: Stelle 3 nicht aufgeloest'; END IF;
END $$;

-- 7: neue Variante einer Szene macht den Clip ueberholt; es wird neu VORGESCHLAGEN, nie von selbst bestellt
DO $$
DECLARE a uuid := (SELECT v FROM t102 WHERE k = 'o1'); s2 uuid := (SELECT v FROM t102 WHERE k = 's2');
        h3 uuid := (SELECT v FROM t102 WHERE k = 'h3'); f jsonb; h4 uuid; r jsonb;
BEGIN
  IF NOT marketing.video_szene_oeffnen(s2, NULL) THEN RAISE EXCEPTION '102.7: oeffnen'; END IF;
  IF (marketing._video_werk_json(a))->'hf_uebergaenge' <> '[]'::jsonb THEN RAISE EXCEPTION '102.7: alter Clip noch im Video'; END IF;
  IF NOT marketing._video_hf_blockiert(a) THEN RAISE EXCEPTION '102.7: veralteter Clip blockiert nicht'; END IF;
  f := marketing.video_hf_abgleichen(a);
  IF (SELECT stand FROM marketing.video_hf_clips WHERE id = h3) <> 'ueberholt' OR jsonb_array_length(f->'fehlend') <> 0 THEN
    RAISE EXCEPTION '102.7: nicht ueberholt %', f; END IF;
  IF marketing.video_hf_nochmal(h3, 'x', 'felix', 'seite') IS NOT NULL THEN RAISE EXCEPTION '102.7: Nochmal an ueberholtem Clip'; END IF;
  PERFORM pg_temp.szene_fertig(s2, 'higgsfield');
  f := marketing.video_hf_abgleichen(a);
  IF jsonb_array_length(f->'fehlend') <> 1 OR (f->'fehlend'->0->>'nach_variante')::int <> 1 THEN
    RAISE EXCEPTION '102.7: neue Variante fehlt nicht %', f; END IF;
  IF EXISTS (SELECT 1 FROM marketing.video_hf_clips WHERE auftrag = a AND stand IN ('freigegeben', 'bestellt'))
     OR marketing.video_hf_nehmen(interval '10 min') IS NOT NULL THEN
    RAISE EXCEPTION '102.7: von selbst nachbestellt'; END IF;
  h4 := marketing.video_hf_vorschlagen(a, s2, pg_temp.teil(0, 1, 'Vierter Text', 5.25));
  IF h4 IS NULL OR (SELECT versuch FROM marketing.video_hf_clips WHERE id = h4) <> 4 THEN RAISE EXCEPTION '102.7: neuer Vorschlag'; END IF;
  IF marketing.video_fertig_setzen(a, '{"datei":"v.mp4"}') THEN RAISE EXCEPTION '102.7: Video trotz offenem Vorschlag'; END IF;
  r := marketing.video_hf_entscheiden(h4, 'verwerfen', NULL, NULL, 'felix', 'rowboat');
  IF r->>'grund' <> 'weg' THEN RAISE EXCEPTION '102.7: verwerfen ueber rowboat %', r; END IF;
  r := marketing.video_hf_entscheiden(h4, 'verwerfen', NULL, NULL, 'felix', 'seite');
  IF r->>'stand' <> 'verworfen' THEN RAISE EXCEPTION '102.7: verwerfen %', r; END IF;
  -- verworfen = normaler Schnitt: nichts fehlt mehr, das Video darf gebaut werden
  IF marketing._video_hf_blockiert(a) OR jsonb_array_length((marketing.video_hf_abgleichen(a))->'fehlend') <> 0 THEN
    RAISE EXCEPTION '102.7: verworfener Clip blockiert'; END IF;
  IF NOT marketing.video_fertig_setzen(a, '{"datei":"v.mp4"}') THEN RAISE EXCEPTION '102.7: Video nicht gemeldet'; END IF;
  -- nach dem Bau: kein Vorschlag, keine Freigabe mehr
  IF jsonb_array_length((marketing.video_hf_abgleichen(a))->'fehlend') <> 0
     OR marketing.video_hf_nochmal(h4, 'x', 'felix', 'seite') IS NOT NULL THEN
    RAISE EXCEPTION '102.7: Arbeit nach dem Bau'; END IF;
  IF (SELECT ergebnis->'werkbank'->>'hf_credits' FROM auftrag.auftraege WHERE id = a) <> '8.75' THEN
    RAISE EXCEPTION '102.7: Summe der Credits'; END IF;
END $$;

-- 8: Rechte und Formgrenzen der Tabelle
DO $$
DECLARE a uuid := (SELECT v FROM t102 WHERE k = 'o1'); s2 uuid := (SELECT v FROM t102 WHERE k = 's2'); s1 uuid := (SELECT v FROM t102 WHERE k = 's1');
BEGIN
  IF has_table_privilege('sales_app', 'marketing.video_hf_clips', 'SELECT')
     OR has_table_privilege('anon', 'marketing.video_hf_clips', 'SELECT')
     OR has_table_privilege('authenticated', 'marketing.video_hf_clips', 'SELECT')
     OR has_function_privilege('sales_app', 'marketing.video_hf_entscheiden(uuid, text, text, numeric, text, text)', 'EXECUTE')
     OR has_function_privilege('anon', 'marketing.video_hf_nehmen(interval)', 'EXECUTE') THEN
    RAISE EXCEPTION '102.8: fremde Rolle hat Zugriff'; END IF;
  BEGIN
    INSERT INTO marketing.video_hf_clips (auftrag, von_szene, nach_szene, von_variante, nach_variante, versuch, prompt, modell, dauer_s, preis, stand)
    VALUES (a, s1, s2, 0, 1, 90, E'mit\nUmbruch', 'kling3_0', 3, 5, 'verworfen');
    RAISE EXCEPTION '102.8: Text mit Steuerzeichen in der Tabelle';
  EXCEPTION WHEN check_violation THEN NULL; END;
  BEGIN
    INSERT INTO marketing.video_hf_clips (auftrag, nach_szene, nach_variante, versuch, prompt, modell, dauer_s, preis, stand)
    VALUES (a, s2, 1, 91, 'ohne von', 'kling3_0', 3, 5, 'verworfen');
    RAISE EXCEPTION '102.8: Uebergang ohne Ausgangsszene';
  EXCEPTION WHEN check_violation THEN NULL; END;
  INSERT INTO marketing.video_hf_clips (auftrag, von_szene, nach_szene, von_variante, nach_variante, versuch, prompt, modell, dauer_s, preis, stand)
  VALUES (a, s1, s2, 0, 1, 92, 'lebt', 'kling3_0', 3, 5, 'vorgeschlagen');
  BEGIN
    INSERT INTO marketing.video_hf_clips (auftrag, von_szene, nach_szene, von_variante, nach_variante, versuch, prompt, modell, dauer_s, preis, stand)
    VALUES (a, s1, s2, 0, 1, 93, 'lebt auch', 'kling3_0', 3, 5, 'fertig');
    RAISE EXCEPTION '102.8: zwei lebende Clips an einer Stelle';
  EXCEPTION WHEN unique_violation THEN NULL; END;
END $$;

SELECT 'verify_102 ok' AS ergebnis;
