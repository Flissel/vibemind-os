-- verify_056.sql — Proben fuer 056_newsletter_bilder.sql. Aendert nichts (ROLLBACK).
-- Lauf: python -m spaces.marketing.scripts.migration_probe spaces/marketing/db/056_newsletter_bilder.sql spaces/marketing/db/verify_056.sql
BEGIN;
CREATE TEMP TABLE _p (k text PRIMARY KEY, v text);

-- Probevorlage mit einem leeren Platz "held" und einem Text
SELECT marketing.pult_vorlage_speichern('probe-bilder', 'Probe', '{
 "root":{"type":"EmailLayout","data":{"backdropColor":"#1d3b39","canvasColor":"#0f2422","textColor":"#cfe3df","fontFamily":"MODERN_SANS","childrenIds":["held","t"]}},
 "held":{"type":"Image","data":{"style":{"padding":{"top":0,"bottom":0,"left":0,"right":0}},"props":{"url":"medien:platzhalter-2x1.png","alt":"Team","width":600,"height":300}}},
 "t":{"type":"Text","data":{"style":{},"props":{"text":"Herbst","markdown":false}}}}'::jsonb, 'probe', 'freigegeben');

-- 1) Neu aus Vorlage legt einen System-Auftrag "alle leeren" an
DO $$ DECLARE v_i uuid; a record; BEGIN
  v_i := marketing.pult_inhalt_aus_vorlage('probe-bilder', 'Probe Bilder', 'vibemind');
  INSERT INTO _p VALUES ('inhalt', v_i::text);
  SELECT * INTO a FROM marketing.bild_auftraege WHERE inhalt = v_i;
  IF a.urheber <> 'system' OR a.platz IS NOT NULL OR NOT a.nur_leere OR a.status <> 'offen' OR a.grund_fassung <> 1 THEN
    RAISE EXCEPTION 'PROBE 1: Auftrag aus Vorlage falsch: %', row_to_json(a); END IF;
END $$;

-- 2) naechster vergibt, datei_fehler prueft, einsetzen schreibt Fassung 2 (Urheber agent)
DO $$ DECLARE j jsonb; r jsonb; v_i uuid := (SELECT v FROM _p WHERE k = 'inhalt')::uuid; BEGIN
  j := marketing.pult_bild_naechster('10 minutes');
  IF j IS NULL OR (j->>'inhalt')::uuid <> v_i OR (j->>'versuche')::int <> 1 OR (j->>'fassung')::int <> 1 THEN
    RAISE EXCEPTION 'PROBE 2a: naechster %', j; END IF;
  IF marketing.pult_bild_datei_fehler((j->>'id')::uuid, 'held') IS NOT NULL THEN RAISE EXCEPTION 'PROBE 2b'; END IF;
  IF marketing.pult_bild_datei_fehler((j->>'id')::uuid, 't') IS DISTINCT FROM 'Bildplatz gibt es nicht' THEN
    RAISE EXCEPTION 'PROBE 2c'; END IF;
  BEGIN
    PERFORM marketing.pult_bild_einsetzen((j->>'id')::uuid, '{"held":"medien:../x.jpg"}', '');
    RAISE EXCEPTION 'PROBE 2d: fremder Name angenommen';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM NOT LIKE 'Ungueltiger Bildname%' THEN RAISE; END IF;
  END;
  r := marketing.pult_bild_einsetzen((j->>'id')::uuid, '{"held":"medien:nl-0123abcd-held.jpg"}', '');
  IF (r->>'fassung')::int <> 2 THEN RAISE EXCEPTION 'PROBE 2e: %', r; END IF;
  IF (SELECT bloecke #>> '{held,data,props,url}' FROM marketing.inhalt_fassungen WHERE inhalt = v_i AND fassung = 2)
     <> 'medien:nl-0123abcd-held.jpg'
     OR (SELECT urheber FROM marketing.inhalt_fassungen WHERE inhalt = v_i AND fassung = 2) <> 'agent' THEN
    RAISE EXCEPTION 'PROBE 2f'; END IF;
  IF (SELECT status FROM marketing.bild_auftraege WHERE id = (j->>'id')::uuid) <> 'fertig' THEN RAISE EXCEPTION 'PROBE 2g'; END IF;
END $$;

-- 3) Neu erzeugen mit Hinweis: Mensch belegt den Platz inzwischen selbst -> sein Bild bleibt
DO $$ DECLARE v_i uuid := (SELECT v FROM _p WHERE k = 'inhalt')::uuid; v_a uuid; j jsonb; r jsonb; d jsonb; BEGIN
  v_a := marketing.pult_bild_auftrag(v_i, 'held', false, 'waermer', 'mensch');
  j := marketing.pult_bild_naechster('10 minutes');
  IF (j->>'id')::uuid <> v_a OR j->>'hinweis' <> 'waermer' THEN RAISE EXCEPTION 'PROBE 3a %', j; END IF;
  d := jsonb_set((SELECT bloecke FROM marketing.inhalt_fassungen WHERE inhalt = v_i AND fassung = 2),
                 '{held,data,props,url}', '"medien:eigen.jpg"');
  PERFORM marketing.pult_bloecke_speichern(v_i, 2, 'Probe Bilder', '', d, 'betreiber', false);
  r := marketing.pult_bild_einsetzen(v_a, '{"held":"medien:nl-4567abcd-held.jpg"}', '');
  IF r->'fassung' <> 'null'::jsonb OR r->>'uebersprungen' NOT LIKE '%inzwischen belegt%' THEN RAISE EXCEPTION 'PROBE 3b %', r; END IF;
  IF (SELECT max(fassung) FROM marketing.inhalt_fassungen WHERE inhalt = v_i) <> 3 THEN RAISE EXCEPTION 'PROBE 3c'; END IF;
END $$;

-- 4) Neu erzeugen, Platz zeigt noch das Agentenbild der Grundfassung -> wird ersetzt
DO $$ DECLARE v_i uuid := (SELECT v FROM _p WHERE k = 'inhalt')::uuid; v_a uuid; j jsonb; r jsonb; d jsonb; BEGIN
  d := jsonb_set((SELECT bloecke FROM marketing.inhalt_fassungen WHERE inhalt = v_i AND fassung = 3),
                 '{held,data,props,url}', '"medien:nl-0123abcd-held.jpg"');
  PERFORM marketing.pult_bloecke_speichern(v_i, 3, 'Probe Bilder', '', d, 'betreiber', false);   -- Fassung 4
  v_a := marketing.pult_bild_auftrag(v_i, 'held', false, '', 'mensch');                         -- Grund 4
  j := marketing.pult_bild_naechster('10 minutes');
  r := marketing.pult_bild_einsetzen(v_a, '{"held":"medien:nl-89abcdef-held.jpg"}', '');
  IF (r->>'fassung')::int <> 5 THEN RAISE EXCEPTION 'PROBE 4 %', r; END IF;
END $$;

-- 5) Platz geloescht waehrend der Arbeit -> uebersprungen, keine Fassung
DO $$ DECLARE v_i uuid := (SELECT v FROM _p WHERE k = 'inhalt')::uuid; v_a uuid; r jsonb; d jsonb; BEGIN
  v_a := marketing.pult_bild_auftrag(v_i, 'held', false, '', 'agent');
  PERFORM marketing.pult_bild_naechster('10 minutes');
  d := (SELECT bloecke FROM marketing.inhalt_fassungen WHERE inhalt = v_i AND fassung = 5) - 'held';
  d := jsonb_set(d, '{root,data,childrenIds}', '["t"]');
  PERFORM marketing.pult_bloecke_speichern(v_i, 5, 'Probe Bilder', '', d, 'betreiber', false);  -- Fassung 6
  r := marketing.pult_bild_einsetzen(v_a, '{"held":"medien:nl-11112222-held.jpg"}', '');
  IF r->'fassung' <> 'null'::jsonb OR r->>'uebersprungen' NOT LIKE '%gibt es nicht mehr%' THEN RAISE EXCEPTION 'PROBE 5 %', r; END IF;
  BEGIN
    PERFORM marketing.pult_bild_auftrag(v_i, 'held', false, '', 'mensch');
    RAISE EXCEPTION 'PROBE 5b: Auftrag fuer fehlenden Platz angenommen';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM NOT LIKE 'Bildplatz held gibt es in der gespeicherten Fassung nicht%' THEN RAISE; END IF;
  END;
END $$;

-- 6) Ablauf der Vergabe, 3 Versuche, ein offener je Platz, Freigabe verwirft
DO $$ DECLARE v_i uuid; v_a uuid; v_b uuid; j jsonb; BEGIN
  v_i := marketing.pult_inhalt_aus_vorlage('probe-bilder', 'Probe Ablauf', 'vibemind');
  v_a := (SELECT id FROM marketing.bild_auftraege WHERE inhalt = v_i);
  -- ein neuer Auftrag fuer "alle" ersetzt den wartenden
  v_b := marketing.pult_bild_auftrag(v_i, NULL, true, 'zweiter', 'mensch');
  IF (SELECT status FROM marketing.bild_auftraege WHERE id = v_a) <> 'verworfen' THEN RAISE EXCEPTION 'PROBE 6a'; END IF;
  -- dreimal vergeben und ablaufen lassen
  FOR n IN 1..3 LOOP
    UPDATE marketing.bild_auftraege SET erstellt_am = now() - interval '1 day' WHERE id = v_b;
    j := marketing.pult_bild_naechster('10 minutes');
    IF (j->>'id')::uuid <> v_b THEN RAISE EXCEPTION 'PROBE 6b Runde %: %', n, j; END IF;
    UPDATE marketing.bild_auftraege SET vergeben_bis = now() - interval '1 second' WHERE id = v_b;
  END LOOP;
  PERFORM marketing.pult_bild_naechster('10 minutes');
  IF (SELECT status FROM marketing.bild_auftraege WHERE id = v_b) <> 'fehler' THEN
    RAISE EXCEPTION 'PROBE 6c: %', (SELECT row_to_json(x) FROM marketing.bild_auftraege x WHERE id = v_b); END IF;
  -- Freigabe verwirft wartende
  v_a := marketing.pult_bild_auftrag(v_i, NULL, false, '', 'mensch');
  PERFORM marketing.pult_entscheiden(v_i, 1, 'freigeben', 'probe', '');
  IF (SELECT status FROM marketing.bild_auftraege WHERE id = v_a) <> 'verworfen' THEN RAISE EXCEPTION 'PROBE 6d'; END IF;
  BEGIN
    PERFORM marketing.pult_bild_auftrag(v_i, NULL, false, '', 'mensch');
    RAISE EXCEPTION 'PROBE 6e: Auftrag auf freigegebenem Inhalt';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM NOT LIKE 'Schon entschieden%' THEN RAISE; END IF;
  END;
END $$;

-- 7) Zurueckgeben: nicht endgueltig -> offen; endgueltig -> fehler; Hinweis > 500 abgelehnt
DO $$ DECLARE v_i uuid; v_a uuid; BEGIN
  v_i := marketing.pult_inhalt_aus_vorlage('probe-bilder', 'Probe Zurueck', 'vibemind');
  v_a := (SELECT id FROM marketing.bild_auftraege WHERE inhalt = v_i);
  UPDATE marketing.bild_auftraege SET erstellt_am = now() - interval '2 days' WHERE id = v_a;
  PERFORM marketing.pult_bild_naechster('10 minutes');
  IF marketing.pult_bild_zurueck(v_a, 'ComfyUI laeuft nicht', false) <> 'offen' THEN RAISE EXCEPTION 'PROBE 7a'; END IF;
  PERFORM marketing.pult_bild_naechster('10 minutes');
  IF marketing.pult_bild_zurueck(v_a, 'Schrift im Bild', true) <> 'fehler' THEN RAISE EXCEPTION 'PROBE 7b'; END IF;
  BEGIN
    PERFORM marketing.pult_bild_auftrag(v_i, NULL, false, repeat('x', 501), 'mensch');
    RAISE EXCEPTION 'PROBE 7c';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM NOT LIKE 'Der Hinweis ist zu lang%' THEN RAISE; END IF;
  END;
END $$;

SELECT 'verify_056: alle Proben gruen' AS ergebnis;
ROLLBACK;
