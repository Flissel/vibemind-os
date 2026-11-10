-- RUNBOOK: nach 096. Idempotent (CREATE OR REPLACE), eine Transaktion. Danach verify_097 (in ROLLBACK).
-- 097: KI-Reels aus dem Auftragsbuch brauchen keinen Bilder-Halt. Traegt das Briefing den Schluessel
-- auftrag, gibt _reel_bilder_abschliessen die Bilder selbst frei: mindestens ein fertiges Bild ->
-- video_ki_freigeben (Status offen, briefing.bilder gesetzt), kein fertiges Bild -> fehler. Ohne auftrag
-- bleibt alles wie in 090 (bilder_pruefen, der Betreiber prueft).
BEGIN;

-- Interner Schritt, Signatur wie in 090 (Sprache jetzt plpgsql wegen des Zweigs). Der Aufrufer haelt die
-- Sperre auf den Auftrag.
CREATE OR REPLACE FUNCTION marketing._reel_bilder_abschliessen(p_auftrag uuid) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE a marketing.video_auftraege;
BEGIN
  UPDATE marketing.video_auftraege SET status = 'bilder_pruefen', geaendert_am = now()
   WHERE id = p_auftrag AND status = 'bilder_laufen'
     AND NOT EXISTS (SELECT 1 FROM marketing.reel_bilder
                      WHERE video_auftrag = p_auftrag AND status IN ('offen', 'in_arbeit'))
  RETURNING * INTO a;
  IF a.id IS NULL OR NOT (a.briefing ? 'auftrag') THEN RETURN; END IF;
  IF EXISTS (SELECT 1 FROM marketing.reel_bilder WHERE video_auftrag = p_auftrag AND status = 'fertig') THEN
    PERFORM marketing.video_ki_freigeben(p_auftrag, a.mandant);
  ELSE
    UPDATE marketing.video_auftraege SET status = 'fehler', befund = 'Keine KI-Bilder entstanden',
           vergeben_bis = NULL, geaendert_am = now()
     WHERE id = p_auftrag;
  END IF;
END $$;

REVOKE ALL ON FUNCTION marketing._reel_bilder_abschliessen(uuid) FROM PUBLIC;

COMMIT;
