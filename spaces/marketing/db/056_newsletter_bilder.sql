-- 056_newsletter_bilder.sql — Bild-Auftraege fuer Newsletter (sales-claw Spec
-- 2026-09-29-newsletter-bilder-und-gestaltung-design.md §6). Nur Ergaenzungen,
-- idempotent. pult_inhalt_aus_vorlage wird ersetzt (gleiche Signatur, Rumpf
-- wie 053 plus Auftrag bei leeren Bildplaetzen).
BEGIN;

CREATE OR REPLACE FUNCTION marketing._bild_platz_leer(p_url text) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
  SELECT p_url IS NULL OR btrim(p_url) = '' OR p_url ~ '^medien:platzhalter-[0-9]{1,3}x[0-9]{1,3}\.png$' $$;

CREATE OR REPLACE FUNCTION marketing._bild_ist_platz(b jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
  SELECT coalesce(b->>'type' = 'Image'
     AND jsonb_typeof(b#>'{data,props,width}') = 'number' AND (b#>>'{data,props,width}')::numeric > 0
     AND jsonb_typeof(b#>'{data,props,height}') = 'number' AND (b#>>'{data,props,height}')::numeric > 0, false) $$;

CREATE TABLE IF NOT EXISTS marketing.bild_auftraege (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    inhalt        uuid NOT NULL REFERENCES marketing.inhalte(id),
    platz         text CHECK (platz IS NULL OR platz ~ '^[A-Za-z0-9_-]{1,64}$'),
    nur_leere     boolean NOT NULL DEFAULT false,
    hinweis       text NOT NULL DEFAULT '' CHECK (length(hinweis) <= 500),
    grund_fassung int  NOT NULL,
    status        text NOT NULL DEFAULT 'offen'
                  CHECK (status IN ('offen','in_arbeit','fertig','fehler','verworfen')),
    versuche      int  NOT NULL DEFAULT 0,
    vergeben_bis  timestamptz,
    befund        text NOT NULL DEFAULT '',
    ergebnis      jsonb NOT NULL DEFAULT '{}'::jsonb,
    urheber       text NOT NULL CHECK (urheber IN ('system','mensch','agent')),
    erstellt_am   timestamptz NOT NULL DEFAULT now(),
    geaendert_am  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS bild_auftraege_status_idx ON marketing.bild_auftraege (status, erstellt_am);
CREATE INDEX IF NOT EXISTS bild_auftraege_inhalt_idx ON marketing.bild_auftraege (inhalt, erstellt_am DESC);
-- je Platz (bzw. "alle") hoechstens EIN wartender Auftrag
CREATE UNIQUE INDEX IF NOT EXISTS bild_auftraege_ein_offener
  ON marketing.bild_auftraege (inhalt, coalesce(platz, '*')) WHERE status = 'offen';

CREATE OR REPLACE FUNCTION marketing.pult_bild_auftrag(
    p_inhalt uuid, p_platz text, p_nur_leere boolean, p_hinweis text, p_urheber text) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v_art text; v_status text; f record; v_id uuid;
BEGIN
  IF p_urheber IS NULL OR p_urheber NOT IN ('system','mensch','agent') THEN
    RAISE EXCEPTION 'Urheber muss system, mensch oder agent sein'; END IF;
  IF length(coalesce(p_hinweis, '')) > 500 THEN
    RAISE EXCEPTION 'Der Hinweis ist zu lang (hoechstens 500 Zeichen)'; END IF;
  SELECT art, status INTO v_art, v_status FROM marketing.inhalte WHERE id = p_inhalt FOR UPDATE;
  IF v_art IS NULL THEN RAISE EXCEPTION 'Unbekannter Inhalt'; END IF;
  IF v_art <> 'newsletter' THEN RAISE EXCEPTION 'Bilder gibt es nur fuer Newsletter'; END IF;
  IF v_status <> 'entwurf' THEN RAISE EXCEPTION 'Schon entschieden - keine neuen Bilder'; END IF;
  SELECT fassung, format, bloecke INTO f FROM marketing.inhalt_fassungen
   WHERE inhalt = p_inhalt ORDER BY fassung DESC LIMIT 1;
  IF f.format IS DISTINCT FROM 'bloecke' THEN
    RAISE EXCEPTION 'Dieser Newsletter ist nicht im Editor-Format'; END IF;
  IF p_platz IS NOT NULL THEN
    IF NOT marketing._bild_ist_platz(f.bloecke->p_platz) THEN
      RAISE EXCEPTION 'Bildplatz % gibt es in der gespeicherten Fassung nicht - erst speichern', p_platz; END IF;
  ELSIF NOT EXISTS (SELECT 1 FROM jsonb_each(f.bloecke) e
                     WHERE marketing._bild_ist_platz(e.value)
                       AND (NOT coalesce(p_nur_leere, false)
                            OR marketing._bild_platz_leer(e.value#>>'{data,props,url}'))) THEN
    RAISE EXCEPTION '%', CASE WHEN coalesce(p_nur_leere, false) THEN 'Keine leeren Bildplaetze'
                              ELSE 'Dieser Newsletter hat keine Bildplaetze' END;
  END IF;
  UPDATE marketing.bild_auftraege
     SET status = 'verworfen', befund = 'Durch einen neueren Auftrag ersetzt', geaendert_am = now()
   WHERE inhalt = p_inhalt AND coalesce(platz, '*') = coalesce(p_platz, '*') AND status = 'offen';
  INSERT INTO marketing.bild_auftraege (inhalt, platz, nur_leere, hinweis, grund_fassung, urheber)
  VALUES (p_inhalt, p_platz, coalesce(p_nur_leere, false), btrim(coalesce(p_hinweis, '')), f.fassung, p_urheber)
  RETURNING id INTO v_id;
  RETURN v_id;
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_bild_naechster(p_frist interval) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE a marketing.bild_auftraege; f record; v_titel text;
BEGIN
  -- Abgelaufene Vergaben einzeln (neueste zuerst): nach 3 Versuchen Fehler;
  -- wartet fuer denselben Platz schon ein neuerer, verworfen; sonst wieder offen.
  FOR a IN SELECT * FROM marketing.bild_auftraege
            WHERE status = 'in_arbeit' AND vergeben_bis < now()
            ORDER BY erstellt_am DESC FOR UPDATE LOOP
    UPDATE marketing.bild_auftraege x SET
       status = CASE WHEN a.versuche >= 3 THEN 'fehler'
                     WHEN EXISTS (SELECT 1 FROM marketing.bild_auftraege o
                                   WHERE o.inhalt = a.inhalt AND coalesce(o.platz, '*') = coalesce(a.platz, '*')
                                     AND o.status = 'offen') THEN 'verworfen'
                     ELSE 'offen' END,
       befund = CASE WHEN a.versuche >= 3 THEN 'Dreimal nicht fertig geworden (Arbeiter abgebrochen)'
                     ELSE x.befund END,
       vergeben_bis = NULL, geaendert_am = now()
     WHERE x.id = a.id;
  END LOOP;
  UPDATE marketing.bild_auftraege b
     SET status = 'verworfen', befund = 'Inhalt inzwischen entschieden', geaendert_am = now()
    FROM marketing.inhalte i
   WHERE i.id = b.inhalt AND b.status = 'offen' AND i.status <> 'entwurf';
  SELECT * INTO a FROM marketing.bild_auftraege WHERE status = 'offen'
   ORDER BY erstellt_am LIMIT 1 FOR UPDATE SKIP LOCKED;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT fassung, bloecke, felder INTO f FROM marketing.inhalt_fassungen
   WHERE inhalt = a.inhalt ORDER BY fassung DESC LIMIT 1;
  -- Grundfassung = die Fassung, die der Arbeiter bekommt. "Belegt" heisst dann
  -- "waehrend der Erzeugung geaendert" - nicht "seit Anlage des Auftrags"; sonst
  -- verfiele ein Neu-erzeugen-Auftrag, wenn vorher ein anderer Auftrag den Platz fuellte.
  UPDATE marketing.bild_auftraege
     SET status = 'in_arbeit', versuche = versuche + 1, vergeben_bis = now() + p_frist,
         grund_fassung = f.fassung, geaendert_am = now()
   WHERE id = a.id;
  SELECT titel INTO v_titel FROM marketing.inhalte WHERE id = a.inhalt;
  RETURN jsonb_build_object('id', a.id, 'inhalt', a.inhalt, 'platz', a.platz, 'nur_leere', a.nur_leere,
           'hinweis', a.hinweis, 'versuche', a.versuche + 1, 'fassung', f.fassung, 'bloecke', f.bloecke,
           'titel', v_titel, 'betreff', f.felder->>'betreff');
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_bild_verlaengern(p_auftrag uuid, p_frist interval) RETURNS boolean
LANGUAGE plpgsql AS $$
BEGIN
  UPDATE marketing.bild_auftraege SET vergeben_bis = now() + p_frist, geaendert_am = now()
   WHERE id = p_auftrag AND status = 'in_arbeit';
  RETURN FOUND;
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_bild_datei_fehler(p_auftrag uuid, p_platz text) RETURNS text
LANGUAGE plpgsql STABLE AS $$
DECLARE a marketing.bild_auftraege; v_doc jsonb;
BEGIN
  SELECT * INTO a FROM marketing.bild_auftraege WHERE id = p_auftrag;
  IF NOT FOUND THEN RETURN 'Unbekannter Auftrag'; END IF;
  IF a.status <> 'in_arbeit' OR a.vergeben_bis IS NULL OR a.vergeben_bis < now() THEN
    RETURN 'Auftrag ist nicht (mehr) in Arbeit'; END IF;
  IF a.platz IS NOT NULL AND p_platz IS DISTINCT FROM a.platz THEN
    RETURN 'Platz gehoert nicht zu diesem Auftrag'; END IF;
  SELECT bloecke INTO v_doc FROM marketing.inhalt_fassungen
   WHERE inhalt = a.inhalt ORDER BY fassung DESC LIMIT 1;
  IF NOT marketing._bild_ist_platz(v_doc->p_platz) THEN RETURN 'Bildplatz gibt es nicht'; END IF;
  RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_bild_einsetzen(p_auftrag uuid, p_ergebnis jsonb, p_befund text) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE a marketing.bild_auftraege; v_status text; f record; v_grund jsonb; v_doc jsonb;
        k text; v text; v_jetzt text; v_alt text;
        v_ein text[] := ARRAY[]::text[]; v_weg text[] := ARRAY[]::text[]; v_n int; v_befund text;
BEGIN
  SELECT * INTO a FROM marketing.bild_auftraege WHERE id = p_auftrag FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannter Auftrag'; END IF;
  IF a.status <> 'in_arbeit' THEN RAISE EXCEPTION 'Auftrag ist nicht in Arbeit (%)', a.status; END IF;
  IF jsonb_typeof(p_ergebnis) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'Ergebnis muss ein Objekt sein'; END IF;
  FOR k, v IN SELECT key, value #>> '{}' FROM jsonb_each(p_ergebnis) LOOP
    IF a.platz IS NOT NULL AND k <> a.platz THEN
      RAISE EXCEPTION 'Platz % gehoert nicht zu diesem Auftrag', k; END IF;
    IF v IS NULL OR v !~ '^medien:nl-[0-9a-f]{8}-[A-Za-z0-9_-]{1,64}\.jpg$' THEN
      RAISE EXCEPTION 'Ungueltiger Bildname fuer %', k; END IF;
  END LOOP;
  SELECT status INTO v_status FROM marketing.inhalte WHERE id = a.inhalt FOR UPDATE;
  IF v_status <> 'entwurf' THEN
    UPDATE marketing.bild_auftraege SET status = 'verworfen', befund = 'Inhalt inzwischen entschieden',
           vergeben_bis = NULL, geaendert_am = now() WHERE id = a.id;
    RETURN jsonb_build_object('fassung', NULL, 'eingesetzt', '[]'::jsonb, 'uebersprungen', '[]'::jsonb);
  END IF;
  SELECT fassung, felder, bloecke INTO f FROM marketing.inhalt_fassungen
   WHERE inhalt = a.inhalt ORDER BY fassung DESC LIMIT 1;
  SELECT bloecke INTO v_grund FROM marketing.inhalt_fassungen WHERE inhalt = a.inhalt AND fassung = a.grund_fassung;
  v_doc := f.bloecke;
  FOR k, v IN SELECT key, value #>> '{}' FROM jsonb_each(p_ergebnis) LOOP
    IF NOT marketing._bild_ist_platz(v_doc->k) THEN
      v_weg := v_weg || (k || ': Platz gibt es nicht mehr'); CONTINUE; END IF;
    v_jetzt := v_doc #>> ARRAY[k, 'data', 'props', 'url'];
    v_alt := v_grund #>> ARRAY[k, 'data', 'props', 'url'];
    IF marketing._bild_platz_leer(v_jetzt) OR v_jetzt IS NOT DISTINCT FROM v_alt THEN
      v_doc := jsonb_set(v_doc, ARRAY[k, 'data', 'props', 'url'], to_jsonb(v));
      v_ein := v_ein || k;
    ELSE
      v_weg := v_weg || (k || ': Platz inzwischen belegt');
    END IF;
  END LOOP;
  IF array_length(v_ein, 1) IS NOT NULL THEN
    v_n := marketing.pult_bloecke_speichern(a.inhalt, f.fassung, f.felder->>'betreff',
             coalesce(f.felder->>'vorschautext', ''), v_doc, 'agent', false);
  END IF;
  v_befund := concat_ws('; ', nullif(btrim(coalesce(p_befund, '')), ''),
                        nullif(array_to_string(v_weg, '; '), ''));
  -- Nichts eingesetzt: fehler mit Befund (nicht fertig), damit die Oberflaeche es zeigt.
  UPDATE marketing.bild_auftraege
     SET status = CASE WHEN array_length(v_ein, 1) IS NULL THEN 'fehler' ELSE 'fertig' END,
         ergebnis = p_ergebnis, befund = coalesce(v_befund, ''),
         vergeben_bis = NULL, geaendert_am = now() WHERE id = a.id;
  RETURN jsonb_build_object('fassung', v_n, 'eingesetzt', to_jsonb(v_ein), 'uebersprungen', to_jsonb(v_weg));
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_bild_zurueck(p_auftrag uuid, p_befund text, p_endgueltig boolean) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE a marketing.bild_auftraege; v_neu text;
BEGIN
  SELECT * INTO a FROM marketing.bild_auftraege WHERE id = p_auftrag FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannter Auftrag'; END IF;
  IF a.status <> 'in_arbeit' THEN RETURN a.status; END IF;   -- inzwischen verworfen: nichts zu tun
  v_neu := CASE WHEN coalesce(p_endgueltig, false) OR a.versuche >= 3 THEN 'fehler'
                WHEN EXISTS (SELECT 1 FROM marketing.bild_auftraege o
                              WHERE o.inhalt = a.inhalt AND coalesce(o.platz, '*') = coalesce(a.platz, '*')
                                AND o.status = 'offen') THEN 'verworfen'
                ELSE 'offen' END;
  UPDATE marketing.bild_auftraege SET status = v_neu, befund = left(coalesce(p_befund, ''), 500),
         vergeben_bis = NULL, geaendert_am = now() WHERE id = a.id;
  RETURN v_neu;
END $$;

-- Entschiedene Inhalte: wartende und laufende Auftraege verwerfen.
CREATE OR REPLACE FUNCTION marketing._bild_auftraege_verwerfen() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status <> 'entwurf' AND OLD.status = 'entwurf' THEN
    UPDATE marketing.bild_auftraege SET status = 'verworfen', befund = 'Inhalt entschieden',
           vergeben_bis = NULL, geaendert_am = now()
     WHERE inhalt = NEW.id AND status IN ('offen', 'in_arbeit');
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_bild_auftraege_verwerfen ON marketing.inhalte;
CREATE TRIGGER trg_bild_auftraege_verwerfen AFTER UPDATE OF status ON marketing.inhalte
  FOR EACH ROW EXECUTE FUNCTION marketing._bild_auftraege_verwerfen();

-- Neu aus Vorlage: wie 053, plus ein Auftrag "alle leeren", wenn es leere Plaetze gibt.
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
  IF EXISTS (SELECT 1 FROM jsonb_each(v_b) e WHERE marketing._bild_ist_platz(e.value)
               AND marketing._bild_platz_leer(e.value#>>'{data,props,url}')) THEN
    PERFORM marketing.pult_bild_auftrag(v_id, NULL, true, '', 'system');
  END IF;
  RETURN v_id;
END $$;

COMMIT;
