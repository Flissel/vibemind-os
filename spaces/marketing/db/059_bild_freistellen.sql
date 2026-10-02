-- 059_bild_freistellen.sql - Auftragsmodus freistellen (sales-claw Spec
-- 2026-10-02-newsletter-bild-freistellen-design.md §2). Additiv, idempotent.
BEGIN;
ALTER TABLE marketing.bild_auftraege DROP CONSTRAINT IF EXISTS bild_auftraege_modus_check;
ALTER TABLE marketing.bild_auftraege ADD CONSTRAINT bild_auftraege_modus_check
  CHECK (modus IN ('neu','ueberarbeiten','freistellen'));

CREATE OR REPLACE FUNCTION marketing.pult_bild_auftrag(
    p_inhalt uuid, p_platz text, p_nur_leere boolean, p_hinweis text, p_urheber text,
    p_staerke int, p_modus text) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v_id uuid;
BEGIN
  IF p_staerke IS NULL OR p_staerke < 0 OR p_staerke > 100 THEN
    RAISE EXCEPTION 'Staerke muss 0 bis 100 sein'; END IF;
  IF p_modus IS NULL OR p_modus NOT IN ('neu', 'ueberarbeiten', 'freistellen') THEN
    RAISE EXCEPTION 'Modus muss neu, ueberarbeiten oder freistellen sein'; END IF;
  IF p_modus = 'freistellen' AND (p_platz IS NULL OR p_platz = '') THEN
    RAISE EXCEPTION 'Freistellen braucht einen Bildplatz'; END IF;
  v_id := marketing.pult_bild_auftrag(p_inhalt, p_platz, p_nur_leere, p_hinweis, p_urheber);
  UPDATE marketing.bild_auftraege
     SET staerke = p_staerke,
         modus = CASE WHEN p_modus = 'freistellen' THEN 'freistellen'
                      WHEN p_staerke = 100 THEN 'neu' ELSE p_modus END
   WHERE id = v_id;
  RETURN v_id;
END $$;
COMMIT;
