-- RUNBOOK: nach 073. Idempotent, eine Transaktion. Nach jedem Replay verify_074 laufen lassen.
-- 074: Podcast-Seite im Pult (19.10.2026). Folgen sind nicht loeschbar, nur abbrechbar; je Auftrag wird
-- festgehalten, ob Agent oder Betreiber ihn angelegt hat. Versendet wird nichts.
BEGIN;

ALTER TABLE marketing.ton_auftraege
    ADD COLUMN IF NOT EXISTS angelegt_von text NOT NULL DEFAULT 'agent'
    CHECK (angelegt_von IN ('agent', 'betreiber'));

ALTER TABLE marketing.ton_auftraege DROP CONSTRAINT IF EXISTS ton_auftraege_status_check;
ALTER TABLE marketing.ton_auftraege ADD CONSTRAINT ton_auftraege_status_check
    CHECK (status IN ('offen', 'in_arbeit', 'fertig', 'fehler', 'abgebrochen'));

-- Die 4-Parameter-Fassung aus 070 entfaellt, sonst waeren zwei Ueberladungen mehrdeutig.
DROP FUNCTION IF EXISTS marketing.ton_auftrag_anlegen(text, text, text, jsonb);
CREATE OR REPLACE FUNCTION marketing.ton_auftrag_anlegen(p_mandant text, p_titel text, p_sprache text,
                                                         p_skript jsonb, p_von text DEFAULT 'agent') RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v uuid;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM marketing.mandanten WHERE id = p_mandant) THEN
    RAISE EXCEPTION 'Unbekannte firma'; END IF;
  INSERT INTO marketing.ton_auftraege (mandant, titel, sprache, skript, angelegt_von)
  VALUES (p_mandant, btrim(p_titel), p_sprache, p_skript, p_von)
  RETURNING id INTO v;
  RETURN v;
END $$;

-- Abbrechen durch den Betreiber: aus offen oder in_arbeit.
CREATE OR REPLACE FUNCTION marketing.ton_abbrechen(p_id uuid) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE v text;
BEGIN
  UPDATE marketing.ton_auftraege SET status = 'abgebrochen', vergeben_bis = NULL,
         befund = 'Im Pult abgebrochen', geaendert_am = now()
   WHERE id = p_id AND status IN ('offen', 'in_arbeit')
  RETURNING status INTO v;
  IF v IS NULL THEN RAISE EXCEPTION 'Auftrag ist nicht mehr offen'; END IF;
  RETURN v;
END $$;

REVOKE ALL ON FUNCTION marketing.ton_auftrag_anlegen(text, text, text, jsonb, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.ton_abbrechen(uuid) FROM PUBLIC;

COMMIT;
