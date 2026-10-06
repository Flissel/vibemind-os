-- 062: Bildzuordnung je Mandant (sales-claw Spec 2026-10-06 mandanten-markenwissen-design.md).
-- Idempotent, eine Transaktion.
--   1) marketing.medien_mandant: Dateiname -> Mandant. Keine Zeile = Gemeinsam;
--      Zeile mit mandant NULL = ausdruecklich Gemeinsam. Dateiname ohne Pfadanteile.
--   2) fin2gether wird aktiv. Das oeffnet keinen Versandweg: Freigeben legt nur ab,
--      ausserhalb geht nur versand_beauftragen -> Entwurf, den ein Mensch freigibt.
-- Danach verify_062 ueber migration_probe laufen lassen.
BEGIN;

CREATE TABLE IF NOT EXISTS marketing.medien_mandant (
  dateiname    text PRIMARY KEY
               CHECK (dateiname ~ '^[^/\\"[:cntrl:]]{1,200}$' AND dateiname NOT LIKE '%..%'),
  mandant      text REFERENCES marketing.mandanten(id),
  geaendert_am timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS medien_mandant_mandant_idx ON marketing.medien_mandant (mandant);

UPDATE marketing.mandanten SET aktiv = true WHERE id = 'fin2gether' AND NOT aktiv;

COMMIT;
