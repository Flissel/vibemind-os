-- RUNBOOK: Nach 063 NIE 051/053/056-061 erneut einspielen (ebenso 050 und 054: sie ueberschreiben
-- pult_fassung_speichern) - sonst fehlen die Freigabe-Sperren. Nach jedem Replay verify_060,
-- verify_061, verify_062 und verify_063 zusammen ueber migration_probe laufen lassen.
-- 063: Newsletter-Freigabe (sales-claw Spec 2026-10-07-newsletter-freigabe-und-entwurfsseite-design.md
-- §1/§2, Plan Task 1, Ruling R1). Idempotent, eine Transaktion.
--   1) marketing.inhalte: Status 'eingereicht', Einreich-Felder; eingereicht braucht keine Entscheidung
--   2) marketing.rueckmeldungen: Kommentare beim Zurueckgeben (offen bis zum naechsten Einreichen)
--   3) Neue Uebergaenge: pult_einreichen, pult_zurueckziehen, pult_zurueckgeben
--   4) pult_entscheiden: freigeben nur aus eingereicht (eingereichte = neueste Fassung),
--      ablehnen aus entwurf und eingereicht
--   5) Sperren bei 'eingereicht' ("Liegt zur Freigabe – erst zurückziehen")
-- Ersetzte Funktionen (jeweils die zuletzt gueltige Definition woertlich, nur die Status-Pruefung ergaenzt):
--   pult_entscheiden(uuid, int, text, text, text)                      aus 051 (Regeln neu, s. 4)
--   pult_fassung_speichern(uuid, jsonb, text, text)                    aus 054
--   pult_bloecke_speichern(uuid, int, text, text, jsonb, text, boolean) Huelle aus 060
--                        (das Original _pult_bloecke_speichern_053 bleibt unveraendert)
--   pult_chat_anlegen(uuid, text, text, jsonb)                         aus 061
--   pult_chat_vormerken(uuid, text, jsonb)                             aus 061
--   pult_bild_auftrag(uuid, text, boolean, text, text)                 aus 056 (die 7-arg-Huelle aus 059
--                        ruft sie und bleibt unveraendert)
--   pult_bild_einsetzen(uuid, jsonb, text)                             aus 059 (die 4-arg-Huelle aus 057
--                        ruft sie und bleibt unveraendert)
-- Sperrreihenfolge wie 061: erst marketing.inhalte, dann Auftraege.
-- Die Trigger aus 056/060/061 (_bild_auftraege_verwerfen, _chat_auftraege_beenden) feuern bei
-- entwurf -> eingereicht: offene Chat-Auftraege kann es dann nicht geben (einreichen lehnt ab), eine
-- liegengebliebene Chat-Vormerkung ('wartet') wird beendet. Wartende Bild-Auftraege verwirft
-- pult_einreichen selbst (R4, Befund "Newsletter eingereicht"); nur 'in_arbeit' sperrt.
-- Rechte: wie 060 - keine eigenen GRANTs, Default Privileges aus 003 gelten.
BEGIN;

-- 1) inhalte: Spalten und Regeln
ALTER TABLE marketing.inhalte
  ADD COLUMN IF NOT EXISTS eingereichte_fassung int,
  ADD COLUMN IF NOT EXISTS eingereicht_am       timestamptz,
  ADD COLUMN IF NOT EXISTS eingereicht_von      text;

-- Status-Regel: Name nachschlagen (050 legte sie inline an), entfernen, neu anlegen
DO $$ DECLARE v_name text; BEGIN
  FOR v_name IN SELECT conname FROM pg_constraint
                 WHERE conrelid = 'marketing.inhalte'::regclass AND contype = 'c'
                   AND pg_get_constraintdef(oid) LIKE '%abgelehnt%' LOOP
    EXECUTE format('ALTER TABLE marketing.inhalte DROP CONSTRAINT %I', v_name);
  END LOOP;
END $$;
ALTER TABLE marketing.inhalte ADD CONSTRAINT inhalte_status_check
  CHECK (status IN ('entwurf','eingereicht','freigegeben','abgelehnt'));

ALTER TABLE marketing.inhalte DROP CONSTRAINT IF EXISTS inhalte_entscheid_hat_urheber;
ALTER TABLE marketing.inhalte ADD CONSTRAINT inhalte_entscheid_hat_urheber
  CHECK (status IN ('entwurf','eingereicht') OR (entschieden_von IS NOT NULL AND entschieden_am IS NOT NULL));

ALTER TABLE marketing.inhalte DROP CONSTRAINT IF EXISTS inhalte_einreichung_vollstaendig;
ALTER TABLE marketing.inhalte ADD CONSTRAINT inhalte_einreichung_vollstaendig
  CHECK (status <> 'eingereicht'
         OR (eingereichte_fassung IS NOT NULL AND eingereicht_am IS NOT NULL AND eingereicht_von IS NOT NULL));

-- 2) Rueckmeldungen
CREATE TABLE IF NOT EXISTS marketing.rueckmeldungen (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    inhalt      uuid NOT NULL REFERENCES marketing.inhalte(id) ON DELETE CASCADE,
    fassung     int  NOT NULL,
    text        text NOT NULL CHECK (length(btrim(text)) BETWEEN 1 AND 2000),
    von         text NOT NULL,
    am          timestamptz NOT NULL DEFAULT now(),
    erledigt_am timestamptz
);
CREATE INDEX IF NOT EXISTS rueckmeldungen_offen_idx
  ON marketing.rueckmeldungen (inhalt) WHERE erledigt_am IS NULL;

-- 3a) Einreichen: entwurf -> eingereicht (neueste Fassung); erledigt offene Rueckmeldungen
CREATE OR REPLACE FUNCTION marketing.pult_einreichen(p_inhalt uuid, p_von text) RETURNS int
LANGUAGE plpgsql AS $$
DECLARE v_status text; v_neueste int;
BEGIN
  IF length(btrim(coalesce(p_von, ''))) = 0 THEN
    RAISE EXCEPTION 'Ohne Namen kein Einreichen'; END IF;
  -- Sperrreihenfolge wie 061: erst der Inhalt, dann die Auftraege
  SELECT status INTO v_status FROM marketing.inhalte WHERE id = p_inhalt FOR UPDATE;
  IF v_status IS NULL THEN RAISE EXCEPTION 'Unbekannter Inhalt'; END IF;
  IF v_status = 'eingereicht' THEN RAISE EXCEPTION 'Liegt schon zur Freigabe'; END IF;
  IF v_status <> 'entwurf' THEN RAISE EXCEPTION 'Schon entschieden (%)', v_status; END IF;
  -- tote Chat-Auftraege (PC aus, Vergabe abgelaufen) sperren nicht
  PERFORM marketing.pult_chat_aufraeumen(p_inhalt);
  IF EXISTS (SELECT 1 FROM marketing.chat_auftraege WHERE inhalt = p_inhalt AND status IN ('offen','in_arbeit')) THEN
    RAISE EXCEPTION 'Der Assistent arbeitet gerade'; END IF;
  -- R4: nur ein laufender Bild-Auftrag sperrt; wartende werden unten verworfen
  IF EXISTS (SELECT 1 FROM marketing.bild_auftraege WHERE inhalt = p_inhalt AND status = 'in_arbeit') THEN
    RAISE EXCEPTION 'Ein Bild wird gerade erzeugt'; END IF;
  SELECT max(fassung) INTO v_neueste FROM marketing.inhalt_fassungen WHERE inhalt = p_inhalt;
  IF v_neueste IS NULL THEN RAISE EXCEPTION 'Ohne Fassung gibt es nichts einzureichen'; END IF;
  -- vor dem Statuswechsel: sonst verwirft sie zuerst der 056-Trigger mit "Inhalt entschieden"
  UPDATE marketing.bild_auftraege
     SET status = 'verworfen', befund = 'Newsletter eingereicht', vergeben_bis = NULL, geaendert_am = now()
   WHERE inhalt = p_inhalt AND status = 'offen';
  UPDATE marketing.inhalte
     SET status = 'eingereicht', eingereichte_fassung = v_neueste, eingereicht_am = now(), eingereicht_von = p_von
   WHERE id = p_inhalt;
  UPDATE marketing.rueckmeldungen SET erledigt_am = now()
   WHERE inhalt = p_inhalt AND erledigt_am IS NULL;
  RETURN v_neueste;
END $$;

-- 3b) Zurueckziehen: eingereicht -> entwurf, ohne Rueckmeldung
CREATE OR REPLACE FUNCTION marketing.pult_zurueckziehen(p_inhalt uuid, p_von text) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE v_status text;
BEGIN
  SELECT status INTO v_status FROM marketing.inhalte WHERE id = p_inhalt FOR UPDATE;
  IF v_status IS DISTINCT FROM 'eingereicht' THEN
    RAISE EXCEPTION 'Schon entschieden (%)', coalesce(v_status, 'unbekannt'); END IF;
  UPDATE marketing.inhalte
     SET status = 'entwurf', eingereichte_fassung = NULL, eingereicht_am = NULL, eingereicht_von = NULL
   WHERE id = p_inhalt;
  RETURN 'entwurf';
END $$;

-- 3c) Zurueckgeben: eingereicht -> entwurf + Rueckmeldung (Kommentar Pflicht, hoechstens 2000 Zeichen)
CREATE OR REPLACE FUNCTION marketing.pult_zurueckgeben(
    p_inhalt uuid, p_fassung int, p_von text, p_text text) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v_status text; v_eingereicht int; v_neueste int; v_id uuid;
BEGIN
  IF length(btrim(coalesce(p_text, ''))) = 0 THEN RAISE EXCEPTION 'Bitte sag kurz, was fehlt'; END IF;
  IF length(btrim(p_text)) > 2000 THEN
    RAISE EXCEPTION 'Der Kommentar ist zu lang (hoechstens 2000 Zeichen)'; END IF;
  IF length(btrim(coalesce(p_von, ''))) = 0 THEN RAISE EXCEPTION 'Ohne Namen kein Zurückgeben'; END IF;
  SELECT status, eingereichte_fassung INTO v_status, v_eingereicht
    FROM marketing.inhalte WHERE id = p_inhalt FOR UPDATE;
  IF v_status IS NULL OR v_status NOT IN ('entwurf','eingereicht') THEN
    RAISE EXCEPTION 'Schon entschieden (%)', coalesce(v_status, 'unbekannt'); END IF;
  SELECT max(fassung) INTO v_neueste FROM marketing.inhalt_fassungen WHERE inhalt = p_inhalt;
  IF p_fassung IS DISTINCT FROM v_neueste THEN
    RAISE EXCEPTION 'Inzwischen gibt es Fassung % – bitte neu laden', v_neueste; END IF;
  IF v_status <> 'eingereicht' THEN RAISE EXCEPTION 'Schon entschieden (%)', v_status; END IF;
  IF p_fassung IS DISTINCT FROM v_eingereicht THEN
    RAISE EXCEPTION 'Inzwischen gibt es Fassung % – bitte neu laden', v_neueste; END IF;
  INSERT INTO marketing.rueckmeldungen (inhalt, fassung, text, von)
  VALUES (p_inhalt, p_fassung, btrim(p_text), p_von)
  RETURNING id INTO v_id;
  UPDATE marketing.inhalte
     SET status = 'entwurf', eingereichte_fassung = NULL, eingereicht_am = NULL, eingereicht_von = NULL
   WHERE id = p_inhalt;
  RETURN v_id;
END $$;

-- 4) Entscheiden (aus 051): freigeben nur aus eingereicht und nur die eingereichte (= neueste)
--    Fassung; ablehnen (= Verwerfen) aus entwurf und eingereicht. Meldung jetzt "– bitte neu laden".
CREATE OR REPLACE FUNCTION marketing.pult_entscheiden(
    p_inhalt uuid, p_fassung int, p_urteil text, p_von text, p_grund text) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE v_status text; v_neueste int; v_eingereicht int;
BEGIN
  IF p_urteil NOT IN ('freigeben','ablehnen') THEN
    RAISE EXCEPTION 'Urteil muss freigeben oder ablehnen sein'; END IF;
  IF length(btrim(coalesce(p_von, ''))) = 0 THEN
    RAISE EXCEPTION 'Ohne Namen kein Urteil'; END IF;
  SELECT status, eingereichte_fassung INTO v_status, v_eingereicht
    FROM marketing.inhalte WHERE id = p_inhalt FOR UPDATE;
  IF v_status IS NULL OR v_status NOT IN ('entwurf','eingereicht') THEN
    RAISE EXCEPTION 'Schon entschieden (%)', coalesce(v_status, 'unbekannt'); END IF;
  SELECT max(fassung) INTO v_neueste FROM marketing.inhalt_fassungen WHERE inhalt = p_inhalt;
  IF v_neueste IS NULL THEN
    RAISE EXCEPTION 'Ohne Fassung gibt es nichts freizugeben'; END IF;
  IF p_fassung <> v_neueste THEN
    RAISE EXCEPTION 'Inzwischen gibt es Fassung % – bitte neu laden', v_neueste; END IF;
  -- 063: freigeben nur, was eingereicht wurde - und genau diese Fassung
  IF p_urteil = 'freigeben' AND v_status <> 'eingereicht' THEN
    RAISE EXCEPTION 'Schon entschieden (%)', v_status; END IF;
  IF p_urteil = 'freigeben' AND p_fassung IS DISTINCT FROM v_eingereicht THEN
    RAISE EXCEPTION 'Inzwischen gibt es Fassung % – bitte neu laden', v_neueste; END IF;
  UPDATE marketing.inhalte
     SET status = CASE p_urteil WHEN 'freigeben' THEN 'freigegeben' ELSE 'abgelehnt' END,
         freigegebene_fassung = CASE p_urteil WHEN 'freigeben' THEN p_fassung END,
         entschieden_von = p_von, entschieden_am = now(), grund = p_grund
   WHERE id = p_inhalt
  RETURNING status INTO v_status;
  RETURN v_status;
END $$;

-- 5a) Feld-Fassungen (aus 054) + Sperre bei eingereicht
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
    -- 063: eingereicht hat eine eigene Meldung
    IF (SELECT status FROM marketing.inhalte WHERE id = p_inhalt) = 'eingereicht' THEN
      RAISE EXCEPTION 'Liegt zur Freigabe – erst zurückziehen'; END IF;
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

-- 5b) Speichern (Huelle aus 060) + Sperre bei eingereicht fuer jeden Urheber (Mensch und Agent)
CREATE OR REPLACE FUNCTION marketing.pult_bloecke_speichern(
    p_inhalt uuid, p_basis int, p_betreff text, p_vorschautext text,
    p_bloecke jsonb, p_urheber text, p_als_kopie boolean) RETURNS int
LANGUAGE plpgsql AS $$
DECLARE v_status text;
BEGIN
  -- 063: Sperre zuerst, dann der Status (wie unten fuer den Betreiber)
  SELECT status INTO v_status FROM marketing.inhalte WHERE id = p_inhalt FOR UPDATE;
  IF v_status = 'eingereicht' THEN RAISE EXCEPTION 'Liegt zur Freigabe – erst zurückziehen'; END IF;
  IF p_urheber = 'betreiber' THEN
    -- Sperre vor der Pruefung (wie pult_chat_anlegen), damit kein Auftrag dazwischenrutscht
    PERFORM 1 FROM marketing.inhalte WHERE id = p_inhalt FOR UPDATE;
    -- Abgelaufene Vergabe sperrt nicht (wieder moeglich nach 5 min). Ein seit
    -- 2 min nicht abgeholter Auftrag ist tot (Arbeiter aus, s. pult_chat_aufraeumen)
    -- und sperrt auch nicht - sonst bliebe der Betreiber bei ausgeschaltetem PC
    -- gesperrt, bis jemand aufraeumt.
    IF EXISTS (SELECT 1 FROM marketing.chat_auftraege
                WHERE inhalt = p_inhalt AND status IN ('offen','in_arbeit')
                  AND (vergeben_bis IS NULL OR vergeben_bis > now())
                  AND NOT (status = 'offen' AND erstellt_am < now() - interval '2 minutes')) THEN
      RAISE EXCEPTION 'Der Assistent arbeitet gerade'; END IF;
  END IF;
  RETURN marketing._pult_bloecke_speichern_053(p_inhalt, p_basis, p_betreff, p_vorschautext,
                                               p_bloecke, p_urheber, p_als_kopie);
END $$;

-- 5c) Anlegen (aus 061): chat nur bei entwurf (eingereicht: eigene Meldung);
--     export bei entwurf, eingereicht und freigegeben (ein Export aendert keine Fassung)
CREATE OR REPLACE FUNCTION marketing.pult_chat_anlegen(
    p_inhalt uuid, p_art text, p_nachricht text, p_kontext jsonb) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v_art text; v_status text; v_neueste int; v_id uuid;
BEGIN
  -- Inhalt sperren: serialisiert Anlegen und Handspeichern desselben Newsletters
  SELECT art, status INTO v_art, v_status FROM marketing.inhalte WHERE id = p_inhalt FOR UPDATE;
  IF v_art = 'newsletter' AND v_status = 'eingereicht' AND p_art IS DISTINCT FROM 'export' THEN
    RAISE EXCEPTION 'Liegt zur Freigabe – erst zurückziehen'; END IF;
  IF v_art IS DISTINCT FROM 'newsletter'
     OR NOT (v_status = 'entwurf' OR (p_art = 'export' AND v_status IN ('eingereicht','freigegeben'))) THEN
    RAISE EXCEPTION 'Nur Newsletter-Entwürfe'; END IF;
  PERFORM marketing.pult_chat_aufraeumen(p_inhalt);
  IF p_art IS NULL OR p_art NOT IN ('chat','export') THEN RAISE EXCEPTION 'Art muss chat oder export sein'; END IF;
  IF p_art = 'chat' AND length(btrim(coalesce(p_nachricht, ''))) = 0 THEN
    RAISE EXCEPTION 'Ohne Nachricht kein Auftrag'; END IF;
  IF length(coalesce(p_nachricht, '')) > 2000 THEN
    RAISE EXCEPTION 'Die Nachricht ist zu lang (hoechstens 2000 Zeichen)'; END IF;
  IF p_kontext IS NOT NULL AND jsonb_typeof(p_kontext) <> 'object' THEN
    RAISE EXCEPTION 'Kontext muss ein Objekt sein'; END IF;
  IF EXISTS (SELECT 1 FROM marketing.chat_auftraege WHERE inhalt = p_inhalt AND status IN ('offen','in_arbeit')) THEN
    RAISE EXCEPTION 'Der Assistent arbeitet gerade'; END IF;
  SELECT max(fassung) INTO v_neueste FROM marketing.inhalt_fassungen WHERE inhalt = p_inhalt;
  IF p_art = 'chat' THEN
    UPDATE marketing.chat_auftraege
       SET nachricht = btrim(p_nachricht), kontext = coalesce(p_kontext, '{}'::jsonb),
           status = 'offen', erstellt_am = now(), stopp = NULL, stopp_am = NULL,
           fassung_vorher = v_neueste, geaendert_am = now()
     WHERE inhalt = p_inhalt AND status = 'wartet'
    RETURNING id INTO v_id;
    IF v_id IS NOT NULL THEN RETURN v_id; END IF;
  END IF;
  INSERT INTO marketing.chat_auftraege (inhalt, art, nachricht, kontext, fassung_vorher)
  VALUES (p_inhalt, p_art, btrim(coalesce(p_nachricht, '')), coalesce(p_kontext, '{}'::jsonb), v_neueste)
  RETURNING id INTO v_id;
  RETURN v_id;
END $$;

-- 5d) Vormerken (aus 061) + Sperre bei eingereicht
CREATE OR REPLACE FUNCTION marketing.pult_chat_vormerken(
    p_inhalt uuid, p_nachricht text, p_kontext jsonb) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE v_art text; v_status text; v_id uuid;
BEGIN
  -- Inhalt sperren: serialisiert mit Anlegen, Stopp, fertig (samt Freigabe der Vormerkung),
  -- Stopp-Abschluss und Handspeichern - die sperren alle zuerst den Inhalt
  SELECT art, status INTO v_art, v_status FROM marketing.inhalte WHERE id = p_inhalt FOR UPDATE;
  IF v_art = 'newsletter' AND v_status = 'eingereicht' THEN
    RAISE EXCEPTION 'Liegt zur Freigabe – erst zurückziehen'; END IF;
  IF v_art IS DISTINCT FROM 'newsletter' OR v_status IS DISTINCT FROM 'entwurf' THEN
    RAISE EXCEPTION 'Nur Newsletter-Entwürfe'; END IF;
  IF length(btrim(coalesce(p_nachricht, ''))) = 0 THEN RAISE EXCEPTION 'Ohne Nachricht kein Auftrag'; END IF;
  IF length(p_nachricht) > 2000 THEN
    RAISE EXCEPTION 'Die Nachricht ist zu lang (hoechstens 2000 Zeichen)'; END IF;
  IF p_kontext IS NOT NULL AND jsonb_typeof(p_kontext) <> 'object' THEN
    RAISE EXCEPTION 'Kontext muss ein Objekt sein'; END IF;
  PERFORM marketing.pult_chat_aufraeumen(p_inhalt);
  IF NOT EXISTS (SELECT 1 FROM marketing.chat_auftraege WHERE inhalt = p_inhalt AND status IN ('offen','in_arbeit')) THEN
    -- Liegengebliebene Vormerkung (nach fehler/Stopp) uebernehmen und starten, damit kein
    -- zweiter Auftrag stehen bleibt, den ein spaeteres fertig ungefragt freigaebe.
    UPDATE marketing.chat_auftraege
       SET nachricht = btrim(p_nachricht), kontext = coalesce(p_kontext, '{}'::jsonb),
           status = 'offen', erstellt_am = now(), stopp = NULL, stopp_am = NULL,
           fassung_vorher = (SELECT max(fassung) FROM marketing.inhalt_fassungen WHERE inhalt = p_inhalt),
           geaendert_am = now()
     WHERE inhalt = p_inhalt AND status = 'wartet'
    RETURNING id INTO v_id;
    IF v_id IS NOT NULL THEN RETURN jsonb_build_object('id', v_id, 'status', 'offen'); END IF;
    v_id := marketing.pult_chat_anlegen(p_inhalt, 'chat', p_nachricht, p_kontext);
    RETURN jsonb_build_object('id', v_id, 'status', 'offen');
  END IF;
  UPDATE marketing.chat_auftraege
     SET nachricht = btrim(p_nachricht), kontext = coalesce(p_kontext, '{}'::jsonb), geaendert_am = now()
   WHERE inhalt = p_inhalt AND status = 'wartet'
  RETURNING id INTO v_id;
  IF v_id IS NULL THEN
    INSERT INTO marketing.chat_auftraege (inhalt, art, nachricht, kontext, status)
    VALUES (p_inhalt, 'chat', btrim(p_nachricht), coalesce(p_kontext, '{}'::jsonb), 'wartet')
    RETURNING id INTO v_id;
  END IF;
  RETURN jsonb_build_object('id', v_id, 'status', 'wartet');
END $$;

-- 5e) Bild-Auftrag (5-arg aus 056) + Sperre bei eingereicht; die 7-arg-Huelle (059) ruft diese
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
  IF v_status = 'eingereicht' THEN RAISE EXCEPTION 'Liegt zur Freigabe – erst zurückziehen'; END IF;
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

-- 5f) Einsetzen (3-arg aus 059), R1: bei eingereicht/freigegeben (wie bei jedem Nicht-Entwurf)
--     keine neue Fassung - der Auftrag wird verworfen; bei eingereicht mit eigenem Befund.
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
    UPDATE marketing.bild_auftraege
       SET status = 'verworfen',
           befund = CASE WHEN v_status = 'eingereicht' THEN 'Liegt zur Freigabe – erst zurückziehen'
                         ELSE 'Inhalt inzwischen entschieden' END,
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
