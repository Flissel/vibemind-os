-- RUNBOOK: Nach 060 NIE erneut einspielen: ueberschreibt die 060-Huellen (Sperre, Gestaltungspruefung).
-- Nach jedem Migrations-Replay verify_060 laufen lassen.
-- 055_newsletter_editor_korrektur.sql — Schlussrunde Newsletter-Editor E1
-- (sales-claw final-fix-findings.md I2/I3/I5/I8). 053/054 sind produktiv
-- angewendet; diese Datei ersetzt pult_bloecke_fehler per CREATE OR REPLACE
-- (Signatur unveraendert), ergaenzt eine Funktion und prueft die CHECK-Regeln
-- gegen den Bestand neu. Idempotent, in einer Transaktion.
--
--   I2  Rahmen (Container) und Spalten (ColumnsContainer) nur direkt unter
--       root. Der Uebersetzer (bloecke_mjml) kann keine MJML-Sektion in eine
--       Sektion schachteln und liess sie still weg.
--   I3  Zahlen-Grund nennt Feld, Block und erlaubten Bereich.
--   I5  Knopf ohne Link wird abgelehnt (der Uebersetzer laesst ihn weg).
--   I8  marketing.pult_in_bloecke_uebernehmen: EIN Newsletter-Entwurf, dessen
--       neueste Fassung im Feldformat ist (Bruecke aus dem alten Weg), bekommt
--       eine Block-Fassung - dieselbe Abbildung wie 053/054 (Ueberschrift je
--       Abschnittstitel, ein Text-Block je Absatz, Knopf bei https-Link).
BEGIN;

CREATE OR REPLACE FUNCTION marketing.pult_bloecke_fehler(p jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
  v_id text; v_b jsonb; v_typ text; v_props jsonb; v_style jsonb;
  v_kinder text[]; v_k text; v_n int; v_text text;
  v_gesehen text[] := ARRAY[]::text[];
  v_front text[]; v_next text[]; v_tiefe int := 0;
  v_farbe text; v_url text;
  v_feld text; v_wert jsonb; v_lo numeric; v_hi numeric;
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
    -- I3: der Grund nennt Feld, Block und Bereich (die Editor-Regler halten dieselben Grenzen)
    FOR v_feld, v_wert, v_lo, v_hi IN
      SELECT x.f, x.w, x.lo, x.hi FROM (VALUES
        ('fontSize',     v_style->'fontSize',     8::numeric, 72::numeric),
        ('borderRadius', v_style->'borderRadius', 0, 32),
        ('width',        v_props->'width',        1, 600),
        ('height',       v_props->'height',       0, 600),
        ('lineHeight',   v_props->'lineHeight',   1, 10),
        ('columnsGap',   v_props->'columnsGap',   0, 48)) x(f, w, lo, hi)
    LOOP
      IF NOT marketing._bloecke_zahl_ok(v_wert, v_lo, v_hi) THEN
        RETURN format('Zahl ausserhalb des erlaubten Bereichs: %s in %s (erlaubt %s–%s)', v_feld, v_id, v_lo, v_hi);
      END IF;
    END LOOP;
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
    -- I5: ein Knopf ohne Link verschwaende still aus der Mail
    IF v_typ = 'Button' AND length(btrim(coalesce(v_props->>'url', ''))) = 0 THEN
      RETURN format('Knopf ohne Link in %s – bitte https://… eintragen', v_id); END IF;
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
      -- I2: Rahmen und Spalten nur direkt unter root
      IF v_id <> 'root' THEN
        FOREACH v_k IN ARRAY v_kinder LOOP
          IF p->v_k->>'type' IN ('Container', 'ColumnsContainer') THEN
            RETURN 'Rahmen und Spalten nur auf oberster Ebene'; END IF;
        END LOOP;
      END IF;
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

-- I8: Feld-Newsletter (Bruecke aus dem alten Weg) ins Editor-Format. Farben
-- aus dem Layout der Feld-Fassung (gepinnte Fassung, sonst aktuelle Gestalt,
-- sonst 'dunkel'); Bedeutung wie 053: backdropColor = flaeche,
-- canvasColor = grund, textColor = text. p_von: wer uebernimmt (Pflicht wie
-- bei pult_entscheiden); die neue Fassung traegt urheber 'betreiber'.
CREATE OR REPLACE FUNCTION marketing.pult_in_bloecke_uebernehmen(p_inhalt uuid, p_von text) RETURNS int
LANGUAGE plpgsql AS $$
DECLARE
  v_art text; v_status text; v_titel text; f record; g jsonb;
  v_kinder jsonb := '[]'::jsonb; v_doc jsonb := '{}'::jsonb; a jsonb; i int := 0; j int; v_abs text;
BEGIN
  IF length(btrim(coalesce(p_von, ''))) = 0 THEN RAISE EXCEPTION 'Wer uebernimmt, fehlt'; END IF;
  SELECT art, status, titel INTO v_art, v_status, v_titel FROM marketing.inhalte WHERE id = p_inhalt FOR UPDATE;
  IF v_art IS NULL THEN RAISE EXCEPTION 'Unbekannter Inhalt'; END IF;
  IF v_art <> 'newsletter' THEN RAISE EXCEPTION 'Nur Newsletter lassen sich ins Editor-Format uebernehmen'; END IF;
  IF v_status <> 'entwurf' THEN RAISE EXCEPTION 'Nur Entwuerfe lassen sich bearbeiten'; END IF;
  SELECT * INTO f FROM marketing.inhalt_fassungen WHERE inhalt = p_inhalt ORDER BY fassung DESC LIMIT 1;
  IF NOT FOUND THEN RAISE EXCEPTION 'Dieser Inhalt hat noch keine Fassung'; END IF;
  IF f.format = 'bloecke' THEN RAISE EXCEPTION 'Dieser Newsletter ist schon im Editor-Format'; END IF;

  g := coalesce((SELECT lf.gestalt FROM marketing.layout_fassungen lf
                  WHERE lf.layout = f.layout AND lf.fassung = f.layout_fassung),
                (SELECT l.gestalt FROM marketing.layout_vorlagen l WHERE l.name = f.layout),
                (SELECT l.gestalt FROM marketing.layout_vorlagen l WHERE l.name = 'dunkel'));
  IF marketing.pult_gestalt_fehler(g) IS NOT NULL THEN
    g := (SELECT l.gestalt FROM marketing.layout_vorlagen l WHERE l.name = 'dunkel');
  END IF;

  FOR a IN SELECT * FROM jsonb_array_elements(CASE WHEN jsonb_typeof(f.felder->'abschnitte') = 'array'
                                                   THEN f.felder->'abschnitte' ELSE '[]'::jsonb END) LOOP
    i := i + 1;
    IF length(btrim(coalesce(a->>'titel', ''))) > 0 THEN              -- Ueberschrift wie 053
      v_doc := v_doc || jsonb_build_object('h' || i, jsonb_build_object('type','Heading','data',
                 jsonb_build_object('style','{"padding":{"top":16,"bottom":4,"left":24,"right":24}}'::jsonb,
                                    'props', jsonb_build_object('text', btrim(a->>'titel'), 'level','h2'))));
      v_kinder := v_kinder || to_jsonb('h' || i);
    END IF;
    j := 0;                                                           -- ein Text-Block je Absatz wie 054
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
  IF coalesce(f.felder->>'knopf_link', '') ~ '^https://' AND length(btrim(coalesce(f.felder->>'knopf_text', ''))) > 0 THEN
    v_doc := v_doc || jsonb_build_object('knopf', jsonb_build_object('type','Button','data',
               jsonb_build_object('style','{"padding":{"top":12,"bottom":24,"left":24,"right":24}}'::jsonb,
                                  'props', jsonb_build_object('text', btrim(f.felder->>'knopf_text'),
                                                             'url', btrim(f.felder->>'knopf_link'),
                                                             'buttonBackgroundColor', g->>'akzent',
                                                             'buttonTextColor', g->>'handlung_text'))));
    v_kinder := v_kinder || '"knopf"'::jsonb;
  END IF;
  v_doc := v_doc || jsonb_build_object('root', jsonb_build_object('type','EmailLayout','data', jsonb_build_object(
             'backdropColor', g->>'flaeche', 'canvasColor', g->>'grund', 'textColor', g->>'text',
             'fontFamily','MODERN_SANS', 'childrenIds', v_kinder)));
  -- pult_bloecke_speichern prueft das Dokument (Grund bei Ablehnung) und die Fassungsfolge
  RETURN marketing.pult_bloecke_speichern(p_inhalt, f.fassung,
           coalesce(nullif(btrim(f.felder->>'betreff'), ''), nullif(btrim(v_titel), ''), 'Newsletter'),
           coalesce(f.felder->>'vorschautext', ''), v_doc, 'betreiber', false);
END $$;

-- I4 (nur lesen): Der Editor verlangt genau 3 columns-Eintraege je Spalten-
-- block. Gemessen vor 055: 0 gespeicherte Dokumente mit einer anderen Zahl.
DO $$ DECLARE v_n int; BEGIN
  SELECT count(*) INTO v_n FROM (
      SELECT bloecke FROM marketing.inhalt_fassungen WHERE format = 'bloecke'
      UNION ALL SELECT bloecke FROM marketing.newsletter_vorlagen
      UNION ALL SELECT bloecke FROM marketing.newsletter_vorlagen_fassungen) d, jsonb_each(d.bloecke) e
   WHERE e.value->>'type' = 'ColumnsContainer'
     AND (jsonb_typeof(e.value#>'{data,props,columns}') IS DISTINCT FROM 'array'
          OR jsonb_array_length(e.value#>'{data,props,columns}') <> 3);
  RAISE NOTICE 'Spaltenbloecke ohne genau 3 columns-Eintraege: %', v_n;
END $$;

-- CHECK-Regeln neu anlegen: prueft den Bestand gegen die strengeren Regeln
-- (gemessen vor 055: 0 verschachtelte Rahmen/Spalten, 0 Knoepfe ohne Link).
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
