-- RUNBOOK: nach 077 (Seitenformat) und 080. Idempotent, eine Transaktion. Nach jedem Replay verify_081 laufen
-- lassen. Ein Replay von 063 beruehrt pult_einreichen, nicht die Funktionen hier; 075 bleibt gueltig (die neue
-- Tabelle haengt mit ON DELETE CASCADE an inhalt_fassungen, pult_inhalt_loeschen raeumt sie also mit ab).
-- 081: Mail-Fassung der Collage (sales-claw Plan 2026-10-20-freie-flaeche-stufe4-mail-versand, Task 4).
--   1) marketing.seite_mailfassungen(inhalt, fassung, mjml_versand, html_vorschau_mjml, text, medien, bytes,
--      erstellt_am), PRIMARY KEY (inhalt, fassung), Fremdschluessel auf inhalt_fassungen (ON DELETE CASCADE).
--      Genau eine Mail-Fassung je (Inhalt, Fassung). Gespeichert wird MJML, kein fertiges HTML: mjml_versand
--      (Bilder als cid:) und html_vorschau_mjml (Bilder unter einer Marken-Adresse statt der Bildbasis - die
--      signierte Bildbasis der sales-ui laeuft ab und wird nie gespeichert) tragen beide statt des Pflicht-Fusses
--      eine Marke; die API setzt je Anfrage den AKTUELLEN Pflichtteil der Firma (und die Bildbasis) ein und
--      rendert mit mrml (Fix-Runde 1: der Fuss ist immer aktuell). text = Textfassung zum Zeitpunkt der
--      Erzeugung (nur zur Nachvollziehbarkeit; die API baut sie je Anfrage mit dem aktuellen Pflichtteil neu).
--      medien = Liste [{name, cid, sw}] der Bildstuecke ms-<inhalt 8 hex>-<fassung>-<nr>.jpg, bytes = Summe der
--      Stuecke (hoechstens 8 MB).
--      (Brief: Spalte html_versand; umbenannt in mjml_versand, weil dort seit Fix-Runde 1 MJML steht.)
--   2) marketing.pult_seite_mail_speichern(...) RETURNS jsonb: nur fuer eine Fassung im Format 'seite',
--      INSERT ... ON CONFLICT DO NOTHING, liefert danach die gespeicherte Zeile (bei einem gleichzeitigen
--      Erzeugen die des Gewinners - die API raeumt dann ihre eigenen Dateien ab).
--   3) marketing.pult_seite_mail_lesen(inhalt, fassung) RETURNS jsonb (NULL ohne Mail-Fassung).
--   4) marketing.pult_seite_einreichen(inhalt, fassung, von) RETURNS int: sperrt den Inhalt (FOR UPDATE, wie
--      pult_seite_speichern), prueft, dass die neueste Fassung genau p_fassung im Format 'seite' ist und ihre
--      Mail-Fassung vorliegt, und ruft dann pult_einreichen (063) unveraendert. Ohne diese Klammer koennte
--      zwischen dem Erzeugen der Mail und dem Einreichen eine neuere Fassung gespeichert werden, die dann ohne
--      Mail-Fassung zur Freigabe laege (pult_einreichen nimmt immer die neueste).
--   5) marketing.pult_seite_mail_aufraeumen(p_alter interval DEFAULT '7 days') RETURNS jsonb: loescht die
--      Mail-Fassungen, deren Fassung weder die neueste noch die eingereichte noch die freigegebene ihres Inhalts
--      ist und die aelter als p_alter sind; liefert die Namen ihrer Bildstuecke (jsonb-Array). Die API loescht
--      die Dateien erst danach (stuendliches Aufraeumen neben den gs-Entwurfsbildern).
-- Ersetzte Funktionen: keine. Block-Newsletter bleiben unveraendert (pult_einreichen selbst unberuehrt).
-- Rechte: wie 077 - EXECUTE der neuen Funktionen nicht fuer PUBLIC; Aufrufer ist die API-Rolle (SUPABASE_DB_USER,
-- Vorgabe supabase_admin = Eigentuemerin). Tabelle ohne eigene GRANTs (Default Privileges aus 003 gelten).
-- Nachweis: verify_081.sql.
BEGIN;

-- 1) Tabelle
CREATE TABLE IF NOT EXISTS marketing.seite_mailfassungen (
    inhalt             uuid NOT NULL,
    fassung            int  NOT NULL,
    mjml_versand       text NOT NULL,
    html_vorschau_mjml text NOT NULL,
    text               text NOT NULL,
    medien             jsonb NOT NULL CHECK (jsonb_typeof(medien) = 'array'),
    bytes              int  NOT NULL CHECK (bytes BETWEEN 0 AND 8388608),
    erstellt_am        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (inhalt, fassung),
    CONSTRAINT seite_mailfassungen_fassung_fk FOREIGN KEY (inhalt, fassung)
      REFERENCES marketing.inhalt_fassungen (inhalt, fassung) ON DELETE CASCADE
);

-- 3) Lesen (vor 2, speichern liefert ueber lesen)
CREATE OR REPLACE FUNCTION marketing.pult_seite_mail_lesen(p_inhalt uuid, p_fassung int) RETURNS jsonb
LANGUAGE sql STABLE AS $$
  SELECT jsonb_build_object('inhalt', m.inhalt, 'fassung', m.fassung, 'mjml_versand', m.mjml_versand,
                            'html_vorschau_mjml', m.html_vorschau_mjml, 'text', m.text, 'medien', m.medien,
                            'bytes', m.bytes, 'erstellt_am', m.erstellt_am)
    FROM marketing.seite_mailfassungen m WHERE m.inhalt = p_inhalt AND m.fassung = p_fassung
$$;

-- 2) Speichern (idempotent)
CREATE OR REPLACE FUNCTION marketing.pult_seite_mail_speichern(
    p_inhalt uuid, p_fassung int, p_mjml_versand text, p_html_vorschau_mjml text, p_text text,
    p_medien jsonb, p_bytes int) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE v_format text; e jsonb;
BEGIN
  SELECT format INTO v_format FROM marketing.inhalt_fassungen WHERE inhalt = p_inhalt AND fassung = p_fassung;
  IF v_format IS NULL THEN RAISE EXCEPTION 'Unbekannte Fassung'; END IF;
  IF v_format <> 'seite' THEN RAISE EXCEPTION 'Das ist kein Collage-Newsletter'; END IF;
  IF length(coalesce(p_mjml_versand, '')) = 0 OR length(coalesce(p_html_vorschau_mjml, '')) = 0 THEN
    RAISE EXCEPTION 'Die Mail-Fassung ist leer'; END IF;
  IF p_bytes IS NULL OR p_bytes < 0 OR p_bytes > 8388608 THEN
    RAISE EXCEPTION 'Die Mail wäre zu groß (höchstens 8 MB) – weniger oder kleinere Bildbereiche'; END IF;
  IF p_medien IS NULL OR jsonb_typeof(p_medien) <> 'array' THEN RAISE EXCEPTION 'medien: Liste erwartet'; END IF;
  FOR e IN SELECT value FROM jsonb_array_elements(p_medien) LOOP
    IF jsonb_typeof(e) <> 'object' OR jsonb_typeof(e->'name') <> 'string'
       OR (e->>'name') !~ '^ms-[0-9a-f]{8}-[0-9]{1,6}-[0-9]{2,3}(-[0-9]{1,3})?\.jpg$' THEN
      RAISE EXCEPTION 'medien: nur Bildstücke ms-…jpg'; END IF;
  END LOOP;
  INSERT INTO marketing.seite_mailfassungen (inhalt, fassung, mjml_versand, html_vorschau_mjml, text, medien, bytes)
  VALUES (p_inhalt, p_fassung, p_mjml_versand, p_html_vorschau_mjml, coalesce(p_text, ''), p_medien, p_bytes)
  ON CONFLICT (inhalt, fassung) DO NOTHING;
  RETURN marketing.pult_seite_mail_lesen(p_inhalt, p_fassung);
END $$;

-- 4) Einreichen einer Collage: dieselbe Sperrreihenfolge wie 061 (erst der Inhalt), dann pult_einreichen
CREATE OR REPLACE FUNCTION marketing.pult_seite_einreichen(p_inhalt uuid, p_fassung int, p_von text) RETURNS int
LANGUAGE plpgsql AS $$
DECLARE v_neueste int; v_format text;
BEGIN
  PERFORM 1 FROM marketing.inhalte WHERE id = p_inhalt FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannter Inhalt'; END IF;
  SELECT fassung, format INTO v_neueste, v_format FROM marketing.inhalt_fassungen
   WHERE inhalt = p_inhalt ORDER BY fassung DESC LIMIT 1;
  IF v_neueste IS NULL THEN RAISE EXCEPTION 'Ohne Fassung gibt es nichts einzureichen'; END IF;
  IF v_format <> 'seite' THEN RAISE EXCEPTION 'Das ist kein Collage-Newsletter'; END IF;
  IF v_neueste IS DISTINCT FROM p_fassung THEN
    RAISE EXCEPTION 'Inzwischen gibt es Fassung % – bitte neu laden', v_neueste; END IF;
  IF NOT EXISTS (SELECT 1 FROM marketing.seite_mailfassungen WHERE inhalt = p_inhalt AND fassung = p_fassung) THEN
    RAISE EXCEPTION 'Die Mail-Fassung fehlt – bitte erneut einreichen'; END IF;
  RETURN marketing.pult_einreichen(p_inhalt, p_von);
END $$;

-- 5) Aufraeumen alter Mail-Fassungen (nie die neueste, eingereichte oder freigegebene Fassung)
CREATE OR REPLACE FUNCTION marketing.pult_seite_mail_aufraeumen(p_alter interval DEFAULT interval '7 days')
RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE v_namen jsonb;
BEGIN
  WITH weg AS (
    DELETE FROM marketing.seite_mailfassungen m
     USING marketing.inhalte i
     WHERE i.id = m.inhalt
       AND m.erstellt_am < now() - p_alter
       AND m.fassung IS DISTINCT FROM i.eingereichte_fassung
       AND m.fassung IS DISTINCT FROM i.freigegebene_fassung
       AND m.fassung < (SELECT max(f.fassung) FROM marketing.inhalt_fassungen f WHERE f.inhalt = m.inhalt)
    RETURNING m.medien)
  SELECT coalesce(jsonb_agg(e->>'name') FILTER (WHERE e->>'name' IS NOT NULL), '[]'::jsonb) INTO v_namen
    FROM weg LEFT JOIN LATERAL jsonb_array_elements(weg.medien) e ON true;
  RETURN v_namen;
END $$;

REVOKE ALL ON FUNCTION marketing.pult_seite_mail_lesen(uuid, int) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.pult_seite_mail_speichern(uuid, int, text, text, text, jsonb, int) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.pult_seite_einreichen(uuid, int, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.pult_seite_mail_aufraeumen(interval) FROM PUBLIC;

COMMIT;
