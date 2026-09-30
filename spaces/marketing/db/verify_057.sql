-- verify_057.sql — Proben fuer 057 (056 ist live). Aendert nichts (ROLLBACK).
-- Lauf: python -m spaces.marketing.scripts.migration_probe spaces/marketing/db/057_bild_ueberarbeiten.sql spaces/marketing/db/verify_057.sql
BEGIN;
CREATE TEMP TABLE _p (k text PRIMARY KEY, v text);
SELECT marketing.pult_vorlage_speichern('probe-ueberarbeiten', 'Probe', '{
 "root":{"type":"EmailLayout","data":{"backdropColor":"#1d3b39","canvasColor":"#0f2422","textColor":"#cfe3df","fontFamily":"MODERN_SANS","childrenIds":["held","t"]}},
 "held":{"type":"Image","data":{"style":{"padding":{"top":0,"bottom":0,"left":0,"right":0}},"props":{"url":"medien:platzhalter-2x1.png","alt":"Team","width":600,"height":300}}},
 "t":{"type":"Text","data":{"style":{},"props":{"text":"Herbst","markdown":false}}}}'::jsonb, 'probe', 'freigegeben');

-- 1) 7-Argument-Form setzt Staerke/Modus; 100 -> neu; Grenzen; 5-Argument-Form bleibt
DO $$ DECLARE v_i uuid; v_a uuid; a record; BEGIN
  v_i := marketing.pult_inhalt_aus_vorlage('probe-ueberarbeiten', 'Probe 057', 'vibemind');
  INSERT INTO _p VALUES ('inhalt', v_i::text);
  SELECT * INTO a FROM marketing.bild_auftraege WHERE inhalt = v_i;              -- System-Auftrag (5 Args)
  IF a.staerke <> 55 OR a.modus <> 'ueberarbeiten' OR a.messung <> '{}'::jsonb THEN
    RAISE EXCEPTION 'PROBE 1a: Standardwerte falsch: %', row_to_json(a); END IF;
  v_a := marketing.pult_bild_auftrag(v_i, 'held', false, 'waermer', 'mensch', 30, 'ueberarbeiten');
  SELECT * INTO a FROM marketing.bild_auftraege WHERE id = v_a;
  IF a.staerke <> 30 OR a.modus <> 'ueberarbeiten' OR a.hinweis <> 'waermer' THEN RAISE EXCEPTION 'PROBE 1b %', row_to_json(a); END IF;
  v_a := marketing.pult_bild_auftrag(v_i, 'held', false, '', 'mensch', 100, 'ueberarbeiten');
  IF (SELECT modus FROM marketing.bild_auftraege WHERE id = v_a) <> 'neu' THEN RAISE EXCEPTION 'PROBE 1c'; END IF;
  BEGIN
    PERFORM marketing.pult_bild_auftrag(v_i, 'held', false, '', 'mensch', 101, 'neu');
    RAISE EXCEPTION 'PROBE 1d: 101 angenommen';
  EXCEPTION WHEN raise_exception THEN IF SQLERRM NOT LIKE 'Staerke muss 0 bis 100%' THEN RAISE; END IF; END;
  BEGIN
    PERFORM marketing.pult_bild_auftrag(v_i, 'held', false, '', 'mensch', 50, 'malen');
    RAISE EXCEPTION 'PROBE 1e: Modus malen angenommen';
  EXCEPTION WHEN raise_exception THEN IF SQLERRM NOT LIKE 'Modus muss%' THEN RAISE; END IF; END;
END $$;

-- 2) Einsetzen mit Messung speichert die Messung, Ergebnis wie 056
DO $$ DECLARE v_i uuid := (SELECT v FROM _p WHERE k = 'inhalt')::uuid; j jsonb; r jsonb; BEGIN
  UPDATE marketing.bild_auftraege SET erstellt_am = now() - interval '5 days' WHERE inhalt = v_i AND status = 'offen';
  j := marketing.pult_bild_naechster('10 minutes');
  IF (j->>'inhalt')::uuid <> v_i THEN RAISE EXCEPTION 'PROBE 2a %', j; END IF;
  r := marketing.pult_bild_einsetzen((j->>'id')::uuid, '{"held":"medien:nl-0123abcd-held.jpg"}', '',
                                     '{"held":{"aehnlich_original":0.82,"naeher_am_hinweis":0.03}}');
  IF (r->>'fassung') IS NULL THEN RAISE EXCEPTION 'PROBE 2b %', r; END IF;
  IF (SELECT messung->'held'->>'aehnlich_original' FROM marketing.bild_auftraege WHERE id = (j->>'id')::uuid) <> '0.82' THEN
    RAISE EXCEPTION 'PROBE 2c'; END IF;
  BEGIN
    PERFORM marketing.pult_bild_einsetzen((j->>'id')::uuid, '{}', '', '[1]'::jsonb);
    RAISE EXCEPTION 'PROBE 2d: Liste als Messung angenommen';
  EXCEPTION WHEN raise_exception THEN IF SQLERRM NOT LIKE 'Messung muss%' THEN RAISE; END IF; END;
END $$;

SELECT 'verify_057: alle Proben gruen' AS ergebnis;
ROLLBACK;
