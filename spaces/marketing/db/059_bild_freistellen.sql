-- 059_bild_freistellen.sql - Auftragsmodus freistellen (sales-claw Spec
-- 2026-10-02-newsletter-bild-freistellen-design.md §2). Additiv, idempotent.
BEGIN;
ALTER TABLE marketing.bild_auftraege DROP CONSTRAINT IF EXISTS bild_auftraege_modus_check;
ALTER TABLE marketing.bild_auftraege ADD CONSTRAINT bild_auftraege_modus_check
  CHECK (modus IN ('neu','ueberarbeiten','freistellen'));

CREATE OR REPLACE FUNCTION marketing.pult_bild_auftrag(
    p_inhalt uuid, p_platz text, p_nur_leere boolean, p_hinweis text, p_urheber text,
    p_staerke int, p_modus text) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v_id uuid;
BEGIN
  IF p_staerke IS NULL OR p_staerke < 0 OR p_staerke > 100 THEN
    RAISE EXCEPTION 'Staerke muss 0 bis 100 sein'; END IF;
  IF p_modus IS NULL OR p_modus NOT IN ('neu', 'ueberarbeiten', 'freistellen') THEN
    RAISE EXCEPTION 'Modus muss neu, ueberarbeiten oder freistellen sein'; END IF;
  IF p_modus = 'freistellen' AND (p_platz IS NULL OR p_platz = '') THEN
    RAISE EXCEPTION 'Freistellen braucht einen Bildplatz'; END IF;
  v_id := marketing.pult_bild_auftrag(p_inhalt, p_platz, p_nur_leere, p_hinweis, p_urheber);
  UPDATE marketing.bild_auftraege
     SET staerke = p_staerke,
         modus = CASE WHEN p_modus = 'freistellen' THEN 'freistellen'
                      WHEN p_staerke = 100 THEN 'neu' ELSE p_modus END
   WHERE id = v_id;
  RETURN v_id;
END $$;

-- Ergebnisname auch fuer freigestellte PNGs (nl-<auftrag8>-<platz>-frei.png); sonst Body wie 056.
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
    IF v IS NULL OR v !~ '^medien:nl-[0-9a-f]{8}-[A-Za-z0-9_-]{1,64}(\.jpg|-frei\.png)$' THEN
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

COMMIT;
