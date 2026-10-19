-- RUNBOOK: nach 074. Idempotent, eine Transaktion. Nach jedem Replay verify_075 laufen lassen.
-- Nach 075 NIE 050 erneut einspielen (setzt den Trigger auf `_fassung_unveraenderlich` zurueck – Loeschen
-- scheitert dann fail-closed). 051 schaltet den Trigger nur ab und wieder an (Name bleibt) und ist hier
-- unschaedlich; 063, 067 und 073 beruehren weder den Trigger noch pult_inhalt_loeschen.
-- 075: Entwuerfe endgueltig loeschen (sales-claw Plan 2026-10-19-editor-entwuerfe-bilder.md, E1/E2).
--   1) marketing._inhalt_fassung_unveraenderlich(): Trigger-Funktion NUR fuer inhalt_fassungen. Wie
--      _fassung_unveraenderlich (050), erlaubt aber DELETE, solange die transaktionslokale Einstellung
--      marketing.inhalt_loeschen genau die id dieses Inhalts traegt (setzt nur pult_inhalt_loeschen).
--      layout_fassungen und newsletter_vorlagen_fassungen behalten _fassung_unveraenderlich.
--   2) marketing.pult_inhalt_loeschen(p_inhalt, p_mandant, p_von) RETURNS text (Titel). Loeschbar sind
--      nur entwurf und abgelehnt. Sperren (Klartext, in dieser Reihenfolge): Unbekannter Inhalt, fremde
--      Firma, eingereicht, freigegeben, laufender Assistent, laufendes Bild, alter Freigabeweg offen.
--      Mitgeloescht: Chat- und Bild-Auftraege, Fassungen; Rueckmeldungen per CASCADE (063); der
--      Kampagnenplan verliert nur die Verknuepfung (SET NULL, 071). Erzeugte Bilder bleiben in der
--      Medienbibliothek. Andere Fremdschluessel ohne CASCADE/SET NULL auf inhalte gibt es nicht
--      (verify_075 Abschnitt 0 prueft das ueber pg_constraint).
-- Sperrreihenfolge wie 061: erst marketing.inhalte, dann Auftraege.
-- Ersetzte Funktionen: keine. Neu: die beiden oben. Keine Ueberladung bestehender Namen.
-- Rechte: wie 071/074 - EXECUTE nicht fuer PUBLIC (service_role behaelt es ueber 003).
-- Nachweis: verify_075.sql.
BEGIN;

-- 1) Trigger-Funktion mit genau einer Ausnahme
CREATE OR REPLACE FUNCTION marketing._inhalt_fassung_unveraenderlich() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE'
     AND current_setting('marketing.inhalt_loeschen', true) = OLD.inhalt::text THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'Fassungen sind unveraenderlich - speichern legt eine neue an';
END $$;
DROP TRIGGER IF EXISTS trg_inhalt_fassung_unveraenderlich ON marketing.inhalt_fassungen;
CREATE TRIGGER trg_inhalt_fassung_unveraenderlich
  BEFORE UPDATE OR DELETE ON marketing.inhalt_fassungen
  FOR EACH ROW EXECUTE FUNCTION marketing._inhalt_fassung_unveraenderlich();

-- 2) Loeschen
CREATE OR REPLACE FUNCTION marketing.pult_inhalt_loeschen(p_inhalt uuid, p_mandant text, p_von text) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE v_mandant text; v_status text; v_titel text; v_proposal uuid;
BEGIN
  -- Sperrreihenfolge wie 061: erst der Inhalt, dann die Auftraege
  SELECT mandant, status, titel, herkunft_proposal INTO v_mandant, v_status, v_titel, v_proposal
    FROM marketing.inhalte WHERE id = p_inhalt FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannter Inhalt'; END IF;
  IF v_mandant IS DISTINCT FROM p_mandant THEN RAISE EXCEPTION 'Gehört einer anderen Firma'; END IF;
  IF v_status = 'eingereicht' THEN RAISE EXCEPTION 'Liegt zur Freigabe – erst zurückziehen'; END IF;
  IF v_status NOT IN ('entwurf', 'abgelehnt') THEN
    RAISE EXCEPTION 'Freigegebene Inhalte bleiben erhalten'; END IF;
  PERFORM 1 FROM marketing.chat_auftraege WHERE inhalt = p_inhalt FOR UPDATE;
  IF EXISTS (SELECT 1 FROM marketing.chat_auftraege
              WHERE inhalt = p_inhalt AND status = 'in_arbeit' AND vergeben_bis > now()) THEN
    RAISE EXCEPTION 'Der Assistent arbeitet gerade'; END IF;
  PERFORM 1 FROM marketing.bild_auftraege WHERE inhalt = p_inhalt FOR UPDATE;
  IF EXISTS (SELECT 1 FROM marketing.bild_auftraege
              WHERE inhalt = p_inhalt AND status = 'in_arbeit' AND vergeben_bis > now()) THEN
    RAISE EXCEPTION 'Ein Bild wird gerade erzeugt'; END IF;
  IF v_proposal IS NOT NULL AND EXISTS (SELECT 1 FROM marketing.broadcast_proposals
                                         WHERE id = v_proposal AND status IN ('draft', 'pending_approval')) THEN
    RAISE EXCEPTION 'Liegt noch im alten Freigabeweg – dort erst ablehnen'; END IF;

  DELETE FROM marketing.chat_auftraege WHERE inhalt = p_inhalt;
  DELETE FROM marketing.bild_auftraege WHERE inhalt = p_inhalt;
  -- transaktionslokal; gilt nur fuer genau diesen Inhalt und wird gleich wieder geleert
  PERFORM set_config('marketing.inhalt_loeschen', p_inhalt::text, true);
  DELETE FROM marketing.inhalt_fassungen WHERE inhalt = p_inhalt;
  DELETE FROM marketing.inhalte WHERE id = p_inhalt;   -- rueckmeldungen CASCADE, kampagnen_plan SET NULL
  PERFORM set_config('marketing.inhalt_loeschen', '', true);
  RETURN v_titel;
END $$;

REVOKE ALL ON FUNCTION marketing.pult_inhalt_loeschen(uuid, text, text) FROM PUBLIC;

COMMIT;
