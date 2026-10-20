-- RUNBOOK: nach 077 (Seitenformat), 079 und 081. Idempotent, eine Transaktion. Nach jedem Replay verify_082 laufen
-- lassen. Ein Replay von 054/055/060/068 setzt die Block-CHECKs der Vorlagen-Tabellen auf die unbedingte Form
-- (pult_bloecke_fehler(bloecke) IS NULL) zurueck und scheitert an vorhandenen Seiten-Vorlagen (bloecke NULL)
-- fail-closed; danach IMMER 082 erneut einspielen. Ein Replay von 058 ersetzt pult_inhalt_aus_vorlage (5 Argumente)
-- ohne die Format-Wache - auch dann 082 erneut einspielen.
-- 082: Collage-Vorlagen und Vorlagen-Freigabe (sales-claw Plan 2026-10-20-freie-flaeche-stufe5-pruefung-vorlagen,
--   Task 2).
--   1) newsletter_vorlagen und newsletter_vorlagen_fassungen: Spalten format text NOT NULL DEFAULT 'bloecke'
--      ('bloecke'|'seite'), seite jsonb, rollen jsonb. bloecke wird NULL-faehig. Format-Regel wie 077:
--      format='bloecke' <=> bloecke IS NOT NULL, format='seite' <=> seite IS NOT NULL; rollen nur bei Seiten (ein
--      JSON-Objekt). Die Block-CHECKs (Stand 068) gelten nur noch bei format='bloecke', neu je Tabelle ein
--      Seiten-CHECK (format <> 'seite' OR pult_seite_fehler(seite) IS NULL). Bestand: alle Zeilen sind 'bloecke'
--      (Vorgabe der neuen Spalte), die neu angelegten CHECKs pruefen ihn. newsletter_vorlagen_fassungen behaelt den
--      Trigger trg_vorlage_fassung_unveraenderlich mit _fassung_unveraenderlich (050; 075 hat nur inhalt_fassungen
--      umgestellt).
--   2) marketing.pult_vorlage_seite_speichern(name, beschreibung, seite, rollen, von, status, fuer_alle) RETURNS int:
--      Upsert und neue Fassung wie pult_vorlage_speichern (058). Eine bestehende Block-Vorlage gleichen Namens wird
--      nicht umgewandelt. fuer_alle NULL laesst den gespeicherten Wert stehen (neu: false).
--   3) marketing.pult_vorlage_entscheiden(name, urteil 'freigeben'|'zurueckziehen', von) RETURNS text (der neue
--      Status): nur von 'vorschlag' oder 'freigegeben' aus; 'freigegeben' -> 'freigeben' ist ein Fehler. Gilt fuer
--      Block- und Seiten-Vorlagen. Legt keine Fassung an (der Status steht nur in newsletter_vorlagen). p_von wird
--      angenommen, aber nicht gespeichert (wie pult_collage_anlegen, 077: es gibt keine Spalte dafuer).
--   4) marketing.pult_collage_aus_vorlage(vorlage, titel, mandant, seite, von) RETURNS uuid: nur freigegebene
--      Seiten-Vorlagen, eigene oder fuer_alle (Sperre FOR SHARE, damit ein gleichzeitiges Zurueckziehen wartet).
--      Die API fuellt die Seite mit der Marke (claw/seite_vorlagen.fuellen); angelegt wird ueber
--      pult_collage_anlegen (077) mit dieser Seite, der Titel wird Betreff.
--   5) marketing.pult_hat_collage_vorlage(mandant) RETURNS boolean: gibt es eine freigegebene Seiten-Vorlage der
--      Firma oder fuer alle?
--   6) pult_inhalt_aus_vorlage (5 Argumente, 058) bekommt die Wache format = 'bloecke' - sonst legte die Block-Kette
--      einen Block-Newsletter unter dem Namen einer Seiten-Vorlage an. Rumpf sonst woertlich aus 058. Die
--      3-Argument-Fassung (056) liest bloecke und scheitert bei Seiten-Vorlagen schon an NULL.
-- pult_vorlage_speichern (058) bleibt unveraendert: unter dem Namen einer Seiten-Vorlage scheitert es fail-closed an
-- newsletter_vorlagen_format_check.
-- Rechte: wie 077/081 - EXECUTE fuer die vier neuen Funktionen nicht fuer PUBLIC. Aufrufer ist die API-Rolle
-- (SUPABASE_DB_USER, Vorgabe supabase_admin = Eigentuemerin). pult_inhalt_aus_vorlage behaelt seine Rechte
-- (CREATE OR REPLACE aendert sie nicht).
-- Nachweis: verify_082.sql.
BEGIN;

-- 1) Spalten, Format-Regel, CHECKs
ALTER TABLE marketing.newsletter_vorlagen ADD COLUMN IF NOT EXISTS format text NOT NULL DEFAULT 'bloecke';
ALTER TABLE marketing.newsletter_vorlagen ADD COLUMN IF NOT EXISTS seite jsonb;
ALTER TABLE marketing.newsletter_vorlagen ADD COLUMN IF NOT EXISTS rollen jsonb;
ALTER TABLE marketing.newsletter_vorlagen ALTER COLUMN bloecke DROP NOT NULL;
ALTER TABLE marketing.newsletter_vorlagen_fassungen ADD COLUMN IF NOT EXISTS format text NOT NULL DEFAULT 'bloecke';
ALTER TABLE marketing.newsletter_vorlagen_fassungen ADD COLUMN IF NOT EXISTS seite jsonb;
ALTER TABLE marketing.newsletter_vorlagen_fassungen ADD COLUMN IF NOT EXISTS rollen jsonb;
ALTER TABLE marketing.newsletter_vorlagen_fassungen ALTER COLUMN bloecke DROP NOT NULL;

ALTER TABLE marketing.newsletter_vorlagen DROP CONSTRAINT IF EXISTS newsletter_vorlagen_format_check;
ALTER TABLE marketing.newsletter_vorlagen ADD CONSTRAINT newsletter_vorlagen_format_check
  CHECK (format IN ('bloecke','seite') AND ((format = 'bloecke') = (bloecke IS NOT NULL))
         AND ((format = 'seite') = (seite IS NOT NULL))
         AND (rollen IS NULL OR (format = 'seite' AND jsonb_typeof(rollen) = 'object')));
ALTER TABLE marketing.newsletter_vorlagen_fassungen DROP CONSTRAINT IF EXISTS newsletter_vorlagen_fassungen_format_check;
ALTER TABLE marketing.newsletter_vorlagen_fassungen ADD CONSTRAINT newsletter_vorlagen_fassungen_format_check
  CHECK (format IN ('bloecke','seite') AND ((format = 'bloecke') = (bloecke IS NOT NULL))
         AND ((format = 'seite') = (seite IS NOT NULL))
         AND (rollen IS NULL OR (format = 'seite' AND jsonb_typeof(rollen) = 'object')));

ALTER TABLE marketing.newsletter_vorlagen DROP CONSTRAINT IF EXISTS newsletter_vorlagen_bloecke_gueltig;
ALTER TABLE marketing.newsletter_vorlagen ADD CONSTRAINT newsletter_vorlagen_bloecke_gueltig
  CHECK (format <> 'bloecke' OR marketing.pult_bloecke_fehler(bloecke) IS NULL);
ALTER TABLE marketing.newsletter_vorlagen_fassungen DROP CONSTRAINT IF EXISTS newsletter_vorlagen_fassungen_bloecke_gueltig;
ALTER TABLE marketing.newsletter_vorlagen_fassungen ADD CONSTRAINT newsletter_vorlagen_fassungen_bloecke_gueltig
  CHECK (format <> 'bloecke' OR marketing.pult_bloecke_fehler(bloecke) IS NULL);

ALTER TABLE marketing.newsletter_vorlagen DROP CONSTRAINT IF EXISTS newsletter_vorlagen_seite_gueltig;
ALTER TABLE marketing.newsletter_vorlagen ADD CONSTRAINT newsletter_vorlagen_seite_gueltig
  CHECK (format <> 'seite' OR marketing.pult_seite_fehler(seite) IS NULL);
ALTER TABLE marketing.newsletter_vorlagen_fassungen DROP CONSTRAINT IF EXISTS newsletter_vorlagen_fassungen_seite_gueltig;
ALTER TABLE marketing.newsletter_vorlagen_fassungen ADD CONSTRAINT newsletter_vorlagen_fassungen_seite_gueltig
  CHECK (format <> 'seite' OR marketing.pult_seite_fehler(seite) IS NULL);

-- 2) Seiten-Vorlage speichern (Muster pult_vorlage_speichern, 058)
CREATE OR REPLACE FUNCTION marketing.pult_vorlage_seite_speichern(
    p_name text, p_beschreibung text, p_seite jsonb, p_rollen jsonb, p_von text, p_status text,
    p_fuer_alle boolean) RETURNS int
LANGUAGE plpgsql AS $$
DECLARE v_f text; v_n int; v_format text; v_rollen jsonb;
BEGIN
  v_f := marketing.pult_seite_fehler(p_seite);
  IF v_f IS NOT NULL THEN RAISE EXCEPTION 'Vorlage ungültig: %', v_f; END IF;
  v_rollen := coalesce(p_rollen, '{}'::jsonb);
  IF jsonb_typeof(v_rollen) <> 'object' THEN RAISE EXCEPTION 'Rollen müssen ein JSON-Objekt sein'; END IF;
  IF p_status IS NULL OR p_status NOT IN ('vorschlag','freigegeben','zurueckgezogen') THEN
    RAISE EXCEPTION 'Status muss vorschlag, freigegeben oder zurueckgezogen sein'; END IF;
  IF length(btrim(coalesce(p_von, ''))) = 0 THEN RAISE EXCEPTION 'Ohne Urheber keine Vorlage'; END IF;
  INSERT INTO marketing.newsletter_vorlagen (name, beschreibung, bloecke, format, seite, rollen, status, fassung,
                                             erstellt_von, fuer_alle)
  VALUES (p_name, coalesce(p_beschreibung, ''), NULL, 'seite', p_seite, v_rollen, p_status, 0, p_von,
          coalesce(p_fuer_alle, false))
  ON CONFLICT (name) DO NOTHING;
  SELECT format INTO v_format FROM marketing.newsletter_vorlagen WHERE name = p_name FOR UPDATE;
  IF v_format <> 'seite' THEN
    RAISE EXCEPTION 'Vorlage % ist eine Block-Vorlage – bitte einen anderen Namen wählen', p_name; END IF;
  SELECT coalesce(max(fassung), 0) + 1 INTO v_n FROM marketing.newsletter_vorlagen_fassungen WHERE vorlage = p_name;
  INSERT INTO marketing.newsletter_vorlagen_fassungen (vorlage, fassung, bloecke, format, seite, rollen, erstellt_von)
  VALUES (p_name, v_n, NULL, 'seite', p_seite, v_rollen, p_von);
  UPDATE marketing.newsletter_vorlagen
     SET seite = p_seite, rollen = v_rollen, beschreibung = coalesce(p_beschreibung, beschreibung),
         status = p_status, fassung = v_n, fuer_alle = coalesce(p_fuer_alle, fuer_alle)
   WHERE name = p_name;
  RETURN v_n;
END $$;

-- 3) Freigabe durch den Betreiber
CREATE OR REPLACE FUNCTION marketing.pult_vorlage_entscheiden(p_name text, p_urteil text, p_von text) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE v_status text; v_neu text;
BEGIN
  IF p_urteil IS NULL OR p_urteil NOT IN ('freigeben','zurueckziehen') THEN
    RAISE EXCEPTION 'Urteil muss freigeben oder zurueckziehen sein'; END IF;
  IF length(btrim(coalesce(p_von, ''))) = 0 THEN RAISE EXCEPTION 'Ohne Namen keine Entscheidung'; END IF;
  SELECT status INTO v_status FROM marketing.newsletter_vorlagen WHERE name = p_name FOR UPDATE;
  IF v_status IS NULL THEN RAISE EXCEPTION 'Vorlage % gibt es nicht', p_name; END IF;
  IF v_status = 'zurueckgezogen' THEN
    RAISE EXCEPTION 'Vorlage % ist zurückgezogen – entscheiden geht nur bei Vorschlägen und freigegebenen Vorlagen',
      p_name; END IF;
  IF v_status = 'freigegeben' AND p_urteil = 'freigeben' THEN
    RAISE EXCEPTION 'Vorlage % ist schon freigegeben', p_name; END IF;
  v_neu := CASE p_urteil WHEN 'freigeben' THEN 'freigegeben' ELSE 'zurueckgezogen' END;
  UPDATE marketing.newsletter_vorlagen SET status = v_neu WHERE name = p_name;
  RETURN v_neu;
END $$;

-- 4) Neu aus Seiten-Vorlage (Muster pult_inhalt_aus_vorlage 058, angelegt wie pult_collage_anlegen 077)
CREATE OR REPLACE FUNCTION marketing.pult_collage_aus_vorlage(
    p_vorlage text, p_titel text, p_mandant text, p_seite jsonb, p_von text) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v_format text;
BEGIN
  IF length(btrim(coalesce(p_titel, ''))) = 0 THEN RAISE EXCEPTION 'Ohne Titel kein Newsletter'; END IF;
  SELECT format INTO v_format FROM marketing.newsletter_vorlagen
   WHERE name = p_vorlage AND status = 'freigegeben' AND (mandant = p_mandant OR fuer_alle)
   FOR SHARE;
  IF v_format IS NULL THEN RAISE EXCEPTION 'Vorlage % gibt es nicht oder sie ist nicht freigegeben', p_vorlage; END IF;
  IF v_format <> 'seite' THEN RAISE EXCEPTION 'Vorlage % ist keine Collage-Vorlage', p_vorlage; END IF;
  RETURN marketing.pult_collage_anlegen(p_mandant, p_titel, NULL, p_seite, p_von);
END $$;

-- 5) Gibt es fuer die Firma eine Collage-Vorlage?
CREATE OR REPLACE FUNCTION marketing.pult_hat_collage_vorlage(p_mandant text) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT EXISTS (SELECT 1 FROM marketing.newsletter_vorlagen
                  WHERE format = 'seite' AND status = 'freigegeben' AND (mandant = p_mandant OR fuer_alle));
$$;

-- 6) Block-Kette nur fuer Block-Vorlagen (Rumpf aus 058, neu nur "AND format = 'bloecke'")
CREATE OR REPLACE FUNCTION marketing.pult_inhalt_aus_vorlage(
    p_vorlage text, p_titel text, p_mandant text, p_bloecke jsonb, p_layout text) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v_id uuid; v_f text; v_layout text;
BEGIN
  IF length(btrim(coalesce(p_titel, ''))) = 0 THEN RAISE EXCEPTION 'Ohne Titel kein Newsletter'; END IF;
  PERFORM 1 FROM marketing.newsletter_vorlagen
   WHERE name = p_vorlage AND status = 'freigegeben' AND (mandant = p_mandant OR fuer_alle) AND format = 'bloecke';
  IF NOT FOUND THEN RAISE EXCEPTION 'Vorlage % gibt es nicht oder sie ist nicht freigegeben', p_vorlage; END IF;
  v_f := marketing.pult_bloecke_fehler(p_bloecke);
  IF v_f IS NOT NULL THEN RAISE EXCEPTION 'Dokument ungueltig: %', v_f; END IF;
  v_layout := coalesce((SELECT name FROM marketing.layout_vorlagen WHERE name = p_layout), 'dunkel');
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES (p_mandant, 'newsletter', btrim(p_titel))
  RETURNING id INTO v_id;
  INSERT INTO marketing.inhalt_fassungen (inhalt, fassung, felder, layout, layout_fassung, urheber, format, bloecke)
  VALUES (v_id, 1, jsonb_build_object('betreff', btrim(p_titel), 'vorschautext', ''),
          v_layout, (SELECT fassung FROM marketing.layout_vorlagen WHERE name = v_layout),
          'betreiber', 'bloecke', p_bloecke);
  IF EXISTS (SELECT 1 FROM jsonb_each(p_bloecke) e WHERE marketing._bild_ist_platz(e.value)
               AND marketing._bild_platz_leer(e.value#>>'{data,props,url}')) THEN
    PERFORM marketing.pult_bild_auftrag(v_id, NULL, true, '', 'system');
  END IF;
  RETURN v_id;
END $$;

REVOKE ALL ON FUNCTION marketing.pult_vorlage_seite_speichern(text, text, jsonb, jsonb, text, text, boolean) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.pult_vorlage_entscheiden(text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.pult_collage_aus_vorlage(text, text, text, jsonb, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.pult_hat_collage_vorlage(text) FROM PUBLIC;

COMMIT;
