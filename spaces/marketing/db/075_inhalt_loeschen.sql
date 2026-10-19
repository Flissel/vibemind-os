-- RUNBOOK: nach 074. Idempotent, eine Transaktion. Nach jedem Replay verify_075 laufen lassen.
-- Nach 075 NIE 050 erneut einspielen (setzt den Trigger auf `_fassung_unveraenderlich` zurueck – Loeschen
-- scheitert dann fail-closed). 051 schaltet den Trigger nur ab und wieder an (Name bleibt) und ist hier
-- unschaedlich; 063, 067 und 073 beruehren weder den Trigger noch pult_inhalt_loeschen.
-- 075: Entwuerfe endgueltig loeschen (sales-claw Plan 2026-10-19-editor-entwuerfe-bilder.md, E1/E2).
--   1) marketing._inhalt_fassung_unveraenderlich(): Trigger-Funktion NUR fuer inhalt_fassungen. Wie
--      _fassung_unveraenderlich (050), erlaubt aber DELETE, solange die transaktionslokale Einstellung
--      marketing.inhalt_loeschen genau die id dieses Inhalts traegt (setzt nur pult_inhalt_loeschen) UND
--      der Inhalt im Status entwurf oder abgelehnt steht (eingereicht/freigegeben bleiben hart) UND die
--      Rolle der Anweisung (current_user) Eigentuemerin von inhalt_fassungen oder Superuser ist (Final-Review
--      I1). Die Ausnahme gilt damit nur fuer Rollen, die den Trigger ohnehin abschalten koennten - nie
--      staerker als vor 075. Die API muss als diese Rolle verbinden, sonst scheitert Loeschen fail-closed.
--      Trigger-Funktion und pult_inhalt_loeschen bleiben SECURITY INVOKER (bei DEFINER waere current_user
--      immer die Eigentuemerin, und ein versehentliches EXECUTE-Grant oeffnete das Loch wieder).
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
-- Rechte: wie 071/074 - EXECUTE nicht fuer PUBLIC. Aufrufer ist die API-Rolle (SUPABASE_DB_USER, Vorgabe
-- supabase_admin = Eigentuemerin); service_role hat danach kein EXECUTE (gemessen in der Probe, s. verify_075).
-- RUNBOOK: Die Ausnahme gilt nur fuer die Eigentuemerin von inhalt_fassungen bzw. fuer Superuser. Die API
-- muss als diese Rolle verbinden, sonst scheitert Loeschen fail-closed. Vor dem Einspielen pruefen:
--   SELECT tableowner FROM pg_tables WHERE schemaname='marketing' AND tablename='inhalt_fassungen';
-- und mit SUPABASE_DB_USER der API vergleichen (VM und PC). Stand 19.10.: Eigentuemerin supabase_admin
-- (Superuser), die API verbindet ohne SUPABASE_DB_USER als supabase_admin.
-- Nachweis: verify_075.sql.
BEGIN;

-- 1) Trigger-Funktion mit genau einer Ausnahme. Die Einstellung kann JEDE Rolle per set_config setzen
--    (service_role hat DELETE auf inhalt_fassungen und UPDATE auf inhalte, ist aber nicht Eigentuemerin und
--    kann den Trigger nicht abschalten). Den Status koennte service_role selbst auf entwurf stellen, darum
--    reicht der Status-Filter allein nicht: die Ausnahme gilt nur, wenn current_user die Tabelle besitzt
--    oder Superuser ist (Final-Review I1). Zusaetzlich bleibt der Status-Filter - dieselbe Menge wie in
--    pult_inhalt_loeschen: entwurf (auch "zurueckgegeben") und abgelehnt ("verworfen"). Fassungen
--    eingereichter und freigegebener Inhalte bleiben auch fuer die Eigentuemerin ausserhalb der Funktion hart.
--    SECURITY INVOKER (Vorgabe) - sonst waere current_user immer die Eigentuemerin.
CREATE OR REPLACE FUNCTION marketing._inhalt_fassung_unveraenderlich() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE'
     AND current_setting('marketing.inhalt_loeschen', true) = OLD.inhalt::text
     -- Nur wer den Trigger ohnehin abschalten koennte (Eigentuemerin/Superuser): nie staerker als vor 075.
     AND EXISTS (SELECT 1 FROM pg_class c JOIN pg_roles r ON r.rolname = current_user
                  WHERE c.oid = TG_RELID AND (c.relowner = r.oid OR r.rolsuper))
     AND EXISTS (SELECT 1 FROM marketing.inhalte
                  WHERE id = OLD.inhalt AND status IN ('entwurf', 'abgelehnt')) THEN
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
