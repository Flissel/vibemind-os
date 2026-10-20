-- RUNBOOK: nach 076. Idempotent, eine Transaktion. Nach jedem Replay verify_077 laufen lassen.
-- Ein Replay von 053 setzt inhalt_fassungen_format_check auf ('felder','bloecke') zurueck und scheitert an
-- vorhandenen Seiten-Fassungen fail-closed; danach IMMER 077 erneut einspielen.
-- 077: Seitenformat fuer Newsletter, die freie Flaeche "Collage" (Plan 2026-10-20-freie-flaeche-stufe1-seite-pdf,
--   Task 2). Die Datenbank ist die zweite Wache; die ausfuehrliche Pruefung macht claw/seite.py (pruefen).
--   1) Spalte inhalt_fassungen.seite jsonb; format_check kennt 'seite' (format='seite' <=> seite IS NOT NULL)
--   2) marketing.pult_seite_fehler(p) RETURNS text (NULL = gueltig), IMMUTABLE: dieselben Grenzen wie seite.py
--      (Breite 600, Hoehe 400..6000 ganz, hoechstens 80 Ebenen, hoechstens 256 KB, Arten bild/text/form/knopf,
--      erlaubte Felder je Art, Bereiche, Aufzaehlungen, #rrggbb, medien:<datei>, https://, Schriftregister
--      marke_schriften() aus 064). Die Regel "Anrede/Links nur in Fliesstext" prueft nur seite.py.
--      CHECK inhalt_fassungen_seite_gueltig haelt jede Seiten-Fassung daran.
--   3) marketing.pult_seite_speichern(...) RETURNS int: Sperren aus pult_bloecke_speichern (067, woertlich:
--      eingereicht; Assistent arbeitet nur fuer betreiber, mit der bereit_am/_chat_pc_lebt-Ausnahme), danach die
--      Regeln aus _pult_bloecke_speichern_053 (Betreff, nur Newsletter, nur Entwurf, Basis, Layout dunkel).
--      Schreibt format='seite', seite, bloecke NULL.
--   4) marketing.pult_collage_anlegen(p_mandant, p_titel, p_betreff, p_seite, p_von) RETURNS uuid: legt den
--      Inhalt an wie pult_inhalt_aus_vorlage (058) und speichert Fassung 1 ueber pult_seite_speichern.
--      Leerer Betreff -> der Titel wird Betreff (wie 058).
--      p_von wird angenommen, aber nicht gespeichert: auch die Anlage aus Vorlagen haelt keinen Urheber fest
--      (marketing.inhalte hat keine Spalte dafuer, die Fassung traegt urheber = 'betreiber').
-- Sperrreihenfolge wie 061: erst marketing.inhalte, dann Auftraege (die Sperre liest chat_auftraege nur).
-- Ersetzte Funktionen: keine. Neu: die oben genannten plus Helfer _seite_zahl_ok, _seite_text_ok, _seite_wert_ok.
-- Block-Newsletter bleiben unveraendert (pult_bloecke_speichern, inhalt_fassungen_bloecke_gueltig unberuehrt).
-- Rechte: wie 071/074/075 - EXECUTE fuer pult_seite_speichern und pult_collage_anlegen nicht fuer PUBLIC.
-- Aufrufer ist die API-Rolle (SUPABASE_DB_USER, Vorgabe supabase_admin = Eigentuemerin). pult_seite_fehler und
-- die Helfer bleiben wie pult_bloecke_fehler (053) offen: der CHECK ruft sie mit den Rechten des Schreibenden auf.
-- Nachweis: verify_077.sql.
BEGIN;

-- 1) Spalte und Format-Regel
ALTER TABLE marketing.inhalt_fassungen ADD COLUMN IF NOT EXISTS seite jsonb;
ALTER TABLE marketing.inhalt_fassungen DROP CONSTRAINT IF EXISTS inhalt_fassungen_format_check;
ALTER TABLE marketing.inhalt_fassungen ADD CONSTRAINT inhalt_fassungen_format_check
  CHECK (format IN ('felder','bloecke','seite') AND ((format = 'bloecke') = (bloecke IS NOT NULL))
         AND ((format = 'seite') = (seite IS NOT NULL)));

-- 2) Pruefer
CREATE OR REPLACE FUNCTION marketing._seite_zahl_ok(v jsonb, lo numeric, hi numeric, ganz boolean DEFAULT false)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE n numeric;
BEGIN
  IF v IS NULL OR jsonb_typeof(v) <> 'number' THEN RETURN false; END IF;   -- true/false sind keine Zahlen
  n := (v #>> '{}')::numeric;
  RETURN n BETWEEN lo AND hi AND (NOT ganz OR n = trunc(n));
END $$;

CREATE OR REPLACE FUNCTION marketing._seite_text_ok(v jsonb, muster text) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
  IF v IS NULL OR jsonb_typeof(v) <> 'string' THEN RETURN false; END IF;
  RETURN (v #>> '{}') ~ muster;
END $$;

-- Ein Feld einer Ebene: true/false = gueltig/ungueltig, NULL = Feld gibt es fuer diese Art nicht.
-- Gleiche Felder, Bereiche und Muster wie _EBENE/_ART in claw/seite.py.
CREATE OR REPLACE FUNCTION marketing._seite_wert_ok(p_art text, p_name text, v jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
  c_farbe  CONSTANT text := '^#[0-9a-fA-F]{6}$';
  c_quelle CONSTANT text := '^medien:[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.(png|jpe?g|gif|webp)$';
  c_link   CONSTANT text := '^https://[^\s"''<>]+$';
  s text := CASE WHEN jsonb_typeof(v) = 'string' THEN v #>> '{}' END;
BEGIN
  -- alle Arten
  CASE p_name
    WHEN 'id'        THEN RETURN marketing._seite_text_ok(v, '^[A-Za-z0-9_-]{1,32}$');
    WHEN 'art'       THEN RETURN coalesce(s IN ('bild', 'text', 'form', 'knopf'), false);
    WHEN 'x'         THEN RETURN marketing._seite_zahl_ok(v, -600, 1200);
    WHEN 'y'         THEN RETURN marketing._seite_zahl_ok(v, -1000, 7000);
    WHEN 'breite'    THEN RETURN marketing._seite_zahl_ok(v, 1, 1200);
    WHEN 'hoehe'     THEN RETURN marketing._seite_zahl_ok(v, 1, 6000);
    WHEN 'drehung'   THEN RETURN marketing._seite_zahl_ok(v, -180, 180);
    WHEN 'deckkraft' THEN RETURN marketing._seite_zahl_ok(v, 0, 1);
    ELSE NULL;
  END CASE;
  IF p_art = 'bild' THEN
    CASE p_name
      WHEN 'quelle'        THEN RETURN coalesce(s ~ c_quelle AND strpos(s, '..') = 0, false);
      WHEN 'passung'       THEN RETURN coalesce(s IN ('fuellen', 'einpassen'), false);
      WHEN 'rundung'       THEN RETURN marketing._seite_zahl_ok(v, 0, 600);
      WHEN 'kreis'         THEN RETURN jsonb_typeof(v) = 'boolean';
      WHEN 'rahmen_breite' THEN RETURN marketing._seite_zahl_ok(v, 0, 20);
      WHEN 'rahmen_farbe'  THEN RETURN marketing._seite_text_ok(v, c_farbe);
      WHEN 'schatten'      THEN RETURN jsonb_typeof(v) = 'boolean';
      ELSE RETURN NULL;
    END CASE;
  ELSIF p_art = 'form' THEN
    CASE p_name
      WHEN 'form'    THEN RETURN coalesce(s IN ('rechteck', 'kreis'), false);
      WHEN 'farbe'   THEN RETURN marketing._seite_text_ok(v, c_farbe);
      WHEN 'rundung' THEN RETURN marketing._seite_zahl_ok(v, 0, 600);
      ELSE RETURN NULL;
    END CASE;
  ELSIF p_art = 'text' THEN
    CASE p_name
      WHEN 'text'              THEN RETURN coalesce(length(s) BETWEEN 1 AND 3000, false);
      WHEN 'rolle'             THEN RETURN coalesce(s IN ('ANZEIGE', 'TEXT', 'HAND'), false);
      WHEN 'gewicht'           THEN RETURN marketing._seite_zahl_ok(v, 300, 900, true);
      WHEN 'groesse'           THEN RETURN marketing._seite_zahl_ok(v, 10, 160);
      WHEN 'zeilenabstand'     THEN RETURN marketing._seite_zahl_ok(v, 0.8, 2.5);
      WHEN 'buchstabenabstand' THEN RETURN marketing._seite_zahl_ok(v, -0.1, 0.5);
      WHEN 'farbe'             THEN RETURN marketing._seite_text_ok(v, c_farbe);
      WHEN 'ausrichtung'       THEN RETURN coalesce(s IN ('links', 'mitte', 'rechts'), false);
      WHEN 'grossbuchstaben'   THEN RETURN jsonb_typeof(v) = 'boolean';
      WHEN 'kursiv'            THEN RETURN jsonb_typeof(v) = 'boolean';
      WHEN 'hintergrund'       THEN RETURN jsonb_typeof(v) = 'null' OR marketing._seite_text_ok(v, c_farbe);
      ELSE RETURN NULL;
    END CASE;
  ELSIF p_art = 'knopf' THEN
    CASE p_name
      WHEN 'text'      THEN RETURN coalesce(length(s) BETWEEN 1 AND 60, false);
      WHEN 'link'      THEN RETURN marketing._seite_text_ok(v, c_link);
      WHEN 'farbe'     THEN RETURN marketing._seite_text_ok(v, c_farbe);
      WHEN 'textfarbe' THEN RETURN marketing._seite_text_ok(v, c_farbe);
      WHEN 'rundung'   THEN RETURN marketing._seite_zahl_ok(v, 0, 60);
      WHEN 'rolle'     THEN RETURN coalesce(s IN ('ANZEIGE', 'TEXT'), false);
      WHEN 'groesse'   THEN RETURN marketing._seite_zahl_ok(v, 10, 40);
      WHEN 'gewicht'   THEN RETURN marketing._seite_zahl_ok(v, 300, 900, true);
      ELSE RETURN NULL;
    END CASE;
  END IF;
  RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_seite_fehler(p jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
  v_hg jsonb; v_sch jsonb; v_eb jsonb; e jsonb; v_art text; v_k text; v_ok boolean;
  v_nr int := 0; v_ids text[] := ARRAY[]::text[];
  v_pflicht text[];
BEGIN
  IF p IS NULL OR jsonb_typeof(p) <> 'object' THEN RETURN 'Seite: Objekt erwartet'; END IF;
  -- UTF-8-Bytes wie seite.pruefen (len(json.dumps(...).encode()))
  IF octet_length(p::text) > 262144 THEN RETURN 'Seite: größer als 256 KB'; END IF;
  SELECT string_agg(k, ', ' ORDER BY k) INTO v_k FROM jsonb_object_keys(p) k
   WHERE k <> ALL (ARRAY['version', 'breite', 'hoehe', 'hintergrund', 'schriften', 'ebenen']);
  IF v_k IS NOT NULL THEN RETURN format('Seite: unbekannte Felder %s', v_k); END IF;
  IF NOT marketing._seite_zahl_ok(p->'version', 1, 1) THEN RETURN 'Seite version: muss 1 sein'; END IF;
  IF NOT marketing._seite_zahl_ok(p->'breite', 600, 600) THEN RETURN 'Seite breite: muss 600 sein'; END IF;
  IF NOT marketing._seite_zahl_ok(p->'hoehe', 400, 6000, true) THEN
    RETURN 'Seite hoehe: ganze Zahl zwischen 400 und 6000 erwartet'; END IF;
  v_hg := p->'hintergrund'; v_sch := p->'schriften'; v_eb := p->'ebenen';
  IF jsonb_typeof(v_hg) IS DISTINCT FROM 'object' THEN RETURN 'Seite hintergrund: Objekt erwartet'; END IF;
  IF jsonb_typeof(v_sch) IS DISTINCT FROM 'object' THEN RETURN 'Seite schriften: Objekt erwartet'; END IF;
  IF jsonb_typeof(v_eb) IS DISTINCT FROM 'array' THEN RETURN 'Seite ebenen: Liste erwartet'; END IF;

  IF EXISTS (SELECT 1 FROM jsonb_object_keys(v_hg) k WHERE k NOT IN ('farbe', 'bild')) THEN
    RETURN 'Hintergrund: unbekannte Felder'; END IF;
  IF NOT marketing._seite_text_ok(v_hg->'farbe', '^#[0-9a-fA-F]{6}$') THEN
    RETURN 'Hintergrund farbe: Farbe im Format #rrggbb erwartet'; END IF;
  IF v_hg ? 'bild' AND jsonb_typeof(v_hg->'bild') <> 'null'
     AND NOT marketing._seite_wert_ok('bild', 'quelle', v_hg->'bild') THEN
    RETURN 'Hintergrund bild: nur Bilder aus der Mediathek (medien:…) erlaubt'; END IF;

  IF EXISTS (SELECT 1 FROM jsonb_object_keys(v_sch) k WHERE k NOT IN ('anzeige', 'text')) THEN
    RETURN 'Schriften: unbekannte Felder'; END IF;
  IF NOT coalesce(jsonb_typeof(v_sch->'anzeige') = 'string'
                  AND (v_sch->>'anzeige') = ANY (marketing.marke_schriften()), false)
     OR NOT coalesce(jsonb_typeof(v_sch->'text') = 'string'
                     AND (v_sch->>'text') = ANY (marketing.marke_schriften()), false) THEN
    RETURN 'Schriften: Schrift nicht im Register'; END IF;

  IF jsonb_array_length(v_eb) > 80 THEN RETURN 'Seite: höchstens 80 Ebenen'; END IF;
  FOR e IN SELECT value FROM jsonb_array_elements(v_eb) LOOP
    v_nr := v_nr + 1;
    IF jsonb_typeof(e) <> 'object' THEN RETURN format('Ebene %s: Objekt erwartet', v_nr); END IF;
    v_art := CASE WHEN jsonb_typeof(e->'art') = 'string' THEN e->>'art' END;
    IF v_art IS NULL OR v_art NOT IN ('bild', 'text', 'form', 'knopf') THEN
      RETURN format('Ebene %s art: erlaubt sind bild, text, form, knopf', v_nr); END IF;
    FOR v_k IN SELECT jsonb_object_keys(e) LOOP
      v_ok := marketing._seite_wert_ok(v_art, v_k, e->v_k);
      IF v_ok IS NULL THEN RETURN format('Ebene %s: unbekanntes Feld %s', v_nr, v_k); END IF;
      IF NOT v_ok THEN RETURN format('Ebene %s %s: ungültiger Wert', v_nr, v_k); END IF;
    END LOOP;
    v_pflicht := ARRAY['id', 'art', 'x', 'y', 'breite', 'hoehe'] || CASE v_art
      WHEN 'bild'  THEN ARRAY['quelle']
      WHEN 'form'  THEN ARRAY['form', 'farbe']
      WHEN 'text'  THEN ARRAY['text', 'rolle', 'groesse', 'farbe']
      WHEN 'knopf' THEN ARRAY['text', 'link', 'farbe', 'textfarbe'] END;
    IF NOT e ?& v_pflicht THEN
      RETURN format('Ebene %s: Feld %s fehlt', v_nr,
                    (SELECT f FROM unnest(v_pflicht) f WHERE NOT e ? f LIMIT 1)); END IF;
    IF (e->>'id') = ANY (v_ids) THEN RETURN format('Ebene %s: ID %s kommt doppelt vor', v_nr, e->>'id'); END IF;
    v_ids := v_ids || (e->>'id');
  END LOOP;
  RETURN NULL;
END $$;

ALTER TABLE marketing.inhalt_fassungen DROP CONSTRAINT IF EXISTS inhalt_fassungen_seite_gueltig;
ALTER TABLE marketing.inhalt_fassungen ADD CONSTRAINT inhalt_fassungen_seite_gueltig
  CHECK (format <> 'seite' OR marketing.pult_seite_fehler(seite) IS NULL);

-- 3) Speichern: Sperren aus pult_bloecke_speichern (067), Regeln aus _pult_bloecke_speichern_053
CREATE OR REPLACE FUNCTION marketing.pult_seite_speichern(
    p_inhalt uuid, p_basis int, p_betreff text, p_vorschautext text,
    p_seite jsonb, p_urheber text, p_als_kopie boolean) RETURNS int
LANGUAGE plpgsql AS $$
DECLARE v_f text; v_art text; v_status text; v_neueste int;
BEGIN
  SELECT art, status INTO v_art, v_status FROM marketing.inhalte WHERE id = p_inhalt FOR UPDATE;
  IF v_status = 'eingereicht' THEN RAISE EXCEPTION 'Liegt zur Freigabe – erst zurückziehen'; END IF;
  IF p_urheber = 'betreiber' THEN
    IF EXISTS (SELECT 1 FROM marketing.chat_auftraege
                WHERE inhalt = p_inhalt AND status IN ('offen','in_arbeit')
                  AND (vergeben_bis IS NULL OR vergeben_bis > now())
                  AND NOT (status = 'offen' AND coalesce(bereit_am, erstellt_am) < now() - interval '2 minutes'
                           AND NOT marketing._chat_pc_lebt())) THEN
      RAISE EXCEPTION 'Der Assistent arbeitet gerade'; END IF;
  END IF;
  v_f := marketing.pult_seite_fehler(p_seite);
  IF v_f IS NOT NULL THEN RAISE EXCEPTION '%', v_f; END IF;
  IF length(btrim(coalesce(p_betreff, ''))) = 0 THEN RAISE EXCEPTION 'Ohne Betreff gibt es keine Fassung'; END IF;
  IF v_art IS NULL THEN RAISE EXCEPTION 'Unbekannter Inhalt'; END IF;
  IF v_art <> 'newsletter' THEN RAISE EXCEPTION 'Seiten gibt es nur für Newsletter'; END IF;
  IF v_status <> 'entwurf' THEN RAISE EXCEPTION 'Nur Entwuerfe lassen sich bearbeiten'; END IF;
  SELECT coalesce(max(fassung), 0) INTO v_neueste FROM marketing.inhalt_fassungen WHERE inhalt = p_inhalt;
  IF p_basis IS DISTINCT FROM v_neueste AND NOT coalesce(p_als_kopie, false) THEN
    RAISE EXCEPTION 'Inzwischen gibt es Fassung % - neu laden oder als Kopie behalten', v_neueste; END IF;
  INSERT INTO marketing.inhalt_fassungen (inhalt, fassung, felder, layout, layout_fassung, urheber, format,
                                          bloecke, seite)
  VALUES (p_inhalt, v_neueste + 1,
          jsonb_build_object('betreff', btrim(p_betreff), 'vorschautext', coalesce(p_vorschautext, '')),
          'dunkel', (SELECT fassung FROM marketing.layout_vorlagen WHERE name = 'dunkel'),
          p_urheber, 'seite', NULL, p_seite);
  RETURN v_neueste + 1;
END $$;

-- 4) Anlegen (Muster pult_inhalt_aus_vorlage, 058). p_von: s. Kopf.
CREATE OR REPLACE FUNCTION marketing.pult_collage_anlegen(
    p_mandant text, p_titel text, p_betreff text, p_seite jsonb, p_von text) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v_id uuid;
BEGIN
  IF length(btrim(coalesce(p_titel, ''))) = 0 THEN RAISE EXCEPTION 'Ohne Titel kein Newsletter'; END IF;
  IF NOT EXISTS (SELECT 1 FROM marketing.mandanten WHERE id = p_mandant) THEN
    RAISE EXCEPTION 'Unbekannte Firma'; END IF;
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES (p_mandant, 'newsletter', btrim(p_titel))
  RETURNING id INTO v_id;
  -- scheitert das Speichern, faellt mit der Ausnahme auch der Inhalt weg. Ohne Betreff gilt der Titel
  -- (wie pult_inhalt_aus_vorlage, 058).
  PERFORM marketing.pult_seite_speichern(v_id, 0, coalesce(nullif(btrim(p_betreff), ''), btrim(p_titel)), '',
                                         p_seite, 'betreiber', false);
  RETURN v_id;
END $$;

REVOKE ALL ON FUNCTION marketing.pult_seite_speichern(uuid, int, text, text, jsonb, text, boolean) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.pult_collage_anlegen(text, text, text, jsonb, text) FROM PUBLIC;

COMMIT;
