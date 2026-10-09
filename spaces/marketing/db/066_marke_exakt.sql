-- RUNBOOK: 066 ersetzt pult_gestalt_fehler, pult_marke_spiegeln, _marke_aufraeumen, pult_marke_anlegen,
-- pult_marke_naechster, pult_marke_vorschlag und pult_marke_uebernehmen aus 064. Nach einem Replay von 064
-- IMMER 066 erneut einspielen; danach verify_060 .. verify_066 zusammen ueber migration_probe.
-- 066: Marke exakt (sales-claw Spec 2026-10-09-marke-exakt-logo-wissen-design.md §1-§3). Idempotent, eine Transaktion.
--   1) marken_auftraege.art zusaetzlich 'bearbeitung' (Formular -> Agent) und 'wissen' (Rowboat-Lauf)
--   2) "ein laufender Auftrag je Firma" gilt fuer chat/uebernehmen/bearbeitung; Wissens-Laeufe zaehlen nicht mit,
--      von ihnen gibt es je Firma hoechstens EINEN WARTENDEN
--   3) pult_gestalt_fehler und pult_marke_spiegeln kennen logo_dunkel (wie logo: PNG/JPEG-data-URL <= 150 KB)
--   4) pult_marke_bearbeitung_anlegen: Formularfassung in kontext.formular, kontext.woertlich = true
--   5) Trigger: nach jeder erfolgreichen Uebernahme ein Wissens-Lauf; ein neuer ersetzt einen wartenden
--      (der alte endet 'fertig' "Ersetzt durch einen neueren Wissens-Lauf."), kontext.seit bleibt der frueheste
--      Beginn - der Arbeiter nimmt die Marke.md-Sicherung ab diesem Zeitpunkt als "altes Profil"
--   6) naechster: Wissens-Laeufe nach allem anderen und nie, solange einer derselben Firma laeuft; ein nicht
--      abgeholter Bearbeitungs-Auftrag stirbt nach 2 min wie ein Chat; ein Wissens-Lauf wartet auf den PC.
-- Sperrreihenfolge wie 064: mandanten -> marken_auftraege -> marken_vorschlaege.
BEGIN;

-- 1) Arten
ALTER TABLE marketing.marken_auftraege DROP CONSTRAINT IF EXISTS marken_auftraege_art_check;
ALTER TABLE marketing.marken_auftraege ADD CONSTRAINT marken_auftraege_art_check
  CHECK (art IN ('chat','uebernehmen','bearbeitung','wissen'));

-- 2) Indizes
DROP INDEX IF EXISTS marketing.marken_auftraege_ein_laufender;
CREATE UNIQUE INDEX marken_auftraege_ein_laufender ON marketing.marken_auftraege (mandant)
  WHERE status IN ('offen','in_arbeit') AND art <> 'wissen';
CREATE UNIQUE INDEX IF NOT EXISTS marken_auftraege_ein_wissen ON marketing.marken_auftraege (mandant)
  WHERE art = 'wissen' AND status = 'offen';

-- 3) Gestalt-Pruefung: woertlich aus 064, nur die logo_dunkel-Pruefung nach logo ergaenzt
CREATE OR REPLACE FUNCTION marketing.pult_gestalt_fehler(p jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE v_f text;
BEGIN
  v_f := marketing.gestalt_pruefen(p);           -- acht Farben, #rrggbb, Kontrastregeln (044)
  IF v_f IS NOT NULL THEN RETURN v_f; END IF;
  IF p ? 'schrift' AND NOT (p->>'schrift' IN ('system','serif','mono')) THEN
    RETURN 'schrift muss system, serif oder mono sein'; END IF;
  IF p ? 'abstand' AND NOT (p->>'abstand' IN ('eng','mittel','weit')) THEN
    RETURN 'abstand muss eng, mittel oder weit sein'; END IF;
  IF p ? 'rundung' THEN
    CASE jsonb_typeof(p->'rundung')
      WHEN 'number' THEN
        IF (p->>'rundung')::numeric < 0 OR (p->>'rundung')::numeric > 24 THEN
          RETURN 'rundung muss eine Zahl von 0 bis 24 sein';
        END IF;
      ELSE
        RETURN 'rundung muss eine Zahl von 0 bis 24 sein';
    END CASE;
  END IF;
  IF p ? 'kopf_text' AND length(p->>'kopf_text') > 120 THEN
    RETURN 'kopf_text hoechstens 120 Zeichen'; END IF;
  IF p ? 'fuss_text' AND length(p->>'fuss_text') > 300 THEN
    RETURN 'fuss_text hoechstens 300 Zeichen'; END IF;
  IF p ? 'logo' AND (p->>'logo' !~ '^data:image/(png|jpeg);base64,[A-Za-z0-9+/=]+$'
                     OR length(p->>'logo') > 204800) THEN
    RETURN 'logo muss ein PNG/JPEG unter 150 KB sein'; END IF;
  -- 066: dunkle Logo-Fassung, gleiche Regel wie logo
  IF p ? 'logo_dunkel' AND (p->>'logo_dunkel' !~ '^data:image/(png|jpeg);base64,[A-Za-z0-9+/=]+$'
                            OR length(p->>'logo_dunkel') > 204800) THEN
    RETURN 'logo_dunkel muss ein PNG/JPEG unter 150 KB sein'; END IF;
  IF p ? 'schriften' THEN
    IF jsonb_typeof(p->'schriften') <> 'object' THEN
      RETURN 'schriften muss genau anzeige und text haben'; END IF;
    IF (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p->'schriften') k)
       IS DISTINCT FROM ARRAY['anzeige','text'] THEN
      RETURN 'schriften muss genau anzeige und text haben'; END IF;
    IF jsonb_typeof(p #> '{schriften,anzeige}') IS DISTINCT FROM 'string'
       OR NOT (p #>> '{schriften,anzeige}' = ANY (marketing.marke_schriften())) THEN
      RETURN 'schriften.anzeige ist keine Schrift aus dem Register'; END IF;
    IF jsonb_typeof(p #> '{schriften,text}') IS DISTINCT FROM 'string'
       OR NOT (p #>> '{schriften,text}' = ANY (marketing.marke_schriften())) THEN
      RETURN 'schriften.text ist keine Schrift aus dem Register'; END IF;
  END IF;
  RETURN NULL;
END $$;

-- 6) Warteschlange
CREATE OR REPLACE FUNCTION marketing._marke_wissen_wartet(p_mandant text) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT EXISTS (SELECT 1 FROM marketing.marken_auftraege
                  WHERE mandant = p_mandant AND art = 'wissen' AND status = 'offen')
$$;

CREATE OR REPLACE FUNCTION marketing._marke_aufraeumen(p_mandant text) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  UPDATE marketing.marken_auftraege
     SET status = 'fehler', antwort = 'Der Assistent läuft am PC und ist gerade aus',
         vergeben_bis = NULL, geaendert_am = now()
   WHERE status = 'offen' AND art IN ('chat','bearbeitung') AND erstellt_am < now() - interval '2 minutes'
     AND (p_mandant IS NULL OR mandant = p_mandant);
  -- Abgelaufene Vergabe: einmal neu, dann fehler. Ein Wissens-Lauf mit wartendem Nachfolger endet als ersetzt
  -- (sonst stuenden zwei wartende Laeufe derselben Firma -> marken_auftraege_ein_wissen).
  WITH tot AS (
    UPDATE marketing.marken_auftraege a
       SET status = CASE WHEN a.art = 'wissen' AND marketing._marke_wissen_wartet(a.mandant) THEN 'fertig'
                         WHEN a.versuche < 2 THEN 'offen' ELSE 'fehler' END,
           antwort = CASE WHEN a.art = 'wissen' AND marketing._marke_wissen_wartet(a.mandant)
                            THEN 'Ersetzt durch einen neueren Wissens-Lauf.'
                          WHEN a.versuche < 2 THEN a.antwort ELSE 'Der Assistent ist nicht fertig geworden' END,
           erstellt_am = CASE WHEN a.art = 'wissen' AND marketing._marke_wissen_wartet(a.mandant) THEN a.erstellt_am
                              WHEN a.versuche < 2 THEN now() ELSE a.erstellt_am END,
           vergeben_bis = NULL, geaendert_am = now()
     WHERE a.status = 'in_arbeit' AND a.vergeben_bis < now()
       AND (p_mandant IS NULL OR a.mandant = p_mandant)
    RETURNING a.art, a.status, a.vorschlag)
  UPDATE marketing.marken_vorschlaege v
     SET status = 'offen', entschieden_von = NULL, entschieden_am = NULL
    FROM tot
   WHERE tot.art = 'uebernehmen' AND tot.status = 'fehler' AND v.id = tot.vorschlag
     AND v.status = 'angenommen';
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_marke_anlegen(
    p_mandant text, p_nachricht text, p_kontext jsonb) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v_id uuid;
BEGIN
  PERFORM 1 FROM marketing.mandanten WHERE id = p_mandant AND aktiv FOR NO KEY UPDATE;
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
  PERFORM 1 FROM marketing.mandanten WHERE id = p_mandant AND aktiv FOR NO KEY UPDATE;
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

CREATE OR REPLACE FUNCTION marketing.pult_marke_naechster(p_frist interval) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE a marketing.marken_auftraege; v_firma text; v_verlauf jsonb; v_vorschlag jsonb;
BEGIN
  PERFORM marketing._marke_aufraeumen(NULL);
  -- Wissens-Laeufe zuletzt (Chat und Uebernehmen warten auf den Betreiber), nie zwei derselben Firma zugleich
  SELECT m.* INTO a FROM marketing.marken_auftraege m
   WHERE m.status = 'offen'
     AND NOT (m.art = 'wissen' AND EXISTS (SELECT 1 FROM marketing.marken_auftraege w
                                            WHERE w.mandant = m.mandant AND w.art = 'wissen'
                                              AND w.status = 'in_arbeit'))
   ORDER BY (m.art = 'wissen'), m.erstellt_am LIMIT 1 FOR UPDATE OF m SKIP LOCKED;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT name INTO v_firma FROM marketing.mandanten WHERE id = a.mandant;
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
           'vorschlag', v_vorschlag);
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_marke_vorschlag(
    p_auftrag uuid, p_vorschlag jsonb, p_antwort text, p_hinweise jsonb) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE a marketing.marken_auftraege; v_id uuid;
BEGIN
  a := marketing._marke_auftrag_sperren(p_auftrag);
  IF a.status <> 'in_arbeit' OR a.vergeben_bis IS NULL OR a.vergeben_bis <= now() THEN
    RAISE EXCEPTION 'Auftrag ist nicht (mehr) in Arbeit'; END IF;
  IF a.art NOT IN ('chat','bearbeitung') THEN RAISE EXCEPTION 'Vorschlaege gibt es nur aus dem Chat'; END IF;
  IF p_vorschlag IS NULL OR jsonb_typeof(p_vorschlag) <> 'object' THEN
    RAISE EXCEPTION 'Vorschlag muss ein Objekt sein'; END IF;
  IF octet_length(p_vorschlag::text) > 65536 THEN RAISE EXCEPTION 'Vorschlag zu groß'; END IF;
  IF p_hinweise IS NOT NULL AND jsonb_typeof(p_hinweise) <> 'array' THEN
    RAISE EXCEPTION 'Hinweise muessen ein Array sein'; END IF;
  UPDATE marketing.marken_vorschlaege SET status = 'ersetzt'
   WHERE mandant = a.mandant AND status = 'offen';
  INSERT INTO marketing.marken_vorschlaege (mandant, auftrag, vorschlag)
  VALUES (a.mandant, a.id, p_vorschlag)
  RETURNING id INTO v_id;
  UPDATE marketing.marken_auftraege
     SET status = 'fertig', antwort = left(coalesce(p_antwort, ''), 4000),
         hinweise = coalesce(p_hinweise, '[]'::jsonb), vorschlag = v_id,
         vergeben_bis = NULL, geaendert_am = now()
   WHERE id = a.id;
  RETURN v_id;
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
  PERFORM 1 FROM marketing.mandanten WHERE id = v_mandant AND aktiv FOR NO KEY UPDATE;
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

-- 5) Wissens-Lauf nach jeder erfolgreichen Uebernahme (laeuft in pult_marke_fertig, die Firma ist gesperrt)
CREATE OR REPLACE FUNCTION marketing._marke_wissen_anlegen() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE w marketing.marken_auftraege; v_seit numeric := extract(epoch FROM NEW.erstellt_am)::numeric;  -- numeric: ein double wuerde im jsonb auf 15 Stellen runden
BEGIN
  SELECT * INTO w FROM marketing.marken_auftraege
   WHERE mandant = NEW.mandant AND art = 'wissen' AND status = 'offen' FOR UPDATE;
  IF FOUND THEN
    v_seit := least(v_seit, coalesce((w.kontext->>'seit')::numeric, v_seit));
    UPDATE marketing.marken_auftraege
       SET status = 'fertig', antwort = 'Ersetzt durch einen neueren Wissens-Lauf.', geaendert_am = now()
     WHERE id = w.id;
  END IF;
  INSERT INTO marketing.marken_auftraege (mandant, art, nachricht, kontext)
  VALUES (NEW.mandant, 'wissen', 'Wissen nach der Übernahme aktualisieren',
          jsonb_build_object('seit', v_seit, 'uebernahme', NEW.id));
  RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS marken_wissen_nach_uebernahme ON marketing.marken_auftraege;
CREATE TRIGGER marken_wissen_nach_uebernahme
  AFTER UPDATE OF status ON marketing.marken_auftraege
  FOR EACH ROW WHEN (NEW.art = 'uebernehmen' AND NEW.status = 'fertig' AND OLD.status IS DISTINCT FROM 'fertig')
  EXECUTE FUNCTION marketing._marke_wissen_anlegen();

-- 3b) Spiegel: woertlich aus 064, logo_dunkel als fuenfter Schluessel (auch beim Erben von 'dunkel' entfernt)
CREATE OR REPLACE FUNCTION marketing.pult_marke_spiegeln(
    p_mandant text, p_gestalt jsonb, p_stand text) RETURNS int
LANGUAGE plpgsql AS $$
DECLARE v_name text; v_alt jsonb; v_neu jsonb; v_f text; v_n int;
BEGIN
  IF p_gestalt IS NULL OR jsonb_typeof(p_gestalt) <> 'object' THEN
    RAISE EXCEPTION 'Gestalt muss ein Objekt sein'; END IF;
  IF EXISTS (SELECT 1 FROM jsonb_object_keys(p_gestalt) k
              WHERE k NOT IN ('akzent','flaeche','logo','logo_dunkel','schriften')) THEN
    RAISE EXCEPTION 'Spiegel kennt nur akzent, flaeche, logo, logo_dunkel, schriften'; END IF;
  PERFORM 1 FROM marketing.mandanten WHERE id = p_mandant FOR NO KEY UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannte Firma'; END IF;
  SELECT name, gestalt INTO v_name, v_alt FROM marketing.layout_vorlagen
   WHERE mandant = p_mandant AND inhaltsart = 'newsletter' AND standard AND art = 'layout' FOR UPDATE;
  IF v_name IS NULL THEN
    v_alt := (SELECT gestalt FROM marketing.layout_vorlagen WHERE name = 'dunkel')
             - ARRAY['logo','logo_dunkel','schriften','kopf_text','fuss_text'];
  END IF;
  SELECT coalesce(jsonb_object_agg(key, value), '{}'::jsonb) INTO v_neu
    FROM jsonb_each(v_alt || p_gestalt) WHERE jsonb_typeof(value) <> 'null';
  v_f := marketing.pult_gestalt_fehler(v_neu);
  IF v_f IS NOT NULL THEN
    INSERT INTO marketing.marken_spiegel (mandant, fehler) VALUES (p_mandant, 'Layout ungueltig: ' || v_f)
    ON CONFLICT (mandant) DO UPDATE SET fehler = EXCLUDED.fehler;
    RETURN NULL;
  END IF;
  IF v_name IS NULL THEN
    v_name := 'marke-' || replace(p_mandant, '_', '-');
    IF EXISTS (SELECT 1 FROM marketing.layout_vorlagen WHERE name = v_name) THEN
      IF NOT EXISTS (SELECT 1 FROM marketing.layout_vorlagen
                      WHERE name = v_name AND mandant = p_mandant AND art = 'layout') THEN
        RAISE EXCEPTION 'Layout % gehoert einer anderen Firma', v_name; END IF;
      v_n := marketing.pult_layout_speichern(v_name, v_neu, 'marke');
      PERFORM marketing.pult_layout_als_standard(v_name);
    ELSE
      INSERT INTO marketing.layout_vorlagen (name, beschreibung, gestalt, status, vorgeschlagen_von,
             entschieden_von, entschieden_am, mandant, inhaltsart, standard)
      VALUES (v_name, 'Aus der Marke der Firma (Marke.md)', v_neu, 'freigegeben', 'marke',
              'marke', now(), p_mandant, 'newsletter', true)
      RETURNING fassung INTO v_n;
    END IF;
  ELSIF v_neu = v_alt THEN
    SELECT fassung INTO v_n FROM marketing.layout_vorlagen WHERE name = v_name;
  ELSE
    v_n := marketing.pult_layout_speichern(v_name, v_neu, 'marke');
  END IF;
  INSERT INTO marketing.marken_spiegel (mandant, stand, gespiegelt_am, fehler)
  VALUES (p_mandant, left(coalesce(p_stand, ''), 200), now(), NULL)
  ON CONFLICT (mandant) DO UPDATE
    SET stand = EXCLUDED.stand, gespiegelt_am = EXCLUDED.gespiegelt_am, fehler = NULL;
  RETURN v_n;
END $$;

COMMIT;
