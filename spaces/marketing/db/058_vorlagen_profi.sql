-- 058: Newsletter-Vorlagen in Profi-Qualitaet (sales-claw Spec 2026-10-01
-- newsletter-vorlagen-profi-design.md). Idempotent, eine Transaktion.
--   1) Vorlagen fuer alle Laeden (fuer_alle) und Status zurueckgezogen
--   2) Bildplaetze: Container mit Hintergrundbild zaehlen, erzeugte Grafiken nicht
--   3) pult_bloecke_fehler kennt die neuen Gestaltungsfelder
--   4) pult_inhalt_aus_vorlage mit fertigem Dokument (5 Argumente, neue Ueberladung)
BEGIN;

-- 1) Vorlagen fuer alle Laeden und zurueckziehen
ALTER TABLE marketing.newsletter_vorlagen ADD COLUMN IF NOT EXISTS fuer_alle boolean NOT NULL DEFAULT false;
DO $$ DECLARE c text; BEGIN
  FOR c IN SELECT conname FROM pg_constraint WHERE conrelid = 'marketing.newsletter_vorlagen'::regclass
             AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%status%' LOOP
    EXECUTE format('ALTER TABLE marketing.newsletter_vorlagen DROP CONSTRAINT %I', c);
  END LOOP;
END $$;
ALTER TABLE marketing.newsletter_vorlagen ADD CONSTRAINT newsletter_vorlagen_status_check
  CHECK (status IN ('vorschlag','freigegeben','zurueckgezogen'));

CREATE OR REPLACE FUNCTION marketing.pult_vorlage_speichern(
    p_name text, p_beschreibung text, p_bloecke jsonb, p_von text, p_status text) RETURNS int
LANGUAGE plpgsql AS $$
DECLARE v_f text; v_n int;
BEGIN
  v_f := marketing.pult_bloecke_fehler(p_bloecke);
  IF v_f IS NOT NULL THEN RAISE EXCEPTION 'Vorlage ungueltig: %', v_f; END IF;
  IF p_status NOT IN ('vorschlag','freigegeben','zurueckgezogen') THEN RAISE EXCEPTION 'Status muss vorschlag, freigegeben oder zurueckgezogen sein'; END IF;
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

-- 2) Bildplaetze: Container mit Hintergrundbild zaehlen, erzeugte Grafiken nicht
CREATE OR REPLACE FUNCTION marketing._bild_ist_platz(b jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
  SELECT coalesce(b->>'type' IN ('Image','Container')
     AND (b->>'type' = 'Image' OR (b#>>'{data,props,url}') IS NOT NULL)
     AND coalesce(b#>'{data,props,grafik}', 'false'::jsonb) <> 'true'::jsonb
     AND jsonb_typeof(b#>'{data,props,width}') = 'number' AND (b#>>'{data,props,width}')::numeric > 0
     AND jsonb_typeof(b#>'{data,props,height}') = 'number' AND (b#>>'{data,props,height}')::numeric > 0, false) $$;

-- 3) Pruefung der Bloecke (Rumpf aus 055, ergaenzt um die neuen Felder)
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
                                       'HEAVY_SANS','ROUNDED_SANS','MODERN_SERIF','BOOK_SERIF','MONOSPACE','ANZEIGE','TEXT'];
  c_vorlagenschriften CONSTANT text[] := ARRAY['cormorant','dm-sans','playfair','poppins','young-serif','manrope','bodoni','montserrat','josefin','oxanium','rajdhani'];
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
        ('columnsGap',   v_props->'columnsGap',   0, 48),
        ('letterSpacing', v_style->'letterSpacing', -2, 8),
        ('styleLineHeight', v_style->'lineHeight', 0.9, 2.0)) x(f, w, lo, hi)
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
    IF NOT marketing._bloecke_wahl_ok(v_style->'textTransform', ARRAY['none','uppercase']) THEN
      RETURN format('Versalien in %s muss none oder uppercase sein', v_id); END IF;
    IF v_style ? 'overlay' AND jsonb_typeof(v_style->'overlay') <> 'null' AND NOT (
         jsonb_typeof(v_style->'overlay') = 'object'
         AND coalesce(v_style->'overlay'->>'farbe', '') ~ '^#[0-9a-fA-F]{6}$'
         AND marketing._bloecke_zahl_ok(v_style->'overlay'->'deckkraft', 0, 100)) THEN
      RETURN format('Farbfeld in %s braucht farbe #rrggbb und deckkraft 0-100', v_id); END IF;
    IF (v_props ? 'sw' AND jsonb_typeof(v_props->'sw') NOT IN ('boolean','null'))
       OR (v_props ? 'grafik' AND jsonb_typeof(v_props->'grafik') NOT IN ('boolean','null')) THEN
      RETURN format('sw/grafik in %s muss true oder false sein', v_id); END IF;
    IF v_id = 'root' AND jsonb_typeof(v_b->'data'->'schriften') = 'object' AND EXISTS (
         SELECT 1 FROM jsonb_each_text(v_b->'data'->'schriften') e
          WHERE e.key NOT IN ('anzeige','text') OR NOT e.value = ANY(c_vorlagenschriften)) THEN
      RETURN 'Schriftpaar der Vorlage ist nicht erlaubt'; END IF;
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
      IF v_typ IN ('Image','Container') AND v_url = v_props->>'url' THEN
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

-- 4) Neu aus Vorlage mit fertigem Dokument (die API fuellt Rollen, Logo, Grafiken)
CREATE OR REPLACE FUNCTION marketing.pult_inhalt_aus_vorlage(
    p_vorlage text, p_titel text, p_mandant text, p_bloecke jsonb, p_layout text) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v_id uuid; v_f text; v_layout text;
BEGIN
  IF length(btrim(coalesce(p_titel, ''))) = 0 THEN RAISE EXCEPTION 'Ohne Titel kein Newsletter'; END IF;
  PERFORM 1 FROM marketing.newsletter_vorlagen
   WHERE name = p_vorlage AND status = 'freigegeben' AND (mandant = p_mandant OR fuer_alle);
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

COMMIT;
