-- RUNBOOK: 078 ersetzt keine Funktion aus 064/066, sondern fuegt nur hinzu - ein Replay von 064/066 laesst 078
-- unberuehrt (pult_marke_rueckgaengig ruft pult_marke_uebernehmen zur Laufzeit beim Namen). Nach einem Replay
-- von 078 verify_060 .. verify_066 und verify_078 zusammen ueber migration_probe laufen lassen.
-- 078: Marken-Chat neu (sales-claw Plan 2026-10-20-marken-chat-neu.md, E7/E8). Idempotent, eine Transaktion.
--   1) marken_auftraege.quellen: die Quellen einer Chat-Runde, [{titel nicht leer <= 120, url <= 500 (http/https,
--      ohne Zugangsdaten, ohne Leer-/Steuerzeichen), art gelesen|websuche}], hoechstens 12 (Check auf der Spalte, Feinpruefung in _marke_quellen_pruefen)
--   2) pult_marke_quellen(aid, quellen): Muster pult_marke_denken (065) - false, wenn der Auftrag nicht
--      (mehr) in Arbeit ist; ungueltige Quellen werfen immer (Pruefung vor der Sperre)
--   3) pult_marke_rueckgaengig(vid, von, mandant): macht die JUENGSTE direkt angenommene Aenderung rueckgaengig.
--      Legt einen neuen Vorschlag {abschnitte: {feld: vorher}, aenderungen: vertauscht, direkt: true,
--      rueckgaengig_von: vid} am Auftrag des Originals an und uebernimmt ihn ueber pult_marke_uebernehmen
--      (066) - Uebernahme-Auftrag, Verlauf-Sicherung, Spiegel, Newsletter-Markierung und Wissens-Lauf laufen
--      also genau wie bei jeder Uebernahme. Ein offener Vorschlag der Firma wird dabei 'ersetzt' (wie in
--      pult_marke_vorschlag): er beruht auf dem Profil vor dem Rueckgaengig, und scheitert die Uebernahme,
--      wird der Rueckgaengig-Vorschlag nach der 064-Regel wieder 'offen' - zwei offene verbietet
--      marken_vorschlaege_ein_offener.
--      "Juenger" = spaeteres erstellt_am; bei Gleichstand gilt keiner als juengster (fail-closed).
-- Sperrreihenfolge wie 064: mandanten -> marken_auftraege -> marken_vorschlaege.
-- Rechte: wie 060/064/066 - kein SECURITY DEFINER, kein SET search_path, keine eigenen GRANTs
-- (Default Privileges aus 003 gelten).
BEGIN;

-- 1) Quellen-Spalte
ALTER TABLE marketing.marken_auftraege ADD COLUMN IF NOT EXISTS quellen jsonb;
ALTER TABLE marketing.marken_auftraege DROP CONSTRAINT IF EXISTS marken_auftraege_quellen_check;
ALTER TABLE marketing.marken_auftraege ADD CONSTRAINT marken_auftraege_quellen_check
  CHECK (CASE WHEN quellen IS NULL THEN true
              WHEN jsonb_typeof(quellen) = 'array' THEN jsonb_array_length(quellen) <= 12
              ELSE false END);

-- 2) Quellen schreiben
CREATE OR REPLACE FUNCTION marketing._marke_quellen_pruefen(p_quellen jsonb) RETURNS void
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE q jsonb;
BEGIN
  IF p_quellen IS NULL OR jsonb_typeof(p_quellen) <> 'array' THEN
    RAISE EXCEPTION 'Quellen muessen eine Liste sein'; END IF;
  IF jsonb_array_length(p_quellen) > 12 THEN RAISE EXCEPTION 'Hoechstens 12 Quellen'; END IF;
  FOR q IN SELECT * FROM jsonb_array_elements(p_quellen) LOOP
    IF jsonb_typeof(q) <> 'object' THEN RAISE EXCEPTION 'Quelle ungueltig'; END IF;
    IF (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(q) k) IS DISTINCT FROM ARRAY['art','titel','url']
       OR jsonb_typeof(q->'titel') <> 'string' OR char_length(q->>'titel') > 120
       OR (q->>'titel') !~ '\S'
       OR jsonb_typeof(q->'url') <> 'string' OR char_length(q->>'url') > 500
       OR (q->>'url') !~ '^https?://'
       -- keine Zugangsdaten ('@' zwischen :// und dem ersten /, ? oder #), keine Leer-/Steuerzeichen
       -- (dieselben Zeichen wie quellen_pruefen in api/marke.py)
       OR (q->>'url') ~ '^https?://[^/?#]*@'
       OR (q->>'url') ~ '[[:space:]\x01-\x1f\x7f-\x9f   -​    　﻿]'
       OR jsonb_typeof(q->'art') <> 'string' OR (q->>'art') NOT IN ('gelesen','websuche') THEN
      RAISE EXCEPTION 'Quelle ungueltig'; END IF;
  END LOOP;
END $$;

-- Sperrt nur die Auftragszeile (wie pult_marke_denken); geaendert_am bleibt unberuehrt.
CREATE OR REPLACE FUNCTION marketing.pult_marke_quellen(p_aid uuid, p_quellen jsonb) RETURNS boolean
LANGUAGE plpgsql AS $$
DECLARE a marketing.marken_auftraege;
BEGIN
  PERFORM marketing._marke_quellen_pruefen(p_quellen);
  SELECT * INTO a FROM marketing.marken_auftraege WHERE id = p_aid FOR UPDATE;
  IF NOT FOUND OR a.status <> 'in_arbeit' OR a.vergeben_bis IS NULL OR a.vergeben_bis <= now() THEN
    RETURN false; END IF;
  UPDATE marketing.marken_auftraege SET quellen = p_quellen WHERE id = a.id;
  RETURN true;
END $$;

-- 3) Rueckgaengig
CREATE OR REPLACE FUNCTION marketing.pult_marke_rueckgaengig(p_vid uuid, p_von text, p_mandant text) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v_mandant text; v marketing.marken_vorschlaege; v_liste jsonb; v_neu jsonb; v_id uuid;
BEGIN
  IF length(btrim(coalesce(p_von, ''))) = 0 THEN RAISE EXCEPTION 'Ohne Namen kein Rückgängig'; END IF;
  SELECT mandant INTO v_mandant FROM marketing.marken_vorschlaege WHERE id = p_vid;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannter Vorschlag'; END IF;
  IF v_mandant IS DISTINCT FROM p_mandant THEN
    RAISE EXCEPTION 'Der Vorschlag gehört zu einer anderen Firma – bitte neu laden'; END IF;
  PERFORM 1 FROM marketing.mandanten WHERE id = v_mandant AND aktiv FOR NO KEY UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannte oder inaktive Firma'; END IF;
  -- Erst aufraeumen, dann den Vorschlag lesen: ein toter Uebernahme-Auftrag setzt seinen Vorschlag hier
  -- wieder auf 'offen' - E7.1 sieht so den echten Stand (sonst wuerde unten das Original selbst 'ersetzt').
  PERFORM marketing._marke_aufraeumen(v_mandant);
  SELECT * INTO v FROM marketing.marken_vorschlaege WHERE id = p_vid FOR UPDATE;
  -- E7.1: angenommen und direkt (JSON-true, kein Text "true")
  IF v.status <> 'angenommen' OR (v.vorschlag->'direkt') IS DISTINCT FROM 'true'::jsonb THEN
    RAISE EXCEPTION 'Rückgängig geht nur für eine direkt übernommene Änderung'; END IF;
  -- E7.2: kein spaeterer angenommener Vorschlag der Firma (Gleichstand zaehlt als spaeter: fail-closed)
  IF EXISTS (SELECT 1 FROM marketing.marken_vorschlaege w
              WHERE w.mandant = v.mandant AND w.status = 'angenommen' AND w.id <> v.id
                AND w.erstellt_am >= v.erstellt_am) THEN
    RAISE EXCEPTION 'Inzwischen gibt es eine neuere Änderung – Rückgängig geht nur für die letzte.'; END IF;
  -- E7.3: kein laufender Marken-Auftrag ausser Wissens-Laeufen (woertlich wie pult_marke_anlegen;
  -- aufgeraeumt ist schon oben)
  IF EXISTS (SELECT 1 FROM marketing.marken_auftraege
              WHERE mandant = v.mandant AND status IN ('offen','in_arbeit') AND art <> 'wissen') THEN
    RAISE EXCEPTION 'Der Assistent arbeitet gerade'; END IF;
  -- Aenderungsliste: nicht leer, je Eintrag feld/vorher/nachher als Text (verschachtelt geprueft, damit
  -- jsonb_array_length nie ein Nicht-Array sieht)
  v_liste := v.vorschlag->'aenderungen';
  IF jsonb_typeof(v_liste) IS DISTINCT FROM 'array' THEN
    RAISE EXCEPTION 'Der Vorschlag hat keine gültige Änderungsliste'; END IF;
  IF jsonb_array_length(v_liste) = 0 OR EXISTS (
       SELECT 1 FROM jsonb_array_elements(v_liste) e
        WHERE jsonb_typeof(e) <> 'object'
           OR jsonb_typeof(e->'feld') IS DISTINCT FROM 'string'
           OR jsonb_typeof(e->'vorher') IS DISTINCT FROM 'string'
           OR jsonb_typeof(e->'nachher') IS DISTINCT FROM 'string') THEN
    RAISE EXCEPTION 'Der Vorschlag hat keine gültige Änderungsliste'; END IF;
  -- Gegen-Vorschlag: abschnitte {feld: vorher} (leerer Text loescht den Abschnitt), Liste vertauscht.
  -- Steht ein Feld mehrfach in der Liste, gewinnt sein ERSTES vorher (der Stand vor der Aenderung).
  SELECT jsonb_build_object(
           'abschnitte', jsonb_object_agg(e->>'feld', e->'vorher' ORDER BY i DESC),
           'aenderungen', jsonb_agg(jsonb_build_object('feld', e->'feld', 'vorher', e->'nachher',
                                                       'nachher', e->'vorher') ORDER BY i),
           'direkt', true,
           'rueckgaengig_von', v.id)
    INTO v_neu
    FROM jsonb_array_elements(v_liste) WITH ORDINALITY AS t(e, i);
  IF octet_length(v_neu::text) > 65536 THEN RAISE EXCEPTION 'Vorschlag zu groß'; END IF;
  UPDATE marketing.marken_vorschlaege SET status = 'ersetzt'
   WHERE mandant = v.mandant AND status = 'offen';
  INSERT INTO marketing.marken_vorschlaege (mandant, auftrag, vorschlag)
  VALUES (v.mandant, v.auftrag, v_neu)
  RETURNING id INTO v_id;
  -- Uebernahme genau wie vom Pult: angenommen + entschieden_von/am + Uebernahme-Auftrag (066)
  RETURN marketing.pult_marke_uebernehmen(v_id, p_von, p_mandant);
END $$;

COMMIT;
