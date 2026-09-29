-- 054_newsletter_bloecke_korrektur.sql — Fix-Runde 1 auf 053 (newsletter-editor-e1,
-- Task 1). 053 ist produktiv angewendet; diese Datei ersetzt Funktionen per
-- CREATE OR REPLACE (Signaturen unveraendert) und ergaenzt. Idempotent.
--
--   1  Bloecke ohne Typ wurden angenommen (NOT IN liefert NULL fuer NULL).
--   2  Markdown-Text (markdown:true) umging Bild-/Linkregeln (marked mit gfm:
--      Bilder, Referenz-Links, Autolinks, rohes HTML). Erlaubt bleibt nur
--      **fett**, *kursiv*, [text](https://...).
--   3  Die 053-Uebernahme legte mehrere Absaetze in EINEN Text-Block
--      (markdown:false) - im Editor gehen die Absatzgrenzen verloren. Neue
--      Fassung mit einem Text-Block je Absatz, nur solange die neueste Fassung
--      genau die 053-Uebernahme ist.
--   4  pult_fassung_speichern (Feldformat) konnte eine Feld-Fassung auf einen
--      Block-Newsletter legen.
--   5  Groesse in Bytes statt Zeichen.
--   Dazu: columnsCount/columns, Aufzaehlungswerte wie in Email Builder JS
--   (ce3e610), strengere Bildnamen, CHECK-Regeln auf allen bloecke-Spalten.
BEGIN;

CREATE OR REPLACE FUNCTION marketing._bloecke_wahl_ok(v jsonb, erlaubt text[]) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
  SELECT v IS NULL OR jsonb_typeof(v) = 'null'
      OR (jsonb_typeof(v) = 'string' AND (v #>> '{}') = ANY(erlaubt)) $$;

CREATE OR REPLACE FUNCTION marketing.pult_bloecke_fehler(p jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
  v_id text; v_b jsonb; v_typ text; v_props jsonb; v_style jsonb;
  v_kinder text[]; v_n int; v_text text;
  v_gesehen text[] := ARRAY[]::text[];
  v_front text[]; v_next text[]; v_tiefe int := 0;
  v_farbe text; v_url text;
  c_schriften CONSTANT text[] := ARRAY['MODERN_SANS','BOOK_SANS','ORGANIC_SANS','GEOMETRIC_SANS',
                                       'HEAVY_SANS','ROUNDED_SANS','MODERN_SERIF','BOOK_SERIF','MONOSPACE'];
BEGIN
  IF p IS NULL OR jsonb_typeof(p) <> 'object' THEN RETURN 'Das Dokument muss ein JSON-Objekt sein'; END IF;
  IF octet_length(p::text) > 262144 THEN RETURN 'Das Dokument ist zu gross (hoechstens 256 KB)'; END IF;
  IF (SELECT count(*) FROM jsonb_object_keys(p)) > 151 THEN RETURN 'Hoechstens 150 Bloecke'; END IF;
  IF p->'root'->>'type' IS DISTINCT FROM 'EmailLayout' THEN RETURN 'Die Wurzel muss ein EmailLayout sein'; END IF;

  FOR v_id, v_b IN SELECT key, value FROM jsonb_each(p) LOOP
    IF jsonb_typeof(v_b) <> 'object' THEN RETURN format('Block %s ist kein Objekt', v_id); END IF;
    v_typ := v_b->>'type';
    IF v_typ IS NULL OR v_typ NOT IN ('EmailLayout','Heading','Text','Button','Image','Divider','Spacer','Container','ColumnsContainer') THEN
      RETURN format('Blocktyp %s ist nicht erlaubt', coalesce(v_typ, '(leer)')); END IF;
    IF v_id <> 'root' AND v_typ = 'EmailLayout' THEN RETURN 'EmailLayout nur als Wurzel'; END IF;
    IF v_id <> 'root' AND v_id !~ '^[A-Za-z0-9_-]{1,64}$' THEN RETURN format('Ungueltige Block-ID %s', v_id); END IF;
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
    -- Aufzaehlungswerte (Email Builder JS ce3e610, packages/block-*)
    IF NOT (marketing._bloecke_wahl_ok(v_style->'fontFamily', c_schriften)
        AND marketing._bloecke_wahl_ok(v_b->'data'->'fontFamily', c_schriften)
        AND marketing._bloecke_wahl_ok(v_props->'fontFamily', c_schriften)) THEN
      RETURN format('Schriftart in %s ist nicht erlaubt', v_id); END IF;
    IF NOT marketing._bloecke_wahl_ok(v_style->'textAlign', ARRAY['left','center','right']) THEN
      RETURN format('Ausrichtung in %s muss left, center oder right sein', v_id); END IF;
    IF NOT marketing._bloecke_wahl_ok(v_style->'fontWeight', ARRAY['bold','normal']) THEN
      RETURN format('Schriftstaerke in %s muss bold oder normal sein', v_id); END IF;
    IF v_typ = 'Heading' AND NOT marketing._bloecke_wahl_ok(v_props->'level', ARRAY['h1','h2','h3']) THEN
      RETURN format('Ueberschrift-Ebene in %s muss h1, h2 oder h3 sein', v_id); END IF;
    IF NOT marketing._bloecke_wahl_ok(v_props->'buttonStyle', ARRAY['rectangle','pill','rounded']) THEN
      RETURN format('Knopfform in %s ist nicht erlaubt', v_id); END IF;
    IF NOT marketing._bloecke_wahl_ok(v_props->'size', ARRAY['x-small','small','medium','large']) THEN
      RETURN format('Knopfgroesse in %s ist nicht erlaubt', v_id); END IF;
    IF NOT marketing._bloecke_wahl_ok(v_props->'contentAlignment', ARRAY['top','middle','bottom']) THEN
      RETURN format('Vertikale Ausrichtung in %s ist nicht erlaubt', v_id); END IF;
    -- Spalten
    IF v_typ = 'ColumnsContainer' THEN
      IF NOT (v_props->'columnsCount' IS NULL OR jsonb_typeof(v_props->'columnsCount') = 'null'
              OR v_props->'columnsCount' IN ('2'::jsonb, '3'::jsonb)) THEN
        RETURN format('Spaltenzahl in %s muss 2 oder 3 sein', v_id); END IF;
      IF jsonb_typeof(v_props->'columns') NOT IN ('array','null') THEN
        RETURN format('Spalten in %s muessen eine Liste sein', v_id); END IF;
      IF jsonb_typeof(v_props->'columns') = 'array' AND jsonb_array_length(v_props->'columns') > 3 THEN
        RETURN format('Hoechstens 3 Spalten in %s', v_id); END IF;
    END IF;
    -- Links und Bilder
    FOR v_url IN SELECT x FROM unnest(ARRAY[v_props->>'url', v_props->>'linkHref']) x WHERE x IS NOT NULL AND x <> '' LOOP
      IF v_typ = 'Image' AND v_url = v_props->>'url' THEN
        IF v_url !~ '^medien:[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.(png|jpe?g|gif|webp)$' OR v_url ~ '\.\.' THEN
          RETURN format('Bilder nur aus den Medien (medien:<datei>) in %s', v_id); END IF;
      ELSIF v_url !~ '^https://[^\s"<>]+$' THEN
        RETURN format('Links nur mit https:// in %s', v_id); END IF;
    END LOOP;
    IF v_typ = 'Text' THEN
      v_text := coalesce(v_props->>'text', '');
      IF v_text ~ '\]\((?!https://)' THEN
        RETURN format('Links im Text nur mit https:// in %s', v_id); END IF;
      IF v_props->'markdown' = 'true'::jsonb THEN
        -- erlaubt: **fett**, *kursiv*, [text](https://...). marked (gfm) kennt mehr.
        IF strpos(v_text, '<') > 0 THEN
          RETURN format('Kein HTML im Markdown-Text (Zeichen <) in %s', v_id); END IF;
        IF strpos(v_text, '![') > 0 THEN
          RETURN format('Keine Bilder im Markdown-Text in %s', v_id); END IF;
        IF v_text ~ '(^|\n)[ \t]*\[[^\]]+\]:' THEN
          RETURN format('Keine Referenz-Links im Markdown-Text in %s', v_id); END IF;
        IF v_text ~* 'http://' THEN
          RETURN format('Links im Text nur mit https:// in %s', v_id); END IF;
        IF v_text ~* '(^|[^/A-Za-z0-9.-])www\.' THEN
          RETURN format('Nackte www-Adressen werden zu http-Links - bitte [text](https://...) in %s', v_id); END IF;
      END IF;
    END IF;
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

-- 4: Feld-Fassungen nicht auf Block-Newsletter. Sonst unveraendert wie 051.
CREATE OR REPLACE FUNCTION marketing.pult_fassung_speichern(
    p_inhalt uuid, p_felder jsonb, p_layout text, p_urheber text) RETURNS int
LANGUAGE plpgsql AS $$
DECLARE v_n int; v_art text;
BEGIN
  IF jsonb_typeof(p_felder) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'Felder muessen ein JSON-Objekt sein'; END IF;
  SELECT art INTO v_art FROM marketing.inhalte WHERE id = p_inhalt;
  IF v_art IS NULL THEN
    RAISE EXCEPTION 'Inhalt % gibt es nicht', p_inhalt; END IF;
  IF v_art = 'newsletter' AND length(btrim(coalesce(p_felder->>'betreff', ''))) = 0 THEN
    RAISE EXCEPTION 'Ohne Betreff gibt es keine Fassung'; END IF;
  IF jsonb_typeof(p_felder->'abschnitte') IS DISTINCT FROM 'array'
     OR jsonb_array_length(p_felder->'abschnitte') = 0 THEN
    RAISE EXCEPTION 'Mindestens ein Abschnitt ist noetig'; END IF;
  IF NOT EXISTS (SELECT 1 FROM jsonb_array_elements(p_felder->'abschnitte') a
                  WHERE length(btrim(coalesce(a->>'text', ''))) > 0) THEN
    RAISE EXCEPTION 'Mindestens ein Abschnitt braucht Text'; END IF;
  IF NOT EXISTS (SELECT 1 FROM marketing.layout_vorlagen WHERE name = p_layout AND art = 'layout') THEN
    RAISE EXCEPTION 'Layout % gibt es nicht', p_layout; END IF;
  PERFORM 1 FROM marketing.inhalte WHERE id = p_inhalt AND status = 'entwurf' FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Nur Entwuerfe lassen sich bearbeiten'; END IF;
  IF (SELECT format FROM marketing.inhalt_fassungen WHERE inhalt = p_inhalt
       ORDER BY fassung DESC LIMIT 1) = 'bloecke' THEN
    RAISE EXCEPTION 'Dieser Newsletter wird im Editor bearbeitet'; END IF;
  SELECT coalesce(max(fassung), 0) + 1 INTO v_n FROM marketing.inhalt_fassungen WHERE inhalt = p_inhalt;
  INSERT INTO marketing.inhalt_fassungen (inhalt, fassung, felder, layout, layout_fassung, urheber)
  VALUES (p_inhalt, v_n, p_felder, p_layout,
          (SELECT fassung FROM marketing.layout_vorlagen WHERE name = p_layout), p_urheber);
  RETURN v_n;
END $$;

-- 3: Absatz-Korrektur der 053-Uebernahme. Greift nur, wenn die neueste
-- Fassung eine Block-Fassung vom Agenten ist und die davor eine Feld-Fassung
-- (= genau die 053-Uebernahme). Zweiter Lauf: neueste ist dann Block auf
-- Block -> nichts.
DO $$ DECLARE r record; v_kinder jsonb; v_doc jsonb; a jsonb; i int; j int; v_abs text; BEGIN
  FOR r IN
    SELECT i.id, n.fassung, n.bloecke, n.felder AS n_felder, v.felder
      FROM marketing.inhalte i
      JOIN LATERAL (SELECT * FROM marketing.inhalt_fassungen x WHERE x.inhalt = i.id
                    ORDER BY x.fassung DESC LIMIT 1) n ON true
      JOIN marketing.inhalt_fassungen v ON v.inhalt = i.id AND v.fassung = n.fassung - 1
     WHERE i.art = 'newsletter' AND i.status = 'entwurf'
       AND n.format = 'bloecke' AND n.urheber = 'agent' AND v.format = 'felder'
  LOOP
    v_kinder := '[]'::jsonb; v_doc := '{}'::jsonb; i := 0;
    FOR a IN SELECT * FROM jsonb_array_elements(coalesce(r.felder->'abschnitte', '[]')) LOOP
      i := i + 1;
      IF r.bloecke ? ('h' || i) THEN                      -- Ueberschrift wie in 053
        v_doc := v_doc || jsonb_build_object('h' || i, r.bloecke->('h' || i));
        v_kinder := v_kinder || to_jsonb('h' || i);
      END IF;
      j := 0;
      FOR v_abs IN
        SELECT btrim(s, E' \t\r\n')
          FROM regexp_split_to_table(replace(coalesce(a->>'text', ''), E'\r\n', E'\n'), E'\\n[ \\t]*\\n') s
      LOOP
        CONTINUE WHEN v_abs = '';
        j := j + 1;
        v_doc := v_doc || jsonb_build_object('t' || i || '_' || j, jsonb_build_object('type','Text','data',
                   jsonb_build_object('style','{"padding":{"top":4,"bottom":12,"left":24,"right":24}}'::jsonb,
                                      'props', jsonb_build_object('text', v_abs, 'markdown', false))));
        v_kinder := v_kinder || to_jsonb('t' || i || '_' || j);
      END LOOP;
    END LOOP;
    IF r.bloecke ? 'knopf' THEN                           -- Knopf wie in 053
      v_doc := v_doc || jsonb_build_object('knopf', r.bloecke->'knopf');
      v_kinder := v_kinder || '"knopf"'::jsonb;
    END IF;
    v_doc := v_doc || jsonb_build_object('root',            -- Wurzel wie in 053, neue Kinder
               jsonb_set(r.bloecke->'root', '{data,childrenIds}', v_kinder));
    IF marketing.pult_bloecke_fehler(v_doc) IS NULL THEN
      PERFORM marketing.pult_bloecke_speichern(r.id, r.fassung,
                coalesce(nullif(btrim(r.n_felder->>'betreff'),''), 'Newsletter'),
                coalesce(r.n_felder->>'vorschautext',''), v_doc, 'agent', false);
    ELSE
      RAISE NOTICE 'Absatz-Korrektur uebersprungen fuer %: %', r.id, marketing.pult_bloecke_fehler(v_doc);
    END IF;
  END LOOP;
END $$;

-- CHECK-Regeln: jede gespeicherte Block-Fassung/Vorlage ist gueltig. Bestehende
-- Zeilen werden beim ADD geprueft - ein Verstoss bricht die Migration ab (kein NOT VALID).
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
