-- RUNBOOK: nach 071. Idempotent, eine Transaktion. Nach jedem Replay verify_072 laufen lassen.
-- 072: Reel-Auftraege fuer den Video-Claw (17.10.2026, Plan docs/superpowers/plans/2026-10-17-video-claw-reels.md).
-- Betreiber (Pult) oder Marketing-Agent bestellen ein Reel; der Video-Claw am PC baut es mit Laura und legt
-- ein MP4 in die erzeugten Medien. Versendet wird nichts - die Datei geht nur ueber versand_beauftragen ->
-- sales-claw mit Freigabe durch einen Menschen. Schreiben nur ueber Funktionen (API liest per SELECT ... FROM).
BEGIN;

CREATE TABLE IF NOT EXISTS marketing.video_auftraege (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    mandant         text NOT NULL REFERENCES marketing.mandanten(id),
    art             text NOT NULL CHECK (art IN ('material', 'bilder', 'kurzfassung')),
    titel           text NOT NULL CHECK (length(btrim(titel)) BETWEEN 1 AND 120),
    briefing        jsonb NOT NULL CHECK (jsonb_typeof(briefing) = 'object'),
    status          text NOT NULL DEFAULT 'offen'
                    CHECK (status IN ('offen', 'in_arbeit', 'fertig', 'fehler', 'abgebrochen')),
    vergeben_bis    timestamptz,
    versuche        int NOT NULL DEFAULT 0,
    datei           text CHECK (datei IS NULL OR datei ~ '^vid-[0-9a-f]{8}-[a-z0-9-]{1,40}\.mp4$'),
    dauer_s         numeric CHECK (dauer_s IS NULL OR dauer_s >= 0),
    bytes           bigint CHECK (bytes IS NULL OR bytes > 0),
    laura_export_id text,
    befund          text NOT NULL DEFAULT '',
    protokoll       jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(protokoll) = 'array'),
    angelegt_von    text NOT NULL CHECK (angelegt_von IN ('agent', 'betreiber')),
    erstellt_am     timestamptz NOT NULL DEFAULT now(),
    geaendert_am    timestamptz NOT NULL DEFAULT now(),
    CHECK (status <> 'fertig' OR datei IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS video_auftraege_offen_idx ON marketing.video_auftraege (erstellt_am)
    WHERE status IN ('offen', 'in_arbeit');
CREATE INDEX IF NOT EXISTS video_auftraege_mandant_idx ON marketing.video_auftraege (mandant, erstellt_am DESC);

CREATE OR REPLACE FUNCTION marketing.video_auftrag_anlegen(p_mandant text, p_art text, p_titel text,
                                                           p_briefing jsonb, p_von text) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v uuid;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM marketing.mandanten WHERE id = p_mandant) THEN
    RAISE EXCEPTION 'Unbekannte firma'; END IF;
  INSERT INTO marketing.video_auftraege (mandant, art, titel, briefing, angelegt_von)
  VALUES (p_mandant, p_art, btrim(p_titel), p_briefing, p_von)
  RETURNING id INTO v;
  RETURN v;
END $$;

-- Naechster Auftrag: offen, oder in Arbeit mit abgelaufener Frist (Arbeiter abgestuerzt). Ein Reel ist
-- teuer (Claude-Lauf + Render): nach zwei Versuchen fehler statt Endlosschleife.
CREATE OR REPLACE FUNCTION marketing.video_naechster(p_frist interval) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE a marketing.video_auftraege;
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
  RETURN jsonb_build_object('id', a.id, 'mandant', a.mandant, 'art', a.art, 'titel', a.titel,
                            'briefing', a.briefing, 'versuch', a.versuche + 1);
END $$;

-- Frist verlaengern; false, wenn der Auftrag nicht mehr in Arbeit ist (z.B. im Pult abgebrochen) -
-- daran erkennt der Arbeiter, dass er aufhoeren soll.
CREATE OR REPLACE FUNCTION marketing.video_verlaengern(p_id uuid, p_frist interval) RETURNS boolean
LANGUAGE sql AS $$
  WITH x AS (UPDATE marketing.video_auftraege SET vergeben_bis = now() + p_frist, geaendert_am = now()
              WHERE id = p_id AND status = 'in_arbeit' RETURNING 1)
  SELECT EXISTS (SELECT 1 FROM x);
$$;

-- Notiz des Video-Claws ins Protokoll (was gewaehlt, warum); hoechstens 60 Eintraege je Auftrag.
CREATE OR REPLACE FUNCTION marketing.video_notiz(p_id uuid, p_text text) RETURNS boolean
LANGUAGE sql AS $$
  WITH x AS (UPDATE marketing.video_auftraege
                SET protokoll = protokoll || jsonb_build_array(jsonb_build_object(
                                  'am', now(), 'text', left(btrim(coalesce(p_text, '')), 500))),
                    geaendert_am = now()
              WHERE id = p_id AND status = 'in_arbeit' AND jsonb_array_length(protokoll) < 60
                AND length(btrim(coalesce(p_text, ''))) > 0
             RETURNING 1)
  SELECT EXISTS (SELECT 1 FROM x);
$$;

-- Abschluss: fertig mit Datei oder fehler mit Befund. Nur aus in_arbeit.
CREATE OR REPLACE FUNCTION marketing.video_abschliessen(p_id uuid, p_datei text, p_dauer numeric,
                                                        p_bytes bigint, p_export text, p_befund text)
RETURNS text LANGUAGE plpgsql AS $$
DECLARE v text;
BEGIN
  UPDATE marketing.video_auftraege
     SET status = CASE WHEN p_datei IS NULL THEN 'fehler' ELSE 'fertig' END,
         datei = p_datei, dauer_s = p_dauer, bytes = p_bytes, laura_export_id = p_export,
         befund = left(coalesce(p_befund, ''), 500), vergeben_bis = NULL, geaendert_am = now()
   WHERE id = p_id AND status = 'in_arbeit'
  RETURNING status INTO v;
  RETURN v;
END $$;

-- Abbrechen durch den Betreiber: aus offen oder in_arbeit.
CREATE OR REPLACE FUNCTION marketing.video_abbrechen(p_id uuid) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE v text;
BEGIN
  UPDATE marketing.video_auftraege SET status = 'abgebrochen', vergeben_bis = NULL,
         befund = 'Im Pult abgebrochen', geaendert_am = now()
   WHERE id = p_id AND status IN ('offen', 'in_arbeit')
  RETURNING status INTO v;
  IF v IS NULL THEN RAISE EXCEPTION 'Auftrag ist nicht mehr offen'; END IF;
  RETURN v;
END $$;

-- Funktionen sind standardmaessig fuer PUBLIC ausfuehrbar: nur die Marketing-API (Eigentuemer) schreibt.
REVOKE ALL ON FUNCTION marketing.video_auftrag_anlegen(text, text, text, jsonb, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_naechster(interval) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_verlaengern(uuid, interval) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_notiz(uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_abschliessen(uuid, text, numeric, bigint, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_abbrechen(uuid) FROM PUBLIC;

COMMIT;
