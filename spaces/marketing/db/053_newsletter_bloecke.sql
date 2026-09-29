-- 053_newsletter_bloecke.sql — Blockformat fuer Newsletter (sales-claw Spec
-- 2026-09-29-newsletter-editor-design.md §3.3/§3.5). Nur Ergaenzungen.
-- Farbbedeutung: backdropColor = Layout flaeche, canvasColor = grund, textColor = text.
-- Idempotent: die Uebernahme greift nur, solange die neueste Fassung 'felder' ist.
BEGIN;

ALTER TABLE marketing.inhalt_fassungen ADD COLUMN IF NOT EXISTS format text NOT NULL DEFAULT 'felder';
ALTER TABLE marketing.inhalt_fassungen ADD COLUMN IF NOT EXISTS bloecke jsonb;
ALTER TABLE marketing.inhalt_fassungen DROP CONSTRAINT IF EXISTS inhalt_fassungen_format_check;
ALTER TABLE marketing.inhalt_fassungen ADD CONSTRAINT inhalt_fassungen_format_check
  CHECK (format IN ('felder','bloecke') AND ((format = 'bloecke') = (bloecke IS NOT NULL)));

CREATE OR REPLACE FUNCTION marketing._bloecke_farbe_ok(v jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
  SELECT v IS NULL OR jsonb_typeof(v) = 'null'
      OR (jsonb_typeof(v) = 'string' AND v #>> '{}' ~ '^#[0-9a-fA-F]{6}$') $$;

CREATE OR REPLACE FUNCTION marketing._bloecke_zahl_ok(v jsonb, lo numeric, hi numeric) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
  SELECT v IS NULL OR jsonb_typeof(v) = 'null'
      OR (jsonb_typeof(v) = 'number' AND (v #>> '{}')::numeric BETWEEN lo AND hi) $$;

CREATE OR REPLACE FUNCTION marketing.pult_bloecke_fehler(p jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
  v_id text; v_b jsonb; v_typ text; v_props jsonb; v_style jsonb;
  v_kinder text[]; v_k text; v_n int;
  v_gesehen text[] := ARRAY[]::text[];
  v_front text[]; v_next text[]; v_tiefe int := 0;
  v_farbe text; v_url text;
BEGIN
  IF p IS NULL OR jsonb_typeof(p) <> 'object' THEN RETURN 'Das Dokument muss ein JSON-Objekt sein'; END IF;
  IF length(p::text) > 262144 THEN RETURN 'Das Dokument ist zu gross (hoechstens 256 KB)'; END IF;
  IF (SELECT count(*) FROM jsonb_object_keys(p)) > 151 THEN RETURN 'Hoechstens 150 Bloecke'; END IF;
  IF p->'root'->>'type' IS DISTINCT FROM 'EmailLayout' THEN RETURN 'Die Wurzel muss ein EmailLayout sein'; END IF;

  FOR v_id, v_b IN SELECT key, value FROM jsonb_each(p) LOOP
    IF jsonb_typeof(v_b) <> 'object' THEN RETURN format('Block %s ist kein Objekt', v_id); END IF;
    v_typ := v_b->>'type';
    IF v_id <> 'root' AND v_typ = 'EmailLayout' THEN RETURN 'EmailLayout nur als Wurzel'; END IF;
    IF v_id <> 'root' AND v_id !~ '^[A-Za-z0-9_-]{1,64}$' THEN RETURN format('Ungueltige Block-ID %s', v_id); END IF;
    IF v_typ NOT IN ('EmailLayout','Heading','Text','Button','Image','Divider','Spacer','Container','ColumnsContainer') THEN
      RETURN format('Blocktyp %s ist nicht erlaubt', coalesce(v_typ, '(leer)')); END IF;
    v_props := coalesce(v_b->'data'->'props', v_b->'data', '{}'::jsonb);
    v_style := coalesce(v_b->'data'->'style', '{}'::jsonb);
    -- Farben in style, props und (Wurzel) data
    FOR v_farbe IN SELECT unnest(ARRAY['color','backgroundColor','backdropColor','canvasColor','textColor',
                                       'buttonBackgroundColor','buttonTextColor','lineColor','borderColor']) LOOP
      IF NOT (marketing._bloecke_farbe_ok(v_style->v_farbe) AND marketing._bloecke_farbe_ok(v_props->v_farbe)
              AND marketing._bloecke_farbe_ok(v_b->'data'->v_farbe)) THEN
        RETURN format('Farbe %s in %s muss #rrggbb sein', v_farbe, v_id); END IF;
    END LOOP;
    IF NOT (marketing._bloecke_zahl_ok(v_style->'fontSize', 8, 72)
        AND marketing._bloecke_zahl_ok(v_style->'borderRadius', 0, 32)
        AND marketing._bloecke_zahl_ok(v_props->'width', 1, 600)
        AND marketing._bloecke_zahl_ok(v_props->'height', 0, 600)
        AND marketing._bloecke_zahl_ok(v_props->'lineHeight', 1, 10)
        AND marketing._bloecke_zahl_ok(v_props->'columnsGap', 0, 48)) THEN
      RETURN format('Zahl ausserhalb des erlaubten Bereichs in %s', v_id); END IF;
    IF jsonb_typeof(v_style->'padding') NOT IN ('object','null') THEN
      RETURN format('Abstand in %s muss ein Objekt sein', v_id); END IF;
    IF jsonb_typeof(v_style->'padding') = 'object' AND EXISTS (
         SELECT 1 FROM jsonb_each(v_style->'padding') e
          WHERE NOT marketing._bloecke_zahl_ok(e.value, 0, 80)) THEN
      RETURN format('Abstand in %s muss 0 bis 80 sein', v_id); END IF;
    -- Links
    FOR v_url IN SELECT x FROM unnest(ARRAY[v_props->>'url', v_props->>'linkHref']) x WHERE x IS NOT NULL AND x <> '' LOOP
      IF v_typ = 'Image' AND v_url = v_props->>'url' THEN
        IF v_url !~ '^medien:[A-Za-z0-9._-]{1,120}$' OR v_url ~ '\.\.' THEN
          RETURN format('Bilder nur aus den Medien (medien:<datei>) in %s', v_id); END IF;
      ELSIF v_url !~ '^https://[^\s"<>]+$' THEN
        RETURN format('Links nur mit https:// in %s', v_id); END IF;
    END LOOP;
    IF v_typ = 'Text' AND (v_props->>'text') ~ '\]\((?!https://)' THEN
      RETURN format('Links im Text nur mit https:// in %s', v_id); END IF;
  END LOOP;

  -- Baum: jeder Block genau einmal erreichbar, Tiefe <= 4 unter root
  v_front := ARRAY['root'];
  WHILE array_length(v_front, 1) IS NOT NULL LOOP
    v_next := ARRAY[]::text[];
    FOREACH v_id IN ARRAY v_front LOOP
      v_b := p->v_id;
      IF v_b IS NULL THEN RETURN format('Verweis auf fehlenden Block %s', v_id); END IF;
      IF v_id = ANY(v_gesehen) THEN RETURN format('Block %s mehrfach verwendet', v_id); END IF;
      v_gesehen := v_gesehen || v_id;
      -- Pfade wie im Editor (Email Builder JS ce3e610): EmailLayout data.childrenIds,
      -- Container data.props.childrenIds, ColumnsContainer data.props.columns[].childrenIds.
      -- null/fehlend = keine Kinder (die Schemas erlauben .nullable()).
      v_kinder := ARRAY(
        SELECT jsonb_array_elements_text(
                 CASE WHEN jsonb_typeof(v_b->'data'->'childrenIds') = 'array' THEN v_b->'data'->'childrenIds'
                      WHEN jsonb_typeof(v_b->'data'->'props'->'childrenIds') = 'array' THEN v_b->'data'->'props'->'childrenIds'
                      ELSE '[]'::jsonb END)
        UNION ALL
        SELECT jsonb_array_elements_text(CASE WHEN jsonb_typeof(c->'childrenIds') = 'array'
                                              THEN c->'childrenIds' ELSE '[]'::jsonb END)
          FROM jsonb_array_elements(CASE WHEN jsonb_typeof(v_b->'data'->'props'->'columns') = 'array'
                                         THEN v_b->'data'->'props'->'columns' ELSE '[]'::jsonb END) c);
      v_next := v_next || v_kinder;
    END LOOP;
    v_front := v_next;
    IF array_length(v_front, 1) IS NOT NULL THEN v_tiefe := v_tiefe + 1; END IF;
    IF v_tiefe > 4 THEN RETURN 'Hoechstens 4 Ebenen verschachtelt'; END IF;
  END LOOP;
  SELECT count(*) INTO v_n FROM jsonb_object_keys(p);
  IF v_n <> array_length(v_gesehen, 1) THEN RETURN 'Es gibt Bloecke, die nirgends eingebunden sind'; END IF;
  RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_bloecke_speichern(
    p_inhalt uuid, p_basis int, p_betreff text, p_vorschautext text,
    p_bloecke jsonb, p_urheber text, p_als_kopie boolean) RETURNS int
LANGUAGE plpgsql AS $$
DECLARE v_f text; v_art text; v_status text; v_neueste int;
BEGIN
  v_f := marketing.pult_bloecke_fehler(p_bloecke);
  IF v_f IS NOT NULL THEN RAISE EXCEPTION '%', v_f; END IF;
  IF length(btrim(coalesce(p_betreff, ''))) = 0 THEN RAISE EXCEPTION 'Ohne Betreff gibt es keine Fassung'; END IF;
  SELECT art, status INTO v_art, v_status FROM marketing.inhalte WHERE id = p_inhalt FOR UPDATE;
  IF v_art IS NULL THEN RAISE EXCEPTION 'Unbekannter Inhalt'; END IF;
  IF v_art <> 'newsletter' THEN RAISE EXCEPTION 'Bloecke gibt es nur fuer Newsletter'; END IF;
  IF v_status <> 'entwurf' THEN RAISE EXCEPTION 'Nur Entwuerfe lassen sich bearbeiten'; END IF;
  SELECT coalesce(max(fassung), 0) INTO v_neueste FROM marketing.inhalt_fassungen WHERE inhalt = p_inhalt;
  IF p_basis IS DISTINCT FROM v_neueste AND NOT coalesce(p_als_kopie, false) THEN
    RAISE EXCEPTION 'Inzwischen gibt es Fassung % - neu laden oder als Kopie behalten', v_neueste; END IF;
  INSERT INTO marketing.inhalt_fassungen (inhalt, fassung, felder, layout, layout_fassung, urheber, format, bloecke)
  VALUES (p_inhalt, v_neueste + 1,
          jsonb_build_object('betreff', btrim(p_betreff), 'vorschautext', coalesce(p_vorschautext, '')),
          'dunkel', (SELECT fassung FROM marketing.layout_vorlagen WHERE name = 'dunkel'),
          p_urheber, 'bloecke', p_bloecke);
  RETURN v_neueste + 1;
END $$;

CREATE TABLE IF NOT EXISTS marketing.newsletter_vorlagen (
    name         text PRIMARY KEY CHECK (name ~ '^[a-z][a-z0-9-]{1,40}$'),
    mandant      text NOT NULL DEFAULT 'vibemind' REFERENCES marketing.mandanten(id),
    beschreibung text NOT NULL DEFAULT '',
    bloecke      jsonb NOT NULL,
    status       text NOT NULL DEFAULT 'vorschlag' CHECK (status IN ('vorschlag','freigegeben')),
    fassung      int  NOT NULL DEFAULT 1,
    erstellt_von text NOT NULL,
    erstellt_am  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS marketing.newsletter_vorlagen_fassungen (
    vorlage      text NOT NULL REFERENCES marketing.newsletter_vorlagen(name),
    fassung      int  NOT NULL,
    bloecke      jsonb NOT NULL,
    erstellt_von text NOT NULL,
    erstellt_am  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (vorlage, fassung)
);
DROP TRIGGER IF EXISTS trg_vorlage_fassung_unveraenderlich ON marketing.newsletter_vorlagen_fassungen;
CREATE TRIGGER trg_vorlage_fassung_unveraenderlich
  BEFORE UPDATE OR DELETE ON marketing.newsletter_vorlagen_fassungen
  FOR EACH ROW EXECUTE FUNCTION marketing._fassung_unveraenderlich();

CREATE OR REPLACE FUNCTION marketing.pult_vorlage_speichern(
    p_name text, p_beschreibung text, p_bloecke jsonb, p_von text, p_status text) RETURNS int
LANGUAGE plpgsql AS $$
DECLARE v_f text; v_n int;
BEGIN
  v_f := marketing.pult_bloecke_fehler(p_bloecke);
  IF v_f IS NOT NULL THEN RAISE EXCEPTION 'Vorlage ungueltig: %', v_f; END IF;
  IF p_status NOT IN ('vorschlag','freigegeben') THEN RAISE EXCEPTION 'Status muss vorschlag oder freigegeben sein'; END IF;
  INSERT INTO marketing.newsletter_vorlagen (name, beschreibung, bloecke, status, fassung, erstellt_von)
  VALUES (p_name, coalesce(p_beschreibung, ''), p_bloecke, p_status, 0, p_von)
  ON CONFLICT (name) DO NOTHING;
  PERFORM 1 FROM marketing.newsletter_vorlagen WHERE name = p_name FOR UPDATE;
  SELECT coalesce(max(fassung), 0) + 1 INTO v_n FROM marketing.newsletter_vorlagen_fassungen WHERE vorlage = p_name;
  INSERT INTO marketing.newsletter_vorlagen_fassungen (vorlage, fassung, bloecke, erstellt_von)
  VALUES (p_name, v_n, p_bloecke, p_von);
  UPDATE marketing.newsletter_vorlagen
     SET bloecke = p_bloecke, beschreibung = coalesce(p_beschreibung, beschreibung),
         status = p_status, fassung = v_n
   WHERE name = p_name;
  RETURN v_n;
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_inhalt_aus_vorlage(
    p_vorlage text, p_titel text, p_mandant text) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v_b jsonb; v_id uuid;
BEGIN
  IF length(btrim(coalesce(p_titel, ''))) = 0 THEN RAISE EXCEPTION 'Ohne Titel kein Newsletter'; END IF;
  SELECT bloecke INTO v_b FROM marketing.newsletter_vorlagen
   WHERE name = p_vorlage AND status = 'freigegeben' AND mandant = p_mandant;
  IF v_b IS NULL THEN RAISE EXCEPTION 'Vorlage % gibt es nicht oder sie ist nicht freigegeben', p_vorlage; END IF;
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES (p_mandant, 'newsletter', btrim(p_titel))
  RETURNING id INTO v_id;
  INSERT INTO marketing.inhalt_fassungen (inhalt, fassung, felder, layout, layout_fassung, urheber, format, bloecke)
  VALUES (v_id, 1, jsonb_build_object('betreff', btrim(p_titel), 'vorschautext', ''),
          'dunkel', (SELECT fassung FROM marketing.layout_vorlagen WHERE name = 'dunkel'),
          'betreiber', 'bloecke', v_b);
  RETURN v_id;
END $$;

-- Vorlage 'leer' (Grundlage fuer die Probe und fuer "leer anfangen"), Farben aus 'dunkel'
DO $$ DECLARE g jsonb; BEGIN
  IF NOT EXISTS (SELECT 1 FROM marketing.newsletter_vorlagen WHERE name = 'leer') THEN
    SELECT gestalt INTO g FROM marketing.layout_vorlagen WHERE name = 'dunkel';
    PERFORM marketing.pult_vorlage_speichern('leer', 'Leerer Newsletter mit Ueberschrift und Text',
      jsonb_build_object(
        'root', jsonb_build_object('type','EmailLayout','data', jsonb_build_object(
            'backdropColor', g->>'flaeche', 'canvasColor', g->>'grund', 'textColor', g->>'text',
            'fontFamily','MODERN_SANS', 'childrenIds', '["kopf","text"]'::jsonb)),
        'kopf', '{"type":"Heading","data":{"style":{"padding":{"top":32,"bottom":8,"left":24,"right":24}},"props":{"text":"Ueberschrift","level":"h1"}}}'::jsonb,
        'text', '{"type":"Text","data":{"style":{"padding":{"top":8,"bottom":24,"left":24,"right":24}},"props":{"text":"Dein Text.","markdown":true}}}'::jsonb),
      'migration-053', 'freigegeben');
  END IF;
END $$;

-- Uebernahme: jeder Newsletter-Entwurf bekommt eine Block-Fassung aus seiner neuesten Feld-Fassung
DO $$ DECLARE r record; g jsonb; v_kinder jsonb; v_doc jsonb; a jsonb; i int; BEGIN
  SELECT gestalt INTO g FROM marketing.layout_vorlagen WHERE name = 'dunkel';
  FOR r IN
    SELECT i.id, f.fassung, f.felder FROM marketing.inhalte i
      JOIN LATERAL (SELECT * FROM marketing.inhalt_fassungen x WHERE x.inhalt = i.id
                    ORDER BY x.fassung DESC LIMIT 1) f ON true
     WHERE i.art = 'newsletter' AND i.status = 'entwurf' AND f.format = 'felder'
  LOOP
    v_kinder := '[]'::jsonb; v_doc := '{}'::jsonb; i := 0;
    FOR a IN SELECT * FROM jsonb_array_elements(coalesce(r.felder->'abschnitte', '[]')) LOOP
      i := i + 1;
      IF length(btrim(coalesce(a->>'titel', ''))) > 0 THEN
        v_doc := v_doc || jsonb_build_object('h' || i, jsonb_build_object('type','Heading','data',
                   jsonb_build_object('style','{"padding":{"top":16,"bottom":4,"left":24,"right":24}}'::jsonb,
                                      'props', jsonb_build_object('text', a->>'titel', 'level','h2'))));
        v_kinder := v_kinder || to_jsonb('h' || i);
      END IF;
      v_doc := v_doc || jsonb_build_object('t' || i, jsonb_build_object('type','Text','data',
                 jsonb_build_object('style','{"padding":{"top":4,"bottom":12,"left":24,"right":24}}'::jsonb,
                                    'props', jsonb_build_object('text', coalesce(a->>'text',''), 'markdown', false))));
      v_kinder := v_kinder || to_jsonb('t' || i);
    END LOOP;
    IF coalesce(r.felder->>'knopf_link','') ~ '^https://' AND length(btrim(coalesce(r.felder->>'knopf_text',''))) > 0 THEN
      v_doc := v_doc || jsonb_build_object('knopf', jsonb_build_object('type','Button','data',
                 jsonb_build_object('style','{"padding":{"top":12,"bottom":24,"left":24,"right":24}}'::jsonb,
                                    'props', jsonb_build_object('text', r.felder->>'knopf_text', 'url', r.felder->>'knopf_link',
                                                                'buttonBackgroundColor', g->>'akzent', 'buttonTextColor', g->>'handlung_text'))));
      v_kinder := v_kinder || '"knopf"'::jsonb;
    END IF;
    v_doc := v_doc || jsonb_build_object('root', jsonb_build_object('type','EmailLayout','data', jsonb_build_object(
               'backdropColor', g->>'flaeche', 'canvasColor', g->>'grund', 'textColor', g->>'text',
               'fontFamily','MODERN_SANS', 'childrenIds', v_kinder)));
    IF marketing.pult_bloecke_fehler(v_doc) IS NULL THEN
      PERFORM marketing.pult_bloecke_speichern(r.id, r.fassung,
                coalesce(nullif(btrim(r.felder->>'betreff'),''), 'Newsletter'),
                coalesce(r.felder->>'vorschautext',''), v_doc, 'agent', false);
    ELSE
      RAISE NOTICE 'Uebernahme uebersprungen fuer %: %', r.id, marketing.pult_bloecke_fehler(v_doc);
    END IF;
  END LOOP;
END $$;

COMMIT;
