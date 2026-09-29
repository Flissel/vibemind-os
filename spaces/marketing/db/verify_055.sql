-- verify_055.sql — Proben fuer 055_newsletter_editor_korrektur.sql. Aendert nichts (ROLLBACK).
BEGIN;
CREATE TEMP TABLE _g AS SELECT '{
 "root":{"type":"EmailLayout","data":{"backdropColor":"#1d3b39","canvasColor":"#0f2422","textColor":"#cfe3df","fontFamily":"MODERN_SANS","childrenIds":["h","b","s","c"]}},
 "h":{"type":"Heading","data":{"style":{"borderRadius":32},"props":{"text":"Neu","level":"h2"}}},
 "b":{"type":"Button","data":{"style":{},"props":{"text":"Los","url":"https://vibemind.space"}}},
 "s":{"type":"ColumnsContainer","data":{"style":{},"props":{"columnsCount":2,"columnsGap":48,"columns":[{"childrenIds":["i"]},{"childrenIds":["d"]},{"childrenIds":[]}]}}},
 "i":{"type":"Image","data":{"style":{},"props":{"url":"medien:logo.png","alt":"Logo","width":600,"height":0}}},
 "d":{"type":"Divider","data":{"style":{},"props":{"lineHeight":10}}},
 "c":{"type":"Container","data":{"style":{},"props":{"childrenIds":["t"]}}},
 "t":{"type":"Text","data":{"style":{"fontSize":72},"props":{"text":"Hallo","markdown":false}}}
}'::jsonb AS d;

CREATE FUNCTION pg_temp.mit(pfad text[], wert jsonb) RETURNS text LANGUAGE sql AS
$$ SELECT marketing.pult_bloecke_fehler(jsonb_set((SELECT d FROM _g), pfad, wert)) $$;
CREATE FUNCTION pg_temp.plus(extra jsonb, eltern text[], kinder jsonb) RETURNS text LANGUAGE sql AS
$$ SELECT marketing.pult_bloecke_fehler(jsonb_set((SELECT d FROM _g) || extra, eltern, kinder)) $$;

-- Grenzwerte (= Editor-Maxima) bleiben gueltig
DO $$ DECLARE v text; BEGIN
  v := marketing.pult_bloecke_fehler((SELECT d FROM _g));
  IF v IS NOT NULL THEN RAISE EXCEPTION 'PROBE: gueltiges Dokument an den Grenzwerten abgelehnt: %', v; END IF;
END $$;

-- I2: Rahmen und Spalten nur auf oberster Ebene
DO $$ DECLARE r record; v text; BEGIN
  FOR r IN SELECT * FROM (VALUES
      ('Rahmen im Rahmen',   '{"x":{"type":"Container","data":{"props":{"childrenIds":[]}}}}', '{c,data,props,childrenIds}', '["t","x"]'),
      ('Spalten im Rahmen',  '{"x":{"type":"ColumnsContainer","data":{"props":{"columns":[{"childrenIds":[]},{"childrenIds":[]},{"childrenIds":[]}]}}}}', '{c,data,props,childrenIds}', '["x","t"]'),
      ('Rahmen in Spalte',   '{"x":{"type":"Container","data":{"props":{"childrenIds":[]}}}}', '{s,data,props,columns,2,childrenIds}', '["x"]'),
      ('Spalten in Spalte',  '{"x":{"type":"ColumnsContainer","data":{"props":{"columns":[{"childrenIds":[]},{"childrenIds":[]},{"childrenIds":[]}]}}}}', '{s,data,props,columns,0,childrenIds}', '["i","x"]')
    ) x(name, extra, eltern, kinder) LOOP
    v := pg_temp.plus(r.extra::jsonb, r.eltern::text[], r.kinder::jsonb);
    IF v IS DISTINCT FROM 'Rahmen und Spalten nur auf oberster Ebene' THEN
      RAISE EXCEPTION 'PROBE: % - Grund war %', r.name, coalesce(v, '(angenommen)'); END IF;
  END LOOP;
END $$;

-- I3: Grund nennt Feld, Block und Bereich
DO $$ DECLARE r record; v text; BEGIN
  FOR r IN SELECT * FROM (VALUES
      ('{s,data,props,columnsGap}', '64',  'Zahl ausserhalb des erlaubten Bereichs: columnsGap in s (erlaubt 0–48)'),
      ('{d,data,props,lineHeight}', '24',  'Zahl ausserhalb des erlaubten Bereichs: lineHeight in d (erlaubt 1–10)'),
      ('{h,data,style,borderRadius}', '48', 'Zahl ausserhalb des erlaubten Bereichs: borderRadius in h (erlaubt 0–32)'),
      ('{i,data,props,width}',      '601', 'Zahl ausserhalb des erlaubten Bereichs: width in i (erlaubt 1–600)'),
      ('{i,data,props,width}',      '0',   'Zahl ausserhalb des erlaubten Bereichs: width in i (erlaubt 1–600)'),
      ('{i,data,props,height}',     '700', 'Zahl ausserhalb des erlaubten Bereichs: height in i (erlaubt 0–600)'),
      ('{t,data,style,fontSize}',   '73',  'Zahl ausserhalb des erlaubten Bereichs: fontSize in t (erlaubt 8–72)'),
      ('{t,data,style,fontSize}',   '"16"', 'Zahl ausserhalb des erlaubten Bereichs: fontSize in t (erlaubt 8–72)')
    ) x(pfad, wert, grund) LOOP
    v := pg_temp.mit(r.pfad::text[], r.wert::jsonb);
    IF v IS DISTINCT FROM r.grund THEN
      RAISE EXCEPTION 'PROBE: % = % ergab %', r.pfad, r.wert, coalesce(v, '(angenommen)'); END IF;
  END LOOP;
END $$;

-- I5: Knopf ohne Link
DO $$ DECLARE w jsonb; v text; BEGIN
  FOREACH w IN ARRAY ARRAY['""'::jsonb, '"   "'::jsonb, 'null'::jsonb] LOOP
    v := pg_temp.mit('{b,data,props,url}', w);
    IF v IS DISTINCT FROM 'Knopf ohne Link in b – bitte https://… eintragen' THEN
      RAISE EXCEPTION 'PROBE: Knopf-Link % ergab %', w, coalesce(v, '(angenommen)'); END IF;
  END LOOP;
  v := marketing.pult_bloecke_fehler(jsonb_set((SELECT d FROM _g), '{b,data,props}', '{"text":"Los"}'));
  IF v IS DISTINCT FROM 'Knopf ohne Link in b – bitte https://… eintragen' THEN
    RAISE EXCEPTION 'PROBE: Knopf ohne url-Feld ergab %', coalesce(v, '(angenommen)'); END IF;
END $$;

-- CHECK-Regeln greifen mit den neuen Regeln (verschachtelter Rahmen direkt eingefuegt)
DO $$ DECLARE v_i uuid; BEGIN
  SELECT id INTO v_i FROM marketing.inhalte WHERE art = 'newsletter' AND status = 'entwurf' LIMIT 1;
  INSERT INTO marketing.inhalt_fassungen (inhalt, fassung, felder, layout, urheber, format, bloecke)
  VALUES (v_i, 999, '{"betreff":"x"}', 'dunkel', 'agent', 'bloecke',
          jsonb_set((SELECT d FROM _g) || '{"x":{"type":"Container","data":{"props":{"childrenIds":[]}}}}',
                    '{c,data,props,childrenIds}', '["t","x"]'));
  RAISE EXCEPTION 'PROBE: verschachtelter Rahmen direkt eingefuegt';
EXCEPTION WHEN check_violation THEN
  IF SQLERRM NOT LIKE '%inhalt_fassungen_bloecke_gueltig%' THEN RAISE EXCEPTION 'PROBE: falsche Regel: %', SQLERRM; END IF;
END $$;

-- Bestand erfuellt die neuen Regeln (sonst waere 055 schon am ADD CONSTRAINT gescheitert - hier ausdruecklich)
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM marketing.inhalt_fassungen WHERE format = 'bloecke'
              AND marketing.pult_bloecke_fehler(bloecke) IS NOT NULL)
     OR EXISTS (SELECT 1 FROM marketing.newsletter_vorlagen WHERE marketing.pult_bloecke_fehler(bloecke) IS NOT NULL) THEN
    RAISE EXCEPTION 'PROBE: gespeichertes Dokument verletzt die 055-Regeln'; END IF;
END $$;

-- I8: Feld-Newsletter ins Editor-Format
DO $$ DECLARE v_i uuid; v_n int; b jsonb; kinder jsonb; BEGIN
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', 'Probe 055')
  RETURNING id INTO v_i;
  PERFORM marketing.pult_fassung_speichern(v_i,
    jsonb_build_object('betreff', 'Oktober', 'vorschautext', 'Kurz',
      'abschnitte', jsonb_build_array(
         jsonb_build_object('titel', 'Neu', 'text', E'Erster Absatz.\n\nZweiter Absatz\nmit Umbruch.'),
         jsonb_build_object('titel', '', 'text', 'Ohne Titel.')),
      'knopf_text', 'Mehr', 'knopf_link', 'https://vibemind.space'), 'dunkel', 'agent');
  BEGIN
    PERFORM marketing.pult_in_bloecke_uebernehmen(v_i, '  ');
    RAISE EXCEPTION 'PROBE: ohne p_von uebernommen';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
    IF SQLERRM <> 'Wer uebernimmt, fehlt' THEN RAISE EXCEPTION 'PROBE: falscher Grund: %', SQLERRM; END IF;
  END;
  v_n := marketing.pult_in_bloecke_uebernehmen(v_i, 'probe');
  IF v_n <> 2 THEN RAISE EXCEPTION 'PROBE: erwartet Fassung 2, war %', v_n; END IF;
  SELECT bloecke INTO b FROM marketing.inhalt_fassungen
   WHERE inhalt = v_i AND fassung = 2 AND format = 'bloecke' AND urheber = 'betreiber'
     AND felder->>'betreff' = 'Oktober' AND felder->>'vorschautext' = 'Kurz';
  IF b IS NULL THEN RAISE EXCEPTION 'PROBE: keine Block-Fassung 2 vom Betreiber mit Betreff'; END IF;
  kinder := b#>'{root,data,childrenIds}';
  IF kinder <> '["h1","t1_1","t1_2","t2_1","knopf"]'::jsonb THEN
    RAISE EXCEPTION 'PROBE: falsche Bloecke %', kinder; END IF;
  IF b#>>'{t1_2,data,props,text}' <> E'Zweiter Absatz\nmit Umbruch.' OR b#>>'{h1,data,props,text}' <> 'Neu'
     OR b#>>'{knopf,data,props,url}' <> 'https://vibemind.space' OR b#>'{t1_1,data,props,markdown}' <> 'false' THEN
    RAISE EXCEPTION 'PROBE: Inhalt falsch abgebildet: %', b; END IF;
  -- zweiter Aufruf: schon Blockformat
  BEGIN
    PERFORM marketing.pult_in_bloecke_uebernehmen(v_i, 'probe');
    RAISE EXCEPTION 'PROBE: zweimal uebernommen';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
    IF SQLERRM <> 'Dieser Newsletter ist schon im Editor-Format' THEN RAISE EXCEPTION 'PROBE: falscher Grund: %', SQLERRM; END IF;
  END;
END $$;

-- I8: nur Newsletter, nur Entwuerfe, unbekannte ID
DO $$ DECLARE v_i uuid; BEGIN
  SELECT id INTO v_i FROM marketing.inhalte WHERE art = 'post' AND status = 'entwurf' LIMIT 1;
  IF v_i IS NULL THEN RAISE EXCEPTION 'PROBE: kein Post-Entwurf fuer den Test vorhanden'; END IF;
  PERFORM marketing.pult_in_bloecke_uebernehmen(v_i, 'probe');
  RAISE EXCEPTION 'PROBE: Post uebernommen';
EXCEPTION WHEN raise_exception THEN
  IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
  IF SQLERRM <> 'Nur Newsletter lassen sich ins Editor-Format uebernehmen' THEN RAISE EXCEPTION 'PROBE: falscher Grund: %', SQLERRM; END IF;
END $$;
DO $$ DECLARE v_i uuid; BEGIN
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', 'Probe 055 b')
  RETURNING id INTO v_i;
  PERFORM marketing.pult_fassung_speichern(v_i, '{"betreff":"x","abschnitte":[{"titel":"","text":"y"}]}', 'dunkel', 'agent');
  PERFORM marketing.pult_entscheiden(v_i, 1, 'ablehnen', 'probe', 'Probe');
  PERFORM marketing.pult_in_bloecke_uebernehmen(v_i, 'probe');
  RAISE EXCEPTION 'PROBE: abgelehnter Newsletter uebernommen';
EXCEPTION WHEN raise_exception THEN
  IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
  IF SQLERRM <> 'Nur Entwuerfe lassen sich bearbeiten' THEN RAISE EXCEPTION 'PROBE: falscher Grund: %', SQLERRM; END IF;
END $$;
DO $$ BEGIN
  PERFORM marketing.pult_in_bloecke_uebernehmen('00000000-0000-0000-0000-000000000000', 'probe');
  RAISE EXCEPTION 'PROBE: unbekannter Inhalt uebernommen';
EXCEPTION WHEN raise_exception THEN
  IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
  IF SQLERRM <> 'Unbekannter Inhalt' THEN RAISE EXCEPTION 'PROBE: falscher Grund: %', SQLERRM; END IF;
END $$;

-- I8: ungueltiger Inhalt (http-Link im Text) wird mit dem Grund der Pruefung abgelehnt, nichts gespeichert
DO $$ DECLARE v_i uuid; BEGIN
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', 'Probe 055 c')
  RETURNING id INTO v_i;
  PERFORM marketing.pult_fassung_speichern(v_i, '{"betreff":"x","abschnitte":[{"titel":"","text":"[a](http://alt.de)"}]}', 'dunkel', 'agent');
  BEGIN
    PERFORM marketing.pult_in_bloecke_uebernehmen(v_i, 'probe');
    RAISE EXCEPTION 'PROBE: http-Link uebernommen';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
    IF SQLERRM <> 'Links im Text nur mit https:// in t1_1' THEN RAISE EXCEPTION 'PROBE: falscher Grund: %', SQLERRM; END IF;
  END;
  IF (SELECT count(*) FROM marketing.inhalt_fassungen WHERE inhalt = v_i) <> 1 THEN
    RAISE EXCEPTION 'PROBE: nach Ablehnung trotzdem eine Fassung gespeichert'; END IF;
END $$;

-- I4 (nur lesen): Spaltenbloecke mit genau 3 columns-Eintraegen
DO $$ DECLARE v_n int; BEGIN
  SELECT count(*) INTO v_n FROM (
      SELECT bloecke FROM marketing.inhalt_fassungen WHERE format = 'bloecke'
      UNION ALL SELECT bloecke FROM marketing.newsletter_vorlagen) d, jsonb_each(d.bloecke) e
   WHERE e.value->>'type' = 'ColumnsContainer'
     AND (jsonb_typeof(e.value#>'{data,props,columns}') IS DISTINCT FROM 'array'
          OR jsonb_array_length(e.value#>'{data,props,columns}') <> 3);
  IF v_n > 0 THEN RAISE EXCEPTION 'PROBE: % Spaltenbloecke ohne genau 3 columns-Eintraege', v_n; END IF;
END $$;
ROLLBACK;
