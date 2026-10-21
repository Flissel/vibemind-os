-- RUNBOOK: nach 072 (video_auftraege). Idempotent, eine Transaktion. Nach jedem Replay verify_085 laufen lassen.
-- 085: Reels-Galerie (21.10.2026, Plan spaces/sales-claw/docs/superpowers/plans/2026-10-21-reels-stufe1.md, E1).
-- Ein Reel laesst sich mit Aenderungswunsch neu bauen (neuer Auftrag mit Vorgaenger), fehlgeschlagene neu
-- versuchen, erledigte archivieren, und der Video-Claw legt ein Vorschaubild (poster) ab. Der Claw bekommt
-- ueber video_naechster den Wunsch und den Vorgaenger (Titel, Protokoll, Datei). Schreiben nur ueber Funktionen.
BEGIN;

ALTER TABLE marketing.video_auftraege
    ADD COLUMN IF NOT EXISTS vorgaenger uuid REFERENCES marketing.video_auftraege(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS aenderungswunsch text NOT NULL DEFAULT ''
        CHECK (char_length(aenderungswunsch) <= 500),
    ADD COLUMN IF NOT EXISTS archiviert boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS poster text
        CHECK (poster IS NULL OR poster ~ '^vid-[0-9a-f]{8}-[a-z0-9-]{1,40}\.jpg$');

-- Nochmal: aus fertig (mit Wunsch) oder fehler (Wunsch darf leer sein = neu versuchen) entsteht ein neuer
-- offener Auftrag mit denselben Eckdaten. Der alte bleibt, wie er ist. Neu versuchen einer gescheiterten
-- Ueberarbeitung (fehler, leerer Wunsch, hat Vorgaenger) wiederholt dieselbe Ueberarbeitung: Vorgaenger und
-- Wunsch kommen vom gescheiterten Auftrag. Laeuft fuer denselben Vorgaenger schon ein Bau, wird abgelehnt.
CREATE OR REPLACE FUNCTION marketing.video_nochmal(p_alt uuid, p_wunsch text, p_von text) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE a marketing.video_auftraege; w text := btrim(coalesce(p_wunsch, '')); vg uuid; v uuid;
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
              WHERE vorgaenger = vg AND status IN ('offen', 'in_arbeit')) THEN
    RAISE EXCEPTION 'Es läuft schon ein neuer Bau für dieses Reel'; END IF;
  INSERT INTO marketing.video_auftraege (mandant, art, titel, briefing, angelegt_von, vorgaenger, aenderungswunsch)
  VALUES (a.mandant, a.art, a.titel, a.briefing, p_von, vg, w)
  RETURNING id INTO v;
  RETURN v;
END $$;

-- Archivieren (p_an = true) oder wieder anzeigen (false); nur erledigte Auftraege, sonst false.
-- p_an NULL aendert nichts (false).
CREATE OR REPLACE FUNCTION marketing.video_archivieren(p_id uuid, p_an boolean) RETURNS boolean
LANGUAGE sql AS $$
  WITH x AS (UPDATE marketing.video_auftraege SET archiviert = p_an, geaendert_am = now()
              WHERE id = p_id AND p_an IS NOT NULL AND status IN ('fertig', 'fehler', 'abgebrochen')
             RETURNING 1)
  SELECT EXISTS (SELECT 1 FROM x);
$$;

-- Vorschaubild des Video-Claws: nur in Arbeit oder fertig, und nur ein Name mit dem Praefix des Auftrags
-- (vid-<erste 8 Zeichen der id>-); sonst false. Die Form prueft der Spalten-Check.
CREATE OR REPLACE FUNCTION marketing.video_poster(p_id uuid, p_poster text) RETURNS boolean
LANGUAGE sql AS $$
  WITH x AS (UPDATE marketing.video_auftraege SET poster = p_poster, geaendert_am = now()
              WHERE id = p_id AND status IN ('in_arbeit', 'fertig')
                AND left(coalesce(p_poster, ''), 13) = 'vid-' || left(p_id::text, 8) || '-'
             RETURNING 1)
  SELECT EXISTS (SELECT 1 FROM x);
$$;

-- Naechster Auftrag wie in 072, zusaetzlich mit aenderungswunsch und vorgaenger {id, titel, protokoll, datei}.
CREATE OR REPLACE FUNCTION marketing.video_naechster(p_frist interval) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE a marketing.video_auftraege; vg jsonb;
BEGIN
  UPDATE marketing.video_auftraege SET status = 'fehler', befund = 'Zwei Versuche ohne Ergebnis',
         vergeben_bis = NULL, geaendert_am = now()
   WHERE status = 'in_arbeit' AND vergeben_bis < now() AND versuche >= 2;
  SELECT * INTO a FROM marketing.video_auftraege
   WHERE status = 'offen' OR (status = 'in_arbeit' AND vergeben_bis < now())
   ORDER BY erstellt_am LIMIT 1 FOR UPDATE SKIP LOCKED;
  IF a.id IS NULL THEN RETURN NULL; END IF;
  UPDATE marketing.video_auftraege SET status = 'in_arbeit', vergeben_bis = now() + p_frist,
         versuche = versuche + 1, geaendert_am = now()
   WHERE id = a.id;
  SELECT jsonb_build_object('id', p.id, 'titel', p.titel, 'protokoll', p.protokoll, 'datei', p.datei)
    INTO vg FROM marketing.video_auftraege p WHERE p.id = a.vorgaenger;
  RETURN jsonb_build_object('id', a.id, 'mandant', a.mandant, 'art', a.art, 'titel', a.titel,
                            'briefing', a.briefing, 'versuch', a.versuche + 1,
                            'aenderungswunsch', a.aenderungswunsch,
                            'vorgaenger', coalesce(vg, 'null'::jsonb));
END $$;

-- Funktionen sind standardmaessig fuer PUBLIC ausfuehrbar: nur die Marketing-API (Eigentuemer) schreibt.
REVOKE ALL ON FUNCTION marketing.video_nochmal(uuid, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_archivieren(uuid, boolean) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_poster(uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_naechster(interval) FROM PUBLIC;

COMMIT;
