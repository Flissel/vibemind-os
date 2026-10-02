-- RUNBOOK: Nach 060 NIE erneut einspielen: ueberschreibt die 060-Huellen (Sperre, Gestaltungspruefung).
-- Nach jedem Migrations-Replay verify_060 laufen lassen.
-- 060: Newsletter-Gestaltung und Assistent (sales-claw Spec 2026-10-02
-- newsletter-gestaltung-und-agent-design.md). Idempotent, eine Transaktion.
--   1) pult_bloecke_fehler kennt props.gestaltung (nur im Image-Block)
--   2) chat_auftraege: Warteschlange fuer den Assistenten am PC
--   3) pult_bloecke_speichern sperrt den Betreiber, solange der Assistent arbeitet
-- Die bisherigen Funktionen werden umbenannt (_058/_053) und von gleichnamigen
-- Huellen gerufen. plpgsql-Aufrufer (pult_vorlage_speichern, pult_bild_einsetzen,
-- pult_in_bloecke_uebernehmen, pult_inhalt_aus_vorlage, API) loesen den Namen zur
-- Laufzeit auf und treffen die Huellen. Nur die CHECK-Regeln binden die Funktion
-- per OID - sie werden unten auf die Huelle neu angelegt.
-- Rechte: die Originale haben weder SECURITY DEFINER noch SET search_path noch
-- eigene GRANTs (Default Privileges aus 003 gelten), die Huellen also auch nicht.
BEGIN;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                 WHERE n.nspname = 'marketing' AND p.proname = '_pult_bloecke_fehler_058') THEN
    ALTER FUNCTION marketing.pult_bloecke_fehler(jsonb) RENAME TO _pult_bloecke_fehler_058;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                 WHERE n.nspname = 'marketing' AND p.proname = '_pult_bloecke_speichern_053') THEN
    ALTER FUNCTION marketing.pult_bloecke_speichern(uuid, int, text, text, jsonb, text, boolean)
      RENAME TO _pult_bloecke_speichern_053;
  END IF;
END $$;

-- 1) Pruefer: Basis + Grobpruefung der Gestaltung (die Feinpruefung macht der Rechner)
CREATE OR REPLACE FUNCTION marketing.pult_bloecke_fehler(p jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE v_basis text; v_id text; v_b jsonb; v_g jsonb; v_alt jsonb;
BEGIN
  v_basis := marketing._pult_bloecke_fehler_058(p);
  IF v_basis IS NOT NULL THEN RETURN v_basis; END IF;
  FOR v_id, v_b IN SELECT key, value FROM jsonb_each(p) LOOP
    v_g := v_b #> '{data,props,gestaltung}';
    IF v_g IS NULL OR jsonb_typeof(v_g) = 'null' THEN CONTINUE; END IF;
    IF v_b->>'type' <> 'Image' THEN RETURN format('Gestaltung nur im Bild-Block (%s)', v_id); END IF;
    -- verschachtelt statt OR, damit jsonb_array_length nie ein Nicht-Array sieht
    IF jsonb_typeof(v_g) <> 'object' THEN RETURN format('Gestaltung ungültig in %s', v_id); END IF;
    IF v_g->'version' IS DISTINCT FROM '1'::jsonb
       OR coalesce(v_g->>'format', '') NOT IN ('quer','quadrat','hoch','banner')
       OR jsonb_typeof(v_g->'ebenen') IS DISTINCT FROM 'array' THEN
      RETURN format('Gestaltung ungültig in %s', v_id); END IF;
    IF jsonb_array_length(v_g->'ebenen') > 20 THEN RETURN format('Gestaltung ungültig in %s', v_id); END IF;
    v_alt := v_b #> '{data,props,alt}';
    IF jsonb_typeof(v_alt) IS DISTINCT FROM 'string' THEN RETURN format('Gestaltung ungültig in %s', v_id); END IF;
    IF length(v_alt #>> '{}') > 200 THEN RETURN format('Gestaltung ungültig in %s', v_id); END IF;
  END LOOP;
  RETURN NULL;
END $$;

-- CHECK-Regeln binden die Funktion per OID (zeigten nach dem Umbenennen auf
-- _pult_bloecke_fehler_058): neu auf die Huelle anlegen, prueft den Bestand.
ALTER TABLE marketing.inhalt_fassungen DROP CONSTRAINT IF EXISTS inhalt_fassungen_bloecke_gueltig;
ALTER TABLE marketing.inhalt_fassungen ADD CONSTRAINT inhalt_fassungen_bloecke_gueltig
  CHECK (format <> 'bloecke' OR marketing.pult_bloecke_fehler(bloecke) IS NULL);
ALTER TABLE marketing.newsletter_vorlagen DROP CONSTRAINT IF EXISTS newsletter_vorlagen_bloecke_gueltig;
ALTER TABLE marketing.newsletter_vorlagen ADD CONSTRAINT newsletter_vorlagen_bloecke_gueltig
  CHECK (marketing.pult_bloecke_fehler(bloecke) IS NULL);
ALTER TABLE marketing.newsletter_vorlagen_fassungen DROP CONSTRAINT IF EXISTS newsletter_vorlagen_fassungen_bloecke_gueltig;
ALTER TABLE marketing.newsletter_vorlagen_fassungen ADD CONSTRAINT newsletter_vorlagen_fassungen_bloecke_gueltig
  CHECK (marketing.pult_bloecke_fehler(bloecke) IS NULL);

-- 2) Chat-Auftraege
CREATE TABLE IF NOT EXISTS marketing.chat_auftraege (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    inhalt          uuid NOT NULL REFERENCES marketing.inhalte(id),
    art             text NOT NULL DEFAULT 'chat' CHECK (art IN ('chat','export')),
    nachricht       text NOT NULL DEFAULT '' CHECK (length(nachricht) <= 2000),
    kontext         jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(kontext) = 'object'),
    status          text NOT NULL DEFAULT 'offen' CHECK (status IN ('offen','in_arbeit','fertig','fehler')),
    antwort         text NOT NULL DEFAULT '',
    hinweise        jsonb NOT NULL DEFAULT '[]'::jsonb,
    ergebnis        jsonb NOT NULL DEFAULT '{}'::jsonb,
    fassung_vorher  int,
    fassung_nachher int,
    versuche        int NOT NULL DEFAULT 0,
    vergeben_bis    timestamptz,
    erstellt_am     timestamptz NOT NULL DEFAULT now(),
    geaendert_am    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chat_auftraege_inhalt_idx ON marketing.chat_auftraege (inhalt, erstellt_am DESC);
CREATE INDEX IF NOT EXISTS chat_auftraege_status_idx ON marketing.chat_auftraege (status, erstellt_am);
-- je Inhalt hoechstens EIN wartender oder laufender Auftrag
CREATE UNIQUE INDEX IF NOT EXISTS chat_auftraege_ein_laufender
  ON marketing.chat_auftraege (inhalt) WHERE status IN ('offen','in_arbeit');

-- Nicht abgeholt (2 min) => fehler; Vergabe abgelaufen => einmal neu, dann fehler.
CREATE OR REPLACE FUNCTION marketing.pult_chat_aufraeumen(p_inhalt uuid) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  UPDATE marketing.chat_auftraege
     SET status = 'fehler', antwort = 'Der Assistent läuft am PC und ist gerade aus',
         vergeben_bis = NULL, geaendert_am = now()
   WHERE status = 'offen' AND erstellt_am < now() - interval '2 minutes'
     AND (p_inhalt IS NULL OR inhalt = p_inhalt);
  UPDATE marketing.chat_auftraege
     SET status = CASE WHEN versuche < 2 THEN 'offen' ELSE 'fehler' END,
         antwort = CASE WHEN versuche < 2 THEN antwort ELSE 'Der Assistent ist nicht fertig geworden' END,
         erstellt_am = CASE WHEN versuche < 2 THEN now() ELSE erstellt_am END,
         vergeben_bis = NULL, geaendert_am = now()
   WHERE status = 'in_arbeit' AND vergeben_bis < now()
     AND (p_inhalt IS NULL OR inhalt = p_inhalt);
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_chat_anlegen(
    p_inhalt uuid, p_art text, p_nachricht text, p_kontext jsonb) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v_art text; v_status text; v_neueste int; v_id uuid;
BEGIN
  -- Inhalt sperren: serialisiert Anlegen und Handspeichern desselben Newsletters
  SELECT art, status INTO v_art, v_status FROM marketing.inhalte WHERE id = p_inhalt FOR UPDATE;
  IF v_art IS DISTINCT FROM 'newsletter' OR v_status IS DISTINCT FROM 'entwurf' THEN
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
  INSERT INTO marketing.chat_auftraege (inhalt, art, nachricht, kontext, fassung_vorher)
  VALUES (p_inhalt, p_art, btrim(coalesce(p_nachricht, '')), coalesce(p_kontext, '{}'::jsonb), v_neueste)
  RETURNING id INTO v_id;
  RETURN v_id;
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_chat_naechster(p_frist interval) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE a marketing.chat_auftraege; f record; i record; v_pflicht jsonb; v_verlauf jsonb;
BEGIN
  PERFORM marketing.pult_chat_aufraeumen(NULL);
  SELECT * INTO a FROM marketing.chat_auftraege WHERE status = 'offen'
   ORDER BY erstellt_am LIMIT 1 FOR UPDATE SKIP LOCKED;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT fassung, bloecke, felder INTO f FROM marketing.inhalt_fassungen
   WHERE inhalt = a.inhalt ORDER BY fassung DESC LIMIT 1;
  SELECT titel, mandant INTO i FROM marketing.inhalte WHERE id = a.inhalt;
  SELECT pflichtteil INTO v_pflicht FROM marketing.mandanten WHERE id = i.mandant;
  SELECT coalesce(jsonb_agg(jsonb_build_object('nachricht', v.nachricht, 'antwort', v.antwort)
                            ORDER BY v.erstellt_am), '[]'::jsonb)
    INTO v_verlauf
    FROM (SELECT nachricht, antwort, erstellt_am FROM marketing.chat_auftraege
           WHERE inhalt = a.inhalt AND art = 'chat' AND status IN ('fertig','fehler')
           ORDER BY erstellt_am DESC LIMIT 10) v;
  -- fassung_vorher = die Fassung, die der Assistent bekommt (wie grund_fassung bei Bildern):
  -- pult_chat_fertig speichert darauf; hat inzwischen jemand anderes gespeichert, scheitert es.
  UPDATE marketing.chat_auftraege
     SET status = 'in_arbeit', versuche = versuche + 1, vergeben_bis = now() + p_frist,
         fassung_vorher = f.fassung, geaendert_am = now()
   WHERE id = a.id;
  RETURN jsonb_build_object('id', a.id, 'inhalt', a.inhalt, 'art', a.art, 'nachricht', a.nachricht,
           'kontext', a.kontext, 'fassung', f.fassung, 'bloecke', f.bloecke,
           'betreff', f.felder->>'betreff', 'vorschautext', coalesce(f.felder->>'vorschautext', ''),
           'titel', i.titel, 'mandant', i.mandant, 'pflichtteil', coalesce(v_pflicht, '{}'::jsonb),
           'verlauf', v_verlauf);
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_chat_verlaengern(p_auftrag uuid, p_frist interval) RETURNS boolean
LANGUAGE plpgsql AS $$
BEGIN
  UPDATE marketing.chat_auftraege SET vergeben_bis = now() + p_frist, geaendert_am = now()
   WHERE id = p_auftrag AND status = 'in_arbeit';
  RETURN FOUND;
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_chat_fertig(
    p_auftrag uuid, p_antwort text, p_bloecke jsonb, p_hinweise jsonb, p_ergebnis jsonb) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE a marketing.chat_auftraege; f record; v_n int;
BEGIN
  SELECT * INTO a FROM marketing.chat_auftraege WHERE id = p_auftrag FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannter Auftrag'; END IF;
  IF a.status <> 'in_arbeit' OR a.vergeben_bis IS NULL OR a.vergeben_bis <= now() THEN
    RAISE EXCEPTION 'Auftrag ist nicht (mehr) in Arbeit'; END IF;
  IF p_hinweise IS NOT NULL AND jsonb_typeof(p_hinweise) <> 'array' THEN
    RAISE EXCEPTION 'Hinweise muessen ein Array sein'; END IF;
  IF p_ergebnis IS NOT NULL AND jsonb_typeof(p_ergebnis) <> 'object' THEN
    RAISE EXCEPTION 'Ergebnis muss ein Objekt sein'; END IF;
  IF p_bloecke IS NOT NULL THEN
    SELECT felder INTO f FROM marketing.inhalt_fassungen
     WHERE inhalt = a.inhalt ORDER BY fassung DESC LIMIT 1;
    v_n := marketing.pult_bloecke_speichern(a.inhalt, a.fassung_vorher, f.felder->>'betreff',
             coalesce(f.felder->>'vorschautext', ''), p_bloecke, 'agent', false);
  END IF;
  UPDATE marketing.chat_auftraege
     SET status = 'fertig', antwort = left(coalesce(p_antwort, ''), 4000),
         hinweise = coalesce(p_hinweise, '[]'::jsonb), ergebnis = coalesce(p_ergebnis, '{}'::jsonb),
         fassung_nachher = v_n, vergeben_bis = NULL, geaendert_am = now()
   WHERE id = a.id;
  RETURN jsonb_build_object('fassung', v_n);
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_chat_zurueck(p_auftrag uuid, p_antwort text) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE a marketing.chat_auftraege;
BEGIN
  SELECT * INTO a FROM marketing.chat_auftraege WHERE id = p_auftrag FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannter Auftrag'; END IF;
  IF a.status NOT IN ('offen','in_arbeit') THEN RETURN a.status; END IF;   -- schon erledigt: nichts zu tun
  UPDATE marketing.chat_auftraege
     SET status = 'fehler', antwort = left(coalesce(p_antwort, ''), 4000),
         vergeben_bis = NULL, geaendert_am = now()
   WHERE id = a.id;
  RETURN 'fehler';
END $$;

-- Entschiedene Inhalte: wartende und laufende Chat-Auftraege beenden.
CREATE OR REPLACE FUNCTION marketing._chat_auftraege_beenden() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status <> 'entwurf' AND OLD.status = 'entwurf' THEN
    UPDATE marketing.chat_auftraege SET status = 'fehler', antwort = 'Newsletter wurde entschieden',
           vergeben_bis = NULL, geaendert_am = now()
     WHERE inhalt = NEW.id AND status IN ('offen', 'in_arbeit');
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_chat_auftraege_beenden ON marketing.inhalte;
CREATE TRIGGER trg_chat_auftraege_beenden AFTER UPDATE OF status ON marketing.inhalte
  FOR EACH ROW EXECUTE FUNCTION marketing._chat_auftraege_beenden();

-- 3) Speichern: Betreiber gesperrt, solange der Assistent arbeitet
CREATE OR REPLACE FUNCTION marketing.pult_bloecke_speichern(
    p_inhalt uuid, p_basis int, p_betreff text, p_vorschautext text,
    p_bloecke jsonb, p_urheber text, p_als_kopie boolean) RETURNS int
LANGUAGE plpgsql AS $$
BEGIN
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

COMMIT;
