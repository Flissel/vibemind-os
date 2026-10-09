-- 065: Denken und Schritte der Agenten-Auftraege (Spec sales-claw 2026-10-09-agent-denken-sichtbar).
-- Idempotent, eine Transaktion.
BEGIN;

ALTER TABLE marketing.chat_auftraege ADD COLUMN IF NOT EXISTS denken text;
ALTER TABLE marketing.chat_auftraege ADD COLUMN IF NOT EXISTS schritte jsonb;
ALTER TABLE marketing.marken_auftraege ADD COLUMN IF NOT EXISTS denken text;
ALTER TABLE marketing.marken_auftraege ADD COLUMN IF NOT EXISTS schritte jsonb;

-- Grenzen auch in der DB: 20 000 Zeichen + Kuerzungsvermerk, 60 Schritte, je 200 Zeichen Text.
CREATE OR REPLACE FUNCTION marketing._spur_pruefen(p_denken text, p_schritte jsonb) RETURNS void
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE s jsonb;
BEGIN
  IF p_denken IS NOT NULL AND char_length(p_denken) > 20100 THEN RAISE EXCEPTION 'Denken zu lang'; END IF;
  IF p_schritte IS NULL OR jsonb_typeof(p_schritte) <> 'array' THEN
    RAISE EXCEPTION 'Schritte muessen eine Liste sein'; END IF;
  IF jsonb_array_length(p_schritte) > 60 THEN RAISE EXCEPTION 'Hoechstens 60 Schritte'; END IF;
  FOR s IN SELECT * FROM jsonb_array_elements(p_schritte) LOOP
    IF jsonb_typeof(s) <> 'object'
       OR jsonb_typeof(s->'zeit') IS DISTINCT FROM 'string' OR char_length(s->>'zeit') > 20
       OR jsonb_typeof(s->'text') IS DISTINCT FROM 'string' OR char_length(s->>'text') > 200 THEN
      RAISE EXCEPTION 'Schritt ungueltig'; END IF;
  END LOOP;
END $$;

-- Sperrt nur die Auftragszeile (eine einzige Sperre => keine Reihenfolge zu beachten).
-- geaendert_am bleibt unberuehrt (es ordnet Verlauf und letzte Uebernahme).
CREATE OR REPLACE FUNCTION marketing.pult_chat_denken(p_auftrag uuid, p_denken text, p_schritte jsonb)
RETURNS boolean LANGUAGE plpgsql AS $$
DECLARE a marketing.chat_auftraege;
BEGIN
  PERFORM marketing._spur_pruefen(p_denken, p_schritte);
  SELECT * INTO a FROM marketing.chat_auftraege WHERE id = p_auftrag FOR UPDATE;
  IF NOT FOUND OR a.status <> 'in_arbeit' OR a.vergeben_bis IS NULL OR a.vergeben_bis <= now() THEN
    RETURN false; END IF;
  UPDATE marketing.chat_auftraege SET denken = p_denken, schritte = p_schritte WHERE id = a.id;
  RETURN true;
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_marke_denken(p_auftrag uuid, p_denken text, p_schritte jsonb)
RETURNS boolean LANGUAGE plpgsql AS $$
DECLARE a marketing.marken_auftraege;
BEGIN
  PERFORM marketing._spur_pruefen(p_denken, p_schritte);
  SELECT * INTO a FROM marketing.marken_auftraege WHERE id = p_auftrag FOR UPDATE;
  IF NOT FOUND OR a.status <> 'in_arbeit' OR a.vergeben_bis IS NULL OR a.vergeben_bis <= now() THEN
    RETURN false; END IF;
  UPDATE marketing.marken_auftraege SET denken = p_denken, schritte = p_schritte WHERE id = a.id;
  RETURN true;
END $$;

COMMIT;
