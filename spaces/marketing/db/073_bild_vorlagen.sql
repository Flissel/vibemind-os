-- RUNBOOK: 073 ersetzt die 7-arg-Huelle pult_bild_auftrag(uuid, text, boolean, text, text, int, text) aus 059.
-- Nach einem Replay von 059 IMMER 073 erneut einspielen; danach verify_059 und verify_073 zusammen ueber
-- migration_probe. Achtung: Gibt es schon Auftraege im Modus 'aendern', scheitert ein Replay von 059 (sein
-- Modus-Check ohne 'aendern' prueft die vorhandenen Zeilen) - dann 059 nicht erneut einspielen.
-- 073: Bildauftraege mit Vorlagen und Modus 'aendern' (sales-claw Spec 2026-10-10-bilder-flux2-klein-design.md,
-- Plan Task 4). Additiv, idempotent, eine Transaktion.
--   1) marketing.bild_auftraege.vorlagen: jsonb-Liste von Mediennamen (hoechstens 3), Vorgabe []
--   2) Modus 'aendern' (FLUX.2: das Bild im Platz ist Referenz 1); alte Zeilen 'ueberarbeiten' bleiben gueltig
--   3) pult_bild_auftrag 7-arg (ersetzt die Huelle aus 059): kennt 'aendern' - braucht einen Platz mit echtem
--      Bild ("Hier ist noch kein Bild – nimm bild_erzeugen"), Staerke 100 macht daraus kein 'neu'
--   4) pult_bild_auftrag 8-arg (neu): Vorlagen - hoechstens 3 verschiedene Texte, freistellen ohne Vorlagen,
--      aendern hoechstens 2 (das Bild selbst ist die erste Referenz)
-- Ersetzte Funktionen (jeweils die zuletzt gueltige Definition):
--   pult_bild_auftrag(uuid, text, boolean, text, text, int, text)      aus 059 (die 5-arg-Fassung aus 063
--                        ruft sie und bleibt unveraendert)
-- Benutzt marketing._bild_platz_leer(text) aus 056.
-- Rechte: wie 060 - keine eigenen GRANTs, Default Privileges aus 003 gelten.
-- Nachweis: verify_073.sql.
BEGIN;

ALTER TABLE marketing.bild_auftraege ADD COLUMN IF NOT EXISTS vorlagen jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE marketing.bild_auftraege DROP CONSTRAINT IF EXISTS bild_auftraege_vorlagen_check;
ALTER TABLE marketing.bild_auftraege ADD CONSTRAINT bild_auftraege_vorlagen_check
  CHECK (jsonb_typeof(vorlagen) = 'array' AND jsonb_array_length(vorlagen) <= 3);
ALTER TABLE marketing.bild_auftraege DROP CONSTRAINT IF EXISTS bild_auftraege_modus_check;
ALTER TABLE marketing.bild_auftraege ADD CONSTRAINT bild_auftraege_modus_check
  CHECK (modus IN ('neu','ueberarbeiten','aendern','freistellen'));

-- 7-arg (aus 059): zusaetzlich 'aendern' (braucht einen Platz mit echtem Bild)
CREATE OR REPLACE FUNCTION marketing.pult_bild_auftrag(
    p_inhalt uuid, p_platz text, p_nur_leere boolean, p_hinweis text, p_urheber text,
    p_staerke int, p_modus text) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v_id uuid; v_url text;
BEGIN
  IF p_staerke IS NULL OR p_staerke < 0 OR p_staerke > 100 THEN
    RAISE EXCEPTION 'Staerke muss 0 bis 100 sein'; END IF;
  IF p_modus IS NULL OR p_modus NOT IN ('neu', 'ueberarbeiten', 'aendern', 'freistellen') THEN
    RAISE EXCEPTION 'Modus muss neu, aendern, ueberarbeiten oder freistellen sein'; END IF;
  IF p_modus IN ('freistellen', 'aendern') AND (p_platz IS NULL OR p_platz = '') THEN
    RAISE EXCEPTION '% braucht einen Bildplatz', initcap(p_modus); END IF;
  IF p_modus = 'aendern' THEN
    SELECT bloecke #>> ARRAY[p_platz, 'data', 'props', 'url'] INTO v_url FROM marketing.inhalt_fassungen
     WHERE inhalt = p_inhalt ORDER BY fassung DESC LIMIT 1;
    IF marketing._bild_platz_leer(v_url) THEN
      RAISE EXCEPTION 'Hier ist noch kein Bild – nimm bild_erzeugen'; END IF;
  END IF;
  v_id := marketing.pult_bild_auftrag(p_inhalt, p_platz, p_nur_leere, p_hinweis, p_urheber);
  UPDATE marketing.bild_auftraege
     SET staerke = p_staerke,
         modus = CASE WHEN p_modus IN ('freistellen', 'aendern') THEN p_modus
                      WHEN p_staerke = 100 THEN 'neu' ELSE p_modus END
   WHERE id = v_id;
  RETURN v_id;
END $$;

-- 8-arg: Vorlagen (Mediennamen, hoechstens 3, nur Texte, ohne Doppelte)
CREATE OR REPLACE FUNCTION marketing.pult_bild_auftrag(
    p_inhalt uuid, p_platz text, p_nur_leere boolean, p_hinweis text, p_urheber text,
    p_staerke int, p_modus text, p_vorlagen jsonb) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v_id uuid; v_v jsonb := coalesce(p_vorlagen, '[]'::jsonb);
BEGIN
  IF jsonb_typeof(v_v) <> 'array' THEN   -- eigener Schritt: OR wertet nicht garantiert von links aus
    RAISE EXCEPTION 'Vorlagen: hoechstens 3 verschiedene Mediennamen'; END IF;
  IF jsonb_array_length(v_v) > 3
     OR EXISTS (SELECT 1 FROM jsonb_array_elements(v_v) e WHERE jsonb_typeof(e) <> 'string')
     OR (SELECT count(DISTINCT e) FROM jsonb_array_elements_text(v_v) e) <> jsonb_array_length(v_v) THEN
    RAISE EXCEPTION 'Vorlagen: hoechstens 3 verschiedene Mediennamen'; END IF;
  IF p_modus = 'freistellen' AND jsonb_array_length(v_v) > 0 THEN
    RAISE EXCEPTION 'Freistellen nimmt keine Vorlagen'; END IF;
  IF p_modus = 'aendern' AND jsonb_array_length(v_v) > 2 THEN
    RAISE EXCEPTION 'Aendern: hoechstens 2 Vorlagen (das Bild selbst ist die erste)'; END IF;
  v_id := marketing.pult_bild_auftrag(p_inhalt, p_platz, p_nur_leere, p_hinweis, p_urheber, p_staerke, p_modus);
  UPDATE marketing.bild_auftraege SET vorlagen = v_v WHERE id = v_id;
  RETURN v_id;
END $$;

COMMIT;
