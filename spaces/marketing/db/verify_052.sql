-- verify_052.sql — Proben fuer 052_marketing_pult_bruecke.sql (Fix-Runde 2:
-- FK ON DELETE SET NULL, Titel-Fallback 'Ohne Titel', AFTER UPDATE OF status
-- Bruecke). Aendert nichts (ROLLBACK).
BEGIN;

-- 1: ein geloeschter Proposal reisst den gespiegelten Inhalt nicht mit -
-- FK ist jetzt ON DELETE SET NULL statt RESTRICT.
DO $$
DECLARE v_p uuid; v_i uuid;
BEGIN
  INSERT INTO marketing.broadcast_proposals (channel, status, draft_subject, draft_body_text, created_by)
  VALUES ('linkedin', 'draft', 'FK-Test', 'FK-Test-Inhalt', 'verify_052')
  RETURNING id INTO v_p;

  SELECT id INTO v_i FROM marketing.inhalte WHERE herkunft_proposal = v_p;
  IF v_i IS NULL THEN RAISE EXCEPTION 'PROBE: kein Inhalt fuer den FK-Test angelegt'; END IF;

  DELETE FROM marketing.broadcast_proposals WHERE id = v_p;

  IF NOT EXISTS (SELECT 1 FROM marketing.inhalte WHERE id = v_i AND herkunft_proposal IS NULL) THEN
    RAISE EXCEPTION 'PROBE: Inhalt ueberlebte das Loeschen des Proposals nicht mit NULL-Herkunft';
  END IF;
END $$;

-- 2: Titel-Fallback. Leerzeichen-Body mit Text ergibt den getrimmten Text;
-- ein Body aus NUR Leerzeichen (von der Tabelle erlaubt, da
-- broadcast_proposals_body_nonempty nur length()>0 prueft, nicht btrim)
-- bricht die Bruecke nicht mehr ab, sondern liefert 'Ohne Titel'.
DO $$
DECLARE v_p uuid; v_t text;
BEGIN
  INSERT INTO marketing.broadcast_proposals (channel, status, draft_subject, draft_body_text, created_by)
  VALUES ('linkedin', 'draft', NULL, '   x', 'verify_052')
  RETURNING id INTO v_p;
  SELECT titel INTO v_t FROM marketing.inhalte WHERE herkunft_proposal = v_p;
  IF v_t IS NULL OR length(btrim(v_t)) = 0 THEN
    RAISE EXCEPTION 'PROBE: Titel-Abbildung fuer Leerzeichen+Text-Body versagt: %', v_t; END IF;
  IF v_t <> 'x' THEN RAISE EXCEPTION 'PROBE: erwarteter Titel "x", bekam %', v_t; END IF;

  INSERT INTO marketing.broadcast_proposals (channel, status, draft_subject, draft_body_text, created_by)
  VALUES ('linkedin', 'draft', NULL, '    ', 'verify_052')
  RETURNING id INTO v_p;
  SELECT titel INTO v_t FROM marketing.inhalte WHERE herkunft_proposal = v_p;
  IF v_t IS NULL OR length(btrim(v_t)) = 0 THEN
    RAISE EXCEPTION 'PROBE: Nur-Leerzeichen-Body brach die Bruecke ab oder ergab einen leeren Titel'; END IF;
  IF v_t <> 'Ohne Titel' THEN
    RAISE EXCEPTION 'PROBE: erwarteter Fallback-Titel "Ohne Titel", bekam %', v_t; END IF;
END $$;

-- 3: eine spaetere Statusaenderung des Proposals (alter Freigabe-Weg) spiegelt
-- sich in den noch offenen Pult-Inhalt; eine im Pult bereits getroffene
-- Entscheidung bleibt von einer spaeteren Proposal-Statusaenderung unberuehrt.
DO $$
DECLARE v_p uuid; v_i uuid; v_p2 uuid; v_i2 uuid; v_n int;
BEGIN
  INSERT INTO marketing.broadcast_proposals (channel, status, draft_subject, draft_body_text, created_by)
  VALUES ('telegram', 'draft', 'Update-Test', 'Update-Test-Inhalt', 'verify_052')
  RETURNING id INTO v_p;
  SELECT id INTO v_i FROM marketing.inhalte WHERE herkunft_proposal = v_p;
  IF (SELECT status FROM marketing.inhalte WHERE id = v_i) <> 'entwurf' THEN
    RAISE EXCEPTION 'PROBE: frischer Proposal nicht als entwurf gespiegelt'; END IF;

  UPDATE marketing.broadcast_proposals
     SET status = 'rejected', rejected_by = 'verify_052', rejected_at = now(),
         rejection_reason = 'Testgrund-Update'
   WHERE id = v_p;

  IF (SELECT status FROM marketing.inhalte WHERE id = v_i) <> 'abgelehnt'
     OR (SELECT grund FROM marketing.inhalte WHERE id = v_i) <> 'Testgrund-Update'
     OR (SELECT entschieden_von FROM marketing.inhalte WHERE id = v_i) <> 'verify_052' THEN
    RAISE EXCEPTION 'PROBE: spaetere Proposal-Statusaenderung wurde nicht auf den offenen Inhalt gespiegelt'; END IF;

  -- Pult-Entscheidung zuerst, dann eine spaetere Proposal-Statusaenderung -
  -- die darf NICHT mehr durchgreifen.
  INSERT INTO marketing.broadcast_proposals (channel, status, draft_subject, draft_body_text, created_by)
  VALUES ('telegram', 'draft', 'Pult-Entscheidung-Test', 'Pult-Entscheidung-Test-Inhalt', 'verify_052')
  RETURNING id INTO v_p2;
  SELECT id INTO v_i2 FROM marketing.inhalte WHERE herkunft_proposal = v_p2;
  SELECT max(fassung) INTO v_n FROM marketing.inhalt_fassungen WHERE inhalt = v_i2;
  PERFORM marketing.pult_entscheiden(v_i2, v_n, 'freigeben', 'felix', NULL);

  UPDATE marketing.broadcast_proposals
     SET status = 'rejected', rejected_by = 'verify_052', rejected_at = now(),
         rejection_reason = 'sollte ignoriert werden'
   WHERE id = v_p2;

  IF (SELECT status FROM marketing.inhalte WHERE id = v_i2) <> 'freigegeben' THEN
    RAISE EXCEPTION 'PROBE: eine im Pult getroffene Entscheidung wurde von einer spaeteren Proposal-Statusaenderung ueberschrieben'; END IF;
END $$;

ROLLBACK;
