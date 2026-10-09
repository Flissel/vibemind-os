-- RUNBOOK: 067 ersetzt pult_chat_aufraeumen, pult_chat_anlegen (063), pult_chat_fertig (061, jetzt mit p_basis),
-- pult_chat_zurueck (061), pult_chat_stoppen (061), pult_chat_stopp_abschliessen (061, jetzt mit p_basis) und
-- pult_bloecke_speichern (Huelle aus 063); es entfernt pult_chat_vormerken, pult_chat_vormerkung_loeschen und
-- pult_chat_vormerkung_starten. Nach einem Replay von 060, 061 oder 063 IMMER 067 erneut einspielen; danach
-- verify_060, verify_062 .. verify_067 zusammen ueber migration_probe. verify_061 prueft das Vormerk-Modell von
-- 061 und gilt nach 067 nicht mehr (Stopp und Zwischenstand je Runde: verify_067 Abschnitt 4).
-- 067: Editor - mehrere Runden gleichzeitig (sales-claw Spec 2026-10-09-editor-parallele-runden-design.md §1).
-- Idempotent, eine Transaktion.
--   1) je Inhalt hoechstens 3 Runden offen/in_arbeit und 5 wartende (durchgesetzt in pult_chat_anlegen unter
--      Sperre der Inhaltszeile); ein Newsletter-Export laeuft allein
--   2) das Ende einer Runde (fertig, zurueck, Stopp, Aufraeumen) laesst die aelteste wartende nachruecken
--      (_chat_nachruecken; bereit_am = jetzt, Basis = neueste Fassung)
--   3) pult_chat_fertig / pult_chat_stopp_abschliessen speichern auf p_basis (die Fassung, auf die der Arbeiter
--      nachgespielt hat) und merken sie als fassung_vorher (Rueckgaengig nimmt sie)
--   4) Bild-Hinweise: pult_chat_bild_ids merkt die Bildauftraege einer Runde, pult_chat_bild_hinweise nennt die
--      gescheiterten
-- Sperrreihenfolge wie 061: erst marketing.inhalte, dann marketing.chat_auftraege. _chat_nachruecken sperrt den
-- Inhalt mit SKIP LOCKED: haelt ihn ein anderer, ruft der selbst nachruecken auf, sobald er seine Runde beendet.
BEGIN;

-- 1) Spalte und Indizes
ALTER TABLE marketing.chat_auftraege ADD COLUMN IF NOT EXISTS bereit_am timestamptz;
DROP INDEX IF EXISTS marketing.chat_auftraege_ein_laufender;
DROP INDEX IF EXISTS marketing.chat_auftraege_eine_vormerkung;
CREATE INDEX IF NOT EXISTS chat_auftraege_inhalt_status_idx ON marketing.chat_auftraege (inhalt, status);

-- 2) Vormerken geht in der Warteschlange auf
DROP FUNCTION IF EXISTS marketing.pult_chat_vormerken(uuid, text, jsonb);
DROP FUNCTION IF EXISTS marketing.pult_chat_vormerkung_loeschen(uuid);
DROP FUNCTION IF EXISTS marketing.pult_chat_vormerkung_starten(uuid);

-- 3) Zaehler und Nachruecken
CREATE OR REPLACE FUNCTION marketing._chat_laufend(p_inhalt uuid) RETURNS int
LANGUAGE sql STABLE AS $$
  SELECT count(*)::int FROM marketing.chat_auftraege WHERE inhalt = p_inhalt AND status IN ('offen','in_arbeit')
$$;

CREATE OR REPLACE FUNCTION marketing._chat_nachruecken(p_inhalt uuid) RETURNS int
LANGUAGE plpgsql AS $$
DECLARE v_status text; v_neueste int; v_frei int; v_n int;
BEGIN
  SELECT status INTO v_status FROM marketing.inhalte WHERE id = p_inhalt FOR UPDATE SKIP LOCKED;
  IF NOT FOUND OR v_status IS DISTINCT FROM 'entwurf' THEN RETURN 0; END IF;
  v_frei := 3 - marketing._chat_laufend(p_inhalt);
  IF v_frei <= 0 THEN RETURN 0; END IF;
  SELECT max(fassung) INTO v_neueste FROM marketing.inhalt_fassungen WHERE inhalt = p_inhalt;
  UPDATE marketing.chat_auftraege
     SET status = 'offen', bereit_am = now(), fassung_vorher = v_neueste, geaendert_am = now()
   WHERE id IN (SELECT id FROM marketing.chat_auftraege
                 WHERE inhalt = p_inhalt AND status = 'wartet'
                 ORDER BY erstellt_am, id LIMIT v_frei FOR UPDATE);
  GET DIAGNOSTICS v_n = ROW_COUNT;
  RETURN v_n;
END $$;

-- 4) Aufraeumen: nicht abgeholt (2 min seit bereit) => fehler, die wartenden Runden desselben Entwurfs mit
--    (der PC ist aus); Vergabe abgelaufen => einmal neu, dann fehler; danach rueckt nach, wer kann.
CREATE OR REPLACE FUNCTION marketing.pult_chat_aufraeumen(p_inhalt uuid) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE v_i uuid;
BEGIN
  WITH tot AS (
    UPDATE marketing.chat_auftraege
       SET status = 'fehler', antwort = 'Der Assistent läuft am PC und ist gerade aus',
           vergeben_bis = NULL, geaendert_am = now()
     WHERE status = 'offen' AND coalesce(bereit_am, erstellt_am) < now() - interval '2 minutes'
       AND (p_inhalt IS NULL OR inhalt = p_inhalt)
    RETURNING inhalt)
  UPDATE marketing.chat_auftraege w
     SET status = 'fehler', antwort = 'Der Assistent läuft am PC und ist gerade aus', geaendert_am = now()
    FROM (SELECT DISTINCT inhalt FROM tot) t
   WHERE w.inhalt = t.inhalt AND w.status = 'wartet';
  UPDATE marketing.chat_auftraege
     SET status = CASE WHEN versuche < 2 THEN 'offen' ELSE 'fehler' END,
         antwort = CASE WHEN versuche < 2 THEN antwort ELSE 'Der Assistent ist nicht fertig geworden' END,
         bereit_am = CASE WHEN versuche < 2 THEN now() ELSE bereit_am END,
         zwischenstand = NULL, schritt = '',
         vergeben_bis = NULL, geaendert_am = now()
   WHERE status = 'in_arbeit' AND vergeben_bis < now() AND stopp IS NULL
     AND (p_inhalt IS NULL OR inhalt = p_inhalt);
  FOR v_i IN SELECT DISTINCT inhalt FROM marketing.chat_auftraege
              WHERE status = 'wartet' AND (p_inhalt IS NULL OR inhalt = p_inhalt) LOOP
    PERFORM marketing._chat_nachruecken(v_i);
  END LOOP;
END $$;

-- 5) Anlegen (aus 063): Grenzen statt "ein laufender"; ein Export laeuft allein
CREATE OR REPLACE FUNCTION marketing.pult_chat_anlegen(
    p_inhalt uuid, p_art text, p_nachricht text, p_kontext jsonb) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v_art text; v_status text; v_neueste int; v_id uuid; v_wartend int;
BEGIN
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
  IF EXISTS (SELECT 1 FROM marketing.chat_auftraege
              WHERE inhalt = p_inhalt AND art = 'export' AND status IN ('offen','in_arbeit'))
     OR (p_art = 'export' AND marketing._chat_laufend(p_inhalt) > 0) THEN
    RAISE EXCEPTION 'Der Assistent arbeitet gerade'; END IF;
  SELECT max(fassung) INTO v_neueste FROM marketing.inhalt_fassungen WHERE inhalt = p_inhalt;
  IF p_art = 'chat' THEN
    SELECT count(*) INTO v_wartend FROM marketing.chat_auftraege WHERE inhalt = p_inhalt AND status = 'wartet';
    IF marketing._chat_laufend(p_inhalt) >= 3 OR v_wartend > 0 THEN
      IF v_wartend >= 5 THEN RAISE EXCEPTION 'Bitte warten, bis eine Runde fertig ist'; END IF;
      INSERT INTO marketing.chat_auftraege (inhalt, art, nachricht, kontext, status)
      VALUES (p_inhalt, 'chat', btrim(p_nachricht), coalesce(p_kontext, '{}'::jsonb), 'wartet')
      RETURNING id INTO v_id;
      RETURN v_id;
    END IF;
  END IF;
  INSERT INTO marketing.chat_auftraege (inhalt, art, nachricht, kontext, fassung_vorher)
  VALUES (p_inhalt, p_art, btrim(coalesce(p_nachricht, '')), coalesce(p_kontext, '{}'::jsonb), v_neueste)
  RETURNING id INTO v_id;
  RETURN v_id;
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_chat_senden(p_inhalt uuid, p_nachricht text, p_kontext jsonb) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE v_id uuid;
BEGIN
  v_id := marketing.pult_chat_anlegen(p_inhalt, 'chat', p_nachricht, p_kontext);
  RETURN jsonb_build_object('id', v_id, 'status', (SELECT status FROM marketing.chat_auftraege WHERE id = v_id));
END $$;

-- 6) fertig mit Basis (aus 061; die Vormerk-Freigabe entfaellt, statt dessen nachruecken)
DROP FUNCTION IF EXISTS marketing.pult_chat_fertig(uuid, text, jsonb, jsonb, jsonb);
CREATE OR REPLACE FUNCTION marketing.pult_chat_fertig(
    p_auftrag uuid, p_antwort text, p_bloecke jsonb, p_hinweise jsonb, p_ergebnis jsonb, p_basis int DEFAULT NULL)
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
  IF p_bloecke IS NOT NULL THEN
    SELECT felder INTO f FROM marketing.inhalt_fassungen WHERE inhalt = a.inhalt ORDER BY fassung DESC LIMIT 1;
    v_n := marketing.pult_bloecke_speichern(a.inhalt, v_basis, f.felder->>'betreff',
             coalesce(f.felder->>'vorschautext', ''), p_bloecke, 'agent', false);
  END IF;
  UPDATE marketing.chat_auftraege
     SET status = 'fertig', antwort = left(coalesce(p_antwort, ''), 4000),
         hinweise = coalesce(p_hinweise, '[]'::jsonb), ergebnis = coalesce(p_ergebnis, '{}'::jsonb),
         fassung_vorher = CASE WHEN p_bloecke IS NOT NULL THEN v_basis ELSE fassung_vorher END,
         fassung_nachher = v_n, vergeben_bis = NULL,
         zwischenstand = NULL, schritt = '', geaendert_am = now()
   WHERE id = a.id;
  PERFORM marketing._chat_nachruecken(a.inhalt);
  RETURN jsonb_build_object('fassung', v_n);
END $$;

-- 7) zurueck (aus 061) + Sperre des Inhalts zuerst + nachruecken
CREATE OR REPLACE FUNCTION marketing.pult_chat_zurueck(p_auftrag uuid, p_antwort text) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE a marketing.chat_auftraege; v_inhalt uuid;
BEGIN
  SELECT inhalt INTO v_inhalt FROM marketing.chat_auftraege WHERE id = p_auftrag;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannter Auftrag'; END IF;
  PERFORM 1 FROM marketing.inhalte WHERE id = v_inhalt FOR UPDATE;
  SELECT * INTO a FROM marketing.chat_auftraege WHERE id = p_auftrag FOR UPDATE;
  IF a.status NOT IN ('offen','in_arbeit') THEN RETURN a.status; END IF;   -- schon erledigt: nichts zu tun
  UPDATE marketing.chat_auftraege
     SET status = 'fehler', antwort = left(coalesce(p_antwort, ''), 4000),
         vergeben_bis = NULL, zwischenstand = NULL, schritt = '', geaendert_am = now()
   WHERE id = a.id;
  PERFORM marketing._chat_nachruecken(a.inhalt);
  RETURN 'fehler';
END $$;

-- 8) Stopp pro Runde (aus 061)
CREATE OR REPLACE FUNCTION marketing.pult_chat_stoppen(
    p_inhalt uuid, p_art text, p_auftrag uuid DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE a marketing.chat_auftraege; v_n int;
BEGIN
  IF p_art IS NULL OR p_art NOT IN ('behalten','verwerfen') THEN
    RAISE EXCEPTION 'Stopp muss behalten oder verwerfen sein'; END IF;
  PERFORM 1 FROM marketing.inhalte WHERE id = p_inhalt FOR UPDATE;
  PERFORM marketing.pult_chat_aufraeumen(p_inhalt);
  IF p_auftrag IS NULL THEN
    v_n := marketing._chat_laufend(p_inhalt);
    IF v_n = 0 THEN RAISE EXCEPTION 'Der Assistent arbeitet gerade nicht'; END IF;
    IF v_n > 1 THEN RAISE EXCEPTION 'Mehrere Runden laufen – bitte die Runde wählen'; END IF;
    SELECT * INTO a FROM marketing.chat_auftraege
     WHERE inhalt = p_inhalt AND status IN ('offen','in_arbeit') FOR UPDATE;
  ELSE
    SELECT * INTO a FROM marketing.chat_auftraege
     WHERE id = p_auftrag AND inhalt = p_inhalt AND status IN ('offen','in_arbeit','wartet') FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('abgeschlossen', false, 'veraltet', true); END IF;
  END IF;
  IF a.status IN ('offen','wartet') THEN
    UPDATE marketing.chat_auftraege
       SET status = 'fehler', antwort = 'Gestoppt, bevor der Assistent begonnen hat',
           stopp = p_art, stopp_am = now(), vergeben_bis = NULL,
           zwischenstand = NULL, schritt = '', geaendert_am = now()
     WHERE id = a.id;
    PERFORM marketing._chat_nachruecken(p_inhalt);
    RETURN jsonb_build_object('abgeschlossen', true, 'id', a.id);
  END IF;
  UPDATE marketing.chat_auftraege
     SET stopp = p_art, stopp_am = coalesce(stopp_am, now()), geaendert_am = now()
   WHERE id = a.id;
  RETURN jsonb_build_object('abgeschlossen', false, 'id', a.id);
END $$;

-- 9) Stopp abschliessen mit Basis (aus 061)
DROP FUNCTION IF EXISTS marketing.pult_chat_stopp_abschliessen(uuid, jsonb, text);
CREATE OR REPLACE FUNCTION marketing.pult_chat_stopp_abschliessen(
    p_auftrag uuid, p_bloecke jsonb, p_hinweis text, p_basis int DEFAULT NULL) RETURNS jsonb
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
  IF p_bloecke IS NULL OR a.stopp = 'verwerfen' THEN
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
  SELECT felder INTO f FROM marketing.inhalt_fassungen WHERE inhalt = a.inhalt ORDER BY fassung DESC LIMIT 1;
  v_n := marketing.pult_bloecke_speichern(a.inhalt, v_basis, f.felder->>'betreff',
           coalesce(f.felder->>'vorschautext', ''), p_bloecke, 'agent', false);
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

-- 10) Speichern (Huelle aus 063, woertlich bis auf bereit_am): Betreiber gesperrt, solange eine Runde laeuft
CREATE OR REPLACE FUNCTION marketing.pult_bloecke_speichern(
    p_inhalt uuid, p_basis int, p_betreff text, p_vorschautext text,
    p_bloecke jsonb, p_urheber text, p_als_kopie boolean) RETURNS int
LANGUAGE plpgsql AS $$
DECLARE v_status text;
BEGIN
  SELECT status INTO v_status FROM marketing.inhalte WHERE id = p_inhalt FOR UPDATE;
  IF v_status = 'eingereicht' THEN RAISE EXCEPTION 'Liegt zur Freigabe – erst zurückziehen'; END IF;
  IF p_urheber = 'betreiber' THEN
    IF EXISTS (SELECT 1 FROM marketing.chat_auftraege
                WHERE inhalt = p_inhalt AND status IN ('offen','in_arbeit')
                  AND (vergeben_bis IS NULL OR vergeben_bis > now())
                  AND NOT (status = 'offen' AND coalesce(bereit_am, erstellt_am) < now() - interval '2 minutes')) THEN
      RAISE EXCEPTION 'Der Assistent arbeitet gerade'; END IF;
  END IF;
  RETURN marketing._pult_bloecke_speichern_053(p_inhalt, p_basis, p_betreff, p_vorschautext,
                                               p_bloecke, p_urheber, p_als_kopie);
END $$;

-- 11) Bild-Hinweise einer Runde
CREATE OR REPLACE FUNCTION marketing.pult_chat_bild_ids(p_auftrag uuid, p_ids jsonb) RETURNS boolean
LANGUAGE plpgsql AS $$
BEGIN
  IF p_ids IS NULL OR jsonb_typeof(p_ids) <> 'array' OR jsonb_array_length(p_ids) > 10 THEN
    RAISE EXCEPTION 'Bild-Ids muessen eine Liste mit hoechstens 10 Eintraegen sein'; END IF;
  UPDATE marketing.chat_auftraege SET ergebnis = ergebnis || jsonb_build_object('bild_ids', p_ids)
   WHERE id = p_auftrag AND status = 'fertig';
  RETURN FOUND;
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_chat_bild_hinweise(p_ergebnis jsonb) RETURNS jsonb
LANGUAGE sql STABLE AS $$
  SELECT coalesce(jsonb_agg('Bild für ' || coalesce(b.platz, 'alle Plätze') || ' nicht erzeugt: '
                            || coalesce(nullif(btrim(b.befund), ''), 'ohne Befund') ORDER BY b.erstellt_am), '[]'::jsonb)
    FROM marketing.bild_auftraege b
   WHERE b.status = 'fehler'
     AND b.id::text IN (SELECT jsonb_array_elements_text(
           CASE WHEN jsonb_typeof(p_ergebnis->'bild_ids') = 'array' THEN p_ergebnis->'bild_ids' ELSE '[]'::jsonb END))
$$;

COMMIT;
