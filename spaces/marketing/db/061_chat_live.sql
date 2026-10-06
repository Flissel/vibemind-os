-- 061: Newsletter-Assistent live (sales-claw Spec 2026-10-02 newsletter-agent-live-design.md
-- §2.2/§3). Idempotent, eine Transaktion. Baut auf 060 auf.
--   1) chat_auftraege: Zwischenstand, Schritt, Stopp; Status zusaetzlich 'wartet' (Vormerkung)
--   2) Vormerken / Loeschen / Starten: hoechstens ein 'wartet' je Inhalt
--   3) Zwischenstand vom Arbeiter (verlaengert die Vergabe, meldet einen Stopp)
--   4) Stopp (behalten|verwerfen); die API schliesst nach 15 s selbst ab
--   5) 060-Funktionen neu: anlegen (uebernimmt eine liegengebliebene Vormerkung), aufraeumen (gestoppte nicht neu starten), fertig (leert den
--      Zwischenstand, gibt die Vormerkung frei, nicht nach Stopp), zurueck (leert), Entscheiden
--      beendet auch 'wartet'.
-- 'wartet' zaehlt weder fuer die Speichersperre (pult_bloecke_speichern prueft nur
-- offen|in_arbeit, bleibt unveraendert) noch fuer chat_auftraege_ein_laufender.
-- Sperrreihenfolge in ALLEN 061-Funktionen, die beides sperren: erst marketing.inhalte,
-- dann marketing.chat_auftraege (pult_bloecke_speichern sperrt den Inhalt ebenfalls) -
-- sonst Deadlock zwischen Stopp-Klick und fertig/Abschluss des Arbeiters.
-- Nach jedem Replay verify_060 und verify_061 laufen lassen.
BEGIN;

-- 1) Spalten
ALTER TABLE marketing.chat_auftraege
  ADD COLUMN IF NOT EXISTS zwischenstand jsonb,
  ADD COLUMN IF NOT EXISTS schritt       text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS schritt_nr    int  NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS zwischen_am   timestamptz,
  ADD COLUMN IF NOT EXISTS stopp         text CHECK (stopp IS NULL OR stopp IN ('behalten','verwerfen')),
  ADD COLUMN IF NOT EXISTS stopp_am      timestamptz;

-- Status-Regel: Name nachschlagen (060 legte sie inline an), entfernen, neu anlegen
DO $$ DECLARE v_name text; BEGIN
  FOR v_name IN SELECT conname FROM pg_constraint
                 WHERE conrelid = 'marketing.chat_auftraege'::regclass AND contype = 'c'
                   AND pg_get_constraintdef(oid) LIKE '%in_arbeit%' LOOP
    EXECUTE format('ALTER TABLE marketing.chat_auftraege DROP CONSTRAINT %I', v_name);
  END LOOP;
END $$;
ALTER TABLE marketing.chat_auftraege ADD CONSTRAINT chat_auftraege_status_check
  CHECK (status IN ('offen','in_arbeit','fertig','fehler','wartet'));

-- je Inhalt hoechstens EINE Vormerkung (chat_auftraege_ein_laufender aus 060 bleibt)
CREATE UNIQUE INDEX IF NOT EXISTS chat_auftraege_eine_vormerkung
  ON marketing.chat_auftraege (inhalt) WHERE status = 'wartet';

-- 5a) Aufraeumen wie 060, aber ein gestoppter Auftrag wird nicht neu gestartet
--     (den schliesst pult_chat_stopp_abschliessen ab).
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
         zwischenstand = NULL, schritt = '',
         vergeben_bis = NULL, geaendert_am = now()
   WHERE status = 'in_arbeit' AND vergeben_bis < now() AND stopp IS NULL
     AND (p_inhalt IS NULL OR inhalt = p_inhalt);
END $$;

-- 2b) Anlegen wie 060 + eine liegengebliebene Vormerkung (wartet) wird bei art='chat'
--     uebernommen statt daneben ein zweiter Auftrag angelegt (R13): Text/Kontext ersetzt,
--     status 'offen', erstellt_am jetzt. Export-Auftraege ignorieren 'wartet'.
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

-- 2) Vormerken
CREATE OR REPLACE FUNCTION marketing.pult_chat_vormerken(
    p_inhalt uuid, p_nachricht text, p_kontext jsonb) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE v_art text; v_status text; v_id uuid;
BEGIN
  -- Inhalt sperren: serialisiert mit Anlegen, Stopp, fertig (samt Freigabe der Vormerkung),
  -- Stopp-Abschluss und Handspeichern - die sperren alle zuerst den Inhalt
  SELECT art, status INTO v_art, v_status FROM marketing.inhalte WHERE id = p_inhalt FOR UPDATE;
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

CREATE OR REPLACE FUNCTION marketing.pult_chat_vormerkung_loeschen(p_inhalt uuid) RETURNS boolean
LANGUAGE plpgsql AS $$
BEGIN
  DELETE FROM marketing.chat_auftraege WHERE inhalt = p_inhalt AND status = 'wartet';
  RETURN FOUND;
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_chat_vormerkung_starten(p_inhalt uuid) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v_id uuid;
BEGIN
  PERFORM 1 FROM marketing.inhalte WHERE id = p_inhalt FOR UPDATE;
  PERFORM marketing.pult_chat_aufraeumen(p_inhalt);
  IF EXISTS (SELECT 1 FROM marketing.chat_auftraege WHERE inhalt = p_inhalt AND status IN ('offen','in_arbeit')) THEN
    RAISE EXCEPTION 'Der Assistent arbeitet gerade'; END IF;
  UPDATE marketing.chat_auftraege
     SET status = 'offen', erstellt_am = now(), geaendert_am = now()
   WHERE inhalt = p_inhalt AND status = 'wartet'
  RETURNING id INTO v_id;
  IF v_id IS NULL THEN RAISE EXCEPTION 'Keine vorgemerkte Nachricht'; END IF;
  RETURN v_id;
END $$;

-- 3) Zwischenstand: nur der Groesse und Form nach geprueft, nie als Fassung gespeichert
CREATE OR REPLACE FUNCTION marketing.pult_chat_zwischenstand(
    p_auftrag uuid, p_bloecke jsonb, p_schritt text, p_nr int, p_frist interval) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE a marketing.chat_auftraege;
BEGIN
  -- sperrt nur den Auftrag (kein Inhalt) - keine Sperrreihenfolge zu beachten
  SELECT * INTO a FROM marketing.chat_auftraege WHERE id = p_auftrag FOR UPDATE;
  IF NOT FOUND OR a.status <> 'in_arbeit' OR a.vergeben_bis IS NULL OR a.vergeben_bis <= now() THEN
    RETURN jsonb_build_object('weiter', false, 'grund', 'verloren'); END IF;
  IF p_bloecke IS NULL OR jsonb_typeof(p_bloecke) <> 'object' THEN
    RAISE EXCEPTION 'Zwischenstand muss ein Objekt sein'; END IF;
  IF octet_length(p_bloecke::text) > 262144 THEN RAISE EXCEPTION 'Zwischenstand zu groß'; END IF;
  UPDATE marketing.chat_auftraege
     SET zwischenstand = p_bloecke, schritt = left(coalesce(p_schritt, ''), 80),
         schritt_nr = coalesce(p_nr, 0), zwischen_am = now(),
         vergeben_bis = now() + coalesce(p_frist, interval '5 minutes'), geaendert_am = now()
   WHERE id = a.id;
  IF a.stopp IS NOT NULL THEN
    RETURN jsonb_build_object('weiter', false, 'grund', 'stopp', 'stopp', a.stopp); END IF;
  RETURN jsonb_build_object('weiter', true);
END $$;

-- 4) Stopp
-- p_auftrag (optional): der Auftrag, den der Betreiber stoppen wollte. Laeuft inzwischen ein
-- anderer (z. B. die gerade freigegebene Vormerkung), passiert nichts: {veraltet:true}.
DROP FUNCTION IF EXISTS marketing.pult_chat_stoppen(uuid, text);
CREATE OR REPLACE FUNCTION marketing.pult_chat_stoppen(
    p_inhalt uuid, p_art text, p_auftrag uuid DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE a marketing.chat_auftraege;
BEGIN
  IF p_art IS NULL OR p_art NOT IN ('behalten','verwerfen') THEN
    RAISE EXCEPTION 'Stopp muss behalten oder verwerfen sein'; END IF;
  PERFORM 1 FROM marketing.inhalte WHERE id = p_inhalt FOR UPDATE;
  PERFORM marketing.pult_chat_aufraeumen(p_inhalt);
  SELECT * INTO a FROM marketing.chat_auftraege
   WHERE inhalt = p_inhalt AND status IN ('offen','in_arbeit') FOR UPDATE;
  IF p_auftrag IS NOT NULL AND (NOT FOUND OR a.id <> p_auftrag) THEN
    RETURN jsonb_build_object('abgeschlossen', false, 'veraltet', true); END IF;
  IF NOT FOUND THEN RAISE EXCEPTION 'Der Assistent arbeitet gerade nicht'; END IF;
  IF a.status = 'offen' THEN
    UPDATE marketing.chat_auftraege
       SET status = 'fehler', antwort = 'Gestoppt, bevor der Assistent begonnen hat',
           stopp = p_art, stopp_am = now(), vergeben_bis = NULL,
           zwischenstand = NULL, schritt = '', geaendert_am = now()
     WHERE id = a.id;
    RETURN jsonb_build_object('abgeschlossen', true, 'id', a.id);
  END IF;
  -- erneutes Stoppen aendert die Art, die 15-s-Frist laeuft ab dem ersten Stopp
  UPDATE marketing.chat_auftraege
     SET stopp = p_art, stopp_am = coalesce(stopp_am, now()), geaendert_am = now()
   WHERE id = a.id;
  RETURN jsonb_build_object('abgeschlossen', false, 'id', a.id);
END $$;

-- Gestoppte Auftraege, deren Arbeiter sich binnen 15 s nicht gemeldet hat
CREATE OR REPLACE FUNCTION marketing.pult_chat_stopp_faellig() RETURNS SETOF uuid
LANGUAGE sql STABLE AS $$
  SELECT id FROM marketing.chat_auftraege
   WHERE status = 'in_arbeit' AND stopp IS NOT NULL AND stopp_am < now() - interval '15 seconds'
   ORDER BY stopp_am;
$$;

-- Schliesst einen gestoppten Auftrag ab (Arbeiter oder VM, wer zuerst kommt).
-- p_bloecke = der von der API gerechnete und gepruefte letzte Zwischenstand oder NULL.
-- Bei 'verwerfen' entsteht nie eine Fassung. Die Vormerkung wird NICHT freigegeben.
CREATE OR REPLACE FUNCTION marketing.pult_chat_stopp_abschliessen(
    p_auftrag uuid, p_bloecke jsonb, p_hinweis text) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE a marketing.chat_auftraege; f record; v_n int; v_hinweise jsonb; v_inhalt uuid;
BEGIN
  -- Sperrreihenfolge: erst den Inhalt (ungesperrt nachgeschlagen), dann den Auftrag
  SELECT inhalt INTO v_inhalt FROM marketing.chat_auftraege WHERE id = p_auftrag;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannter Auftrag'; END IF;
  PERFORM 1 FROM marketing.inhalte WHERE id = v_inhalt FOR UPDATE;
  SELECT * INTO a FROM marketing.chat_auftraege WHERE id = p_auftrag FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannter Auftrag'; END IF;
  IF a.status <> 'in_arbeit' THEN                -- schon abgeschlossen: nichts zu tun
    RETURN jsonb_strip_nulls(jsonb_build_object('status', a.status, 'fassung', a.fassung_nachher)); END IF;
  IF a.stopp IS NULL THEN RAISE EXCEPTION 'Auftrag wurde nicht gestoppt'; END IF;
  v_hinweise := CASE WHEN coalesce(btrim(p_hinweis), '') = '' THEN '[]'::jsonb
                     ELSE jsonb_build_array(left(p_hinweis, 500)) END;
  IF p_bloecke IS NULL OR a.stopp = 'verwerfen' THEN
    UPDATE marketing.chat_auftraege
       SET status = 'fehler', antwort = 'Gestoppt – nichts übernommen', hinweise = v_hinweise,
           zwischenstand = NULL, schritt = '', vergeben_bis = NULL, geaendert_am = now()
     WHERE id = a.id;
    RETURN jsonb_build_object('status', 'fehler');
  END IF;
  SELECT felder INTO f FROM marketing.inhalt_fassungen
   WHERE inhalt = a.inhalt ORDER BY fassung DESC LIMIT 1;
  v_n := marketing.pult_bloecke_speichern(a.inhalt, a.fassung_vorher, f.felder->>'betreff',
           coalesce(f.felder->>'vorschautext', ''), p_bloecke, 'agent', false);
  UPDATE marketing.chat_auftraege
     SET status = 'fertig',
         antwort = format('Gestoppt nach Schritt %s – bisherige Schritte übernommen', a.schritt_nr),
         hinweise = v_hinweise,
         ergebnis = a.ergebnis || jsonb_build_object('notiz', format('gestoppt nach Schritt %s', a.schritt_nr)),
         fassung_nachher = v_n, zwischenstand = NULL, schritt = '',
         vergeben_bis = NULL, geaendert_am = now()
   WHERE id = a.id;
  RETURN jsonb_build_object('status', 'fertig', 'fassung', v_n);
END $$;

-- 5b) fertig wie 060 + Zwischenstand leeren + Vormerkung freigeben.
--     Ein gestoppter Auftrag kann nicht mehr fertig werden (Stopp gewinnt).
CREATE OR REPLACE FUNCTION marketing.pult_chat_fertig(
    p_auftrag uuid, p_antwort text, p_bloecke jsonb, p_hinweise jsonb, p_ergebnis jsonb) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE a marketing.chat_auftraege; f record; v_n int; v_inhalt uuid;
BEGIN
  -- Sperrreihenfolge: erst den Inhalt (ungesperrt nachgeschlagen), dann den Auftrag
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
  IF p_bloecke IS NOT NULL THEN
    SELECT felder INTO f FROM marketing.inhalt_fassungen
     WHERE inhalt = a.inhalt ORDER BY fassung DESC LIMIT 1;
    v_n := marketing.pult_bloecke_speichern(a.inhalt, a.fassung_vorher, f.felder->>'betreff',
             coalesce(f.felder->>'vorschautext', ''), p_bloecke, 'agent', false);
  END IF;
  UPDATE marketing.chat_auftraege
     SET status = 'fertig', antwort = left(coalesce(p_antwort, ''), 4000),
         hinweise = coalesce(p_hinweise, '[]'::jsonb), ergebnis = coalesce(p_ergebnis, '{}'::jsonb),
         fassung_nachher = v_n, vergeben_bis = NULL,
         zwischenstand = NULL, schritt = '', geaendert_am = now()
   WHERE id = a.id;
  -- Vormerkung freigeben (nur nach einem Chat-Auftrag, nie nach einem Export): der Arbeiter holt
  -- sie normal ab und baut auf der neuen Fassung auf
  UPDATE marketing.chat_auftraege
     SET status = 'offen', erstellt_am = now(), geaendert_am = now()
   WHERE inhalt = a.inhalt AND status = 'wartet' AND a.art = 'chat';
  RETURN jsonb_build_object('fassung', v_n);
END $$;

-- 5c) zurueck wie 060 + Zwischenstand leeren; die Vormerkung bleibt stehen
CREATE OR REPLACE FUNCTION marketing.pult_chat_zurueck(p_auftrag uuid, p_antwort text) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE a marketing.chat_auftraege;
BEGIN
  SELECT * INTO a FROM marketing.chat_auftraege WHERE id = p_auftrag FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannter Auftrag'; END IF;
  IF a.status NOT IN ('offen','in_arbeit') THEN RETURN a.status; END IF;   -- schon erledigt: nichts zu tun
  UPDATE marketing.chat_auftraege
     SET status = 'fehler', antwort = left(coalesce(p_antwort, ''), 4000),
         vergeben_bis = NULL, zwischenstand = NULL, schritt = '', geaendert_am = now()
   WHERE id = a.id;
  RETURN 'fehler';
END $$;

-- 5d) Entschiedene Inhalte: auch die Vormerkung beenden (Trigger aus 060 bleibt)
CREATE OR REPLACE FUNCTION marketing._chat_auftraege_beenden() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status <> 'entwurf' AND OLD.status = 'entwurf' THEN
    UPDATE marketing.chat_auftraege SET status = 'fehler', antwort = 'Newsletter wurde entschieden',
           vergeben_bis = NULL, zwischenstand = NULL, schritt = '', geaendert_am = now()
     WHERE inhalt = NEW.id AND status IN ('offen', 'in_arbeit', 'wartet');
  END IF;
  RETURN NEW;
END $$;

COMMIT;
