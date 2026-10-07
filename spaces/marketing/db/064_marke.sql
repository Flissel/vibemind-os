-- RUNBOOK: Nach 064 NIE 050/051 erneut einspielen - sie ueberschreiben pult_gestalt_fehler ohne
-- die schriften-Pruefung. Nach jedem Replay verify_060 .. verify_064 zusammen ueber migration_probe.
-- 064: Marke per Chat (sales-claw Spec 2026-10-07-marke-per-chat-design.md §3/§4, Plan Task 1,
-- Ruling R1: eigene Tabelle marken_auftraege statt chat_auftraege; R3: Ausblenden setzt
-- inhalte.marke_geaendert_am NULL). Idempotent, eine Transaktion.
--   1) Schriftregister als SQL-Array: marketing.marke_schriften() - GENAU die ids aus
--      spaces/marketing/claw/schriften.py REGISTER (Task 4 vergleicht beide):
--      ARRAY['cormorant','dm-sans','playfair','poppins','young-serif','manrope',
--            'bodoni','montserrat','josefin','oxanium','rajdhani']
--   2) pult_gestalt_fehler (aktive Definition aus 051, woertlich) + optionaler Schluessel
--      schriften = Objekt mit genau anzeige und text, je eine id aus dem Register
--   3) marketing.marken_auftraege (Arten chat|uebernehmen; je Firma hoechstens ein offen|in_arbeit)
--      marketing.marken_vorschlaege (offen|angenommen|verworfen|ersetzt; je Firma hoechstens ein offener)
--      marketing.marken_spiegel (letzter gespiegelter Stand je Firma, Fehler des letzten Versuchs)
--      marketing.inhalte.marke_geaendert_am (Hinweis "Die Marke hat sich geändert – übernehmen?")
--   4) Warteschlange wie 060/061: anlegen, naechster, verlaengern, vorschlag, fertig, zurueck
--      Aufraeumen: ein nicht abgeholter CHAT stirbt nach 2 min ("PC aus"), ein UEBERNEHMEN wartet,
--      bis der PC laeuft ("Wird übernommen, sobald der PC läuft"); abgelaufene Vergabe: einmal neu,
--      dann fehler. Endet ein Uebernehmen mit fehler, wird sein Vorschlag wieder 'offen'
--      (sonst bliebe er 'angenommen', ohne je geschrieben worden zu sein).
--   5) Entscheiden: uebernehmen (nur der offene = neueste offene Vorschlag), verwerfen
--   6) Spiegel: pult_marke_spiegeln schreibt akzent/flaeche/logo/schriften ins Standard-Newsletter-
--      Layout der Firma (neue Fassung ueber pult_layout_speichern aus 051) oder legt es an
--      ('marke-<firma>', Gestalt von 'dunkel' als Grundlage, standard, freigegeben). Ein JSON-null
--      entfernt den Schluessel (Logo geloescht). Ungueltige Gestalt: nichts gespiegelt, Fehler in
--      marken_spiegel.fehler, Rueckgabe NULL (Spec §5: der Spiegel behaelt den letzten gueltigen Stand).
--   7) Markierung: pult_marke_markieren (alle Newsletter-Entwuerfe der Firma), pult_marke_fertig
--      eines Uebernehmen-Auftrags markiert selbst; pult_marke_hinweis_aus setzt zurueck.
-- Sperrreihenfolge (wie 061/063: Elternzeile zuerst): erst marketing.mandanten (FOR NO KEY UPDATE -
-- blockiert keine FK-Pruefungen anderer Schreiber, serialisiert aber alle Marken-Funktionen je
-- Firma), dann marken_auftraege, dann marken_vorschlaege. pult_marke_naechster/verlaengern sperren
-- nur den Auftrag.
-- Rechte: wie 060 - keine eigenen GRANTs, Default Privileges aus 003 gelten.
BEGIN;

-- 1) Schriftregister (gleiche ids und Reihenfolge wie claw/schriften.py REGISTER)
CREATE OR REPLACE FUNCTION marketing.marke_schriften() RETURNS text[]
LANGUAGE sql IMMUTABLE AS $$
  SELECT ARRAY['cormorant','dm-sans','playfair','poppins','young-serif','manrope',
               'bodoni','montserrat','josefin','oxanium','rajdhani']::text[]
$$;

-- 2) Gestalt-Pruefung: woertlich aus 051, nur die schriften-Pruefung vor RETURN NULL ergaenzt
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
  -- 064: Schriftpaar (verschachtelt, damit jsonb_object_keys nie ein Nicht-Objekt sieht)
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

-- 3) Tabellen
ALTER TABLE marketing.inhalte ADD COLUMN IF NOT EXISTS marke_geaendert_am timestamptz;

CREATE TABLE IF NOT EXISTS marketing.marken_auftraege (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    mandant      text NOT NULL REFERENCES marketing.mandanten(id),
    art          text NOT NULL DEFAULT 'chat' CHECK (art IN ('chat','uebernehmen')),
    nachricht    text NOT NULL DEFAULT '' CHECK (length(nachricht) <= 2000),
    kontext      jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(kontext) = 'object'),
    status       text NOT NULL DEFAULT 'offen' CHECK (status IN ('offen','in_arbeit','fertig','fehler')),
    antwort      text NOT NULL DEFAULT '',
    hinweise     jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(hinweise) = 'array'),
    vorschlag    uuid,
    versuche     int NOT NULL DEFAULT 0,
    vergeben_bis timestamptz,
    erstellt_am  timestamptz NOT NULL DEFAULT now(),
    geaendert_am timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT marken_auftraege_uebernehmen_hat_vorschlag CHECK (art <> 'uebernehmen' OR vorschlag IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS marken_auftraege_mandant_idx ON marketing.marken_auftraege (mandant, erstellt_am DESC);
CREATE INDEX IF NOT EXISTS marken_auftraege_status_idx ON marketing.marken_auftraege (status, erstellt_am);
-- je Firma hoechstens EIN wartender oder laufender Auftrag
CREATE UNIQUE INDEX IF NOT EXISTS marken_auftraege_ein_laufender
  ON marketing.marken_auftraege (mandant) WHERE status IN ('offen','in_arbeit');

CREATE TABLE IF NOT EXISTS marketing.marken_vorschlaege (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    mandant         text NOT NULL REFERENCES marketing.mandanten(id),
    auftrag         uuid NOT NULL REFERENCES marketing.marken_auftraege(id),
    vorschlag       jsonb NOT NULL CHECK (jsonb_typeof(vorschlag) = 'object'),
    status          text NOT NULL DEFAULT 'offen' CHECK (status IN ('offen','angenommen','verworfen','ersetzt')),
    erstellt_am     timestamptz NOT NULL DEFAULT now(),
    entschieden_von text,
    entschieden_am  timestamptz,
    CONSTRAINT marken_vorschlaege_entscheid_hat_urheber
      CHECK (status NOT IN ('angenommen','verworfen') OR (entschieden_von IS NOT NULL AND entschieden_am IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS marken_vorschlaege_mandant_idx ON marketing.marken_vorschlaege (mandant, erstellt_am DESC);
-- je Firma hoechstens EIN offener Vorschlag (ein neuer ersetzt den alten): offen = neuester offener
CREATE UNIQUE INDEX IF NOT EXISTS marken_vorschlaege_ein_offener
  ON marketing.marken_vorschlaege (mandant) WHERE status = 'offen';

-- Rueckverweis Auftrag -> Vorschlag (zirkulaer, deshalb nach beiden Tabellen)
ALTER TABLE marketing.marken_auftraege DROP CONSTRAINT IF EXISTS marken_auftraege_vorschlag_fkey;
ALTER TABLE marketing.marken_auftraege ADD CONSTRAINT marken_auftraege_vorschlag_fkey
  FOREIGN KEY (vorschlag) REFERENCES marketing.marken_vorschlaege(id);

CREATE TABLE IF NOT EXISTS marketing.marken_spiegel (
    mandant       text PRIMARY KEY REFERENCES marketing.mandanten(id),
    stand         text NOT NULL DEFAULT '',
    gespiegelt_am timestamptz,
    fehler        text
);

-- 4) Warteschlange
-- Aufraeumen (wie 060/061): nicht abgeholter Chat nach 2 min tot; Uebernehmen wartet auf den PC.
-- Abgelaufene Vergabe: einmal neu, dann fehler - ein gescheitertes Uebernehmen gibt seinen
-- Vorschlag wieder frei.
CREATE OR REPLACE FUNCTION marketing._marke_aufraeumen(p_mandant text) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  UPDATE marketing.marken_auftraege
     SET status = 'fehler', antwort = 'Der Assistent läuft am PC und ist gerade aus',
         vergeben_bis = NULL, geaendert_am = now()
   WHERE status = 'offen' AND art = 'chat' AND erstellt_am < now() - interval '2 minutes'
     AND (p_mandant IS NULL OR mandant = p_mandant);
  WITH tot AS (
    UPDATE marketing.marken_auftraege
       SET status = CASE WHEN versuche < 2 THEN 'offen' ELSE 'fehler' END,
           antwort = CASE WHEN versuche < 2 THEN antwort ELSE 'Der Assistent ist nicht fertig geworden' END,
           erstellt_am = CASE WHEN versuche < 2 THEN now() ELSE erstellt_am END,
           vergeben_bis = NULL, geaendert_am = now()
     WHERE status = 'in_arbeit' AND vergeben_bis < now()
       AND (p_mandant IS NULL OR mandant = p_mandant)
    RETURNING art, status, vorschlag)
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
  -- Firma sperren: serialisiert Anlegen, Uebernehmen und Arbeiter-Rueckmeldungen je Firma
  PERFORM 1 FROM marketing.mandanten WHERE id = p_mandant AND aktiv FOR NO KEY UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannte oder inaktive Firma'; END IF;
  IF length(btrim(coalesce(p_nachricht, ''))) = 0 THEN RAISE EXCEPTION 'Ohne Nachricht kein Auftrag'; END IF;
  IF length(p_nachricht) > 2000 THEN
    RAISE EXCEPTION 'Die Nachricht ist zu lang (hoechstens 2000 Zeichen)'; END IF;
  IF p_kontext IS NOT NULL AND jsonb_typeof(p_kontext) <> 'object' THEN
    RAISE EXCEPTION 'Kontext muss ein Objekt sein'; END IF;
  PERFORM marketing._marke_aufraeumen(p_mandant);
  IF EXISTS (SELECT 1 FROM marketing.marken_auftraege
              WHERE mandant = p_mandant AND status IN ('offen','in_arbeit')) THEN
    RAISE EXCEPTION 'Der Assistent arbeitet gerade'; END IF;
  INSERT INTO marketing.marken_auftraege (mandant, art, nachricht, kontext)
  VALUES (p_mandant, 'chat', btrim(p_nachricht), coalesce(p_kontext, '{}'::jsonb))
  RETURNING id INTO v_id;
  RETURN v_id;
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_marke_naechster(p_frist interval) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE a marketing.marken_auftraege; v_firma text; v_verlauf jsonb; v_vorschlag jsonb;
BEGIN
  PERFORM marketing._marke_aufraeumen(NULL);
  SELECT * INTO a FROM marketing.marken_auftraege WHERE status = 'offen'
   ORDER BY erstellt_am LIMIT 1 FOR UPDATE SKIP LOCKED;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT name INTO v_firma FROM marketing.mandanten WHERE id = a.mandant;
  -- Verlauf: die letzten 10 fertigen Chat-Runden der Firma, aelteste zuerst
  SELECT coalesce(jsonb_agg(jsonb_build_object('nachricht', v.nachricht, 'antwort', v.antwort)
                            ORDER BY v.erstellt_am), '[]'::jsonb)
    INTO v_verlauf
    FROM (SELECT nachricht, antwort, erstellt_am FROM marketing.marken_auftraege
           WHERE mandant = a.mandant AND art = 'chat' AND status = 'fertig'
           ORDER BY erstellt_am DESC LIMIT 10) v;
  IF a.art = 'uebernehmen' THEN
    SELECT jsonb_build_object('id', id, 'vorschlag', vorschlag) INTO v_vorschlag
      FROM marketing.marken_vorschlaege WHERE id = a.vorschlag;
  END IF;
  UPDATE marketing.marken_auftraege
     SET status = 'in_arbeit', versuche = versuche + 1, vergeben_bis = now() + p_frist, geaendert_am = now()
   WHERE id = a.id;
  RETURN jsonb_build_object('id', a.id, 'art', a.art, 'mandant', a.mandant, 'firma', v_firma,
           'nachricht', a.nachricht, 'kontext', a.kontext, 'verlauf', v_verlauf,
           'vorschlag', v_vorschlag);
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_marke_verlaengern(p_auftrag uuid, p_frist interval) RETURNS boolean
LANGUAGE plpgsql AS $$
BEGIN
  UPDATE marketing.marken_auftraege SET vergeben_bis = now() + p_frist, geaendert_am = now()
   WHERE id = p_auftrag AND status = 'in_arbeit';
  RETURN FOUND;
END $$;

-- Sperrt Firma (ungesperrt nachgeschlagen), dann den Auftrag; prueft gueltige Vergabe.
CREATE OR REPLACE FUNCTION marketing._marke_auftrag_sperren(p_auftrag uuid) RETURNS marketing.marken_auftraege
LANGUAGE plpgsql AS $$
DECLARE a marketing.marken_auftraege; v_mandant text;
BEGIN
  SELECT mandant INTO v_mandant FROM marketing.marken_auftraege WHERE id = p_auftrag;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannter Auftrag'; END IF;
  PERFORM 1 FROM marketing.mandanten WHERE id = v_mandant FOR NO KEY UPDATE;
  SELECT * INTO a FROM marketing.marken_auftraege WHERE id = p_auftrag FOR UPDATE;
  RETURN a;
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_marke_vorschlag(
    p_auftrag uuid, p_vorschlag jsonb, p_antwort text, p_hinweise jsonb) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE a marketing.marken_auftraege; v_id uuid;
BEGIN
  a := marketing._marke_auftrag_sperren(p_auftrag);
  IF a.status <> 'in_arbeit' OR a.vergeben_bis IS NULL OR a.vergeben_bis <= now() THEN
    RAISE EXCEPTION 'Auftrag ist nicht (mehr) in Arbeit'; END IF;
  IF a.art <> 'chat' THEN RAISE EXCEPTION 'Vorschlaege gibt es nur aus dem Chat'; END IF;
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

-- 7) Markierung (vor fertig, das sie ruft)
CREATE OR REPLACE FUNCTION marketing.pult_marke_markieren(p_mandant text) RETURNS int
LANGUAGE plpgsql AS $$
DECLARE v_n int;
BEGIN
  UPDATE marketing.inhalte SET marke_geaendert_am = now()
   WHERE mandant = p_mandant AND art = 'newsletter' AND status = 'entwurf';
  GET DIAGNOSTICS v_n = ROW_COUNT;
  RETURN v_n;
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_marke_hinweis_aus(p_inhalt uuid) RETURNS boolean
LANGUAGE plpgsql AS $$
BEGIN
  UPDATE marketing.inhalte SET marke_geaendert_am = NULL WHERE id = p_inhalt;
  RETURN FOUND;
END $$;

-- fertig: Antwort ohne Vorschlag (chat) oder Uebernehmen abgeschlossen (markiert die Entwuerfe).
-- Rueckgabe: Zahl der markierten Newsletter (0 bei chat).
CREATE OR REPLACE FUNCTION marketing.pult_marke_fertig(
    p_auftrag uuid, p_antwort text, p_hinweise jsonb) RETURNS int
LANGUAGE plpgsql AS $$
DECLARE a marketing.marken_auftraege; v_n int := 0;
BEGIN
  a := marketing._marke_auftrag_sperren(p_auftrag);
  IF a.status <> 'in_arbeit' OR a.vergeben_bis IS NULL OR a.vergeben_bis <= now() THEN
    RAISE EXCEPTION 'Auftrag ist nicht (mehr) in Arbeit'; END IF;
  IF p_hinweise IS NOT NULL AND jsonb_typeof(p_hinweise) <> 'array' THEN
    RAISE EXCEPTION 'Hinweise muessen ein Array sein'; END IF;
  UPDATE marketing.marken_auftraege
     SET status = 'fertig', antwort = left(coalesce(p_antwort, ''), 4000),
         hinweise = coalesce(p_hinweise, '[]'::jsonb), vergeben_bis = NULL, geaendert_am = now()
   WHERE id = a.id;
  IF a.art = 'uebernehmen' THEN
    v_n := marketing.pult_marke_markieren(a.mandant);
  END IF;
  RETURN v_n;
END $$;

-- zurueck: Arbeiter gibt auf (fehler); ein Uebernehmen gibt seinen Vorschlag wieder frei
CREATE OR REPLACE FUNCTION marketing.pult_marke_zurueck(p_auftrag uuid, p_antwort text) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE a marketing.marken_auftraege;
BEGIN
  a := marketing._marke_auftrag_sperren(p_auftrag);
  IF a.status NOT IN ('offen','in_arbeit') THEN RETURN a.status; END IF;   -- schon erledigt: nichts zu tun
  UPDATE marketing.marken_auftraege
     SET status = 'fehler', antwort = left(coalesce(p_antwort, ''), 4000),
         vergeben_bis = NULL, geaendert_am = now()
   WHERE id = a.id;
  IF a.art = 'uebernehmen' THEN
    UPDATE marketing.marken_vorschlaege
       SET status = 'offen', entschieden_von = NULL, entschieden_am = NULL
     WHERE id = a.vorschlag AND status = 'angenommen';
  END IF;
  RETURN 'fehler';
END $$;

-- 5) Entscheiden
CREATE OR REPLACE FUNCTION marketing.pult_marke_uebernehmen(p_vorschlag uuid, p_von text) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v_mandant text; v_status text; v_id uuid;
BEGIN
  IF length(btrim(coalesce(p_von, ''))) = 0 THEN RAISE EXCEPTION 'Ohne Namen kein Übernehmen'; END IF;
  SELECT mandant INTO v_mandant FROM marketing.marken_vorschlaege WHERE id = p_vorschlag;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannter Vorschlag'; END IF;
  PERFORM 1 FROM marketing.mandanten WHERE id = v_mandant AND aktiv FOR NO KEY UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannte oder inaktive Firma'; END IF;
  SELECT status INTO v_status FROM marketing.marken_vorschlaege WHERE id = p_vorschlag FOR UPDATE;
  -- offen = der neueste offene (Unique-Index marken_vorschlaege_ein_offener)
  IF v_status <> 'offen' THEN
    RAISE EXCEPTION 'Inzwischen gibt es ein neueres Profil – bitte neu laden'; END IF;
  PERFORM marketing._marke_aufraeumen(v_mandant);
  IF EXISTS (SELECT 1 FROM marketing.marken_auftraege
              WHERE mandant = v_mandant AND status IN ('offen','in_arbeit')) THEN
    RAISE EXCEPTION 'Der Assistent arbeitet gerade'; END IF;
  UPDATE marketing.marken_vorschlaege
     SET status = 'angenommen', entschieden_von = btrim(p_von), entschieden_am = now()
   WHERE id = p_vorschlag;
  INSERT INTO marketing.marken_auftraege (mandant, art, vorschlag)
  VALUES (v_mandant, 'uebernehmen', p_vorschlag)
  RETURNING id INTO v_id;
  RETURN v_id;
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_marke_verwerfen(p_vorschlag uuid, p_von text) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE v_mandant text; v_status text;
BEGIN
  IF length(btrim(coalesce(p_von, ''))) = 0 THEN RAISE EXCEPTION 'Ohne Namen kein Verwerfen'; END IF;
  SELECT mandant INTO v_mandant FROM marketing.marken_vorschlaege WHERE id = p_vorschlag;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannter Vorschlag'; END IF;
  PERFORM 1 FROM marketing.mandanten WHERE id = v_mandant FOR NO KEY UPDATE;
  SELECT status INTO v_status FROM marketing.marken_vorschlaege WHERE id = p_vorschlag FOR UPDATE;
  IF v_status = 'verworfen' THEN RETURN 'verworfen'; END IF;            -- zweiter Klick: nichts zu tun
  IF v_status <> 'offen' THEN
    RAISE EXCEPTION 'Inzwischen gibt es ein neueres Profil – bitte neu laden'; END IF;
  UPDATE marketing.marken_vorschlaege
     SET status = 'verworfen', entschieden_von = btrim(p_von), entschieden_am = now()
   WHERE id = p_vorschlag;
  RETURN 'verworfen';
END $$;

-- 6) Spiegel ins Standard-Newsletter-Layout der Firma
CREATE OR REPLACE FUNCTION marketing.pult_marke_spiegeln(
    p_mandant text, p_gestalt jsonb, p_stand text) RETURNS int
LANGUAGE plpgsql AS $$
DECLARE v_name text; v_alt jsonb; v_neu jsonb; v_f text; v_n int;
BEGIN
  IF p_gestalt IS NULL OR jsonb_typeof(p_gestalt) <> 'object' THEN
    RAISE EXCEPTION 'Gestalt muss ein Objekt sein'; END IF;
  IF EXISTS (SELECT 1 FROM jsonb_object_keys(p_gestalt) k
              WHERE k NOT IN ('akzent','flaeche','logo','schriften')) THEN
    RAISE EXCEPTION 'Spiegel kennt nur akzent, flaeche, logo, schriften'; END IF;
  -- Firma zuerst (serialisiert das Anlegen des Standard-Layouts), dann das Layout
  PERFORM 1 FROM marketing.mandanten WHERE id = p_mandant FOR NO KEY UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannte Firma'; END IF;
  SELECT name, gestalt INTO v_name, v_alt FROM marketing.layout_vorlagen
   WHERE mandant = p_mandant AND inhaltsart = 'newsletter' AND standard AND art = 'layout' FOR UPDATE;
  IF v_name IS NULL THEN
    v_alt := (SELECT gestalt FROM marketing.layout_vorlagen WHERE name = 'dunkel');
  END IF;
  -- JSON-null entfernt den Schluessel (z. B. Logo geloescht); nur die oberste Ebene
  SELECT coalesce(jsonb_object_agg(key, value), '{}'::jsonb) INTO v_neu
    FROM jsonb_each(v_alt || p_gestalt) WHERE jsonb_typeof(value) <> 'null';
  v_f := marketing.pult_gestalt_fehler(v_neu);
  IF v_f IS NOT NULL THEN
    -- nichts spiegeln; der letzte gueltige Stand bleibt, der Fehler wird sichtbar
    INSERT INTO marketing.marken_spiegel (mandant, fehler) VALUES (p_mandant, 'Layout ungueltig: ' || v_f)
    ON CONFLICT (mandant) DO UPDATE SET fehler = EXCLUDED.fehler;
    RETURN NULL;
  END IF;
  IF v_name IS NULL THEN
    v_name := 'marke-' || replace(p_mandant, '_', '-');
    IF EXISTS (SELECT 1 FROM marketing.layout_vorlagen WHERE name = v_name) THEN
      -- liegengebliebenes, nicht (mehr) als Standard gesetztes Marken-Layout der Firma wiederverwenden
      IF NOT EXISTS (SELECT 1 FROM marketing.layout_vorlagen
                      WHERE name = v_name AND mandant = p_mandant AND art = 'layout') THEN
        RAISE EXCEPTION 'Layout % gehoert einer anderen Firma', v_name; END IF;
      v_n := marketing.pult_layout_speichern(v_name, v_neu, 'marke');
      PERFORM marketing.pult_layout_als_standard(v_name);
    ELSE
      -- Trigger aus 051 ziehen Fassung 1 und zeichnen sie in layout_fassungen auf
      INSERT INTO marketing.layout_vorlagen (name, beschreibung, gestalt, status, vorgeschlagen_von,
             entschieden_von, entschieden_am, mandant, inhaltsart, standard)
      VALUES (v_name, 'Aus der Marke der Firma (Marke.md)', v_neu, 'freigegeben', 'marke',
              'marke', now(), p_mandant, 'newsletter', true)
      RETURNING fassung INTO v_n;
    END IF;
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
