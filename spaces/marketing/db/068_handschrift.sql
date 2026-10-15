-- RUNBOOK: nach 060. Nach jedem Migrations-Replay verify_068 (und verify_060) laufen lassen.
-- 068: Handschrift als Schriftrolle "HAND" in Textbloecken (Newsletter-Paket 3, 15.10.2026).
-- style.fontFamily kennt bisher die 9 Altschluessel + ANZEIGE/TEXT (c_schriften in 058). "HAND" ist eine
-- Rolle wie TEXT, nur mit fester Schrift (caveat, claw/schriften.py HAND_ROLLE); fuer die Basispruefung
-- wird sie deshalb auf TEXT abgebildet. Das Vorlagen-Schriftpaar (root.data.schriften) bleibt ohne caveat.
-- Muster wie 060: die bisherige Huelle wird umbenannt (_060) und von einer gleichnamigen Huelle gerufen;
-- die CHECK-Regeln binden per OID und werden auf die neue Huelle neu angelegt. Idempotent, eine Transaktion.
-- jsonb::text ist kanonisch ("schluessel": "wert"); Anfuehrungszeichen in Werten sind dort escaped, ein
-- Text, der wie das Muster aussieht, kann also nicht getroffen werden.
BEGIN;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                 WHERE n.nspname = 'marketing' AND p.proname = '_pult_bloecke_fehler_060') THEN
    ALTER FUNCTION marketing.pult_bloecke_fehler(jsonb) RENAME TO _pult_bloecke_fehler_060;
  END IF;
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_bloecke_fehler(p jsonb) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT marketing._pult_bloecke_fehler_060(
           CASE WHEN p IS NULL THEN NULL
                ELSE replace(p::text, '"fontFamily": "HAND"', '"fontFamily": "TEXT"')::jsonb END);
$$;

ALTER TABLE marketing.inhalt_fassungen DROP CONSTRAINT IF EXISTS inhalt_fassungen_bloecke_gueltig;
ALTER TABLE marketing.inhalt_fassungen ADD CONSTRAINT inhalt_fassungen_bloecke_gueltig
  CHECK (format <> 'bloecke' OR marketing.pult_bloecke_fehler(bloecke) IS NULL);
ALTER TABLE marketing.newsletter_vorlagen DROP CONSTRAINT IF EXISTS newsletter_vorlagen_bloecke_gueltig;
ALTER TABLE marketing.newsletter_vorlagen ADD CONSTRAINT newsletter_vorlagen_bloecke_gueltig
  CHECK (marketing.pult_bloecke_fehler(bloecke) IS NULL);
ALTER TABLE marketing.newsletter_vorlagen_fassungen DROP CONSTRAINT IF EXISTS newsletter_vorlagen_fassungen_bloecke_gueltig;
ALTER TABLE marketing.newsletter_vorlagen_fassungen ADD CONSTRAINT newsletter_vorlagen_fassungen_bloecke_gueltig
  CHECK (marketing.pult_bloecke_fehler(bloecke) IS NULL);

COMMIT;
