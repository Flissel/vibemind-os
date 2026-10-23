-- RUNBOOK: nach 085 (video_auftraege mit Galerie). Idempotent, eine Transaktion. Nach jedem Replay
-- verify_090 laufen lassen. Achtung: 072 danach nicht einzeln wiederholen - es legte die alte
-- 5-stellige video_auftrag_anlegen wieder an (Aufrufe mit fuenf Argumenten waeren dann mehrdeutig).
-- 090: Reels aus KI-Bildern (23.10.2026, Plan spaces/sales-claw/docs/superpowers/plans/
-- 2026-10-23-reels-stufe3-ki-bilder.md, E1/E2/E9). Ein Reel-Auftrag mit briefing.ki = {anzahl: 3..5}
-- durchlaeuft vor 'offen' drei neue Status: ideen -> bilder_laufen -> bilder_pruefen. Der Bild-Arbeiter
-- schreibt die Ideen und erzeugt je Bildplatz eine Zeile in marketing.reel_bilder; der Betreiber prueft,
-- laesst einzelne neu erzeugen und gibt frei (briefing.bilder, Status offen). Der Video-Claw
-- (video_naechster, unveraendert) sieht die neuen Status nie. Schreiben nur ueber Funktionen.
BEGIN;

ALTER TABLE marketing.video_auftraege DROP CONSTRAINT IF EXISTS video_auftraege_status_check;
ALTER TABLE marketing.video_auftraege ADD CONSTRAINT video_auftraege_status_check
    CHECK (status IN ('offen', 'in_arbeit', 'fertig', 'fehler', 'abgebrochen',
                      'ideen', 'bilder_laufen', 'bilder_pruefen'));

-- Ein Bildplatz je Zeile. Neu erzeugen verwirft die alte Zeile und legt eine neue mit versuch+1 an:
-- der Dateiname ki-<auftrag8>-<nr>-<versuch>.jpg ist je Versuch neu, nichts wird ueberschrieben.
-- vergaben zaehlt die Abholungen durch den Arbeiter (Frist abgelaufen = Absturz); nach zwei ohne
-- Ergebnis wird das Bild fehler.
CREATE TABLE IF NOT EXISTS marketing.reel_bilder (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    video_auftrag uuid NOT NULL REFERENCES marketing.video_auftraege(id) ON DELETE CASCADE,
    nr            smallint NOT NULL CHECK (nr BETWEEN 1 AND 5),
    idee          text NOT NULL CHECK (char_length(idee) BETWEEN 1 AND 600),
    status        text NOT NULL DEFAULT 'offen'
                  CHECK (status IN ('offen', 'in_arbeit', 'fertig', 'fehler', 'verworfen')),
    datei         text CHECK (datei IS NULL OR datei ~ '^ki-[0-9a-f]{8}-[1-5]-[0-9]{1,3}\.jpg$'),
    versuch       smallint NOT NULL DEFAULT 1 CHECK (versuch BETWEEN 1 AND 999),
    vergaben      smallint NOT NULL DEFAULT 0,
    vergeben_bis  timestamptz,
    befund        text NOT NULL DEFAULT '',
    erstellt_am   timestamptz NOT NULL DEFAULT now(),
    geaendert_am  timestamptz NOT NULL DEFAULT now(),
    CHECK (status <> 'fertig' OR datei IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS reel_bilder_ein_aktives ON marketing.reel_bilder (video_auftrag, nr)
    WHERE status <> 'verworfen';
CREATE INDEX IF NOT EXISTS reel_bilder_offen_idx ON marketing.reel_bilder (video_auftrag, nr)
    WHERE status IN ('offen', 'in_arbeit');

-- Anlegen wie in 072, zusaetzlich mit Startstatus: offen (Vorgabe) oder ideen (KI-Bilder; nur art
-- bilder mit briefing.ki.anzahl 3..5). Erst die 5-stellige Fassung entfernen, sonst waeren Aufrufe mit
-- fuenf Argumenten mehrdeutig.
DROP FUNCTION IF EXISTS marketing.video_auftrag_anlegen(text, text, text, jsonb, text);
CREATE OR REPLACE FUNCTION marketing.video_auftrag_anlegen(p_mandant text, p_art text, p_titel text,
                                                           p_briefing jsonb, p_von text,
                                                           p_status text DEFAULT 'offen') RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v uuid; s text := coalesce(p_status, 'offen'); n text;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM marketing.mandanten WHERE id = p_mandant) THEN
    RAISE EXCEPTION 'Unbekannte firma'; END IF;
  IF s NOT IN ('offen', 'ideen') THEN
    RAISE EXCEPTION 'Neue Auftraege beginnen offen oder bei den Bildideen'; END IF;
  IF s = 'ideen' THEN
    n := p_briefing->'ki'->>'anzahl';
    IF p_art <> 'bilder' OR jsonb_typeof(p_briefing->'ki'->'anzahl') IS DISTINCT FROM 'number'
       OR n NOT IN ('3', '4', '5') THEN
      RAISE EXCEPTION 'KI-Bilder: art bilder mit ki.anzahl 3 bis 5'; END IF;
  END IF;
  INSERT INTO marketing.video_auftraege (mandant, art, titel, briefing, angelegt_von, status)
  VALUES (p_mandant, p_art, btrim(p_titel), p_briefing, p_von, s)
  RETURNING id INTO v;
  RETURN v;
END $$;

-- Interner Schritt: sind alle aktiven Bilder eines laufenden Auftrags fertig oder fehler, wartet er auf
-- die Pruefung (bilder_pruefen). Der Aufrufer haelt die Sperre auf den Auftrag.
CREATE OR REPLACE FUNCTION marketing._reel_bilder_abschliessen(p_auftrag uuid) RETURNS void
LANGUAGE sql AS $$
  UPDATE marketing.video_auftraege SET status = 'bilder_pruefen', geaendert_am = now()
   WHERE id = p_auftrag AND status = 'bilder_laufen'
     AND NOT EXISTS (SELECT 1 FROM marketing.reel_bilder
                      WHERE video_auftrag = p_auftrag AND status IN ('offen', 'in_arbeit'));
$$;

-- Naechster Auftrag fuer die Bildideen: Status ideen, Frist frei oder abgelaufen. Der Status bleibt ideen
-- (in_arbeit gehoert dem Video-Claw); die Frist steht in vergeben_bis, die Vergaben in versuche. Nach zwei
-- Vergaben ohne Ergebnis -> fehler.
CREATE OR REPLACE FUNCTION marketing.video_ideen_naechster(p_frist interval) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE a marketing.video_auftraege;
BEGIN
  UPDATE marketing.video_auftraege SET status = 'fehler', vergeben_bis = NULL, geaendert_am = now(),
         befund = 'Bildideen konnten nicht erzeugt werden (zwei Versuche ohne Ergebnis)'
   WHERE status = 'ideen' AND vergeben_bis < now() AND versuche >= 2;
  SELECT * INTO a FROM marketing.video_auftraege
   WHERE status = 'ideen' AND (vergeben_bis IS NULL OR vergeben_bis < now())
   ORDER BY erstellt_am LIMIT 1 FOR UPDATE SKIP LOCKED;
  IF a.id IS NULL THEN RETURN NULL; END IF;
  UPDATE marketing.video_auftraege SET vergeben_bis = now() + p_frist, versuche = versuche + 1,
         geaendert_am = now()
   WHERE id = a.id;
  RETURN jsonb_build_object('id', a.id, 'mandant', a.mandant, 'titel', a.titel, 'briefing', a.briefing);
END $$;

-- Ideen setzen: Array aus genau briefing.ki.anzahl Strings (getrimmt 1..600 Zeichen). Legt die Bildplaetze
-- 1..n an und setzt den Auftrag auf bilder_laufen; versuche/Frist gehen zurueck auf null fuer den Video-Claw.
-- Alles oder nichts: jeder Fehler wirft, bevor eine Zeile entsteht.
CREATE OR REPLACE FUNCTION marketing.video_ideen_setzen(p_id uuid, p_ideen jsonb) RETURNS int
LANGUAGE plpgsql AS $$
DECLARE a marketing.video_auftraege; n int; i int; t text;
BEGIN
  SELECT * INTO a FROM marketing.video_auftraege WHERE id = p_id FOR UPDATE;
  IF a.id IS NULL OR a.status <> 'ideen' THEN
    RAISE EXCEPTION 'Auftrag wartet nicht auf Bildideen'; END IF;
  -- erst die Form pruefen, dann casten: "vier" oder 3.5 geben die eigene Meldung, keinen Cast-Fehler
  IF jsonb_typeof(a.briefing->'ki'->'anzahl') IS DISTINCT FROM 'number'
     OR (a.briefing->'ki'->>'anzahl') NOT IN ('3', '4', '5') THEN
    RAISE EXCEPTION 'Kein Auftrag mit KI-Bildern'; END IF;
  n := (a.briefing->'ki'->>'anzahl')::int;
  IF jsonb_typeof(p_ideen) IS DISTINCT FROM 'array' OR jsonb_array_length(p_ideen) <> n THEN
    RAISE EXCEPTION 'Es braucht genau % Bildideen', n; END IF;
  FOR i IN 0 .. n - 1 LOOP
    IF jsonb_typeof(p_ideen->i) <> 'string'
       OR char_length(btrim(p_ideen->>i)) NOT BETWEEN 1 AND 600 THEN
      RAISE EXCEPTION 'Bildidee % muss 1 bis 600 Zeichen Text sein', i + 1; END IF;
  END LOOP;
  IF EXISTS (SELECT 1 FROM marketing.reel_bilder WHERE video_auftrag = p_id) THEN
    RAISE EXCEPTION 'Bildideen sind schon gesetzt'; END IF;
  FOR i IN 0 .. n - 1 LOOP
    t := btrim(p_ideen->>i);
    INSERT INTO marketing.reel_bilder (video_auftrag, nr, idee) VALUES (p_id, i + 1, t);
  END LOOP;
  UPDATE marketing.video_auftraege SET status = 'bilder_laufen', vergeben_bis = NULL, versuche = 0,
         geaendert_am = now()
   WHERE id = p_id;
  RETURN n;
END $$;

-- Ideen gescheitert (Shim und Ollama): beim ersten Mal bleibt der Auftrag bei ideen und ist sofort wieder
-- abholbar, ab der zweiten Vergabe -> fehler. Rueckgabe: neuer Status, NULL wenn nicht bei ideen.
CREATE OR REPLACE FUNCTION marketing.video_ideen_fehler(p_id uuid, p_befund text) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE v text; d text := left(btrim(coalesce(p_befund, '')), 300);
BEGIN
  UPDATE marketing.video_auftraege
     SET status = CASE WHEN versuche >= 2 THEN 'fehler' ELSE 'ideen' END,
         befund = CASE WHEN versuche >= 2
                       THEN 'Bildideen konnten nicht erzeugt werden'
                            || CASE WHEN d <> '' THEN ' (' || d || ')' ELSE '' END
                       ELSE befund END,
         vergeben_bis = NULL, geaendert_am = now()
   WHERE id = p_id AND status = 'ideen'
  RETURNING status INTO v;
  RETURN v;
END $$;

-- Naechstes Bild: offen oder in Arbeit mit abgelaufener Frist, nur von Auftraegen in bilder_laufen,
-- aeltester Auftrag zuerst, dann nach nr. Nach zwei Vergaben ohne Ergebnis -> fehler (der Auftrag geht
-- dann ggf. auf bilder_pruefen). Liefert auch Firma, Titel und Format (briefing.format, Vorgabe reel).
CREATE OR REPLACE FUNCTION marketing.reel_bild_naechster(p_frist interval) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE b marketing.reel_bilder; a marketing.video_auftraege; t uuid;
BEGIN
  -- Sperren in derselben Reihenfolge wie reel_bild_fertig: erst Auftrag, dann Bild
  FOR t IN SELECT DISTINCT video_auftrag FROM marketing.reel_bilder
            WHERE status = 'in_arbeit' AND vergeben_bis < now() AND vergaben >= 2
            ORDER BY video_auftrag LOOP
    PERFORM 1 FROM marketing.video_auftraege WHERE id = t FOR UPDATE;
    UPDATE marketing.reel_bilder SET status = 'fehler', vergeben_bis = NULL, geaendert_am = now(),
           befund = 'Zwei Versuche ohne Ergebnis'
     WHERE video_auftrag = t AND status = 'in_arbeit' AND vergeben_bis < now() AND vergaben >= 2;
    PERFORM marketing._reel_bilder_abschliessen(t);
  END LOOP;
  SELECT r.* INTO b FROM marketing.reel_bilder r JOIN marketing.video_auftraege v ON v.id = r.video_auftrag
   WHERE v.status = 'bilder_laufen'
     AND (r.status = 'offen' OR (r.status = 'in_arbeit' AND r.vergeben_bis < now()))
   ORDER BY v.erstellt_am, r.nr LIMIT 1 FOR UPDATE OF r SKIP LOCKED;
  IF b.id IS NULL THEN RETURN NULL; END IF;
  UPDATE marketing.reel_bilder SET status = 'in_arbeit', vergeben_bis = now() + p_frist,
         vergaben = vergaben + 1, geaendert_am = now()
   WHERE id = b.id;
  SELECT * INTO a FROM marketing.video_auftraege WHERE id = b.video_auftrag;
  RETURN jsonb_build_object('id', b.id, 'video_auftrag', b.video_auftrag, 'nr', b.nr, 'idee', b.idee,
                            'versuch', b.versuch, 'mandant', a.mandant, 'titel', a.titel,
                            'format', coalesce(a.briefing->>'format', 'reel'));
END $$;

-- Frist verlaengern; false, wenn das Bild nicht mehr in Arbeit ist oder der Auftrag nicht mehr laeuft
-- (z.B. abgebrochen) - daran erkennt der Arbeiter, dass er aufhoeren soll.
CREATE OR REPLACE FUNCTION marketing.reel_bild_verlaengern(p_id uuid, p_frist interval) RETURNS boolean
LANGUAGE sql AS $$
  WITH x AS (UPDATE marketing.reel_bilder r SET vergeben_bis = now() + p_frist, geaendert_am = now()
               FROM marketing.video_auftraege v
              WHERE r.id = p_id AND r.status = 'in_arbeit' AND v.id = r.video_auftrag
                AND v.status = 'bilder_laufen'
             RETURNING 1)
  SELECT EXISTS (SELECT 1 FROM x);
$$;

-- Bild fertig: nur in Arbeit und nur mit genau dem Namen dieses Versuchs (ki-<auftrag8>-<nr>-<versuch>.jpg),
-- sonst Ausnahme. false, wenn das Bild nicht mehr in Arbeit ist oder der Auftrag nicht mehr laeuft.
CREATE OR REPLACE FUNCTION marketing.reel_bild_fertig(p_id uuid, p_datei text) RETURNS boolean
LANGUAGE plpgsql AS $$
DECLARE b marketing.reel_bilder; t uuid;
BEGIN
  SELECT video_auftrag INTO t FROM marketing.reel_bilder WHERE id = p_id;
  IF t IS NULL THEN RETURN false; END IF;
  PERFORM 1 FROM marketing.video_auftraege WHERE id = t FOR UPDATE;
  SELECT * INTO b FROM marketing.reel_bilder WHERE id = p_id FOR UPDATE;
  IF p_datei IS DISTINCT FROM 'ki-' || left(t::text, 8) || '-' || b.nr || '-' || b.versuch || '.jpg' THEN
    RAISE EXCEPTION 'Dateiname passt nicht zu diesem Bild'; END IF;
  IF b.status <> 'in_arbeit'
     OR NOT EXISTS (SELECT 1 FROM marketing.video_auftraege WHERE id = t AND status = 'bilder_laufen') THEN
    RETURN false; END IF;
  UPDATE marketing.reel_bilder SET status = 'fertig', datei = p_datei, vergeben_bis = NULL, befund = '',
         geaendert_am = now()
   WHERE id = p_id;
  PERFORM marketing._reel_bilder_abschliessen(t);
  RETURN true;
END $$;

-- Bild gescheitert: wie fertig, nur mit Befund statt Datei.
CREATE OR REPLACE FUNCTION marketing.reel_bild_fehler(p_id uuid, p_befund text) RETURNS boolean
LANGUAGE plpgsql AS $$
DECLARE b marketing.reel_bilder; t uuid;
BEGIN
  SELECT video_auftrag INTO t FROM marketing.reel_bilder WHERE id = p_id;
  IF t IS NULL THEN RETURN false; END IF;
  PERFORM 1 FROM marketing.video_auftraege WHERE id = t FOR UPDATE;
  SELECT * INTO b FROM marketing.reel_bilder WHERE id = p_id FOR UPDATE;
  IF b.status <> 'in_arbeit'
     OR NOT EXISTS (SELECT 1 FROM marketing.video_auftraege WHERE id = t AND status = 'bilder_laufen') THEN
    RETURN false; END IF;
  UPDATE marketing.reel_bilder SET status = 'fehler', vergeben_bis = NULL,
         befund = left(btrim(coalesce(p_befund, '')), 500), geaendert_am = now()
   WHERE id = p_id;
  PERFORM marketing._reel_bilder_abschliessen(t);
  RETURN true;
END $$;

-- Neu erzeugen (Betreiber): nur bei bilder_pruefen, nur ein fertiges oder fehlgeschlagenes Bild der eigenen
-- Firma. Die alte Zeile wird verworfen (Datei bleibt in den Medien), die neue hat dieselbe nr, versuch+1 und
-- dieselbe oder die neue Idee; der Auftrag geht zurueck auf bilder_laufen. Rueckgabe: id der neuen Zeile.
CREATE OR REPLACE FUNCTION marketing.reel_bild_neu(p_id uuid, p_mandant text, p_idee text DEFAULT NULL)
RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE b marketing.reel_bilder; t uuid; i text := btrim(coalesce(p_idee, '')); v uuid;
BEGIN
  SELECT r.video_auftrag INTO t FROM marketing.reel_bilder r
    JOIN marketing.video_auftraege a ON a.id = r.video_auftrag
   WHERE r.id = p_id AND a.mandant = p_mandant;
  IF t IS NULL THEN RAISE EXCEPTION 'Bild nicht gefunden'; END IF;
  PERFORM 1 FROM marketing.video_auftraege WHERE id = t FOR UPDATE;
  IF NOT EXISTS (SELECT 1 FROM marketing.video_auftraege WHERE id = t AND status = 'bilder_pruefen') THEN
    RAISE EXCEPTION 'Bilder lassen sich nur beim Prüfen neu erzeugen'; END IF;
  SELECT * INTO b FROM marketing.reel_bilder WHERE id = p_id FOR UPDATE;
  IF b.status NOT IN ('fertig', 'fehler') THEN
    RAISE EXCEPTION 'Dieses Bild lässt sich nicht neu erzeugen'; END IF;
  IF char_length(i) > 600 THEN
    RAISE EXCEPTION 'Die Bildidee ist zu lang (höchstens 600 Zeichen)'; END IF;
  IF b.versuch >= 999 THEN RAISE EXCEPTION 'Zu viele Versuche für dieses Bild'; END IF;
  UPDATE marketing.reel_bilder SET status = 'verworfen', vergeben_bis = NULL, geaendert_am = now()
   WHERE id = p_id;
  INSERT INTO marketing.reel_bilder (video_auftrag, nr, idee, versuch)
  VALUES (t, b.nr, CASE WHEN i = '' THEN b.idee ELSE i END, b.versuch + 1)
  RETURNING id INTO v;
  UPDATE marketing.video_auftraege SET status = 'bilder_laufen', geaendert_am = now() WHERE id = t;
  RETURN v;
END $$;

-- Freigeben (Betreiber): nur bei bilder_pruefen mit mindestens einem fertigen Bild der eigenen Firma.
-- briefing.bilder = Dateien der fertigen Bilder in nr-Reihenfolge, Status offen -> der Video-Claw baut.
CREATE OR REPLACE FUNCTION marketing.video_ki_freigeben(p_id uuid, p_mandant text) RETURNS boolean
LANGUAGE plpgsql AS $$
DECLARE a marketing.video_auftraege; namen jsonb;
BEGIN
  SELECT * INTO a FROM marketing.video_auftraege WHERE id = p_id AND mandant = p_mandant FOR UPDATE;
  IF a.id IS NULL THEN RAISE EXCEPTION 'Auftrag nicht gefunden'; END IF;
  IF a.status <> 'bilder_pruefen' THEN
    RAISE EXCEPTION 'Das Reel wartet nicht auf die Bildprüfung'; END IF;
  SELECT jsonb_agg(datei ORDER BY nr) INTO namen FROM marketing.reel_bilder
   WHERE video_auftrag = p_id AND status = 'fertig';
  IF namen IS NULL THEN RAISE EXCEPTION 'Es gibt noch kein fertiges Bild'; END IF;
  UPDATE marketing.video_auftraege
     SET briefing = briefing || jsonb_build_object('bilder', namen), status = 'offen',
         vergeben_bis = NULL, versuche = 0, geaendert_am = now()
   WHERE id = p_id;
  RETURN true;
END $$;

-- Abbrechen wie in 072, zusaetzlich aus den drei KI-Status.
CREATE OR REPLACE FUNCTION marketing.video_abbrechen(p_id uuid) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE v text;
BEGIN
  UPDATE marketing.video_auftraege SET status = 'abgebrochen', vergeben_bis = NULL,
         befund = 'Im Pult abgebrochen', geaendert_am = now()
   WHERE id = p_id AND status IN ('offen', 'in_arbeit', 'ideen', 'bilder_laufen', 'bilder_pruefen')
  RETURNING status INTO v;
  IF v IS NULL THEN RAISE EXCEPTION 'Auftrag ist nicht mehr offen'; END IF;
  RETURN v;
END $$;

-- Nochmal wie in 085 (alle Regeln unveraendert), dazu KI-Bilder: hat das alte Briefing ki, aber keine
-- (oder eine leere) Bilderliste - gescheitert bei den Ideen oder Bildern -, startet der neue Auftrag bei
-- ideen ohne briefing.bilder. Mit Bildern wie bisher offen mit denselben Bildern. Ein laufender Neubau
-- sperrt auch in den KI-Status.
CREATE OR REPLACE FUNCTION marketing.video_nochmal(p_alt uuid, p_wunsch text, p_von text,
                                                  p_stimme text DEFAULT NULL,
                                                  p_stimme_hinweis text DEFAULT NULL) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE a marketing.video_auftraege; w text := btrim(coalesce(p_wunsch, '')); vg uuid; v uuid; b jsonb;
        s text := 'offen';
BEGIN
  SELECT * INTO a FROM marketing.video_auftraege WHERE id = p_alt;
  IF a.id IS NULL OR a.status NOT IN ('fertig', 'fehler') THEN
    RAISE EXCEPTION 'Nur fertige oder fehlgeschlagene Reels lassen sich neu bauen'; END IF;
  IF a.status = 'fertig' AND char_length(w) = 0 THEN
    RAISE EXCEPTION 'Bitte beschreiben, was am Reel anders werden soll'; END IF;
  IF char_length(w) > 500 THEN
    RAISE EXCEPTION 'Der Änderungswunsch ist zu lang (höchstens 500 Zeichen)'; END IF;
  vg := a.id;
  IF a.status = 'fehler' AND char_length(w) = 0 AND a.vorgaenger IS NOT NULL THEN
    vg := a.vorgaenger; w := a.aenderungswunsch;
  END IF;
  -- Sperre auf die Vorgaenger-Zeile: zwei gleichzeitige Klicks legen nicht zwei Bauten an.
  PERFORM 1 FROM marketing.video_auftraege WHERE id = vg FOR UPDATE;
  IF EXISTS (SELECT 1 FROM marketing.video_auftraege
              WHERE vorgaenger = vg
                AND status IN ('offen', 'in_arbeit', 'ideen', 'bilder_laufen', 'bilder_pruefen')) THEN
    RAISE EXCEPTION 'Es läuft schon ein neuer Bau für dieses Reel'; END IF;
  b := a.briefing - 'stimme_hinweis';
  IF p_stimme IS NOT NULL THEN b := b || jsonb_build_object('stimme', p_stimme); END IF;
  IF p_stimme_hinweis IS NOT NULL THEN
    b := b || jsonb_build_object('stimme_hinweis', left(p_stimme_hinweis, 200)); END IF;
  IF a.art = 'bilder' AND b ? 'ki'
     AND (jsonb_typeof(b->'bilder') IS DISTINCT FROM 'array' OR jsonb_array_length(b->'bilder') = 0) THEN
    s := 'ideen'; b := b - 'bilder';
  END IF;
  INSERT INTO marketing.video_auftraege (mandant, art, titel, briefing, angelegt_von, vorgaenger,
                                         aenderungswunsch, status)
  VALUES (a.mandant, a.art, a.titel, b, p_von, vg, w, s)
  RETURNING id INTO v;
  RETURN v;
END $$;

-- Funktionen sind standardmaessig fuer PUBLIC ausfuehrbar: nur die Marketing-API (Eigentuemer) schreibt.
REVOKE ALL ON FUNCTION marketing.video_nochmal(uuid, text, text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_auftrag_anlegen(text, text, text, jsonb, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing._reel_bilder_abschliessen(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_ideen_naechster(interval) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_ideen_setzen(uuid, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_ideen_fehler(uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.reel_bild_naechster(interval) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.reel_bild_verlaengern(uuid, interval) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.reel_bild_fertig(uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.reel_bild_fehler(uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.reel_bild_neu(uuid, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_ki_freigeben(uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_abbrechen(uuid) FROM PUBLIC;

COMMIT;
