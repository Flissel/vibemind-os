-- RUNBOOK: nach 069. Idempotent, eine Transaktion. Nach jedem Replay verify_070 laufen lassen.
-- 070: Podcast-Auftrag anlegen als Funktion (17.10.2026). Die Marketing-API liest Ergebnisse ueber
-- `SELECT ... FROM (<sql>)` (sync/_db.query_via_docker); ein INSERT - auch als CTE - geht darin nicht.
-- Gemessen beim Livegang: "syntax error at or near INTO". Darum wie die uebrigen Pult-Routen eine Funktion.
BEGIN;

CREATE OR REPLACE FUNCTION marketing.ton_auftrag_anlegen(p_mandant text, p_titel text, p_sprache text,
                                                         p_skript jsonb) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v uuid;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM marketing.mandanten WHERE id = p_mandant) THEN
    RAISE EXCEPTION 'Unbekannte firma'; END IF;
  INSERT INTO marketing.ton_auftraege (mandant, titel, sprache, skript)
  VALUES (p_mandant, btrim(p_titel), p_sprache, p_skript)
  RETURNING id INTO v;
  RETURN v;
END $$;

COMMIT;
