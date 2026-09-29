-- verify_054.sql — Proben fuer 054_newsletter_bloecke_korrektur.sql. Aendert nichts (ROLLBACK).
BEGIN;
CREATE TEMP TABLE _g AS SELECT '{
 "root":{"type":"EmailLayout","data":{"backdropColor":"#1d3b39","canvasColor":"#0f2422","textColor":"#cfe3df","fontFamily":"MODERN_SANS","childrenIds":["h","t","b","s"]}},
 "h":{"type":"Heading","data":{"style":{"textAlign":"center","fontWeight":"bold"},"props":{"text":"Neu","level":"h2"}}},
 "t":{"type":"Text","data":{"style":{"fontFamily":"BOOK_SERIF"},"props":{"text":"Hallo","markdown":true}}},
 "b":{"type":"Button","data":{"style":{},"props":{"text":"Los","url":"https://vibemind.space","buttonStyle":"pill","size":"large"}}},
 "s":{"type":"ColumnsContainer","data":{"style":{},"props":{"columnsCount":3,"contentAlignment":"top","columns":[{"childrenIds":["i"]},{"childrenIds":[]},{"childrenIds":[]}]}}},
 "i":{"type":"Image","data":{"style":{},"props":{"url":"medien:logo-2.webp","alt":"Logo"}}}
}'::jsonb AS d;

-- Hilfsfunktion der Probe: Grund fuer ein Dokument mit geaendertem Wert
CREATE FUNCTION pg_temp.mit(pfad text[], wert jsonb) RETURNS text LANGUAGE sql AS
$$ SELECT marketing.pult_bloecke_fehler(jsonb_set((SELECT d FROM _g), pfad, wert)) $$;

DO $$ DECLARE v text; BEGIN
  v := marketing.pult_bloecke_fehler((SELECT d FROM _g));
  IF v IS NOT NULL THEN RAISE EXCEPTION 'PROBE: gueltiges Dokument abgelehnt: %', v; END IF;
  v := pg_temp.mit('{t,data,props,text}', '"**fett** *kursiv* [a](https://x)"');
  IF v IS NOT NULL THEN RAISE EXCEPTION 'PROBE: erlaubtes Markdown abgelehnt: %', v; END IF;
  -- markdown:false darf < und http:// als reinen Text enthalten (wird nicht als Markdown gerendert)
  v := marketing.pult_bloecke_fehler(jsonb_set(jsonb_set((SELECT d FROM _g), '{t,data,props,markdown}', 'false'),
         '{t,data,props,text}', '"a < b, siehe http://alt.de"'));
  IF v IS NOT NULL THEN RAISE EXCEPTION 'PROBE: Klartext ohne Markdown abgelehnt: %', v; END IF;
END $$;

-- 1: Block ohne Typ
DO $$ BEGIN
  IF marketing.pult_bloecke_fehler((SELECT d FROM _g) || '{"x":{"data":{"props":{"height":4}}}}'
       || jsonb_build_object('root', jsonb_set((SELECT d->'root' FROM _g), '{data,childrenIds}', '["h","t","b","s","x"]')))
     IS DISTINCT FROM 'Blocktyp (leer) ist nicht erlaubt'
    THEN RAISE EXCEPTION 'PROBE: Block ohne Typ angenommen'; END IF;
END $$;

-- 2: Markdown-Umgehungen
DO $$ DECLARE t text; v text; BEGIN
  FOREACH t IN ARRAY ARRAY['![](https://t/p.gif)', '<img src=https://t/p.gif>', E'[a][r]\n[r]: http://x',
                           'http://evil.de', E'[a]\n(http://y)', '[a](https://x) and [b](http://y)',
                           'besuche www.evil.de', E'[a][r]\n  [r]: https://x'] LOOP
    v := pg_temp.mit('{t,data,props,text}', to_jsonb(t));
    IF v IS NULL THEN RAISE EXCEPTION 'PROBE: Markdown angenommen: %', t; END IF;
  END LOOP;
END $$;

-- 5: Groesse in Bytes (140000 Zeichen 'ae' = 280000 Bytes, aber < 262144 Zeichen)
DO $$ BEGIN
  IF pg_temp.mit('{t,data,props,text}', to_jsonb(repeat('ä', 140000))) IS DISTINCT FROM
     'Das Dokument ist zu gross (hoechstens 256 KB)'
    THEN RAISE EXCEPTION 'PROBE: Mehrbyte-Dokument ueber 256 KB angenommen'; END IF;
END $$;

-- Aufzaehlungen, Spalten, Bildnamen
DO $$ DECLARE r record; BEGIN
  FOR r IN SELECT * FROM (VALUES
      ('{root,data,fontFamily}', '"COMIC_SANS"'), ('{t,data,style,fontFamily}', '"Arial"'),
      ('{h,data,style,textAlign}', '"justify"'), ('{h,data,style,fontWeight}', '"light"'),
      ('{h,data,props,level}', '"h4"'), ('{b,data,props,buttonStyle}', '"circle"'),
      ('{b,data,props,size}', '"huge"'), ('{s,data,props,contentAlignment}', '"center"'),
      ('{s,data,props,columnsCount}', '4'), ('{s,data,props,columnsCount}', '1'),
      ('{s,data,props,columns}', '[{"childrenIds":["i"]},{"childrenIds":[]},{"childrenIds":[]},{"childrenIds":[]}]'),
      ('{i,data,props,url}', '"medien:.versteckt.png"'), ('{i,data,props,url}', '"medien:bild.svg"'),
      ('{i,data,props,url}', '"medien:a..b.png"'), ('{i,data,props,url}', '"medien:ordner/bild.png"'),
      ('{i,data,props,url}', '"medien:bild"')) x(pfad, wert) LOOP
    IF pg_temp.mit(r.pfad::text[], r.wert::jsonb) IS NULL THEN
      RAISE EXCEPTION 'PROBE: % = % angenommen', r.pfad, r.wert; END IF;
  END LOOP;
  IF pg_temp.mit('{i,data,props,url}', '"medien:foto_1.jpeg"') IS NOT NULL THEN
    RAISE EXCEPTION 'PROBE: gueltiger Bildname abgelehnt'; END IF;
END $$;

-- 3: Absatz-Korrektur - keine Uebernahme-Fassung mehr offen, kein Text-Block mit Leerzeile
DO $$ BEGIN
  IF EXISTS (
      SELECT 1 FROM marketing.inhalte i
        JOIN LATERAL (SELECT * FROM marketing.inhalt_fassungen x WHERE x.inhalt = i.id
                      ORDER BY x.fassung DESC LIMIT 1) n ON true
        JOIN marketing.inhalt_fassungen v ON v.inhalt = i.id AND v.fassung = n.fassung - 1
       WHERE i.art = 'newsletter' AND i.status = 'entwurf'
         AND n.format = 'bloecke' AND n.urheber = 'agent' AND v.format = 'felder') THEN
    RAISE EXCEPTION 'PROBE: 053-Uebernahme ohne Absatz-Korrektur'; END IF;
  IF EXISTS (
      SELECT 1 FROM marketing.inhalte i
        JOIN LATERAL (SELECT * FROM marketing.inhalt_fassungen x WHERE x.inhalt = i.id
                      ORDER BY x.fassung DESC LIMIT 1) n ON true,
        jsonb_each(n.bloecke) e
       WHERE i.art = 'newsletter' AND i.status = 'entwurf' AND n.format = 'bloecke' AND n.urheber = 'agent'
         AND e.value->>'type' = 'Text' AND e.value#>>'{data,props,text}' ~ E'\\n[ \\t\\r]*\\n') THEN
    RAISE EXCEPTION 'PROBE: Text-Block mit mehreren Absaetzen'; END IF;
END $$;

-- 4: Feld-Fassung auf Block-Newsletter
DO $$ DECLARE v_i uuid; BEGIN
  SELECT i.id INTO v_i FROM marketing.inhalte i
   WHERE i.art = 'newsletter' AND i.status = 'entwurf'
     AND (SELECT format FROM marketing.inhalt_fassungen f WHERE f.inhalt = i.id ORDER BY fassung DESC LIMIT 1) = 'bloecke'
   LIMIT 1;
  IF v_i IS NULL THEN RAISE EXCEPTION 'PROBE: kein Block-Newsletter-Entwurf fuer den Test vorhanden'; END IF;
  PERFORM marketing.pult_fassung_speichern(v_i, '{"betreff":"x","abschnitte":[{"titel":"","text":"y"}]}', 'dunkel', 'betreiber');
  RAISE EXCEPTION 'PROBE: Feld-Fassung auf Block-Newsletter angenommen';
EXCEPTION WHEN raise_exception THEN
  IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
  IF SQLERRM <> 'Dieser Newsletter wird im Editor bearbeitet' THEN
    RAISE EXCEPTION 'PROBE: falscher Grund: %', SQLERRM; END IF;
END $$;

-- Posts bekommen keine Bloecke (verschaerft: echter Post-Entwurf, Grund geprueft)
DO $$ DECLARE v_i uuid; BEGIN
  SELECT id INTO v_i FROM marketing.inhalte WHERE art = 'post' AND status = 'entwurf' LIMIT 1;
  IF v_i IS NULL THEN RAISE EXCEPTION 'PROBE: kein Post-Entwurf fuer den Test vorhanden'; END IF;
  PERFORM marketing.pult_bloecke_speichern(v_i,
    (SELECT max(fassung) FROM marketing.inhalt_fassungen WHERE inhalt = v_i), 'x', '', (SELECT d FROM _g), 'betreiber', false);
  RAISE EXCEPTION 'PROBE: Bloecke fuer einen Post angenommen';
EXCEPTION WHEN raise_exception THEN
  IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
  IF SQLERRM <> 'Bloecke gibt es nur fuer Newsletter' THEN RAISE EXCEPTION 'PROBE: falscher Grund: %', SQLERRM; END IF;
END $$;

-- Ungueltiges Dokument wird mit Grund abgelehnt (verschaerft)
DO $$ DECLARE v_i uuid; v_n int; BEGIN
  v_i := marketing.pult_inhalt_aus_vorlage('leer', 'Probe 054', 'vibemind');
  BEGIN
    PERFORM marketing.pult_bloecke_speichern(v_i, 1, 'B', '',
      jsonb_set((SELECT d FROM _g), '{i,data,props,url}', '"https://x.de/a.png"'), 'agent', false);
    RAISE EXCEPTION 'PROBE: ungueltiges Dokument gespeichert';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
    IF SQLERRM <> 'Bilder nur aus den Medien (medien:<datei>) in i' THEN
      RAISE EXCEPTION 'PROBE: falscher Grund: %', SQLERRM; END IF;
  END;
  v_n := marketing.pult_bloecke_speichern(v_i, 1, 'B', '', (SELECT d FROM _g), 'betreiber', false);
  IF v_n <> 2 THEN RAISE EXCEPTION 'PROBE: gueltiges Dokument nicht als Fassung 2 gespeichert'; END IF;
END $$;

-- CHECK-Regeln greifen auch am Funktionsweg vorbei
DO $$ DECLARE v_i uuid; BEGIN
  SELECT id INTO v_i FROM marketing.inhalte WHERE art = 'newsletter' AND status = 'entwurf' LIMIT 1;
  INSERT INTO marketing.inhalt_fassungen (inhalt, fassung, felder, layout, urheber, format, bloecke)
  VALUES (v_i, 999, '{"betreff":"x"}', 'dunkel', 'agent', 'bloecke', '{"root":{"type":"Text"}}');
  RAISE EXCEPTION 'PROBE: ungueltige Block-Fassung direkt eingefuegt';
EXCEPTION WHEN check_violation THEN
  IF SQLERRM NOT LIKE '%inhalt_fassungen_bloecke_gueltig%' THEN RAISE EXCEPTION 'PROBE: falsche Regel: %', SQLERRM; END IF;
END $$;
DO $$ BEGIN
  INSERT INTO marketing.newsletter_vorlagen (name, bloecke, erstellt_von) VALUES ('probe-054', '{}', 'probe');
  RAISE EXCEPTION 'PROBE: ungueltige Vorlage direkt eingefuegt';
EXCEPTION WHEN check_violation THEN
  IF SQLERRM NOT LIKE '%newsletter_vorlagen_bloecke_gueltig%' THEN RAISE EXCEPTION 'PROBE: falsche Regel: %', SQLERRM; END IF;
END $$;
DO $$ BEGIN
  INSERT INTO marketing.newsletter_vorlagen_fassungen (vorlage, fassung, bloecke, erstellt_von) VALUES ('leer', 999, '{}', 'probe');
  RAISE EXCEPTION 'PROBE: ungueltige Vorlagen-Fassung direkt eingefuegt';
EXCEPTION WHEN check_violation THEN
  IF SQLERRM NOT LIKE '%newsletter_vorlagen_fassungen_bloecke_gueltig%' THEN RAISE EXCEPTION 'PROBE: falsche Regel: %', SQLERRM; END IF;
END $$;
ROLLBACK;
