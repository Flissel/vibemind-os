-- RUNBOOK: 086 ersetzt pult_marke_anlegen, pult_marke_bearbeitung_anlegen, pult_marke_uebernehmen,
-- pult_marke_naechster(interval, text[]) (aus 066) und pult_marke_rueckgaengig (aus 078). Ein Replay von
-- 064, 066 oder 078 ueberschreibt diese Funktionen wieder mit der alten Fassung (nur aktive Firmen, ohne
-- einrichtung in naechster) - danach IMMER 086 erneut einspielen und verify_060, verify_062 .. verify_066,
-- verify_078 und verify_086 zusammen ueber migration_probe laufen lassen. verify_061 ist unabhaengig von 086
-- schon rot ("0: Index laufend bleibt" - der Index wurde in 066 umgebaut) und gehoert nicht in diese Probe.
-- 086: Einrichtung neuer Marken (/marke_neu, sales-claw Spec 2026-10-21-marke-neu-skill-design.md).
-- Idempotent, eine Transaktion.
--   1) mandanten.einrichtung ('laeuft' | 'abgebrochen' | NULL), einrichtung_stand (jsonb, {} vorgegeben:
--      uebersprungen = Liste der uebersprungenen Schritte, vermittler = Antwort auf die Vermittler-Frage),
--      eingerichtet_von. Eine Firma in Einrichtung ist nie aktiv (Tabellen-CHECK).
--   2) _mandant_id_aus_name: Umlaute -> ae/oe/ue/ss, klein, [^a-z0-9] -> _, Folgen zu einem, _ am Rand weg,
--      ohne Buchstaben vorn Praefix m_, kuerzer als 2 -> Anhang _marke, auf 31 Zeichen gekuerzt.
--      Ohne Kollisionspruefung (die macht pult_mandant_einrichten: _2, _3 ..., vor dem Anhang gekuerzt).
--   3) pult_mandant_einrichten(name, von): neue Firma inaktiv in Einrichtung, oder eine Einrichtung
--      gleichen Namens (auch abgebrochene) fortsetzen. Name einer aktiven oder einer inaktiven Firma ohne
--      Einrichtung wird abgelehnt. Gleicher Name = gleich ohne Gross/Klein, getrimmt. Pro Name hoechstens
--      eine Firma: eine Transaktions-Sperre (advisory) reiht gleichzeitige Einrichtungen hintereinander.
--   4) pult_mandant_pflichtteil_setzen(mandant, pflichtteil, stand): mischt Pflichtangaben und Stand, nur
--      waehrend der Einrichtung. Ein nicht leerer Abmeldehinweis braucht {abmeldelink}.
--   5) pult_mandant_aktivieren(mandant, von): prueft Name, Impressum, Abmeldehinweis, Vermittler-Regel und
--      dass der Assistent nicht arbeitet; setzt aktiv = true, einrichtung = NULL.
--   6) pult_mandant_einrichtung_abbrechen(mandant, von): laeuft -> abgebrochen, nichts wird geloescht.
--   7) Marken-Chat waehrend der Einrichtung: anlegen, bearbeitung_anlegen, uebernehmen und rueckgaengig
--      woertlich aus 066/078, einzige Aenderung "AND aktiv" -> "AND (aktiv OR einrichtung = 'laeuft')".
--      pult_marke_naechster woertlich aus 066, die Rueckgabe zusaetzlich mit einrichtung und - nur bei
--      'laeuft' - pflichtteil und einrichtung_stand (der Arbeiter fuehrt damit durch die Schritte).
-- Sperrreihenfolge wie 064: mandanten -> marken_auftraege -> marken_vorschlaege.
-- Rechte: wie 060/064/066/078 - kein SECURITY DEFINER, kein SET search_path, keine eigenen GRANTs
-- (Default Privileges aus 003 gelten).
BEGIN;

-- 1) Spalten
ALTER TABLE marketing.mandanten ADD COLUMN IF NOT EXISTS einrichtung text;
ALTER TABLE marketing.mandanten ADD COLUMN IF NOT EXISTS einrichtung_stand jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE marketing.mandanten ADD COLUMN IF NOT EXISTS eingerichtet_von text;
ALTER TABLE marketing.mandanten DROP CONSTRAINT IF EXISTS mandanten_einrichtung_check;
ALTER TABLE marketing.mandanten ADD CONSTRAINT mandanten_einrichtung_check
  CHECK (einrichtung IN ('laeuft','abgebrochen'));
ALTER TABLE marketing.mandanten DROP CONSTRAINT IF EXISTS mandanten_einrichtung_inaktiv;
ALTER TABLE marketing.mandanten ADD CONSTRAINT mandanten_einrichtung_inaktiv
  CHECK (einrichtung IS NULL OR NOT aktiv);
ALTER TABLE marketing.mandanten DROP CONSTRAINT IF EXISTS mandanten_einrichtung_stand_check;
ALTER TABLE marketing.mandanten ADD CONSTRAINT mandanten_einrichtung_stand_check
  CHECK (jsonb_typeof(einrichtung_stand) = 'object');

-- 2) id aus dem Namen
CREATE OR REPLACE FUNCTION marketing._mandant_id_aus_name(p_name text) RETURNS text
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE v text;
BEGIN
  v := lower(replace(replace(replace(replace(replace(replace(replace(replace(coalesce(p_name, ''),
         'Ä', 'ae'), 'Ö', 'oe'), 'Ü', 'ue'), 'ä', 'ae'), 'ö', 'oe'), 'ü', 'ue'), 'ẞ', 'ss'), 'ß', 'ss'));
  v := btrim(regexp_replace(v, '[^a-z0-9]+', '_', 'g'), '_');
  IF v !~ '^[a-z]' THEN v := 'm_' || v; END IF;
  IF char_length(v) < 2 THEN v := v || '_marke'; END IF;
  RETURN left(v, 31);
END $$;

-- Gemeinsame Namenspruefung (einrichten, aktivieren): NULL = gueltig
CREATE OR REPLACE FUNCTION marketing._mandant_name_fehler(p_name text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN p_name IS NULL
                OR char_length(btrim(p_name)) NOT BETWEEN 2 AND 80
                OR btrim(p_name) ~ '[<>:"/\\|?*\x01-\x1f\x7f-\x9f]'
                OR btrim(p_name) IN ('.', '..')
              THEN 'Ungültiger Firmenname' END
$$;

-- 3) Einrichten / fortsetzen
CREATE OR REPLACE FUNCTION marketing.pult_mandant_einrichten(p_name text, p_von text) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE v_name text; v_f text; m marketing.mandanten; v_basis text; v_id text; v_n int := 1;
BEGIN
  v_f := marketing._mandant_name_fehler(p_name);
  IF v_f IS NOT NULL THEN RAISE EXCEPTION '%', v_f; END IF;
  IF length(btrim(coalesce(p_von, ''))) = 0 THEN RAISE EXCEPTION 'Ohne Namen keine Einrichtung'; END IF;
  v_name := btrim(p_name);
  -- Gleichzeitige Einrichtungen hintereinander: sonst legten zwei Aufrufe mit demselben Namen zwei Firmen an
  PERFORM pg_advisory_xact_lock(hashtext('marketing.pult_mandant_einrichten'));
  IF EXISTS (SELECT 1 FROM marketing.mandanten WHERE lower(btrim(name)) = lower(v_name) AND aktiv) THEN
    RAISE EXCEPTION 'Diese Marke gibt es schon – bitte im normalen Marken-Chat weiterarbeiten'; END IF;
  SELECT * INTO m FROM marketing.mandanten
   WHERE lower(btrim(name)) = lower(v_name) AND einrichtung IS NOT NULL
   ORDER BY id LIMIT 1 FOR NO KEY UPDATE;
  IF FOUND THEN
    UPDATE marketing.mandanten SET einrichtung = 'laeuft' WHERE id = m.id;
    RETURN jsonb_build_object('id', m.id, 'name', m.name, 'fortgesetzt', true);
  END IF;
  IF EXISTS (SELECT 1 FROM marketing.mandanten WHERE lower(btrim(name)) = lower(v_name)) THEN
    RAISE EXCEPTION 'Eine inaktive Marke mit diesem Namen gibt es schon'; END IF;
  v_basis := marketing._mandant_id_aus_name(v_name);
  v_id := v_basis;
  WHILE EXISTS (SELECT 1 FROM marketing.mandanten WHERE id = v_id) LOOP
    v_n := v_n + 1;
    v_id := left(v_basis, 31 - char_length('_' || v_n)) || '_' || v_n;
  END LOOP;
  INSERT INTO marketing.mandanten (id, name, aktiv, verteiler, pflichtteil, einrichtung, eingerichtet_von)
  VALUES (v_id, v_name, false, '{}'::text[],
          '{"impressum":"","abmelde_hinweis":"","vermittlerangaben":""}'::jsonb, 'laeuft', btrim(p_von));
  RETURN jsonb_build_object('id', v_id, 'name', v_name, 'fortgesetzt', false);
END $$;

-- 4) Pflichtangaben und Stand
CREATE OR REPLACE FUNCTION marketing.pult_mandant_pflichtteil_setzen(
    p_mandant text, p_pflichtteil jsonb, p_stand jsonb DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE m marketing.mandanten;
BEGIN
  -- Pruefung vor der Sperre
  IF p_pflichtteil IS NOT NULL THEN
    IF jsonb_typeof(p_pflichtteil) <> 'object' THEN RAISE EXCEPTION 'Pflichtangaben muessen ein Objekt sein'; END IF;
    IF EXISTS (SELECT 1 FROM jsonb_object_keys(p_pflichtteil) k
                WHERE k NOT IN ('impressum','abmelde_hinweis','vermittlerangaben')) THEN
      RAISE EXCEPTION 'Pflichtangaben kennen nur impressum, abmelde_hinweis, vermittlerangaben'; END IF;
    IF EXISTS (SELECT 1 FROM jsonb_each(p_pflichtteil) e WHERE jsonb_typeof(e.value) <> 'string') THEN
      RAISE EXCEPTION 'Pflichtangaben muessen Text sein'; END IF;
    IF char_length(p_pflichtteil->>'impressum') > 2000 THEN
      RAISE EXCEPTION 'Das Impressum ist zu lang (hoechstens 2000 Zeichen)'; END IF;
    IF char_length(p_pflichtteil->>'abmelde_hinweis') > 500 THEN
      RAISE EXCEPTION 'Der Abmeldehinweis ist zu lang (hoechstens 500 Zeichen)'; END IF;
    IF char_length(p_pflichtteil->>'vermittlerangaben') > 2000 THEN
      RAISE EXCEPTION 'Die Vermittlerangaben sind zu lang (hoechstens 2000 Zeichen)'; END IF;
    IF length(btrim(coalesce(p_pflichtteil->>'abmelde_hinweis', ''))) > 0
       AND position('{abmeldelink}' IN p_pflichtteil->>'abmelde_hinweis') = 0 THEN
      RAISE EXCEPTION 'Der Abmeldehinweis braucht {abmeldelink}'; END IF;
  END IF;
  IF p_stand IS NOT NULL THEN
    IF jsonb_typeof(p_stand) <> 'object' THEN RAISE EXCEPTION 'Stand muss ein Objekt sein'; END IF;
    IF EXISTS (SELECT 1 FROM jsonb_object_keys(p_stand) k WHERE k NOT IN ('uebersprungen','vermittler')) THEN
      RAISE EXCEPTION 'Stand kennt nur uebersprungen und vermittler'; END IF;
    -- Schritt 2 (Pflichtangaben) und 7 (Bestaetigung) lassen sich nie ueberspringen
    -- (verschachtelt geprueft, damit jsonb_array_elements nie ein Nicht-Array sieht)
    IF p_stand ? 'uebersprungen' THEN
      IF jsonb_typeof(p_stand->'uebersprungen') <> 'array' THEN
        RAISE EXCEPTION 'uebersprungen muss eine Liste aus den Schritten 1, 3, 4, 5, 6 sein'; END IF;
      -- nur ganze Zahlen in Textform (3.0 oder 3e0 sind kein Schritt), jeder Schritt hoechstens einmal
      IF EXISTS (SELECT 1 FROM jsonb_array_elements(p_stand->'uebersprungen') s
                  WHERE jsonb_typeof(s) <> 'number' OR s::text NOT IN ('1','3','4','5','6')) THEN
        RAISE EXCEPTION 'uebersprungen muss eine Liste aus den Schritten 1, 3, 4, 5, 6 sein'; END IF;
      IF (SELECT count(*) <> count(DISTINCT s::text) FROM jsonb_array_elements(p_stand->'uebersprungen') s) THEN
        RAISE EXCEPTION 'Jeder Schritt darf in uebersprungen nur einmal stehen'; END IF;
    END IF;
    IF p_stand ? 'vermittler' AND jsonb_typeof(p_stand->'vermittler') NOT IN ('boolean','null') THEN
      RAISE EXCEPTION 'vermittler muss ja, nein oder offen sein'; END IF;
  END IF;
  SELECT * INTO m FROM marketing.mandanten WHERE id = p_mandant FOR NO KEY UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannte Firma'; END IF;
  IF m.einrichtung IS DISTINCT FROM 'laeuft' THEN RAISE EXCEPTION 'Keine laufende Einrichtung'; END IF;
  UPDATE marketing.mandanten
     SET pflichtteil = pflichtteil || coalesce(p_pflichtteil, '{}'::jsonb),
         einrichtung_stand = einrichtung_stand || coalesce(p_stand, '{}'::jsonb)
   WHERE id = m.id
  RETURNING pflichtteil, einrichtung_stand INTO m.pflichtteil, m.einrichtung_stand;
  RETURN jsonb_build_object('pflichtteil', m.pflichtteil, 'stand', m.einrichtung_stand);
END $$;

-- 5) Aktivieren
CREATE OR REPLACE FUNCTION marketing.pult_mandant_aktivieren(p_mandant text, p_von text) RETURNS boolean
LANGUAGE plpgsql AS $$
DECLARE m marketing.mandanten; v_f text;
BEGIN
  IF length(btrim(coalesce(p_von, ''))) = 0 THEN RAISE EXCEPTION 'Ohne Namen kein Aktivieren'; END IF;
  SELECT * INTO m FROM marketing.mandanten WHERE id = p_mandant FOR NO KEY UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannte Firma'; END IF;
  IF m.einrichtung IS DISTINCT FROM 'laeuft' THEN RAISE EXCEPTION 'Keine laufende Einrichtung'; END IF;
  v_f := marketing._mandant_name_fehler(m.name);
  IF v_f IS NOT NULL THEN RAISE EXCEPTION '%', v_f; END IF;
  IF EXISTS (SELECT 1 FROM marketing.mandanten
              WHERE lower(btrim(name)) = lower(btrim(m.name)) AND aktiv AND id <> m.id) THEN
    RAISE EXCEPTION 'Diese Marke gibt es schon – bitte im normalen Marken-Chat weiterarbeiten'; END IF;
  IF length(btrim(coalesce(m.pflichtteil->>'impressum', ''))) = 0 THEN
    RAISE EXCEPTION 'Das Impressum fehlt noch'; END IF;
  IF position('{abmeldelink}' IN coalesce(m.pflichtteil->>'abmelde_hinweis', '')) = 0 THEN
    RAISE EXCEPTION 'Der Abmeldehinweis braucht {abmeldelink}'; END IF;
  IF jsonb_typeof(m.einrichtung_stand->'vermittler') IS DISTINCT FROM 'boolean' THEN
    RAISE EXCEPTION 'Vermittler-Frage offen: Seid ihr Finanz- oder Versicherungsvermittler?'; END IF;
  IF (m.einrichtung_stand->>'vermittler')::boolean
     AND length(btrim(coalesce(m.pflichtteil->>'vermittlerangaben', ''))) = 0 THEN
    RAISE EXCEPTION 'Die Vermittlerangaben fehlen noch'; END IF;
  PERFORM marketing._marke_aufraeumen(m.id);
  IF EXISTS (SELECT 1 FROM marketing.marken_auftraege
              WHERE mandant = m.id AND status IN ('offen','in_arbeit') AND art <> 'wissen') THEN
    RAISE EXCEPTION 'Der Assistent arbeitet gerade'; END IF;
  UPDATE marketing.mandanten SET aktiv = true, einrichtung = NULL WHERE id = m.id;
  RETURN true;
END $$;

-- 6) Abbrechen
CREATE OR REPLACE FUNCTION marketing.pult_mandant_einrichtung_abbrechen(p_mandant text, p_von text) RETURNS boolean
LANGUAGE plpgsql AS $$
DECLARE m marketing.mandanten;
BEGIN
  IF length(btrim(coalesce(p_von, ''))) = 0 THEN RAISE EXCEPTION 'Ohne Namen kein Abbrechen'; END IF;
  SELECT * INTO m FROM marketing.mandanten WHERE id = p_mandant FOR NO KEY UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannte Firma'; END IF;
  IF m.einrichtung IS DISTINCT FROM 'laeuft' THEN RAISE EXCEPTION 'Keine laufende Einrichtung'; END IF;
  UPDATE marketing.mandanten SET einrichtung = 'abgebrochen' WHERE id = m.id;
  RETURN true;
END $$;

-- 7) Marken-Chat waehrend der Einrichtung: woertlich aus 066, nur "AND aktiv" erweitert
CREATE OR REPLACE FUNCTION marketing.pult_marke_anlegen(
    p_mandant text, p_nachricht text, p_kontext jsonb) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v_id uuid;
BEGIN
  PERFORM 1 FROM marketing.mandanten WHERE id = p_mandant AND (aktiv OR einrichtung = 'laeuft') FOR NO KEY UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannte oder inaktive Firma'; END IF;
  IF length(btrim(coalesce(p_nachricht, ''))) = 0 THEN RAISE EXCEPTION 'Ohne Nachricht kein Auftrag'; END IF;
  IF length(p_nachricht) > 2000 THEN
    RAISE EXCEPTION 'Die Nachricht ist zu lang (hoechstens 2000 Zeichen)'; END IF;
  IF p_kontext IS NOT NULL AND jsonb_typeof(p_kontext) <> 'object' THEN
    RAISE EXCEPTION 'Kontext muss ein Objekt sein'; END IF;
  PERFORM marketing._marke_aufraeumen(p_mandant);
  IF EXISTS (SELECT 1 FROM marketing.marken_auftraege
              WHERE mandant = p_mandant AND status IN ('offen','in_arbeit') AND art <> 'wissen') THEN
    RAISE EXCEPTION 'Der Assistent arbeitet gerade'; END IF;
  INSERT INTO marketing.marken_auftraege (mandant, art, nachricht, kontext)
  VALUES (p_mandant, 'chat', btrim(p_nachricht), coalesce(p_kontext, '{}'::jsonb))
  RETURNING id INTO v_id;
  RETURN v_id;
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_marke_bearbeitung_anlegen(p_mandant text, p_formular jsonb) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v_id uuid;
BEGIN
  PERFORM 1 FROM marketing.mandanten WHERE id = p_mandant AND (aktiv OR einrichtung = 'laeuft') FOR NO KEY UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannte oder inaktive Firma'; END IF;
  IF p_formular IS NULL OR jsonb_typeof(p_formular) <> 'object' THEN
    RAISE EXCEPTION 'Formular muss ein Objekt sein'; END IF;
  IF octet_length(p_formular::text) > 65536 THEN RAISE EXCEPTION 'Formular zu groß'; END IF;
  PERFORM marketing._marke_aufraeumen(p_mandant);
  IF EXISTS (SELECT 1 FROM marketing.marken_auftraege
              WHERE mandant = p_mandant AND status IN ('offen','in_arbeit') AND art <> 'wissen') THEN
    RAISE EXCEPTION 'Der Assistent arbeitet gerade'; END IF;
  INSERT INTO marketing.marken_auftraege (mandant, art, nachricht, kontext)
  VALUES (p_mandant, 'bearbeitung', 'Profil bearbeitet (Formular)',
          jsonb_build_object('formular', p_formular, 'woertlich', true))
  RETURNING id INTO v_id;
  RETURN v_id;
END $$;

-- woertlich aus 066; neu nur v_einrichtung/v_pflichtteil/v_stand und die erweiterte Rueckgabe
CREATE OR REPLACE FUNCTION marketing.pult_marke_naechster(p_frist interval, p_arten text[])
RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE a marketing.marken_auftraege; v_firma text; v_verlauf jsonb; v_vorschlag jsonb;
  v_einrichtung text; v_pflichtteil jsonb; v_stand jsonb;
BEGIN
  IF p_arten IS NOT NULL AND (cardinality(p_arten) = 0 OR EXISTS (
       SELECT 1 FROM unnest(p_arten) x WHERE x IS NULL OR x NOT IN ('chat','uebernehmen','bearbeitung','wissen'))) THEN
    RAISE EXCEPTION 'Unbekannte Auftragsart'; END IF;
  PERFORM marketing._marke_aufraeumen(NULL);
  -- p_arten NULL = Marken-Faden (alles ausser wissen), sonst genau die genannten Arten (Wissens-Faden:
  -- ARRAY['wissen']). Nie zwei Wissens-Laeufe derselben Firma zugleich.
  SELECT m.* INTO a FROM marketing.marken_auftraege m
   WHERE m.status = 'offen'
     AND (CASE WHEN p_arten IS NULL THEN m.art <> 'wissen' ELSE m.art = ANY (p_arten) END)
     AND NOT (m.art = 'wissen' AND EXISTS (SELECT 1 FROM marketing.marken_auftraege w
                                            WHERE w.mandant = m.mandant AND w.art = 'wissen'
                                              AND w.status = 'in_arbeit'))
   ORDER BY (m.art = 'wissen'), m.erstellt_am LIMIT 1 FOR UPDATE OF m SKIP LOCKED;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT name, einrichtung, pflichtteil, einrichtung_stand INTO v_firma, v_einrichtung, v_pflichtteil, v_stand
    FROM marketing.mandanten WHERE id = a.mandant;
  -- Verlauf: die letzten 10 fertigen Chat- und Bearbeitungs-Runden der Firma, aelteste zuerst
  SELECT coalesce(jsonb_agg(jsonb_build_object('nachricht', v.nachricht, 'antwort', v.antwort)
                            ORDER BY v.erstellt_am), '[]'::jsonb)
    INTO v_verlauf
    FROM (SELECT nachricht, antwort, erstellt_am FROM marketing.marken_auftraege
           WHERE mandant = a.mandant AND art IN ('chat','bearbeitung') AND status = 'fertig'
           ORDER BY erstellt_am DESC LIMIT 10) v;
  IF a.art = 'uebernehmen' THEN
    SELECT jsonb_build_object('id', id, 'vorschlag', vorschlag) INTO v_vorschlag
      FROM marketing.marken_vorschlaege WHERE id = a.vorschlag;
  ELSIF a.art IN ('chat','bearbeitung') THEN
    SELECT jsonb_build_object('id', id, 'vorschlag', vorschlag) INTO v_vorschlag
      FROM marketing.marken_vorschlaege WHERE mandant = a.mandant AND status = 'offen';
  END IF;
  UPDATE marketing.marken_auftraege
     SET status = 'in_arbeit', versuche = versuche + 1, vergeben_bis = now() + p_frist, geaendert_am = now()
   WHERE id = a.id;
  RETURN jsonb_build_object('id', a.id, 'art', a.art, 'mandant', a.mandant, 'firma', v_firma,
           'nachricht', a.nachricht, 'kontext', a.kontext, 'verlauf', v_verlauf,
           'vorschlag', v_vorschlag, 'einrichtung', v_einrichtung)
         || CASE WHEN v_einrichtung = 'laeuft'
                 THEN jsonb_build_object('pflichtteil', v_pflichtteil, 'einrichtung_stand', v_stand)
                 ELSE '{}'::jsonb END;
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_marke_uebernehmen(p_vorschlag uuid, p_von text, p_mandant text) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v_mandant text; v_status text; v_id uuid;
BEGIN
  IF length(btrim(coalesce(p_von, ''))) = 0 THEN RAISE EXCEPTION 'Ohne Namen kein Übernehmen'; END IF;
  SELECT mandant INTO v_mandant FROM marketing.marken_vorschlaege WHERE id = p_vorschlag;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannter Vorschlag'; END IF;
  IF v_mandant IS DISTINCT FROM p_mandant THEN
    RAISE EXCEPTION 'Der Vorschlag gehört zu einer anderen Firma – bitte neu laden'; END IF;
  PERFORM 1 FROM marketing.mandanten WHERE id = v_mandant AND (aktiv OR einrichtung = 'laeuft') FOR NO KEY UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannte oder inaktive Firma'; END IF;
  SELECT status INTO v_status FROM marketing.marken_vorschlaege WHERE id = p_vorschlag FOR UPDATE;
  IF v_status <> 'offen' THEN
    RAISE EXCEPTION 'Inzwischen gibt es ein neueres Profil – bitte neu laden'; END IF;
  PERFORM marketing._marke_aufraeumen(v_mandant);
  IF EXISTS (SELECT 1 FROM marketing.marken_auftraege
              WHERE mandant = v_mandant AND status IN ('offen','in_arbeit') AND art <> 'wissen') THEN
    RAISE EXCEPTION 'Der Assistent arbeitet gerade'; END IF;
  UPDATE marketing.marken_vorschlaege
     SET status = 'angenommen', entschieden_von = btrim(p_von), entschieden_am = now()
   WHERE id = p_vorschlag;
  INSERT INTO marketing.marken_auftraege (mandant, art, vorschlag)
  VALUES (v_mandant, 'uebernehmen', p_vorschlag)
  RETURNING id INTO v_id;
  RETURN v_id;
END $$;

-- woertlich aus 078, nur "AND aktiv" erweitert
CREATE OR REPLACE FUNCTION marketing.pult_marke_rueckgaengig(p_vid uuid, p_von text, p_mandant text) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v_mandant text; v marketing.marken_vorschlaege; v_liste jsonb; v_neu jsonb; v_id uuid;
BEGIN
  IF length(btrim(coalesce(p_von, ''))) = 0 THEN RAISE EXCEPTION 'Ohne Namen kein Rückgängig'; END IF;
  SELECT mandant INTO v_mandant FROM marketing.marken_vorschlaege WHERE id = p_vid;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannter Vorschlag'; END IF;
  IF v_mandant IS DISTINCT FROM p_mandant THEN
    RAISE EXCEPTION 'Der Vorschlag gehört zu einer anderen Firma – bitte neu laden'; END IF;
  PERFORM 1 FROM marketing.mandanten WHERE id = v_mandant AND (aktiv OR einrichtung = 'laeuft') FOR NO KEY UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannte oder inaktive Firma'; END IF;
  -- Erst aufraeumen, dann den Vorschlag lesen: ein toter Uebernahme-Auftrag setzt seinen Vorschlag hier
  -- wieder auf 'offen' - E7.1 sieht so den echten Stand (sonst wuerde unten das Original selbst 'ersetzt').
  PERFORM marketing._marke_aufraeumen(v_mandant);
  SELECT * INTO v FROM marketing.marken_vorschlaege WHERE id = p_vid FOR UPDATE;
  -- E7.1: angenommen und direkt (JSON-true, kein Text "true")
  IF v.status <> 'angenommen' OR (v.vorschlag->'direkt') IS DISTINCT FROM 'true'::jsonb THEN
    RAISE EXCEPTION 'Rückgängig geht nur für eine direkt übernommene Änderung'; END IF;
  -- E7.2: kein spaeterer angenommener Vorschlag der Firma (Gleichstand zaehlt als spaeter: fail-closed)
  IF EXISTS (SELECT 1 FROM marketing.marken_vorschlaege w
              WHERE w.mandant = v.mandant AND w.status = 'angenommen' AND w.id <> v.id
                AND w.erstellt_am >= v.erstellt_am) THEN
    RAISE EXCEPTION 'Inzwischen gibt es eine neuere Änderung – Rückgängig geht nur für die letzte.'; END IF;
  -- E7.3: kein laufender Marken-Auftrag ausser Wissens-Laeufen (woertlich wie pult_marke_anlegen;
  -- aufgeraeumt ist schon oben)
  IF EXISTS (SELECT 1 FROM marketing.marken_auftraege
              WHERE mandant = v.mandant AND status IN ('offen','in_arbeit') AND art <> 'wissen') THEN
    RAISE EXCEPTION 'Der Assistent arbeitet gerade'; END IF;
  -- Aenderungsliste: nicht leer, je Eintrag feld/vorher/nachher als Text (verschachtelt geprueft, damit
  -- jsonb_array_length nie ein Nicht-Array sieht)
  v_liste := v.vorschlag->'aenderungen';
  IF jsonb_typeof(v_liste) IS DISTINCT FROM 'array' THEN
    RAISE EXCEPTION 'Der Vorschlag hat keine gültige Änderungsliste'; END IF;
  IF jsonb_array_length(v_liste) = 0 OR EXISTS (
       SELECT 1 FROM jsonb_array_elements(v_liste) e
        WHERE jsonb_typeof(e) <> 'object'
           OR jsonb_typeof(e->'feld') IS DISTINCT FROM 'string'
           OR jsonb_typeof(e->'vorher') IS DISTINCT FROM 'string'
           OR jsonb_typeof(e->'nachher') IS DISTINCT FROM 'string') THEN
    RAISE EXCEPTION 'Der Vorschlag hat keine gültige Änderungsliste'; END IF;
  -- Gegen-Vorschlag: abschnitte {feld: vorher} (leerer Text loescht den Abschnitt), Liste vertauscht.
  -- Steht ein Feld mehrfach in der Liste, gewinnt sein ERSTES vorher (der Stand vor der Aenderung).
  SELECT jsonb_build_object(
           'abschnitte', jsonb_object_agg(e->>'feld', e->'vorher' ORDER BY i DESC),
           'aenderungen', jsonb_agg(jsonb_build_object('feld', e->'feld', 'vorher', e->'nachher',
                                                       'nachher', e->'vorher') ORDER BY i),
           'direkt', true,
           'rueckgaengig_von', v.id)
    INTO v_neu
    FROM jsonb_array_elements(v_liste) WITH ORDINALITY AS t(e, i);
  IF octet_length(v_neu::text) > 65536 THEN RAISE EXCEPTION 'Vorschlag zu groß'; END IF;
  UPDATE marketing.marken_vorschlaege SET status = 'ersetzt'
   WHERE mandant = v.mandant AND status = 'offen';
  INSERT INTO marketing.marken_vorschlaege (mandant, auftrag, vorschlag)
  VALUES (v.mandant, v.auftrag, v_neu)
  RETURNING id INTO v_id;
  -- Uebernahme genau wie vom Pult: angenommen + entschieden_von/am + Uebernahme-Auftrag (066)
  RETURN marketing.pult_marke_uebernehmen(v_id, p_von, p_mandant);
END $$;

COMMIT;
