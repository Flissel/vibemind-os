-- 057_bild_ueberarbeiten.sql — Bild-Auftraege mit Staerke, Modus, Messung (sales-claw
-- Spec 2026-09-30-newsletter-bild-ueberarbeiten-design.md §6). Additiv, idempotent.
BEGIN;

ALTER TABLE marketing.bild_auftraege ADD COLUMN IF NOT EXISTS staerke int NOT NULL DEFAULT 55;
ALTER TABLE marketing.bild_auftraege ADD COLUMN IF NOT EXISTS modus text NOT NULL DEFAULT 'ueberarbeiten';
ALTER TABLE marketing.bild_auftraege ADD COLUMN IF NOT EXISTS messung jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE marketing.bild_auftraege DROP CONSTRAINT IF EXISTS bild_auftraege_staerke_check;
ALTER TABLE marketing.bild_auftraege ADD CONSTRAINT bild_auftraege_staerke_check CHECK (staerke BETWEEN 0 AND 100);
ALTER TABLE marketing.bild_auftraege DROP CONSTRAINT IF EXISTS bild_auftraege_modus_check;
ALTER TABLE marketing.bild_auftraege ADD CONSTRAINT bild_auftraege_modus_check CHECK (modus IN ('neu','ueberarbeiten'));
ALTER TABLE marketing.bild_auftraege DROP CONSTRAINT IF EXISTS bild_auftraege_messung_check;
ALTER TABLE marketing.bild_auftraege ADD CONSTRAINT bild_auftraege_messung_check CHECK (jsonb_typeof(messung) = 'object');

CREATE OR REPLACE FUNCTION marketing.pult_bild_auftrag(
    p_inhalt uuid, p_platz text, p_nur_leere boolean, p_hinweis text, p_urheber text,
    p_staerke int, p_modus text) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v_id uuid;
BEGIN
  IF p_staerke IS NULL OR p_staerke < 0 OR p_staerke > 100 THEN
    RAISE EXCEPTION 'Staerke muss 0 bis 100 sein'; END IF;
  IF p_modus IS NULL OR p_modus NOT IN ('neu', 'ueberarbeiten') THEN
    RAISE EXCEPTION 'Modus muss neu oder ueberarbeiten sein'; END IF;
  v_id := marketing.pult_bild_auftrag(p_inhalt, p_platz, p_nur_leere, p_hinweis, p_urheber);
  UPDATE marketing.bild_auftraege
     SET staerke = p_staerke, modus = CASE WHEN p_staerke = 100 THEN 'neu' ELSE p_modus END
   WHERE id = v_id;
  RETURN v_id;
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_bild_einsetzen(
    p_auftrag uuid, p_ergebnis jsonb, p_befund text, p_messung jsonb) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE r jsonb;
BEGIN
  IF p_messung IS NOT NULL AND jsonb_typeof(p_messung) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'Messung muss ein Objekt sein'; END IF;
  r := marketing.pult_bild_einsetzen(p_auftrag, p_ergebnis, p_befund);
  UPDATE marketing.bild_auftraege SET messung = coalesce(p_messung, '{}'::jsonb) WHERE id = p_auftrag;
  RETURN r;
END $$;

COMMIT;
