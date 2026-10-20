-- RUNBOOK: nach 077 (und 079), unabhaengig von 078 (Marken-Chat). Idempotent, eine Transaktion. Nach jedem
-- Replay verify_080 laufen lassen. Nach einem Replay von 056, 057, 059, 063, 067 oder 073 IMMER 080 erneut
-- einspielen: die ueberschreiben pult_bild_auftrag/pult_bild_einsetzen/pult_bild_datei_fehler bzw.
-- pult_chat_fertig/pult_chat_stopp_abschliessen per Namen und damit die Huellen von 080 (die umbenannten
-- Originale bleiben, ein erneutes 080 legt nur die Huellen neu an).
-- 080: Assistent fuer Collagen (sales-claw Plan 2026-10-20-freie-flaeche-stufe3-assistent, Task 1).
--   Hinweis: der Plan nennt diese Migration 079; die Nummer hat inzwischen 079_titel_eindeutig belegt (live).
--   1) pult_chat_fertig_seite / pult_chat_stopp_abschliessen_seite: woertlich wie pult_chat_fertig und
--      pult_chat_stopp_abschliessen (067), gespeichert wird aber die Seite ueber pult_seite_speichern(...,
--      'agent', false). Veraltete Basis => dieselbe Ausnahme "Inzwischen gibt es Fassung n ..." (die API macht
--      daraus {"status":"veraltet"}). Zusaetzlich: eine Seite nur auf einer Collage ("Dieser Newsletter ist keine
--      Collage"), damit der Assistent einen Block-Newsletter nie in eine Seite umschreibt.
--   2) pult_bild_auftrag (5 und 7 Argumente) und pult_bild_einsetzen (3 Argumente) bekommen einen Seiten-Zweig,
--      Huellen-Muster wie 060/068: das bisherige Original heisst _pult_bild_auftrag_vor080 bzw.
--      _pult_bild_einsetzen_vor080, die gleichnamige Huelle ruft es, solange die neueste Fassung nicht 'seite' ist
--      (Block-Weg unveraendert). Die 8-arg-Fassung (073) ruft die 7-arg-, die 4-arg-Fassung (057) die 3-arg-Huelle
--      - beide bleiben unveraendert. Seite: platz ist die ID einer Bild-Ebene in seite.ebenen; einsetzen setzt
--      deren quelle (Platzhalter oder seit dem Auftrag unveraendert, sonst "Platz inzwischen belegt") und speichert
--      ueber pult_seite_speichern(..., 'agent', false).
--   3) pult_seite_kompositionen(p_mandant, p_ausser) RETURNS jsonb: [{"inhalt", "seite"}] der neuesten Fassung der
--      letzten 5 Collagen (neueste Fassung im Format 'seite') dieser Firma, nach inhalte.erstellt_am absteigend,
--      ohne p_ausser.
--   4) Fix-Runde 1: Bloecke nie ueber eine Collage. pult_chat_fertig und pult_chat_stopp_abschliessen (067)
--      bekommen Huellen (Originale _pult_chat_fertig_vor080 / _pult_chat_stopp_abschliessen_vor080): sollen
--      Bloecke gespeichert werden und ist die neueste Fassung 'seite', Ausnahme "Das ist ein Collage-Newsletter
--      – Blöcke werden nicht übernommen" (ein alter Block-Arbeiter waehrend des Umstiegs). Ohne Bloecke und bei
--      Block-Newslettern laeuft unveraendert das Original.
--   5) Fix-Runde 1: pult_bild_datei_fehler (056) bekommt eine Huelle (Original _pult_bild_datei_fehler_vor080):
--      bei einer Collage dieselben Pruefungen (Auftrag, Vergabe, Platz), der Bildplatz ist eine Bild-Ebene mit
--      Breite und Hoehe > 0 in der neuesten Seite. Sonst das Original.
-- Sperrreihenfolge wie 061: erst marketing.inhalte, dann Auftraege.
-- Rechte: wie 077 - EXECUTE fuer die drei neuen pult_-Funktionen nicht fuer PUBLIC (Aufrufer ist die API-Rolle).
-- Die Huellen bekommen wie in 060 keine eigenen GRANTs (die Originale haben keine). Helfer bleiben offen.
-- Nachweis: verify_080.sql.
BEGIN;

-- 0) Originale umbenennen (nur beim ersten Einspielen)
DO $$ BEGIN
  IF to_regprocedure('marketing._pult_bild_auftrag_vor080(uuid, text, boolean, text, text)') IS NULL THEN
    ALTER FUNCTION marketing.pult_bild_auftrag(uuid, text, boolean, text, text) RENAME TO _pult_bild_auftrag_vor080;
  END IF;
  IF to_regprocedure('marketing._pult_bild_auftrag_vor080(uuid, text, boolean, text, text, int, text)') IS NULL THEN
    ALTER FUNCTION marketing.pult_bild_auftrag(uuid, text, boolean, text, text, int, text)
      RENAME TO _pult_bild_auftrag_vor080;
  END IF;
  IF to_regprocedure('marketing._pult_bild_einsetzen_vor080(uuid, jsonb, text)') IS NULL THEN
    ALTER FUNCTION marketing.pult_bild_einsetzen(uuid, jsonb, text) RENAME TO _pult_bild_einsetzen_vor080;
  END IF;
  IF to_regprocedure('marketing._pult_chat_fertig_vor080(uuid, text, jsonb, jsonb, jsonb, int)') IS NULL THEN
    ALTER FUNCTION marketing.pult_chat_fertig(uuid, text, jsonb, jsonb, jsonb, int) RENAME TO _pult_chat_fertig_vor080;
  END IF;
  IF to_regprocedure('marketing._pult_chat_stopp_abschliessen_vor080(uuid, jsonb, text, int)') IS NULL THEN
    ALTER FUNCTION marketing.pult_chat_stopp_abschliessen(uuid, jsonb, text, int)
      RENAME TO _pult_chat_stopp_abschliessen_vor080;
  END IF;
  IF to_regprocedure('marketing._pult_bild_datei_fehler_vor080(uuid, text)') IS NULL THEN
    ALTER FUNCTION marketing.pult_bild_datei_fehler(uuid, text) RENAME TO _pult_bild_datei_fehler_vor080;
  END IF;
END $$;

-- 1) Helfer
CREATE OR REPLACE FUNCTION marketing._neuestes_format(p_inhalt uuid) RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT format FROM marketing.inhalt_fassungen WHERE inhalt = p_inhalt ORDER BY fassung DESC LIMIT 1
$$;

-- Stelle (ab 0) der Ebene mit dieser ID in seite.ebenen, NULL = gibt es nicht
CREATE OR REPLACE FUNCTION marketing._seite_ebene_nr(p_seite jsonb, p_id text) RETURNS int
LANGUAGE sql IMMUTABLE AS $$
  SELECT (t.o - 1)::int
    FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p_seite->'ebenen') = 'array' THEN p_seite->'ebenen'
                                   ELSE '[]'::jsonb END) WITH ORDINALITY t(e, o)
   WHERE jsonb_typeof(t.e) = 'object' AND t.e->>'id' = p_id
   LIMIT 1
$$;

-- 2) fertig fuer Seiten (aus pult_chat_fertig, 067)
CREATE OR REPLACE FUNCTION marketing.pult_chat_fertig_seite(
    p_auftrag uuid, p_antwort text, p_seite jsonb, p_hinweise jsonb, p_ergebnis jsonb, p_basis int DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE a marketing.chat_auftraege; f record; v_n int; v_inhalt uuid; v_basis int;
BEGIN
  SELECT inhalt INTO v_inhalt FROM marketing.chat_auftraege WHERE id = p_auftrag;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannter Auftrag'; END IF;
  PERFORM 1 FROM marketing.inhalte WHERE id = v_inhalt FOR UPDATE;
  SELECT * INTO a FROM marketing.chat_auftraege WHERE id = p_auftrag FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannter Auftrag'; END IF;
  IF a.status <> 'in_arbeit' OR a.vergeben_bis IS NULL OR a.vergeben_bis <= now() THEN
    RAISE EXCEPTION 'Auftrag ist nicht (mehr) in Arbeit'; END IF;
  IF a.stopp IS NOT NULL THEN RAISE EXCEPTION 'Auftrag wurde gestoppt'; END IF;
  IF p_hinweise IS NOT NULL AND jsonb_typeof(p_hinweise) <> 'array' THEN
    RAISE EXCEPTION 'Hinweise muessen ein Array sein'; END IF;
  IF p_ergebnis IS NOT NULL AND jsonb_typeof(p_ergebnis) <> 'object' THEN
    RAISE EXCEPTION 'Ergebnis muss ein Objekt sein'; END IF;
  IF p_basis IS NOT NULL AND p_basis < coalesce(a.fassung_vorher, 0) THEN
    RAISE EXCEPTION 'Basis-Fassung % passt nicht zu diesem Auftrag', p_basis; END IF;
  v_basis := coalesce(p_basis, a.fassung_vorher);
  IF p_seite IS NOT NULL THEN
    SELECT format, felder INTO f FROM marketing.inhalt_fassungen WHERE inhalt = a.inhalt ORDER BY fassung DESC LIMIT 1;
    IF f.format IS DISTINCT FROM 'seite' THEN RAISE EXCEPTION 'Dieser Newsletter ist keine Collage'; END IF;
    v_n := marketing.pult_seite_speichern(a.inhalt, v_basis, f.felder->>'betreff',
             coalesce(f.felder->>'vorschautext', ''), p_seite, 'agent', false);
  END IF;
  UPDATE marketing.chat_auftraege
     SET status = 'fertig', antwort = left(coalesce(p_antwort, ''), 4000),
         hinweise = coalesce(p_hinweise, '[]'::jsonb), ergebnis = coalesce(p_ergebnis, '{}'::jsonb),
         fassung_vorher = CASE WHEN p_seite IS NOT NULL THEN v_basis ELSE fassung_vorher END,
         fassung_nachher = v_n, vergeben_bis = NULL,
         zwischenstand = NULL, schritt = '', geaendert_am = now()
   WHERE id = a.id;
  PERFORM marketing._chat_nachruecken(a.inhalt);
  RETURN jsonb_build_object('fassung', v_n);
END $$;

-- 3) Stopp abschliessen fuer Seiten (aus pult_chat_stopp_abschliessen, 067)
CREATE OR REPLACE FUNCTION marketing.pult_chat_stopp_abschliessen_seite(
    p_auftrag uuid, p_seite jsonb, p_hinweis text, p_basis int DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE a marketing.chat_auftraege; f record; v_n int; v_hinweise jsonb; v_inhalt uuid; v_basis int;
BEGIN
  SELECT inhalt INTO v_inhalt FROM marketing.chat_auftraege WHERE id = p_auftrag;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannter Auftrag'; END IF;
  PERFORM 1 FROM marketing.inhalte WHERE id = v_inhalt FOR UPDATE;
  SELECT * INTO a FROM marketing.chat_auftraege WHERE id = p_auftrag FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannter Auftrag'; END IF;
  IF a.status <> 'in_arbeit' THEN
    RETURN jsonb_strip_nulls(jsonb_build_object('status', a.status, 'fassung', a.fassung_nachher)); END IF;
  IF a.stopp IS NULL THEN RAISE EXCEPTION 'Auftrag wurde nicht gestoppt'; END IF;
  v_hinweise := CASE WHEN coalesce(btrim(p_hinweis), '') = '' THEN '[]'::jsonb
                     ELSE jsonb_build_array(left(p_hinweis, 500)) END;
  IF p_seite IS NULL OR a.stopp = 'verwerfen' THEN
    UPDATE marketing.chat_auftraege
       SET status = 'fehler', antwort = 'Gestoppt – nichts übernommen', hinweise = v_hinweise,
           zwischenstand = NULL, schritt = '', vergeben_bis = NULL, geaendert_am = now()
     WHERE id = a.id;
    PERFORM marketing._chat_nachruecken(a.inhalt);
    RETURN jsonb_build_object('status', 'fehler');
  END IF;
  IF p_basis IS NOT NULL AND p_basis < coalesce(a.fassung_vorher, 0) THEN
    RAISE EXCEPTION 'Basis-Fassung % passt nicht zu diesem Auftrag', p_basis; END IF;
  v_basis := coalesce(p_basis, a.fassung_vorher);
  SELECT format, felder INTO f FROM marketing.inhalt_fassungen WHERE inhalt = a.inhalt ORDER BY fassung DESC LIMIT 1;
  IF f.format IS DISTINCT FROM 'seite' THEN RAISE EXCEPTION 'Dieser Newsletter ist keine Collage'; END IF;
  v_n := marketing.pult_seite_speichern(a.inhalt, v_basis, f.felder->>'betreff',
           coalesce(f.felder->>'vorschautext', ''), p_seite, 'agent', false);
  UPDATE marketing.chat_auftraege
     SET status = 'fertig',
         antwort = format('Gestoppt nach Schritt %s – bisherige Schritte übernommen', a.schritt_nr),
         hinweise = v_hinweise,
         ergebnis = a.ergebnis || jsonb_build_object('notiz', format('gestoppt nach Schritt %s', a.schritt_nr)),
         fassung_vorher = v_basis, fassung_nachher = v_n, zwischenstand = NULL, schritt = '',
         vergeben_bis = NULL, geaendert_am = now()
   WHERE id = a.id;
  PERFORM marketing._chat_nachruecken(a.inhalt);
  RETURN jsonb_build_object('status', 'fertig', 'fassung', v_n);
END $$;

-- 4) Bildauftrag, 5 Argumente: Seiten-Zweig (Pruefungen und Ablauf wie 063), sonst das Original
CREATE OR REPLACE FUNCTION marketing.pult_bild_auftrag(
    p_inhalt uuid, p_platz text, p_nur_leere boolean, p_hinweis text, p_urheber text) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v_art text; v_status text; f record; v_nr int; v_id uuid;
BEGIN
  IF marketing._neuestes_format(p_inhalt) IS DISTINCT FROM 'seite' THEN
    RETURN marketing._pult_bild_auftrag_vor080(p_inhalt, p_platz, p_nur_leere, p_hinweis, p_urheber);
  END IF;
  IF p_urheber IS NULL OR p_urheber NOT IN ('system','mensch','agent') THEN
    RAISE EXCEPTION 'Urheber muss system, mensch oder agent sein'; END IF;
  IF length(coalesce(p_hinweis, '')) > 500 THEN
    RAISE EXCEPTION 'Der Hinweis ist zu lang (hoechstens 500 Zeichen)'; END IF;
  SELECT art, status INTO v_art, v_status FROM marketing.inhalte WHERE id = p_inhalt FOR UPDATE;
  IF v_art IS NULL THEN RAISE EXCEPTION 'Unbekannter Inhalt'; END IF;
  IF v_art <> 'newsletter' THEN RAISE EXCEPTION 'Bilder gibt es nur fuer Newsletter'; END IF;
  IF v_status = 'eingereicht' THEN RAISE EXCEPTION 'Liegt zur Freigabe – erst zurückziehen'; END IF;
  IF v_status <> 'entwurf' THEN RAISE EXCEPTION 'Schon entschieden - keine neuen Bilder'; END IF;
  SELECT fassung, format, seite INTO f FROM marketing.inhalt_fassungen
   WHERE inhalt = p_inhalt ORDER BY fassung DESC LIMIT 1;
  IF f.format IS DISTINCT FROM 'seite' THEN RAISE EXCEPTION 'Dieser Newsletter ist keine Collage'; END IF;
  IF p_platz IS NULL OR btrim(p_platz) = '' THEN
    RAISE EXCEPTION 'Bei einer Collage braucht der Bildauftrag eine Bild-Ebene'; END IF;
  v_nr := marketing._seite_ebene_nr(f.seite, p_platz);
  IF v_nr IS NULL THEN
    RAISE EXCEPTION 'Ebene % gibt es in der gespeicherten Fassung nicht – erst speichern', p_platz; END IF;
  IF (f.seite->'ebenen'->v_nr->>'art') IS DISTINCT FROM 'bild' THEN
    RAISE EXCEPTION 'Ebene % ist keine Bild-Ebene', p_platz; END IF;
  UPDATE marketing.bild_auftraege
     SET status = 'verworfen', befund = 'Durch einen neueren Auftrag ersetzt', geaendert_am = now()
   WHERE inhalt = p_inhalt AND coalesce(platz, '*') = p_platz AND status = 'offen';
  INSERT INTO marketing.bild_auftraege (inhalt, platz, nur_leere, hinweis, grund_fassung, urheber)
  VALUES (p_inhalt, p_platz, coalesce(p_nur_leere, false), btrim(coalesce(p_hinweis, '')), f.fassung, p_urheber)
  RETURNING id INTO v_id;
  RETURN v_id;
END $$;

-- 5) Bildauftrag, 7 Argumente: Seiten-Zweig (wie 073, 'aendern' prueft die quelle der Bild-Ebene)
CREATE OR REPLACE FUNCTION marketing.pult_bild_auftrag(
    p_inhalt uuid, p_platz text, p_nur_leere boolean, p_hinweis text, p_urheber text,
    p_staerke int, p_modus text) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v_id uuid; v_seite jsonb; v_nr int;
BEGIN
  IF marketing._neuestes_format(p_inhalt) IS DISTINCT FROM 'seite' THEN
    RETURN marketing._pult_bild_auftrag_vor080(p_inhalt, p_platz, p_nur_leere, p_hinweis, p_urheber,
                                               p_staerke, p_modus);
  END IF;
  IF p_staerke IS NULL OR p_staerke < 0 OR p_staerke > 100 THEN
    RAISE EXCEPTION 'Staerke muss 0 bis 100 sein'; END IF;
  IF p_modus IS NULL OR p_modus NOT IN ('neu', 'ueberarbeiten', 'aendern', 'freistellen') THEN
    RAISE EXCEPTION 'Modus muss neu, aendern, ueberarbeiten oder freistellen sein'; END IF;
  IF p_modus IN ('freistellen', 'aendern') AND (p_platz IS NULL OR p_platz = '') THEN
    RAISE EXCEPTION '% braucht einen Bildplatz', initcap(p_modus); END IF;
  IF p_modus = 'aendern' THEN
    SELECT seite INTO v_seite FROM marketing.inhalt_fassungen
     WHERE inhalt = p_inhalt ORDER BY fassung DESC LIMIT 1;
    v_nr := marketing._seite_ebene_nr(v_seite, p_platz);
    -- unbekannte oder Nicht-Bild-Ebene: den Grund nennt die 5-arg-Fassung
    IF v_nr IS NOT NULL AND (v_seite->'ebenen'->v_nr->>'art') = 'bild'
       AND marketing._bild_platz_leer(v_seite->'ebenen'->v_nr->>'quelle') THEN
      RAISE EXCEPTION 'Hier ist noch kein Bild – nimm bild_erzeugen'; END IF;
  END IF;
  v_id := marketing.pult_bild_auftrag(p_inhalt, p_platz, p_nur_leere, p_hinweis, p_urheber);
  UPDATE marketing.bild_auftraege
     SET staerke = p_staerke,
         modus = CASE WHEN p_modus IN ('freistellen', 'aendern') THEN p_modus
                      WHEN p_staerke = 100 THEN 'neu' ELSE p_modus END
   WHERE id = v_id;
  RETURN v_id;
END $$;

-- 6) Einsetzen, 3 Argumente: Seiten-Zweig (Ablauf wie 063, Platz = Bild-Ebene, Wert = quelle)
CREATE OR REPLACE FUNCTION marketing.pult_bild_einsetzen(p_auftrag uuid, p_ergebnis jsonb, p_befund text) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE a marketing.bild_auftraege; v_inhalt uuid; v_status text; f record; v_grund jsonb; v_doc jsonb;
        k text; v text; v_jetzt text; v_alt text; v_nr int; v_gnr int;
        v_ein text[] := ARRAY[]::text[]; v_weg text[] := ARRAY[]::text[]; v_n int; v_befund text;
BEGIN
  SELECT inhalt INTO v_inhalt FROM marketing.bild_auftraege WHERE id = p_auftrag;
  IF NOT FOUND OR marketing._neuestes_format(v_inhalt) IS DISTINCT FROM 'seite' THEN
    RETURN marketing._pult_bild_einsetzen_vor080(p_auftrag, p_ergebnis, p_befund);
  END IF;
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
    UPDATE marketing.bild_auftraege
       SET status = 'verworfen',
           befund = CASE WHEN v_status = 'eingereicht' THEN 'Liegt zur Freigabe – erst zurückziehen'
                         ELSE 'Inhalt inzwischen entschieden' END,
           vergeben_bis = NULL, geaendert_am = now() WHERE id = a.id;
    RETURN jsonb_build_object('fassung', NULL, 'eingesetzt', '[]'::jsonb, 'uebersprungen', '[]'::jsonb);
  END IF;
  SELECT fassung, felder, seite INTO f FROM marketing.inhalt_fassungen
   WHERE inhalt = a.inhalt ORDER BY fassung DESC LIMIT 1;
  SELECT seite INTO v_grund FROM marketing.inhalt_fassungen WHERE inhalt = a.inhalt AND fassung = a.grund_fassung;
  v_doc := f.seite;
  FOR k, v IN SELECT key, value #>> '{}' FROM jsonb_each(p_ergebnis) LOOP
    v_nr := marketing._seite_ebene_nr(v_doc, k);
    IF v_nr IS NULL OR (v_doc->'ebenen'->v_nr->>'art') IS DISTINCT FROM 'bild' THEN
      v_weg := v_weg || (k || ': Platz gibt es nicht mehr'); CONTINUE; END IF;
    v_jetzt := v_doc->'ebenen'->v_nr->>'quelle';
    v_gnr := marketing._seite_ebene_nr(v_grund, k);
    v_alt := CASE WHEN v_gnr IS NOT NULL THEN v_grund->'ebenen'->v_gnr->>'quelle' END;
    IF marketing._bild_platz_leer(v_jetzt) OR v_jetzt IS NOT DISTINCT FROM v_alt THEN
      v_doc := jsonb_set(v_doc, ARRAY['ebenen', v_nr::text, 'quelle'], to_jsonb(v));
      v_ein := v_ein || k;
    ELSE
      v_weg := v_weg || (k || ': Platz inzwischen belegt');
    END IF;
  END LOOP;
  IF array_length(v_ein, 1) IS NOT NULL THEN
    v_n := marketing.pult_seite_speichern(a.inhalt, f.fassung, f.felder->>'betreff',
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

-- 7) Kompositionen der letzten 5 Collagen einer Firma (fuer die Abwechslung im Prompt)
CREATE OR REPLACE FUNCTION marketing.pult_seite_kompositionen(p_mandant text, p_ausser uuid) RETURNS jsonb
LANGUAGE sql STABLE AS $$
  SELECT coalesce(jsonb_agg(jsonb_build_object('inhalt', k.id, 'seite', k.seite)
                            ORDER BY k.erstellt_am DESC, k.id DESC), '[]'::jsonb)
    FROM (SELECT i.id, i.erstellt_am, n.seite
            FROM marketing.inhalte i
            CROSS JOIN LATERAL (SELECT f.format, f.seite FROM marketing.inhalt_fassungen f
                                 WHERE f.inhalt = i.id ORDER BY f.fassung DESC LIMIT 1) n
           WHERE i.mandant = p_mandant AND i.art = 'newsletter' AND i.id IS DISTINCT FROM p_ausser
             AND n.format = 'seite'
           ORDER BY i.erstellt_am DESC, i.id DESC
           LIMIT 5) k
$$;

-- 8) Bloecke nie ueber eine Collage (Huellen um 067). Erst den Inhalt sperren (Reihenfolge wie 061), dann das
--    Format der neuesten Fassung pruefen - das Original sperrt denselben Inhalt in derselben Transaktion erneut.
CREATE OR REPLACE FUNCTION marketing.pult_chat_fertig(
    p_auftrag uuid, p_antwort text, p_bloecke jsonb, p_hinweise jsonb, p_ergebnis jsonb, p_basis int DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE v_inhalt uuid;
BEGIN
  IF p_bloecke IS NOT NULL THEN
    SELECT inhalt INTO v_inhalt FROM marketing.chat_auftraege WHERE id = p_auftrag;
    IF FOUND THEN
      PERFORM 1 FROM marketing.inhalte WHERE id = v_inhalt FOR UPDATE;
      IF marketing._neuestes_format(v_inhalt) = 'seite' THEN
        RAISE EXCEPTION 'Das ist ein Collage-Newsletter – Blöcke werden nicht übernommen'; END IF;
    END IF;
  END IF;
  RETURN marketing._pult_chat_fertig_vor080(p_auftrag, p_antwort, p_bloecke, p_hinweise, p_ergebnis, p_basis);
END $$;

-- Stopp: nur wenn tatsaechlich gespeichert wuerde (in Arbeit, behalten); sonst meldet das Original den Endstand
CREATE OR REPLACE FUNCTION marketing.pult_chat_stopp_abschliessen(
    p_auftrag uuid, p_bloecke jsonb, p_hinweis text, p_basis int DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE v_inhalt uuid; a record;
BEGIN
  IF p_bloecke IS NOT NULL THEN
    SELECT inhalt INTO v_inhalt FROM marketing.chat_auftraege WHERE id = p_auftrag;
    IF FOUND THEN
      PERFORM 1 FROM marketing.inhalte WHERE id = v_inhalt FOR UPDATE;
      SELECT status, stopp INTO a FROM marketing.chat_auftraege WHERE id = p_auftrag;
      IF a.status = 'in_arbeit' AND a.stopp = 'behalten' AND marketing._neuestes_format(v_inhalt) = 'seite' THEN
        RAISE EXCEPTION 'Das ist ein Collage-Newsletter – Blöcke werden nicht übernommen'; END IF;
    END IF;
  END IF;
  RETURN marketing._pult_chat_stopp_abschliessen_vor080(p_auftrag, p_bloecke, p_hinweis, p_basis);
END $$;

-- 9) Dateipruefung des Bild-Arbeiters: Collage-Zweig (Pruefungen und Wortlaut wie 056), sonst das Original
CREATE OR REPLACE FUNCTION marketing.pult_bild_datei_fehler(p_auftrag uuid, p_platz text) RETURNS text
LANGUAGE plpgsql STABLE AS $$
DECLARE a marketing.bild_auftraege; v_seite jsonb; v_nr int; e jsonb;
BEGIN
  SELECT * INTO a FROM marketing.bild_auftraege WHERE id = p_auftrag;
  IF NOT FOUND OR marketing._neuestes_format(a.inhalt) IS DISTINCT FROM 'seite' THEN
    RETURN marketing._pult_bild_datei_fehler_vor080(p_auftrag, p_platz);
  END IF;
  IF a.status <> 'in_arbeit' OR a.vergeben_bis IS NULL OR a.vergeben_bis < now() THEN
    RETURN 'Auftrag ist nicht (mehr) in Arbeit'; END IF;
  IF a.platz IS NOT NULL AND p_platz IS DISTINCT FROM a.platz THEN
    RETURN 'Platz gehoert nicht zu diesem Auftrag'; END IF;
  SELECT seite INTO v_seite FROM marketing.inhalt_fassungen
   WHERE inhalt = a.inhalt ORDER BY fassung DESC LIMIT 1;
  v_nr := marketing._seite_ebene_nr(v_seite, p_platz);
  e := CASE WHEN v_nr IS NOT NULL THEN v_seite->'ebenen'->v_nr END;
  IF NOT coalesce(CASE WHEN e->>'art' = 'bild' AND jsonb_typeof(e->'breite') = 'number'
                            AND jsonb_typeof(e->'hoehe') = 'number'
                       THEN (e->>'breite')::numeric > 0 AND (e->>'hoehe')::numeric > 0 END, false) THEN
    RETURN 'Bildplatz gibt es nicht'; END IF;
  RETURN NULL;
END $$;

REVOKE ALL ON FUNCTION marketing.pult_chat_fertig_seite(uuid, text, jsonb, jsonb, jsonb, int) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.pult_chat_stopp_abschliessen_seite(uuid, jsonb, text, int) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.pult_seite_kompositionen(text, uuid) FROM PUBLIC;

COMMIT;
