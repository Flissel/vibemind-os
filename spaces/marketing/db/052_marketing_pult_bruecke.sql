-- 052_marketing_pult_bruecke.sql — Fix-Runde 2 auf 051 (task-1-fix2-review,
-- 2026-09-29). 051 ist bereits produktiv angewendet; diese Datei AENDERT
-- NICHT, was 051 bereits geschrieben hat, sondern korrigiert Regressionen,
-- die 051s Bruecke selbst eingefuehrt hat, und schliesst eine Luecke.
-- Additiv, idempotent, in einer Transaktion.
--
-- Behobene Befunde:
--   1 (wichtig) — die FK inhalte_herkunft_proposal_fkey hatte keine ON
--        DELETE-Aktion (impliziert RESTRICT). Seit 051 zeigt jeder Proposal
--        per Bruecke auf einen Inhalt, also blockiert jedes
--        "DELETE FROM marketing.broadcast_proposals" jetzt mit einem FK-
--        Fehler — u.a. verify_036_037.sql:100. Fix: Constraint droppen und
--        mit ON DELETE SET NULL neu anlegen; der Inhalt bleibt bestehen,
--        verliert nur den Verweis auf die geloeschte Quelle.
--   2 (minor) — die Titel-Abbildung (coalesce(nullif(btrim(subject),''),
--        left(body,60))) konnte bei einem reinen Leerzeichen-Body einen nur-
--        Leerzeichen-Titel liefern und damit inhalte_titel_check verletzen -
--        die INSERT-Bruecke waere mit einer haesslichen Constraint-
--        Verletzung abgebrochen statt mit einem Fallback. Fix: dritte Stufe
--        'Ohne Titel' im coalesce, body vor dem Abschneiden bereits
--        getrimmt.
--   3 (Rand, aber verbindlich) — die Bruecke reagierte nur auf INSERT. Wenn
--        der ALTE Freigabe-Weg (approval_channel/Token, 037ff) einen
--        Proposal spaeter per UPDATE auf rejected/approved/sent setzt, blieb
--        der gespiegelte Pult-Inhalt für immer 'entwurf'. Fix: ein zweiter
--        Trigger, AFTER UPDATE OF status, der dieselbe Abbildungsfunktion
--        wie die Bruecke benutzt (keine zweite Kopie der Regeln) und nur
--        greift, solange der Inhalt noch 'entwurf' ist — eine im Pult
--        getroffene Entscheidung wird nie ueberschrieben.
--   Zusaetzlich: _inhalt_abbildung_aus_proposal wird IMMUTABLE -> STABLE
--        (sie liest jetzt implizit nichts Tabellenabhaengiges mehr an sich,
--        aber IMMUTABLE war ohnehin zu stark fuer eine Funktion, die einen
--        Tabellenzeilentyp als Parameter nimmt und von Aufrufern in
--        Trigger-Kontext verwendet wird — STABLE ist die korrekte, billige
--        Einstufung).
BEGIN;

-- 1: FK mit ON DELETE SET NULL statt implizitem RESTRICT.
ALTER TABLE marketing.inhalte DROP CONSTRAINT IF EXISTS inhalte_herkunft_proposal_fkey;
ALTER TABLE marketing.inhalte
  ADD CONSTRAINT inhalte_herkunft_proposal_fkey
  FOREIGN KEY (herkunft_proposal) REFERENCES marketing.broadcast_proposals(id) ON DELETE SET NULL;

-- 2 + STABLE: dieselbe EINE Abbildungsfunktion, die schon die Bruecke (051)
-- benutzt - Titel bekommt eine dritte Fallback-Stufe, damit ein reiner
-- Leerzeichen-Body niemals inhalte_titel_check verletzt.
CREATE OR REPLACE FUNCTION marketing._inhalt_abbildung_aus_proposal(p marketing.broadcast_proposals)
RETURNS TABLE(art text, status text, titel text, freigegebene_fassung int,
              entschieden_von text, entschieden_am timestamptz, grund text)
LANGUAGE plpgsql STABLE AS $$
BEGIN
  art   := CASE WHEN p.channel = 'email' THEN 'newsletter' ELSE 'post' END;
  titel := coalesce(nullif(btrim(p.draft_subject), ''),
                     nullif(btrim(left(btrim(p.draft_body_text), 60)), ''),
                     'Ohne Titel');
  freigegebene_fassung := NULL;
  entschieden_von := NULL;
  entschieden_am  := NULL;
  grund := NULL;
  IF p.status = 'rejected' THEN
    status := 'abgelehnt';
    entschieden_von := coalesce(p.rejected_by, 'bestand');
    entschieden_am  := coalesce(p.rejected_at, p.created_at);
    grund := p.rejection_reason;
  ELSIF p.status IN ('approved', 'sent') THEN
    status := 'freigegeben';
    freigegebene_fassung := 1;
    entschieden_von := coalesce(p.approved_by, 'bestand');
    entschieden_am  := coalesce(p.approved_at, p.sent_at, p.created_at);
  ELSE
    status := 'entwurf';
  END IF;
  RETURN NEXT;
END $$;

-- 3: AFTER UPDATE OF status auf broadcast_proposals - derselbe Weg wie der
-- alte Freigabe-Fluss (approval_channel/Token) den Status heute schon setzt,
-- spiegelt sich jetzt auch nachtraeglich in den Pult-Inhalt. Greift nur,
-- solange der Inhalt noch 'entwurf' ist (eine im Pult getroffene
-- Entscheidung bleibt unangetastet - dieselbe Regel wie 051s Korrektur-UPDATE
-- und die INSERT-Bruecke).
CREATE OR REPLACE FUNCTION marketing._broadcast_proposal_status_zu_inhalt() RETURNS trigger AS $$
DECLARE m record;
BEGIN
  SELECT * INTO m FROM marketing._inhalt_abbildung_aus_proposal(NEW);
  UPDATE marketing.inhalte
     SET status = m.status,
         freigegebene_fassung = m.freigegebene_fassung,
         entschieden_von = m.entschieden_von,
         entschieden_am  = m.entschieden_am,
         grund = m.grund
   WHERE herkunft_proposal = NEW.id
     AND status = 'entwurf';
  RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_broadcast_proposal_status_zu_inhalt ON marketing.broadcast_proposals;
CREATE TRIGGER trg_broadcast_proposal_status_zu_inhalt
  AFTER UPDATE OF status ON marketing.broadcast_proposals
  FOR EACH ROW WHEN (NEW.status IS DISTINCT FROM OLD.status)
  EXECUTE FUNCTION marketing._broadcast_proposal_status_zu_inhalt();

COMMIT;
